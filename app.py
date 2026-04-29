import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from io import StringIO
from datetime import timedelta

st.set_page_config(page_title="Budget Pulse", layout="wide")

# ── Helpers ──────────────────────────────────────────────────────────────────

def safe_div(num, den):
    return np.where(den != 0, num / den, np.nan)


def fmt_eur(x):
    if pd.isna(x):
        return "N/A"
    return f"€{x:,.0f}"


def fmt_x(x):
    if pd.isna(x):
        return "N/A"
    return f"{x:.2f}x"


def paragraph(*lines):
    return "\n\n".join([str(line).strip().replace("—", "-") for line in lines if line])


def pct_text(x):
    if pd.isna(x):
        return "N/A"
    return f"{x:+.1f}%"


def signal_label(row):
    rd = row.get("roas_delta_pct", np.nan)
    cd = row.get("cac_delta_pct", np.nan)

    if pd.isna(rd) and pd.isna(cd):
        return "Not enough data"

    if not pd.isna(rd) and rd <= -25:
        return "Efficiency weakening"

    if not pd.isna(cd) and cd >= 45:
        return "Acquisition cost rising"

    if not pd.isna(rd) and rd <= -10:
        return "Slight decline"

    return "Stable"


def signal_note(row):
    label = row["signal"]
    ch = row["channel"]

    if label == "Efficiency weakening":
        return f"{ch} is generating less revenue per euro spent than last week."

    if label == "Acquisition cost rising":
        return f"{ch} is becoming more expensive to convert into customers."

    if label == "Slight decline":
        return f"{ch} has softened slightly, but the movement is not severe."

    if label == "Stable":
        return f"{ch} is broadly consistent compared with last week."

    return f"{ch} does not have enough comparison data yet."


# ── Page style ───────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    .summary-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 16px;
        margin-top: 18px;
        margin-bottom: 32px;
    }

    .summary-card {
        border: 1px solid #E6E8EF;
        border-radius: 18px;
        padding: 20px;
        background: #FFFFFF;
    }

    .summary-label {
        font-size: 13px;
        color: #71768A;
        margin-bottom: 8px;
    }

    .summary-value {
        font-size: 30px;
        font-weight: 750;
        color: #2F3140;
        margin-bottom: 6px;
    }

    .summary-note {
        font-size: 13px;
        color: #8A8FA3;
    }

    .budget-strip {
        border: 1px solid #E6E8EF;
        border-radius: 18px;
        padding: 22px;
        background: #F8F9FC;
        margin-bottom: 34px;
    }

    .signal-card {
        border: 1px solid #E6E8EF;
        border-radius: 14px;
        padding: 18px 20px;
        margin-bottom: 14px;
        background: #FFFFFF;
    }

    .signal-title {
        font-size: 18px;
        font-weight: 700;
        margin-bottom: 6px;
        color: #2F3140;
    }

    .signal-meta {
        font-size: 14px;
        color: #6F7485;
        margin-bottom: 12px;
    }

    .signal-note {
        font-size: 15px;
        color: #343747;
        margin-bottom: 10px;
    }

    .signal-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 12px;
        margin-top: 10px;
    }

    .mini-metric {
        background: #F8F9FC;
        border-radius: 10px;
        padding: 10px 12px;
    }

    .mini-label {
        font-size: 12px;
        color: #7B8092;
        margin-bottom: 3px;
    }

    .mini-value {
        font-size: 16px;
        font-weight: 650;
        color: #2F3140;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# ── Intro ────────────────────────────────────────────────────────────────────

st.title("Budget Pulse")
st.caption("A cleaner view of marketing performance, budget use, and weekly movement.")

st.markdown(
    paragraph(
        "This app looks at your marketing data and helps you understand what is improving, what is weakening, and where budget is going.",
        "The focus is not only on showing numbers, but on making the dashboard easier to read."
    )
)

with st.expander("How to read the main metrics"):
    st.markdown(
        paragraph(
            "**ROAS** shows how much revenue you generate for each euro spent.",
            "**CAC** shows how much it costs to acquire one customer.",
            "**CPL** shows how much it costs to generate one lead.",
            "**CTR** shows how often people click after seeing an ad.",
            "**Conversion rate** shows how many clicks become sales."
        )
    )


# ── Upload ───────────────────────────────────────────────────────────────────

uploaded_file = st.file_uploader("Upload daily marketing CSV", type=["csv"])

st.markdown(
    "Required columns: `date`, `channel`, `campaign`, `spend`, "
    "`impressions`, `clicks`, `leads`, `sales`, `revenue`."
)

if not uploaded_file:
    st.info("Upload a CSV file to begin.")
    st.stop()


# ── Parse CSV ────────────────────────────────────────────────────────────────

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
    "date", "channel", "campaign", "spend", "impressions",
    "clicks", "leads", "sales", "revenue"
]

