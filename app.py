import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from io import StringIO
from datetime import date, timedelta

st.set_page_config(page_title="Budget Pulse", layout="wide")

st.title("Budget Pulse")
st.caption("Rolling performance trends, end-of-month projections, and plain-language alerts.")

# ── helpers ──────────────────────────────────────────────────────────────────

def safe_div(num, den):
    return np.where(den != 0, num / den, np.nan)


def fmt_eur(x):
    if pd.isna(x):
        return "N/A"
    return f"€{x:,.0f}"


def fmt_pct(x):
    if pd.isna(x):
        return "N/A"
    return f"{x:.1%}"


def fmt_x(x):
    if pd.isna(x):
        return "N/A"
    return f"{x:.2f}x"


# ── file upload ───────────────────────────────────────────────────────────────

uploaded_file = st.file_uploader("Upload daily marketing CSV", type=["csv"])

st.markdown(
    "**Required columns:** `date`, `channel`, `campaign`, `spend`, "
    "`impressions`, `clicks`, `leads`, `sales`, `revenue`  \n"
    "**Expected granularity:** one row per day × channel × campaign (28–35 days works best)."
)

if not uploaded_file:
    st.info("Upload a CSV file to begin.")
    st.stop()

# ── parse ─────────────────────────────────────────────────────────────────────

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

required_cols = ["date", "channel", "campaign", "spend", "impressions",
                 "clicks", "leads", "sales", "revenue"]
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
    st.error("Date column could not be parsed.")
    st.stop()

# ── sidebar filters ───────────────────────────────────────────────────────────

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
selected_channels = st.sidebar.multiselect("Channels", all_channels, default=all_channels)
if selected_channels:
    df = df[df["channel"].isin(selected_channels)]

st.sidebar.markdown("---")
st.sidebar.subheader("Monthly budget cap")
st.sidebar.caption("Used for end-of-month projection.")

today_input = st.sidebar.date_input(
    "Today's date (for projection)",
    value=max_date,
    min_value=min_date,
    max_value=max_date + timedelta(days=31),
)
monthly_budget = st.sidebar.number_input(
    "Total monthly budget (€)", min_value=0, value=45000, step=1000
)

if df.empty:
    st.warning("No data matches the current filters.")
    st.stop()

# ── compute per-row metrics ───────────────────────────────────────────────────

df["roas"] = safe_div(df["revenue"], df["spend"])
df["cac"] = safe_div(df["spend"], df["sales"])
df["cpl"] = safe_div(df["spend"], df["leads"])
df["ctr"] = safe_div(df["clicks"], df["impressions"])
df["conversion_rate"] = safe_div(df["sales"], df["clicks"])
df = df.replace([np.inf, -np.inf], np.nan)

# ── rolling window trend (7d vs prior 7d) ────────────────────────────────────

data_max = df["date"].max()
cutoff_curr = data_max
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


curr = window_summary(df_curr).add_suffix("_curr").rename(columns={"channel_curr": "channel"})
prev = window_summary(df_prev).add_suffix("_prev").rename(columns={"channel_prev": "channel"})

trend = curr.merge(prev, on="channel", how="left")

trend["roas_delta_pct"] = safe_div(
    trend["roas_curr"] - trend["roas_prev"], trend["roas_prev"]
) * 100
trend["cac_delta_pct"] = safe_div(
    trend["cac_curr"] - trend["cac_prev"], trend["cac_prev"]
) * 100
trend["spend_delta_pct"] = safe_div(
    trend["spend_curr"] - trend["spend_prev"], trend["spend_prev"]
) * 100
trend = trend.replace([np.inf, -np.inf], np.nan)

# ── end-of-month projection ───────────────────────────────────────────────────

today_dt = pd.Timestamp(today_input)
month_start = today_dt.replace(day=1)
month_end = (month_start + pd.offsets.MonthEnd(0))
days_elapsed = max(1, (today_dt - month_start).days + 1)
days_remaining = max(0, (month_end - today_dt).days)
days_in_month = (month_end - month_start).days + 1

month_df = df[df["date"] >= month_start]
spent_to_date = month_df["spend"].sum()
revenue_to_date = month_df["revenue"].sum()

daily_spend_rate = spent_to_date / days_elapsed
daily_revenue_rate = revenue_to_date / days_elapsed

