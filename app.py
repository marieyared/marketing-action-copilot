import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from io import StringIO

st.set_page_config(page_title="Marketing Action Copilot", layout="wide")

st.title("Marketing Action Copilot")
st.caption("Upload marketing data and get clear, decision-ready recommendations.")

uploaded_file = st.file_uploader("Upload marketing CSV", type=["csv"])


def safe_div(num, den):
    return np.where(den != 0, num / den, np.nan)


def format_eur(x):
    if pd.isna(x):
        return "N/A"
    return f"€{x:,.0f}"


def format_pct(x):
    if pd.isna(x):
        return "N/A"
    return f"{x:.1%}"


if uploaded_file:
    uploaded_file.seek(0)
    raw = uploaded_file.read().decode("utf-8-sig").strip()
    lines = raw.splitlines()
    cleaned_lines = [line.strip().strip('"') for line in lines if line.strip()]

    try:
        df = pd.read_csv(StringIO("\n".join(cleaned_lines)))
    except Exception as e:
        st.error(f"Could not read CSV: {e}")
        st.stop()

    df.columns = df.columns.str.strip().str.lower()
    df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)

    required_cols = [
        "date", "channel", "campaign",
        "spend", "impressions", "clicks", "leads", "sales", "revenue"
    ]

    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        st.error(f"Missing columns: {missing}")
        st.info(f"Found columns: {list(df.columns)}")
        st.stop()

    number_cols = ["spend", "impressions", "clicks", "leads", "sales", "revenue"]

    for col in number_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    if df["date"].isna().all():
        st.error("Date column could not be parsed.")
        st.stop()

    df = df.dropna(subset=["date", "channel", "campaign"])

    # Sidebar filters
    st.sidebar.header("Filters")

    min_date = df["date"].min().date()
    max_date = df["date"].max().date()

    date_range = st.sidebar.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date
    )

    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
        df = df[(df["date"].dt.date >= start_date) & (df["date"].dt.date <= end_date)]

    all_channels = sorted(df["channel"].dropna().unique())
    selected_channels = st.sidebar.multiselect(
        "Channels",
        all_channels,
        default=all_channels
    )

    if selected_channels:
        df = df[df["channel"].isin(selected_channels)]

    if df.empty:
        st.warning("No data matches the current filters.")
        st.stop()

    # Metrics
    df["ctr"] = safe_div(df["clicks"], df["impressions"])
    df["cpc"] = safe_div(df["spend"], df["clicks"])
    df["cpl"] = safe_div(df["spend"], df["leads"])
    df["cac"] = safe_div(df["spend"], df["sales"])
    df["conversion_rate"] = safe_div(df["sales"], df["clicks"])
    df["roas"] = safe_div(df["revenue"], df["spend"])
    df["profit_proxy"] = df["revenue"] - df["spend"]

    df = df.replace([np.inf, -np.inf], np.nan)

    channel_summary = df.groupby("channel").agg({
        "spend": "sum",
        "revenue": "sum",
        "clicks": "sum",
        "leads": "sum",
        "sales": "sum",
        "impressions": "sum"
    }).reset_index()

    channel_summary["roas"] = safe_div(channel_summary["revenue"], channel_summary["spend"])
    channel_summary["cac"] = safe_div(channel_summary["spend"], channel_summary["sales"])
    channel_summary["cpl"] = safe_div(channel_summary["spend"], channel_summary["leads"])
    channel_summary["ctr"] = safe_div(channel_summary["clicks"], channel_summary["impressions"])
    channel_summary["conversion_rate"] = safe_div(channel_summary["sales"], channel_summary["clicks"])
    channel_summary["profit_proxy"] = channel_summary["revenue"] - channel_summary["spend"]

    channel_summary = channel_summary.replace([np.inf, -np.inf], np.nan)

    avg_roas = channel_summary["roas"].mean()
    avg_cac = channel_summary["cac"].mean()

    best_channel = channel_summary.sort_values("roas", ascending=False).iloc[0]
    worst_channel = channel_summary.sort_values("roas", ascending=True).iloc[0]

    # Recommendation engine
    def recommend(row):
        roas = row["roas"]
        cac = row["cac"]

        if pd.isna(roas) or pd.isna(cac):
            return "Insufficient data to evaluate."

        roas_vs_avg = ((roas - avg_roas) / avg_roas * 100) if avg_roas else 0
        cac_vs_avg = ((cac - avg_cac) / avg_cac * 100) if avg_cac else 0

        if roas > avg_roas * 1.25 and cac < avg_cac:
            return (
                f"Scale this channel. It has strong efficiency with ROAS of {roas:.2f}x "
                f"({roas_vs_avg:+.0f}% vs average) and CAC of {format_eur(cac)}."
            )

        if roas < avg_roas * 0.75:
            return (
                f"Reduce or investigate this channel. ROAS is only {roas:.2f}x, "
                f"which is {abs(roas_vs_avg):.0f}% below average."
            )

        if cac > avg_cac * 1.25:
            return (
                f"Improve conversion quality. CAC is {format_eur(cac)}, "
                f"{cac_vs_avg:+.0f}% above average."
            )

        return (
            f"Maintain and monitor. Performance is stable with ROAS of {roas:.2f}x "
            f"and CAC of {format_eur(cac)}."
        )

    channel_summary["recommendation"] = channel_summary.apply(recommend, axis=1)

    # Executive Summary
    st.subheader("Executive Summary")

    total_spend = df["spend"].sum()
    total_revenue = df["revenue"].sum()
    total_sales = df["sales"].sum()
    total_roas = total_revenue / total_spend if total_spend else np.nan
    total_cac = total_spend / total_sales if total_sales else np.nan

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total Spend", format_eur(total_spend))
    col2.metric("Total Revenue", format_eur(total_revenue))
    col3.metric("Overall ROAS", f"{total_roas:.2f}x")
    col4.metric("Average CAC", format_eur(total_cac))

    # Top Actions
    st.subheader("Top Recommended Actions")

    if best_channel["channel"] != worst_channel["channel"]:
        potential_shift = worst_channel["spend"] * 0.15
        estimated_revenue_gain = potential_shift * best_channel["roas"]

        st.success(
            f"1. Reallocate approximately {format_eur(potential_shift)} "
            f"from {worst_channel['channel']} to {best_channel['channel']}. "
            f"Estimated revenue impact: around {format_eur(estimated_revenue_gain)}."
        )

    st.info(
        f"2. Best channel to scale: {best_channel['channel']} "
        f"with ROAS of {best_channel['roas']:.2f}x."
    )

    st.warning(
        f"3. Channel to review: {worst_channel['channel']} "
        f"with ROAS of {worst_channel['roas']:.2f}x."
    )

    # Channel performance
    st.subheader("Channel Performance")

    display_cols = [
        "channel", "spend", "revenue", "sales",
        "roas", "cac", "cpl", "conversion_rate", "recommendation"
    ]

    styled = (
        channel_summary[display_cols]
        .style
        .background_gradient(subset=["roas"], cmap="RdYlGn")
        .background_gradient(subset=["cac"], cmap="RdYlGn_r")
        .format({
            "spend": "€{:,.0f}",
            "revenue": "€{:,.0f}",
            "roas": "{:.2f}x",
            "cac": "€{:,.0f}",
            "cpl": "€{:,.0f}",
            "conversion_rate": "{:.1%}"
        })
    )

    st.dataframe(styled, use_container_width=True)

    # Charts
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Spend vs Revenue")
        fig = px.bar(
            channel_summary,
            x="channel",
            y=["spend", "revenue"],
            barmode="group"
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("ROAS by Channel")
        fig_roas = px.bar(
            channel_summary.sort_values("roas", ascending=False),
            x="channel",
            y="roas",
            text="roas"
        )
        fig_roas.update_traces(texttemplate="%{text:.2f}x", textposition="outside")
        st.plotly_chart(fig_roas, use_container_width=True)

    # Campaign drill-down
    st.subheader("Campaign Drill-down")

    filtered_channels = sorted(df["channel"].dropna().unique())
    selected_channel = st.selectbox(
        "Select a channel to explore campaigns",
        filtered_channels
    )

    campaign_df = df[df["channel"] == selected_channel].groupby("campaign").agg({
        "spend": "sum",
        "revenue": "sum",
        "clicks": "sum",
        "leads": "sum",
        "sales": "sum"
    }).reset_index()

    campaign_df["roas"] = safe_div(campaign_df["revenue"], campaign_df["spend"])
    campaign_df["cac"] = safe_div(campaign_df["spend"], campaign_df["sales"])
    campaign_df["cpl"] = safe_div(campaign_df["spend"], campaign_df["leads"])
    campaign_df["profit_proxy"] = campaign_df["revenue"] - campaign_df["spend"]

    campaign_df = campaign_df.replace([np.inf, -np.inf], np.nan)

    best_campaign = campaign_df.sort_values("roas", ascending=False).iloc[0]
    worst_campaign = campaign_df.sort_values("roas", ascending=True).iloc[0]

    st.write(
        f"Best campaign in **{selected_channel}**: "
        f"**{best_campaign['campaign']}** with ROAS of {best_campaign['roas']:.2f}x."
    )

    st.write(
        f"Campaign to review in **{selected_channel}**: "
        f"**{worst_campaign['campaign']}** with ROAS of {worst_campaign['roas']:.2f}x."
    )

    campaign_styled = (
        campaign_df
        .style
        .background_gradient(subset=["roas"], cmap="RdYlGn")
        .background_gradient(subset=["cac"], cmap="RdYlGn_r")
        .format({
            "spend": "€{:,.0f}",
            "revenue": "€{:,.0f}",
            "roas": "{:.2f}x",
            "cac": "€{:,.0f}",
            "cpl": "€{:,.0f}",
            "profit_proxy": "€{:,.0f}"
        })
    )

    st.dataframe(campaign_styled, use_container_width=True)

    fig2 = px.bar(
        campaign_df,
        x="campaign",
        y=["spend", "revenue"],
        barmode="group",
        title=f"{selected_channel} — Campaign Breakdown"
    )
    st.plotly_chart(fig2, use_container_width=True)

    # Recommended actions
    st.subheader("Detailed Recommended Actions")

    for _, row in channel_summary.sort_values("roas", ascending=False).iterrows():
        st.write(f"**{row['channel']}** — {row['recommendation']}")

else:
    st.info("Upload a CSV file to begin.")