missing = [c for c in required_cols if c not in df.columns]

if missing:
    st.error(f"Missing columns: {missing}")
    st.info(f"Found columns: {list(df.columns)}")
    st.stop()

numeric_cols = ["spend", "impressions", "clicks", "leads", "sales", "revenue"]

for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df["date"] = pd.to_datetime(df["date"], errors="coerce")
df = df.dropna(subset=["date", "channel", "campaign"])

if df["date"].isna().all():
    st.error("The date column could not be parsed.")
    st.stop()


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.header("Filters")

min_date = df["date"].min().date()
max_date = df["date"].max().date()

date_range = st.sidebar.date_input(
    "Date range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
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

st.sidebar.markdown("---")
st.sidebar.subheader("Budget context")

today_input = st.sidebar.date_input(
    "Today’s date",
    value=max_date,
    min_value=min_date,
    max_value=max_date + timedelta(days=31),
)

monthly_budget = st.sidebar.number_input(
    "Monthly budget (€)",
    min_value=0,
    value=45000,
    step=1000
)

if df.empty:
    st.warning("No data matches the current filters.")
    st.stop()


# ── Metrics ──────────────────────────────────────────────────────────────────

df["roas"] = safe_div(df["revenue"], df["spend"])
df["cac"] = safe_div(df["spend"], df["sales"])
df["cpl"] = safe_div(df["spend"], df["leads"])
df["ctr"] = safe_div(df["clicks"], df["impressions"])
df["conversion_rate"] = safe_div(df["sales"], df["clicks"])
df = df.replace([np.inf, -np.inf], np.nan)


# ── Weekly trend ─────────────────────────────────────────────────────────────

data_max = df["date"].max()
cutoff_mid = data_max - timedelta(days=7)
cutoff_prev = data_max - timedelta(days=14)

df_curr = df[df["date"] > cutoff_mid]
df_prev = df[(df["date"] > cutoff_prev) & (df["date"] <= cutoff_mid)]


def window_summary(frame):
    g = frame.groupby("channel").agg(
        spend=("spend", "sum"),
        revenue=("revenue", "sum"),
        clicks=("clicks", "sum"),
        leads=("leads", "sum"),
        sales=("sales", "sum"),
        impressions=("impressions", "sum"),
    ).reset_index()

    g["roas"] = safe_div(g["revenue"], g["spend"])
    g["cac"] = safe_div(g["spend"], g["sales"])
    g["cpl"] = safe_div(g["spend"], g["leads"])
    g["daily_spend"] = g["spend"] / 7

    return g.replace([np.inf, -np.inf], np.nan)


curr = window_summary(df_curr).add_suffix("_curr").rename(
    columns={"channel_curr": "channel"}
)

prev = window_summary(df_prev).add_suffix("_prev").rename(
    columns={"channel_prev": "channel"}
)

trend = curr.merge(prev, on="channel", how="left")

trend["roas_delta_pct"] = safe_div(
    trend["roas_curr"] - trend["roas_prev"],
    trend["roas_prev"]
) * 100

trend["cac_delta_pct"] = safe_div(
    trend["cac_curr"] - trend["cac_prev"],
    trend["cac_prev"]
) * 100

trend["spend_delta_pct"] = safe_div(
    trend["spend_curr"] - trend["spend_prev"],
    trend["spend_prev"]
) * 100

trend = trend.replace([np.inf, -np.inf], np.nan)

trend["signal"] = trend.apply(signal_label, axis=1)
trend["signal_note"] = trend.apply(signal_note, axis=1)

signal_order = {
    "Efficiency weakening": 0,
    "Acquisition cost rising": 1,
    "Slight decline": 2,
    "Stable": 3,
    "Not enough data": 4,
}

trend["_sort"] = trend["signal"].map(signal_order)
trend = trend.sort_values("_sort").drop(columns="_sort")


# ── Month context ────────────────────────────────────────────────────────────

today_dt = pd.Timestamp(today_input)
month_start = today_dt.replace(day=1)
month_end = month_start + pd.offsets.MonthEnd(0)

days_elapsed = max(1, (today_dt - month_start).days + 1)

month_df = df[df["date"] >= month_start]

spent_to_date = month_df["spend"].sum()
daily_spend_rate = spent_to_date / days_elapsed
budget_remaining = monthly_budget - spent_to_date


# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════

# ── Executive summary ────────────────────────────────────────────────────────

total_spend = df["spend"].sum()
total_revenue = df["revenue"].sum()
total_sales = df["sales"].sum()
overall_roas = total_revenue / total_spend if total_spend else np.nan
overall_cac = total_spend / total_sales if total_sales else np.nan

st.subheader("Executive summary")

st.markdown(
    f"""
    <div class="summary-grid">
        <div class="summary-card">
            <div class="summary-label">Spend</div>
            <div class="summary-value">{fmt_eur(total_spend)}</div>
            <div class="summary-note">Total media spend</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">Revenue</div>
            <div class="summary-value">{fmt_eur(total_revenue)}</div>
            <div class="summary-note">Revenue generated</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">ROAS</div>
            <div class="summary-value">{fmt_x(overall_roas)}</div>
            <div class="summary-note">Revenue per euro spent</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">CAC</div>
            <div class="summary-value">{fmt_eur(overall_cac)}</div>
            <div class="summary-note">Cost per customer</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)


# ── Budget context, no graph ─────────────────────────────────────────────────

st.subheader("Budget context")

st.markdown(
    f"""
    <div class="budget-strip">
        <div class="summary-grid" style="margin: 0;">
            <div class="summary-card">
                <div class="summary-label">Monthly budget</div>
                <div class="summary-value">{fmt_eur(monthly_budget)}</div>
                <div class="summary-note">Planned spend</div>
            </div>
            <div class="summary-card">
                <div class="summary-label">Spent this month</div>
                <div class="summary-value">{fmt_eur(spent_to_date)}</div>
                <div class="summary-note">Actual spend so far</div>
            </div>
            <div class="summary-card">
                <div class="summary-label">Budget remaining</div>
                <div class="summary-value">{fmt_eur(budget_remaining)}</div>
                <div class="summary-note">Still available</div>
            </div>
            <div class="summary-card">
                <div class="summary-label">Daily pace</div>
                <div class="summary-value">{fmt_eur(daily_spend_rate)}</div>
                <div class="summary-note">Average spend per day</div>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)


# ── Signals, clean UI cards ──────────────────────────────────────────────────

st.subheader("Recent channel movement")

st.markdown(
    paragraph(
        "This section compares the most recent 7 days with the 7 days before that.",
        "It is meant to highlight movement, not give strict instructions."
    )
)

for _, row in trend.iterrows():
    st.markdown(
        f"""
        <div class="signal-card">
            <div class="signal-title">{row["channel"]}</div>
            <div class="signal-meta">{row["signal"]}</div>
            <div class="signal-note">{row["signal_note"]}</div>
            <div class="signal-grid">
                <div class="mini-metric">
                    <div class="mini-label">ROAS previous 7 days</div>
                    <div class="mini-value">{fmt_x(row.get("roas_prev", np.nan))}</div>
                </div>
                <div class="mini-metric">
                    <div class="mini-label">ROAS recent 7 days</div>
                    <div class="mini-value">{fmt_x(row.get("roas_curr", np.nan))}</div>
                </div>
                <div class="mini-metric">
                    <div class="mini-label">ROAS change</div>
                    <div class="mini-value">{pct_text(row.get("roas_delta_pct", np.nan))}</div>
                </div>
                <div class="mini-metric">
                    <div class="mini-label">CAC change</div>
                    <div class="mini-value">{pct_text(row.get("cac_delta_pct", np.nan))}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


# ── Weekly ROAS chart ────────────────────────────────────────────────────────

st.subheader("ROAS comparison by channel")

fig_trend = go.Figure()

fig_trend.add_trace(go.Bar(
    name="Previous 7 days",
    x=trend["channel"],
    y=trend["roas_prev"].round(2),
    marker_color="#B5D4F4",
    text=trend["roas_prev"].apply(lambda v: fmt_x(v) if not pd.isna(v) else ""),
    textposition="outside",
))

fig_trend.add_trace(go.Bar(
    name="Recent 7 days",
    x=trend["channel"],
    y=trend["roas_curr"].round(2),
    marker_color="#185FA5",
    text=trend["roas_curr"].apply(lambda v: fmt_x(v) if not pd.isna(v) else ""),
    textposition="outside",
))

fig_trend.update_layout(
    barmode="group",
    yaxis_title="ROAS",
    height=360,
    margin=dict(t=40, b=20),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1,
    ),
)

st.plotly_chart(fig_trend, use_container_width=True)


# ── Channel trend table ──────────────────────────────────────────────────────

st.subheader("Channel details")

display_trend = trend[[
    "channel",
    "signal",
    "roas_prev",
    "roas_curr",
    "roas_delta_pct",
    "cac_curr",
    "cac_delta_pct",
    "daily_spend_curr",
]].copy()

display_trend.columns = [
    "Channel",
    "Signal",
    "ROAS previous 7 days",
    "ROAS recent 7 days",
    "ROAS change",
    "CAC recent 7 days",
    "CAC change",
    "Average daily spend",
]


def color_delta(val):
    if pd.isna(val):
        return ""
    if val <= -25:
        return "color: #A32D2D; font-weight: 600"
    if val <= -10:
        return "color: #8A5A00; font-weight: 500"
    if val >= 25:
        return "color: #247A3E; font-weight: 600"
    return ""


styled_trend = (
    display_trend.style
    .map(color_delta, subset=["ROAS change"])
    .format({
        "ROAS previous 7 days": lambda v: fmt_x(v),
        "ROAS recent 7 days": lambda v: fmt_x(v),
        "ROAS change": lambda v: pct_text(v),
        "CAC recent 7 days": lambda v: fmt_eur(v),
        "CAC change": lambda v: pct_text(v),
        "Average daily spend": lambda v: fmt_eur(v),
    })
)

st.dataframe(styled_trend, use_container_width=True)


# ── Daily ROAS ───────────────────────────────────────────────────────────────

st.subheader("Daily ROAS by channel")

daily_ch = (
    df.groupby(["date", "channel"])
    .agg(spend=("spend", "sum"), revenue=("revenue", "sum"))
    .reset_index()
)

daily_ch["roas"] = safe_div(daily_ch["revenue"], daily_ch["spend"])
daily_ch = daily_ch.replace([np.inf, -np.inf], np.nan)

fig_ts = px.line(
    daily_ch,
    x="date",
    y="roas",
    color="channel",
    markers=False,
    labels={"roas": "ROAS", "date": ""},
)

fig_ts.add_hline(
    y=1.0,
    line_dash="dot",
    line_color="#E24B4A",
    annotation_text="Break-even 1.0x",
    annotation_position="bottom right",
)

fig_ts.update_layout(
    height=360,
    margin=dict(t=20, b=20),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1,
    ),
)

st.plotly_chart(fig_ts, use_container_width=True)


# ── Daily spend ──────────────────────────────────────────────────────────────

st.subheader("Daily spend by channel")

daily_spend_ch = (
    df.groupby(["date", "channel"])
    .agg(spend=("spend", "sum"))
    .reset_index()
)

fig_spend = px.area(
    daily_spend_ch,
    x="date",
    y="spend",
    color="channel",
    labels={"spend": "Spend (€)", "date": ""},
)

fig_spend.update_layout(
    height=320,
    margin=dict(t=20, b=20),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="right",
        x=1,
    ),
)

st.plotly_chart(fig_spend, use_container_width=True)


# ── Overall channel performance ──────────────────────────────────────────────

st.subheader("Overall channel performance")

channel_summary = df.groupby("channel").agg(
    spend=("spend", "sum"),
    revenue=("revenue", "sum"),
    clicks=("clicks", "sum"),
    leads=("leads", "sum"),
    sales=("sales", "sum"),
    impressions=("impressions", "sum"),
).reset_index()

channel_summary["roas"] = safe_div(channel_summary["revenue"], channel_summary["spend"])
channel_summary["cac"] = safe_div(channel_summary["spend"], channel_summary["sales"])
channel_summary["cpl"] = safe_div(channel_summary["spend"], channel_summary["leads"])
channel_summary["conversion_rate"] = safe_div(channel_summary["sales"], channel_summary["clicks"])
channel_summary["profit_proxy"] = channel_summary["revenue"] - channel_summary["spend"]

channel_summary = channel_summary.replace([np.inf, -np.inf], np.nan)

styled_summary = (
    channel_summary[[
        "channel", "spend", "revenue", "sales",
        "roas", "cac", "cpl", "conversion_rate"
    ]]
    .style
    .background_gradient(subset=["roas"], cmap="RdYlGn")
    .background_gradient(subset=["cac"], cmap="RdYlGn_r")
    .format({
        "spend": "€{:,.0f}",
        "revenue": "€{:,.0f}",
        "roas": "{:.2f}x",
        "cac": "€{:,.0f}",
        "cpl": "€{:,.0f}",
        "conversion_rate": "{:.1%}",
    })
)

st.dataframe(styled_summary, use_container_width=True)


# ── Campaign drill-down ──────────────────────────────────────────────────────

st.subheader("Campaign drill-down")

filtered_channels = sorted(df["channel"].dropna().unique())
selected_channel = st.selectbox("Select a channel to inspect", filtered_channels)

campaign_df = (
    df[df["channel"] == selected_channel]
    .groupby("campaign")
    .agg(
        spend=("spend", "sum"),
        revenue=("revenue", "sum"),
        clicks=("clicks", "sum"),
        leads=("leads", "sum"),
        sales=("sales", "sum"),
    )
    .reset_index()
)

campaign_df["roas"] = safe_div(campaign_df["revenue"], campaign_df["spend"])
campaign_df["cac"] = safe_div(campaign_df["spend"], campaign_df["sales"])
campaign_df["cpl"] = safe_div(campaign_df["spend"], campaign_df["leads"])
campaign_df["profit_proxy"] = campaign_df["revenue"] - campaign_df["spend"]

campaign_df = campaign_df.replace([np.inf, -np.inf], np.nan)

if not campaign_df.empty:
    best = campaign_df.sort_values("roas", ascending=False).iloc[0]
    worst = campaign_df.sort_values("roas", ascending=True).iloc[0]

    st.markdown(
        paragraph(
            f"Strongest campaign in **{selected_channel}**: **{best['campaign']}**, with {fmt_x(best['roas'])} ROAS.",
            f"Lowest ROAS campaign in **{selected_channel}**: **{worst['campaign']}**, with {fmt_x(worst['roas'])} ROAS."
        )
    )

campaign_styled = (
    campaign_df.style
    .background_gradient(subset=["roas"], cmap="RdYlGn")
    .background_gradient(subset=["cac"], cmap="RdYlGn_r")
    .format({
        "spend": "€{:,.0f}",
        "revenue": "€{:,.0f}",
        "roas": "{:.2f}x",
        "cac": "€{:,.0f}",
        "cpl": "€{:,.0f}",
        "profit_proxy": "€{:,.0f}",
    })
)

st.dataframe(campaign_styled, use_container_width=True)

camp_daily = (
    df[df["channel"] == selected_channel]
    .groupby(["date", "campaign"])
    .agg(spend=("spend", "sum"), revenue=("revenue", "sum"))
    .reset_index()
)

camp_daily["roas"] = safe_div(camp_daily["revenue"], camp_daily["spend"])
camp_daily = camp_daily.replace([np.inf, -np.inf], np.nan)

fig_camp = px.line(
    camp_daily,
    x="date",
    y="roas",
    color="campaign",
    markers=False,
    title=f"{selected_channel}: daily ROAS by campaign",
    labels={"roas": "ROAS", "date": ""},
)

fig_camp.update_layout(height=320, margin=dict(t=40, b=20))

st.plotly_chart(fig_camp, use_container_width=True)