projected_spend = spent_to_date + daily_spend_rate * days_remaining
projected_revenue = revenue_to_date + daily_revenue_rate * days_remaining
projected_roas = projected_revenue / projected_spend if projected_spend else np.nan
budget_variance = projected_spend - monthly_budget
budget_variance_pct = (budget_variance / monthly_budget * 100) if monthly_budget else np.nan

# ── alert engine ─────────────────────────────────────────────────────────────

ROAS_URGENT = -40    # % drop → Urgent
ROAS_ACT = -25       # % drop → Act
CAC_ACT = 30         # % increase → Act


def alert_level(row):
    rd = row.get("roas_delta_pct", np.nan)
    cd = row.get("cac_delta_pct", np.nan)
    if pd.isna(rd) and pd.isna(cd):
        return "insufficient_data"
    if (not pd.isna(rd) and rd <= ROAS_URGENT):
        return "urgent"
    if (not pd.isna(rd) and rd <= ROAS_ACT) or (not pd.isna(cd) and cd >= CAC_ACT * 1.5):
        return "act"
    if (not pd.isna(rd) and rd <= -10) or (not pd.isna(cd) and cd >= CAC_ACT):
        return "watch"
    return "stable"


def alert_text(row):
    ch = row["channel"]
    rd = row.get("roas_delta_pct", np.nan)
    cd = row.get("cac_delta_pct", np.nan)
    roas_curr = row.get("roas_curr", np.nan)
    roas_prev = row.get("roas_prev", np.nan)
    cac_curr = row.get("cac_curr", np.nan)
    daily_spend = row.get("daily_spend_curr", np.nan)
    days_left = days_remaining

    level = alert_level(row)

    if level == "urgent":
        waste = daily_spend * days_left if not pd.isna(daily_spend) else np.nan
        return (
            f"ROAS dropped {abs(rd):.0f}% in 7 days "
            f"({fmt_x(roas_prev)} → {fmt_x(roas_curr)}). "
            f"At {fmt_eur(daily_spend)}/day, ~{fmt_eur(waste)} more at risk this month. "
            f"**Pause or cut spend by 50–60% immediately.** Investigate creative fatigue or audience saturation."
        )
    elif level == "act":
        if not pd.isna(rd) and rd <= ROAS_ACT:
            return (
                f"ROAS down {abs(rd):.0f}% ({fmt_x(roas_prev)} → {fmt_x(roas_curr)}). "
                f"Efficiency eroding. "
                f"**Review within 3 days:** pause worst-performing campaigns, shift budget to top performers."
            )
        else:
            return (
                f"CAC up {cd:.0f}% to {fmt_eur(cac_curr)}. Leads getting more expensive. "
                f"**Tighten targeting or refresh creatives within 3 days.**"
            )
    elif level == "watch":
        return (
            f"Mild decline — ROAS {fmt_x(roas_curr)} ({rd:+.0f}% vs last week). "
            f"Monitor daily. No action needed yet."
        )
    elif level == "stable":
        return (
            f"Performing well — ROAS {fmt_x(roas_curr)} ({rd:+.0f}% vs last week). "
            f"No action needed."
        )
    else:
        return "Insufficient data to evaluate trend."


trend["alert_level"] = trend.apply(alert_level, axis=1)
trend["alert_text"] = trend.apply(alert_text, axis=1)

level_order = {"urgent": 0, "act": 1, "watch": 2, "stable": 3, "insufficient_data": 4}
trend["_sort"] = trend["alert_level"].map(level_order)
trend = trend.sort_values("_sort").drop(columns="_sort")

# ═══════════════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. executive summary ──────────────────────────────────────────────────────

st.subheader("Executive summary")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Spend to date", fmt_eur(spent_to_date))
c2.metric("Revenue to date", fmt_eur(revenue_to_date))
c3.metric(
    "Projected EOM spend",
    fmt_eur(projected_spend),
    delta=f"{fmt_eur(abs(budget_variance))} {'over' if budget_variance > 0 else 'under'} budget",
    delta_color="inverse" if budget_variance > 0 else "normal",
)
c4.metric(
    "Projected EOM ROAS",
    fmt_x(projected_roas),
)

# ── 2. alert cards ────────────────────────────────────────────────────────────

st.subheader("Alerts — actions needed now")

ALERT_FN = {
    "urgent": st.error,
    "act":    st.warning,
    "watch":  st.info,
    "stable": st.success,
    "insufficient_data": st.info,
}
ALERT_PREFIX = {
    "urgent": "🔴 URGENT",
    "act":    "🟡 ACT WITHIN 3 DAYS",
    "watch":  "🔵 WATCH",
    "stable": "🟢 STABLE",
    "insufficient_data": "⚪ INSUFFICIENT DATA",
}

for _, row in trend.iterrows():
    lvl = row["alert_level"]
    ALERT_FN[lvl](f"**{ALERT_PREFIX[lvl]} — {row['channel']}**  \n{row['alert_text']}")

# ── 3. rolling trend chart ────────────────────────────────────────────────────

st.subheader("ROAS trend — last 7 days vs prior 7 days")

fig_trend = go.Figure()

fig_trend.add_trace(go.Bar(
    name="Prior 7d",
    x=trend["channel"],
    y=trend["roas_prev"].round(2),
    marker_color="#B5D4F4",
    text=trend["roas_prev"].apply(lambda v: fmt_x(v) if not pd.isna(v) else ""),
    textposition="outside",
))
fig_trend.add_trace(go.Bar(
    name="Last 7d",
    x=trend["channel"],
    y=trend["roas_curr"].round(2),
    marker_color="#185FA5",
    text=trend["roas_curr"].apply(lambda v: fmt_x(v) if not pd.isna(v) else ""),
    textposition="outside",
))

fig_trend.update_layout(
    barmode="group",
    yaxis_title="ROAS",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=360,
    margin=dict(t=40, b=20),
)
st.plotly_chart(fig_trend, use_container_width=True)

# ── 4. ROAS delta table ───────────────────────────────────────────────────────

st.subheader("Channel trend detail")

display_trend = trend[[
    "channel", "roas_prev", "roas_curr", "roas_delta_pct",
    "cac_curr", "cac_delta_pct", "daily_spend_curr", "alert_level"
]].copy()

display_trend.columns = [
    "Channel", "ROAS prior 7d", "ROAS last 7d", "ROAS Δ %",
    "CAC last 7d", "CAC Δ %", "Avg daily spend", "Alert level"
]

def color_delta(val):
    if pd.isna(val):
        return ""
    if val <= -25:
        return "color: #A32D2D; font-weight: 500"
    if val <= -10:
        return "color: #854F0B"
    if val >= 25:
        return "color: #3B6D11; font-weight: 500"
    return ""

styled_trend = (
    display_trend.style
    .applymap(color_delta, subset=["ROAS Δ %"])
    .format({
        "ROAS prior 7d": lambda v: fmt_x(v),
        "ROAS last 7d":  lambda v: fmt_x(v),
        "ROAS Δ %":      lambda v: f"{v:+.1f}%" if not pd.isna(v) else "N/A",
        "CAC last 7d":   lambda v: fmt_eur(v),
        "CAC Δ %":       lambda v: f"{v:+.1f}%" if not pd.isna(v) else "N/A",
        "Avg daily spend": lambda v: fmt_eur(v),
    })
)
st.dataframe(styled_trend, use_container_width=True)

# ── 5. end-of-month projection chart ─────────────────────────────────────────

st.subheader("End-of-month budget projection")

eom_labels = ["Spent to date", "Projected remaining", "Monthly budget"]
eom_values = [spent_to_date, max(0, projected_spend - spent_to_date), monthly_budget]
bar_colors = [
    "#185FA5",
    "#E24B4A" if projected_spend > monthly_budget else "#1D9E75",
    "#D3D1C7",
]

fig_eom = go.Figure()
fig_eom.add_trace(go.Bar(
    name="Spent to date",
    x=["Budget projection"],
    y=[spent_to_date],
    marker_color="#185FA5",
    text=[fmt_eur(spent_to_date)],
    textposition="inside",
))
fig_eom.add_trace(go.Bar(
    name="Projected remaining",
    x=["Budget projection"],
    y=[max(0, projected_spend - spent_to_date)],
    marker_color="#E24B4A" if projected_spend > monthly_budget else "#85B7EB",
    text=[fmt_eur(max(0, projected_spend - spent_to_date))],
    textposition="inside",
))
fig_eom.add_hline(
    y=monthly_budget,
    line_dash="dash",
    line_color="#888780",
    annotation_text=f"Budget cap: {fmt_eur(monthly_budget)}",
    annotation_position="top right",
)
fig_eom.update_layout(
    barmode="stack",
    yaxis_title="Spend (€)",
    height=300,
    margin=dict(t=40, b=20),
    showlegend=True,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_eom, use_container_width=True)

st.caption(
    f"Projection based on {days_elapsed} days of data. "
    f"{days_remaining} days remaining in the month. "
    f"Daily burn rate: {fmt_eur(daily_spend_rate)}."
)

# ── 6. daily time-series ──────────────────────────────────────────────────────

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
    annotation_text="Break-even (1x)",
    annotation_position="bottom right",
)
fig_ts.update_layout(
    height=360,
    margin=dict(t=20, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_ts, use_container_width=True)

# ── 7. daily spend by channel ─────────────────────────────────────────────────

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
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_spend, use_container_width=True)

# ── 8. original channel summary (carried forward from MVP) ───────────────────

st.subheader("Overall channel performance (full period)")

channel_summary = df.groupby("channel").agg(
    spend=("spend", "sum"),
    revenue=("revenue", "sum"),
    clicks=("clicks", "sum"),
    leads=("leads", "sum"),
    sales=("sales", "sum"),
    impressions=("impressions", "sum"),
).reset_index()

channel_summary["roas"]            = safe_div(channel_summary["revenue"], channel_summary["spend"])
channel_summary["cac"]             = safe_div(channel_summary["spend"],   channel_summary["sales"])
channel_summary["cpl"]             = safe_div(channel_summary["spend"],   channel_summary["leads"])
channel_summary["conversion_rate"] = safe_div(channel_summary["sales"],   channel_summary["clicks"])
channel_summary["profit_proxy"]    = channel_summary["revenue"] - channel_summary["spend"]
channel_summary = channel_summary.replace([np.inf, -np.inf], np.nan)

styled_summary = (
    channel_summary[[
        "channel", "spend", "revenue", "sales",
        "roas", "cac", "cpl", "conversion_rate"
    ]]
    .style
    .background_gradient(subset=["roas"], cmap="RdYlGn")
    .background_gradient(subset=["cac"],  cmap="RdYlGn_r")
    .format({
        "spend":           "€{:,.0f}",
        "revenue":         "€{:,.0f}",
        "roas":            "{:.2f}x",
        "cac":             "€{:,.0f}",
        "cpl":             "€{:,.0f}",
        "conversion_rate": "{:.1%}",
    })
)
st.dataframe(styled_summary, use_container_width=True)

# ── 9. campaign drill-down ────────────────────────────────────────────────────

st.subheader("Campaign drill-down")

filtered_channels = sorted(df["channel"].dropna().unique())
selected_channel = st.selectbox("Select a channel", filtered_channels)

campaign_df = (
    df[df["channel"] == selected_channel]
    .groupby("campaign")
    .agg(spend=("spend","sum"), revenue=("revenue","sum"),
         clicks=("clicks","sum"), leads=("leads","sum"), sales=("sales","sum"))
    .reset_index()
)
campaign_df["roas"]         = safe_div(campaign_df["revenue"], campaign_df["spend"])
campaign_df["cac"]          = safe_div(campaign_df["spend"],   campaign_df["sales"])
campaign_df["cpl"]          = safe_div(campaign_df["spend"],   campaign_df["leads"])
campaign_df["profit_proxy"] = campaign_df["revenue"] - campaign_df["spend"]
campaign_df = campaign_df.replace([np.inf, -np.inf], np.nan)

best = campaign_df.sort_values("roas", ascending=False).iloc[0]
worst = campaign_df.sort_values("roas", ascending=True).iloc[0]
st.write(
    f"Best in **{selected_channel}**: **{best['campaign']}** — {fmt_x(best['roas'])} ROAS  \n"
    f"Needs review: **{worst['campaign']}** — {fmt_x(worst['roas'])} ROAS"
)

campaign_styled = (
    campaign_df.style
    .background_gradient(subset=["roas"], cmap="RdYlGn")
    .background_gradient(subset=["cac"],  cmap="RdYlGn_r")
    .format({
        "spend":         "€{:,.0f}",
        "revenue":       "€{:,.0f}",
        "roas":          "{:.2f}x",
        "cac":           "€{:,.0f}",
        "cpl":           "€{:,.0f}",
        "profit_proxy":  "€{:,.0f}",
    })
)
st.dataframe(campaign_styled, use_container_width=True)

# Campaign daily ROAS trend
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
    title=f"{selected_channel} — daily ROAS by campaign",
    labels={"roas": "ROAS", "date": ""},
)
fig_camp.update_layout(height=320, margin=dict(t=40, b=20))
st.plotly_chart(fig_camp, use_container_width=True)
