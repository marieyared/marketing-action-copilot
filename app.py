import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from io import StringIO, BytesIO
from datetime import date, timedelta
from fpdf import FPDF
from fpdf.enums import XPos, YPos

st.set_page_config(page_title="Budget Pulse", layout="wide")


# =============================================================================
# Helpers
# =============================================================================

def safe_div(num, den):
    return np.where(den != 0, num / den, np.nan)

def fmt_eur(x):
    if pd.isna(x): return "N/A"
    return f"€{x:,.0f}"

def fmt_x(x):
    if pd.isna(x): return "N/A"
    return f"{x:.2f}x"

def pct_text(x):
    if pd.isna(x): return "N/A"
    return f"{x:+.1f}%"

def pdf_safe(text):
    return (
        str(text)
        .replace("\u20ac", "EUR ")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


# =============================================================================
# Signal label
# =============================================================================

def signal_label(row):
    rd = row.get("roas_delta_pct", np.nan)
    cd = row.get("cac_delta_pct",  np.nan)
    if pd.isna(rd) and pd.isna(cd):   return "Not enough data"
    if not pd.isna(rd) and rd <= -25: return "Efficiency weakening"
    if not pd.isna(cd) and cd >= 45:  return "Acquisition cost rising"
    if not pd.isna(rd) and rd <= -10: return "ROAS slightly down"
    return "Stable"


# =============================================================================
# Diagnostic layer — the "why"
# One short sentence added to weak/declining channels only.
# Reads CTR delta, conversion rate delta, CPM delta together.
# =============================================================================

def diagnose(row):
    rd   = row.get("roas_delta_pct",  np.nan)
    ctr  = row.get("ctr_delta_pct",   np.nan)
    cvr  = row.get("cvr_delta_pct",   np.nan)
    cpm  = row.get("cpm_delta_pct",   np.nan)
    sig  = row.get("signal", "")

    # Only diagnose channels that are actually declining
    if sig not in ("Efficiency weakening", "Acquisition cost rising", "ROAS slightly down"):
        return ""

    # CTR dropped sharply -> audience has seen these ads too many times
    if not pd.isna(ctr) and ctr <= -20:
        return "CTR dropped sharply while spend held steady - likely creative fatigue. Rotate your creatives."

    # CTR stable but conversion rate dropped -> post-click problem
    if not pd.isna(ctr) and ctr > -10 and not pd.isna(cvr) and cvr <= -20:
        return "People are still clicking but fewer are converting - check your landing page or offer, not the ad itself."

    # CPM spiked -> auction pressure from competitors
    if not pd.isna(cpm) and cpm >= 25:
        return "Cost per impression rose significantly - a competitor may have increased bids in your auction."

    # CAC rising but ROAS only slightly down -> lead quality issue
    if sig == "Acquisition cost rising" and not pd.isna(rd) and rd > -15:
        return "Spend is holding but fewer leads are converting to sales - likely a lead quality or follow-up issue."

    # Generic fallback for confirmed decline with no clear signal
    if not pd.isna(rd) and rd <= -10:
        return "Cause unclear from this data alone - check platform-level frequency and audience overlap."

    return ""


# =============================================================================
# Signal note — the "what"
# =============================================================================

def signal_note(row):
    label    = row["signal"]
    ch       = row["channel"]
    rd       = row.get("roas_delta_pct", np.nan)
    rev_curr = row.get("revenue_curr",   np.nan)
    rev_prev = row.get("revenue_prev",   np.nan)
    cac_curr = row.get("cac_curr",       np.nan)

    if label == "Efficiency weakening":
        rev_diff = rev_curr - rev_prev if not (pd.isna(rev_curr) or pd.isna(rev_prev)) else np.nan
        diff_str = (
            f" That is {fmt_eur(abs(rev_diff))} less revenue than the week before, at the same spend."
        ) if not pd.isna(rev_diff) else ""
        return f"{ch} is generating less revenue per euro spent than last week (ROAS {pct_text(rd)}).{diff_str}"

    if label == "Acquisition cost rising":
        return (
            f"{ch} is becoming more expensive to convert into customers. "
            f"CAC is now {fmt_eur(cac_curr)}, above last week's level."
        )

    if label == "ROAS slightly down":
        return (
            f"{ch} has softened slightly (ROAS {pct_text(rd)} versus last week). "
            f"Worth monitoring before acting."
        )

    if label == "Stable":
        return f"{ch} is broadly consistent compared with last week. No action needed."

    return f"{ch} does not have enough comparison data yet."


# =============================================================================
# "So what" banner
# =============================================================================

def build_so_what(trend, projected_spend, monthly_budget):
    urgent  = trend[trend["signal"] == "Efficiency weakening"]
    rising  = trend[trend["signal"] == "Acquisition cost rising"]
    slight  = trend[trend["signal"] == "ROAS slightly down"]
    is_over = projected_spend > monthly_budget
    parts   = []

    if is_over:
        parts.append(
            f"You are on track to exceed your monthly budget by "
            f"{fmt_eur(projected_spend - monthly_budget)} at the current daily pace."
        )
    else:
        parts.append(
            f"Spend is on track, with {fmt_eur(monthly_budget - projected_spend)} of headroom remaining."
        )

    if not urgent.empty:
        names = " and ".join(urgent["channel"].tolist())
        worst = urgent.sort_values("roas_delta_pct").iloc[0]
        parts.append(
            f"{names} {'has' if len(urgent) == 1 else 'have'} deteriorated significantly "
            f"in the last 7 days (ROAS {pct_text(worst['roas_delta_pct'])}) "
            f"and {'deserves' if len(urgent) == 1 else 'deserve'} your attention today."
        )
    elif not rising.empty:
        names = " and ".join(rising["channel"].tolist())
        parts.append(
            f"{names} {'is' if len(rising) == 1 else 'are'} showing rising acquisition costs -"
            f"worth reviewing before end of week."
        )
    elif not slight.empty:
        names = " and ".join(slight["channel"].tolist())
        parts.append(f"{names} has softened slightly but nothing requiring immediate action.")
    else:
        parts.append("All channels are broadly stable compared with last week.")

    stable = trend[trend["signal"] == "Stable"]
    if not stable.empty and not urgent.empty:
        best = stable.sort_values("roas_curr", ascending=False).iloc[0]
        parts.append(
            f"{best['channel']} remains your strongest channel at "
            f"{fmt_x(best['roas_curr'])} ROAS - a candidate to absorb reallocated budget."
        )

    return " ".join(parts)


# =============================================================================
# PDF export
# =============================================================================

def generate_pdf(so_what, trend, spent_to_date, projected_spend,
                 monthly_budget, days_elapsed, days_remaining,
                 projected_roas, daily_spend_rate):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(20, 20, 20)

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(47, 49, 64)
    pdf.cell(0, 12, "Budget Pulse", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(113, 118, 138)
    pdf.cell(0, 6, "Marketing performance summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)
    pdf.set_draw_color(230, 232, 239)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(47, 49, 64)
    pdf.cell(0, 7, "Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(52, 55, 71)
    pdf.multi_cell(pdf.epw, 6, pdf_safe(so_what), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(47, 49, 64)
    pdf.cell(0, 7, "Budget context", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(52, 55, 71)
    for line in [
        f"Monthly budget: {fmt_eur(monthly_budget)}",
        f"Spent so far (day {days_elapsed}): {fmt_eur(spent_to_date)}",
        f"Projected end-of-month spend: {fmt_eur(projected_spend)}",
        f"Projected end-of-month ROAS: {fmt_x(projected_roas)}",
        f"Daily burn rate: {fmt_eur(daily_spend_rate)}",
        f"Days remaining: {days_remaining}",
    ]:
        pdf.cell(0, 6, pdf_safe(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(47, 49, 64)
    pdf.cell(0, 7, "Channel signals", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    colours = {
        "Efficiency weakening":    (192, 57, 43),
        "Acquisition cost rising": (175, 90, 0),
        "ROAS slightly down":      (58, 80, 104),
        "Stable":                  (29, 122, 69),
        "Not enough data":         (100, 100, 100),
    }

    for _, row in trend.iterrows():
        pdf.ln(3)
        r, g, b = colours.get(row["signal"], (100, 100, 100))
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(47, 49, 64)
        pdf.cell(60, 6, row["channel"], new_x=XPos.END, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(r, g, b)
        pdf.cell(0, 6, row["signal"], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(80, 80, 80)
        pdf.multi_cell(pdf.epw, 5, pdf_safe(row["signal_note"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if row.get("diagnosis", ""):
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(100, 100, 120)
            pdf.multi_cell(pdf.epw, 5, pdf_safe("Why: " + row["diagnosis"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(123, 128, 146)
        cols = [
            f"ROAS prev: {fmt_x(row.get('roas_prev', np.nan))}",
            f"ROAS now: {fmt_x(row.get('roas_curr', np.nan))}",
            f"Change: {pct_text(row.get('roas_delta_pct', np.nan))}",
            f"CAC change: {pct_text(row.get('cac_delta_pct', np.nan))}",
        ]
        pdf.cell(0, 5, pdf_safe("  |  ".join(cols)), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(8)
    pdf.set_draw_color(230, 232, 239)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(0, 5, "Generated by Budget Pulse", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    buf = BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf.read()


# =============================================================================
# CSS
# =============================================================================

st.markdown("""
<style>
.sowhat-banner {
    border-radius: 16px;
    padding: 20px 24px;
    background: #F0F4FF;
    border: 1px solid #C7D5F5;
    margin-bottom: 28px;
    margin-top: 8px;
}
.sowhat-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #4A5BA8;
    margin-bottom: 8px;
}
.sowhat-text {
    font-size: 15px;
    color: #2F3140;
    line-height: 1.7;
}
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
.summary-card.alert { border-color: #F5C2C2; background: #FFF5F5; }
.summary-label { font-size: 13px; color: #71768A; margin-bottom: 8px; }
.summary-value { font-size: 30px; font-weight: 750; color: #2F3140; margin-bottom: 6px; }
.summary-value.red   { color: #C0392B; }
.summary-value.green { color: #1D7A45; }
.summary-note { font-size: 13px; color: #8A8FA3; }
.summary-note.red   { color: #C0392B; font-weight: 500; }
.summary-note.green { color: #1D7A45; font-weight: 500; }
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
.signal-card.weakening { border-left: 4px solid #E24B4A; }
.signal-card.rising    { border-left: 4px solid #EF9F27; }
.signal-card.slight    { border-left: 4px solid #A0B4C8; }
.signal-card.stable    { border-left: 4px solid #1D9E75; }
.signal-card.nodata    { border-left: 4px solid #CCCCCC; }
.signal-title { font-size: 18px; font-weight: 700; margin-bottom: 4px; color: #2F3140; }
.signal-badge {
    display: inline-block;
    font-size: 12px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 20px;
    margin-bottom: 10px;
}
.badge-weakening { background: #FDE8E8; color: #A52B2B; }
.badge-rising    { background: #FEF3E2; color: #7A5000; }
.badge-slight    { background: #EBF0F5; color: #3A5068; }
.badge-stable    { background: #E3F5EC; color: #1A5C36; }
.badge-nodata    { background: #F0F0F0; color: #666666; }
.signal-what { font-size: 15px; color: #343747; margin-bottom: 8px; line-height: 1.6; }
.signal-why {
    font-size: 13px;
    color: #5A5F78;
    background: #F4F5FA;
    border-radius: 8px;
    padding: 8px 12px;
    margin-bottom: 12px;
    line-height: 1.5;
}
.signal-why-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #8A8FA3;
    margin-bottom: 3px;
}
.signal-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin-top: 10px;
}
.mini-metric { background: #F8F9FC; border-radius: 10px; padding: 10px 12px; }
.mini-label  { font-size: 12px; color: #7B8092; margin-bottom: 3px; }
.mini-value  { font-size: 16px; font-weight: 650; color: #2F3140; }
.empty-state {
    border: 1.5px dashed #C5CAD8;
    border-radius: 14px;
    padding: 28px 24px;
    background: #F8F9FC;
    margin-bottom: 24px;
    color: #5A6070;
    font-size: 14px;
    line-height: 1.7;
}
.empty-state strong { color: #2F3140; }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# Header
# =============================================================================

st.title("Budget Pulse")
st.caption(
    "Most marketing reports tell you what happened last month. "
    "This one tells you what is changing right now, so you can act before the month-end review."
)


# =============================================================================
# Upload
# =============================================================================

uploaded_file = st.file_uploader("Upload daily marketing CSV", type=["csv"])
st.markdown(
    "Required columns: `date`, `channel`, `campaign`, `spend`, "
    "`impressions`, `clicks`, `leads`, `sales`, `revenue`. "
    "One row per day per channel per campaign, covering 28 to 35 days."
)

if not uploaded_file:
    st.info("Upload a CSV file to begin.")
    st.stop()


# =============================================================================
# Parse
# =============================================================================

uploaded_file.seek(0)
raw = uploaded_file.read().decode("utf-8-sig").strip()
cleaned_lines = [l.strip().strip('"') for l in raw.splitlines() if l.strip()]

try:
    df = pd.read_csv(StringIO("\n".join(cleaned_lines)))
except Exception as e:
    st.error(f"Could not read CSV: {e}")
    st.stop()

df.columns = df.columns.str.strip().str.lower()
df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)

required_cols = ["date","channel","campaign","spend","impressions","clicks","leads","sales","revenue"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    st.error(f"Missing columns: {missing}")
    st.info(f"Columns found: {list(df.columns)}")
    st.stop()

for col in ["spend","impressions","clicks","leads","sales","revenue"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df["date"] = pd.to_datetime(df["date"], errors="coerce")
df = df.dropna(subset=["date","channel","campaign"])

if df["date"].isna().all():
    st.error("The date column could not be parsed. Use YYYY-MM-DD format.")
    st.stop()


# =============================================================================
# Empty state guard
# =============================================================================

n_days = (df["date"].max() - df["date"].min()).days + 1
if n_days < 14:
    st.markdown(f"""
    <div class="empty-state">
        <strong>Not enough data for trend analysis.</strong><br><br>
        The rolling comparison needs at least <strong>14 days</strong> to work.<br>
        Your file covers <strong>{n_days} day{'s' if n_days != 1 else ''}</strong>
        ({df['date'].min().strftime('%d %b')} to {df['date'].max().strftime('%d %b')}).<br><br>
        The budget projection and full-period summary below will still work.
    </div>
    """, unsafe_allow_html=True)
    TREND_READY = False
else:
    TREND_READY = True


# =============================================================================
# Sidebar
# =============================================================================

st.sidebar.header("Settings")

min_date = df["date"].min().date()
max_date = df["date"].max().date()

date_range = st.sidebar.date_input(
    "Date range", value=(min_date, max_date),
    min_value=min_date, max_value=max_date,
)
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_date, end_date = date_range
    df = df[(df["date"].dt.date >= start_date) & (df["date"].dt.date <= end_date)]

all_channels = sorted(df["channel"].dropna().unique())
selected_channels = st.sidebar.multiselect("Channels", all_channels, default=all_channels)
if selected_channels:
    df = df[df["channel"].isin(selected_channels)]

st.sidebar.markdown("---")
st.sidebar.subheader("Budget context")
st.sidebar.caption("Used to calculate whether you are on track to stay within budget.")

today_input = st.sidebar.date_input(
    "Today's date",
    value=max_date,
    min_value=min_date,
    max_value=max_date + timedelta(days=31),
)
monthly_budget = st.sidebar.number_input(
    "Monthly budget (€)", min_value=0, value=45000, step=1000
)

if df.empty:
    st.warning("No data matches the current filters.")
    st.stop()


# =============================================================================
# Compute metrics
# =============================================================================

df["roas"]            = safe_div(df["revenue"].values, df["spend"].values)
df["cac"]             = safe_div(df["spend"].values,   df["sales"].values)
df["cpl"]             = safe_div(df["spend"].values,   df["leads"].values)
df["ctr"]             = safe_div(df["clicks"].values,  df["impressions"].values)
df["cpm"]             = safe_div(df["spend"].values,   df["impressions"].values) * 1000
df["conversion_rate"] = safe_div(df["sales"].values,   df["clicks"].values)
df = df.replace([np.inf, -np.inf], np.nan)


# =============================================================================
# Rolling window: last 7d vs prior 7d
# =============================================================================

trend = pd.DataFrame()

if TREND_READY:
    data_max    = df["date"].max()
    cutoff_mid  = data_max - timedelta(days=7)
    cutoff_prev = data_max - timedelta(days=14)

    df_curr = df[df["date"] > cutoff_mid]
    df_prev = df[(df["date"] > cutoff_prev) & (df["date"] <= cutoff_mid)]

    def window_summary(frame):
        g = frame.groupby("channel").agg(
            spend       = ("spend",           "sum"),
            revenue     = ("revenue",         "sum"),
            clicks      = ("clicks",          "sum"),
            leads       = ("leads",           "sum"),
            sales       = ("sales",           "sum"),
            impressions = ("impressions",     "sum"),
        ).reset_index()
        g["roas"]        = safe_div(g["revenue"].values,     g["spend"].values)
        g["cac"]         = safe_div(g["spend"].values,       g["sales"].values)
        g["cpl"]         = safe_div(g["spend"].values,       g["leads"].values)
        g["ctr"]         = safe_div(g["clicks"].values,      g["impressions"].values)
        g["cpm"]         = safe_div(g["spend"].values,       g["impressions"].values) * 1000
        g["cvr"]         = safe_div(g["sales"].values,       g["clicks"].values)
        g["daily_spend"] = g["spend"] / 7
        return g.replace([np.inf, -np.inf], np.nan)

    curr  = window_summary(df_curr).add_suffix("_curr").rename(columns={"channel_curr": "channel"})
    prev  = window_summary(df_prev).add_suffix("_prev").rename(columns={"channel_prev": "channel"})
    trend = curr.merge(prev, on="channel", how="left")

    trend["roas_delta_pct"]  = safe_div(
        (trend["roas_curr"]  - trend["roas_prev"]).values,  trend["roas_prev"].values)  * 100
    trend["cac_delta_pct"]   = safe_div(
        (trend["cac_curr"]   - trend["cac_prev"]).values,   trend["cac_prev"].values)   * 100
    trend["ctr_delta_pct"]   = safe_div(
        (trend["ctr_curr"]   - trend["ctr_prev"]).values,   trend["ctr_prev"].values)   * 100
    trend["cvr_delta_pct"]   = safe_div(
        (trend["cvr_curr"]   - trend["cvr_prev"]).values,   trend["cvr_prev"].values)   * 100
    trend["cpm_delta_pct"]   = safe_div(
        (trend["cpm_curr"]   - trend["cpm_prev"]).values,   trend["cpm_prev"].values)   * 100
    trend["spend_delta_pct"] = safe_div(
        (trend["spend_curr"] - trend["spend_prev"]).values, trend["spend_prev"].values) * 100
    trend = trend.replace([np.inf, -np.inf], np.nan)

    trend["signal"]      = trend.apply(signal_label, axis=1)
    trend["signal_note"] = trend.apply(signal_note,  axis=1)
    trend["diagnosis"]   = trend.apply(diagnose,     axis=1)

    signal_order = {
        "Efficiency weakening":    0,
        "Acquisition cost rising": 1,
        "ROAS slightly down":      2,
        "Stable":                  3,
        "Not enough data":         4,
    }
    trend["_sort"] = trend["signal"].map(signal_order)
    trend = trend.sort_values("_sort").drop(columns="_sort")


# =============================================================================
# Month projection
# =============================================================================

today_dt        = pd.Timestamp(today_input)
month_start     = today_dt.replace(day=1)
month_end       = month_start + pd.offsets.MonthEnd(0)
days_elapsed    = max(1, (today_dt - month_start).days + 1)
days_remaining  = max(0, (month_end - today_dt).days)

month_df           = df[df["date"] >= month_start]
spent_to_date      = month_df["spend"].sum()
revenue_to_date    = month_df["revenue"].sum()
daily_spend_rate   = spent_to_date / days_elapsed
daily_revenue_rate = revenue_to_date / days_elapsed

projected_spend   = spent_to_date + daily_spend_rate   * days_remaining
projected_revenue = revenue_to_date + daily_revenue_rate * days_remaining
projected_roas    = projected_revenue / projected_spend if projected_spend else np.nan
budget_remaining  = monthly_budget - spent_to_date
budget_variance   = projected_spend - monthly_budget
is_overspend      = budget_variance > 0


# =============================================================================
# "So what" banner
# =============================================================================

so_what = ""
if TREND_READY and not trend.empty:
    so_what = build_so_what(trend, projected_spend, monthly_budget)
    st.markdown(f"""
    <div class="sowhat-banner">
        <div class="sowhat-label">This week at a glance</div>
        <div class="sowhat-text">{so_what}</div>
    </div>
    """, unsafe_allow_html=True)


# =============================================================================
# SECTION 1: Executive summary
# =============================================================================

total_spend   = df["spend"].sum()
total_revenue = df["revenue"].sum()
total_sales   = df["sales"].sum()
overall_roas  = total_revenue / total_spend if total_spend else np.nan
overall_cac   = total_spend / total_sales   if total_sales  else np.nan

st.subheader("Executive summary")
st.markdown(f"""
<div class="summary-grid">
    <div class="summary-card">
        <div class="summary-label">Total spend</div>
        <div class="summary-value">{fmt_eur(total_spend)}</div>
        <div class="summary-note">Across all channels and campaigns</div>
    </div>
    <div class="summary-card">
        <div class="summary-label">Total revenue</div>
        <div class="summary-value">{fmt_eur(total_revenue)}</div>
        <div class="summary-note">Revenue attributed to marketing</div>
    </div>
    <div class="summary-card">
        <div class="summary-label">Overall ROAS</div>
        <div class="summary-value">{fmt_x(overall_roas)}</div>
        <div class="summary-note">Revenue per euro spent</div>
    </div>
    <div class="summary-card">
        <div class="summary-label">Overall CAC</div>
        <div class="summary-value">{fmt_eur(overall_cac)}</div>
        <div class="summary-note">Cost to acquire one customer</div>
    </div>
</div>
""", unsafe_allow_html=True)


# =============================================================================
# SECTION 2: Budget context
# =============================================================================

st.subheader("Budget context")

remaining_class      = "red" if budget_remaining < 0 else ""
remaining_note       = f"Already {fmt_eur(abs(budget_remaining))} over budget" if budget_remaining < 0 else "Still available this month"
remaining_note_class = "red" if budget_remaining < 0 else ""
proj_class           = "red" if is_overspend else "green"
proj_note            = f"{fmt_eur(abs(budget_variance))} over your cap at this pace" if is_overspend else f"{fmt_eur(abs(budget_variance))} under your cap at this pace"
proj_note_class      = "red" if is_overspend else "green"

st.markdown(f"""
<div class="budget-strip">
    <div class="summary-grid" style="margin: 0;">
        <div class="summary-card">
            <div class="summary-label">Monthly budget</div>
            <div class="summary-value">{fmt_eur(monthly_budget)}</div>
            <div class="summary-note">Planned total spend</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">Spent so far</div>
            <div class="summary-value">{fmt_eur(spent_to_date)}</div>
            <div class="summary-note">Day {days_elapsed} of the month</div>
        </div>
        <div class="summary-card {'alert' if budget_remaining < 0 else ''}">
            <div class="summary-label">Budget remaining</div>
            <div class="summary-value {remaining_class}">{fmt_eur(abs(budget_remaining))}</div>
            <div class="summary-note {remaining_note_class}">{remaining_note}</div>
        </div>
        <div class="summary-card {'alert' if is_overspend else ''}">
            <div class="summary-label">Projected end-of-month spend</div>
            <div class="summary-value {proj_class}">{fmt_eur(projected_spend)}</div>
            <div class="summary-note {proj_note_class}">{proj_note}</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

st.caption(
    f"Projection based on a daily burn rate of {fmt_eur(daily_spend_rate)}, "
    f"with {days_remaining} days left in the month. "
    f"Projected end-of-month ROAS: {fmt_x(projected_roas)}."
)


# =============================================================================
# SECTION 3: Signal cards with diagnosis
# =============================================================================

if TREND_READY and not trend.empty:
    st.subheader("Recent channel movement")
    st.markdown(
        "Comparing the last 7 days against the 7 days before. "
        "Channels are ordered from most to least urgent."
    )

    with st.expander("What do these metrics mean?"):
        st.markdown(
            "**ROAS** - revenue generated per euro spent. Higher is better.\n\n"
            "**CAC** - cost to acquire one paying customer. Lower is better.\n\n"
            "**CTR** - share of people who clicked after seeing the ad. Higher is better.\n\n"
            "**Conversion rate** - share of clicks that became sales. Higher is better.\n\n"
            "**CPM** - cost per 1,000 impressions. A rising CPM can signal auction pressure."
        )

    css_map = {
        "Efficiency weakening":    ("weakening", "badge-weakening"),
        "Acquisition cost rising": ("rising",    "badge-rising"),
        "ROAS slightly down":      ("slight",    "badge-slight"),
        "Stable":                  ("stable",    "badge-stable"),
        "Not enough data":         ("nodata",    "badge-nodata"),
    }

    for _, row in trend.iterrows():
        card_cls, badge_cls = css_map.get(row["signal"], ("nodata", "badge-nodata"))

        diag_block = ""
        raw_diag = str(row.get("diagnosis", "") or "")
        if raw_diag:
            raw_diag = raw_diag.replace("—", "-").replace("–", "-").replace('"', "&quot;")
            diag_block = (
                '<div class="signal-why">'
                '<span class="signal-why-label">Possible cause &nbsp;</span>'
                + raw_diag
                + '</div>'
            )

        html = (
            '<div class="signal-card ' + card_cls + '">'
            + '<div class="signal-title">' + str(row["channel"]) + '</div>'
            + '<span class="signal-badge ' + badge_cls + '">' + str(row["signal"]) + '</span>'
            + '<div class="signal-what">' + str(row["signal_note"]) + '</div>'
            + diag_block
            + '<div class="signal-grid">'
            + '<div class="mini-metric"><div class="mini-label">ROAS previous 7d</div>'
            + '<div class="mini-value">' + fmt_x(row.get("roas_prev", np.nan)) + '</div></div>'
            + '<div class="mini-metric"><div class="mini-label">ROAS last 7d</div>'
            + '<div class="mini-value">' + fmt_x(row.get("roas_curr", np.nan)) + '</div></div>'
            + '<div class="mini-metric"><div class="mini-label">CTR change</div>'
            + '<div class="mini-value">' + pct_text(row.get("ctr_delta_pct", np.nan)) + '</div></div>'
            + '<div class="mini-metric"><div class="mini-label">Conv. rate change</div>'
            + '<div class="mini-value">' + pct_text(row.get("cvr_delta_pct", np.nan)) + '</div></div>'
            + '</div>'
            + '</div>'
        )
        st.markdown(html, unsafe_allow_html=True)

    # SECTION 4: ROAS comparison chart
    st.subheader("ROAS by channel: last 7 days vs week before")
    st.caption("A shorter dark bar means performance dropped in the last 7 days.")

    fig_trend = go.Figure()
    fig_trend.add_trace(go.Bar(
        name="Previous 7 days",
        x=trend["channel"], y=trend["roas_prev"].round(2),
        marker_color="#B5D4F4",
        text=trend["roas_prev"].apply(lambda v: fmt_x(v) if not pd.isna(v) else ""),
        textposition="outside",
    ))
    fig_trend.add_trace(go.Bar(
        name="Last 7 days",
        x=trend["channel"], y=trend["roas_curr"].round(2),
        marker_color="#185FA5",
        text=trend["roas_curr"].apply(lambda v: fmt_x(v) if not pd.isna(v) else ""),
        textposition="outside",
    ))
    fig_trend.update_layout(
        barmode="group", yaxis_title="ROAS", height=360,
        margin=dict(t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_trend, use_container_width=True)

    # SECTION 5: Channel trend table
    st.subheader("Channel details")
    st.caption("The ROAS change column is the key figure. Red means it dropped, green means it improved.")

    display_trend = trend[[
        "channel","signal","roas_prev","roas_curr","roas_delta_pct",
        "ctr_delta_pct","cvr_delta_pct","cpm_delta_pct","daily_spend_curr",
    ]].copy()
    display_trend.columns = [
        "Channel","Signal","ROAS prev 7d","ROAS last 7d",
        "ROAS change","CTR change","Conv. rate change","CPM change","Avg daily spend",
    ]

    def color_delta(val):
        if pd.isna(val): return ""
        if val <= -25:   return "color: #A32D2D; font-weight: 600"
        if val <= -10:   return "color: #8A5A00; font-weight: 500"
        if val >= 25:    return "color: #247A3E; font-weight: 600"
        return ""

    styled_trend = (
        display_trend.style
        .map(color_delta, subset=["ROAS change"])
        .format({
            "ROAS prev 7d":      lambda v: fmt_x(v),
            "ROAS last 7d":      lambda v: fmt_x(v),
            "ROAS change":       lambda v: pct_text(v),
            "CTR change":        lambda v: pct_text(v),
            "Conv. rate change": lambda v: pct_text(v),
            "CPM change":        lambda v: pct_text(v),
            "Avg daily spend":   lambda v: fmt_eur(v),
        })
    )
    st.dataframe(styled_trend, use_container_width=True)


# =============================================================================
# SECTION 6: Daily ROAS time series
# =============================================================================

st.subheader("Daily ROAS by channel")
st.caption(
    "A channel can look fine in a weekly summary but show a clear downward trend here. "
    "Any line approaching 1.0x means you are spending close to what you earn back."
)

daily_ch = (
    df.groupby(["date","channel"])
    .agg(spend=("spend","sum"), revenue=("revenue","sum"))
    .reset_index()
)
daily_ch["roas"] = safe_div(daily_ch["revenue"].values, daily_ch["spend"].values)
daily_ch = daily_ch.replace([np.inf, -np.inf], np.nan)

fig_ts = px.line(
    daily_ch, x="date", y="roas", color="channel",
    markers=False, labels={"roas": "ROAS", "date": ""},
)
fig_ts.add_hline(
    y=1.0, line_dash="dot", line_color="#E24B4A",
    annotation_text="Break-even 1.0x", annotation_position="bottom right",
)
fig_ts.update_layout(
    height=360, margin=dict(t=20, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_ts, use_container_width=True)


# =============================================================================
# SECTION 7: Daily spend
# =============================================================================

st.subheader("Daily spend by channel")
st.caption("Useful for spotting pacing issues, budget gaps, or channels that went dark mid-month.")

daily_spend_ch = (
    df.groupby(["date","channel"])
    .agg(spend=("spend","sum"))
    .reset_index()
)
fig_spend = px.area(
    daily_spend_ch, x="date", y="spend", color="channel",
    labels={"spend": "Spend (€)", "date": ""},
)
fig_spend.update_layout(
    height=320, margin=dict(t=20, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig_spend, use_container_width=True)


# =============================================================================
# SECTION 8: Full period channel summary
# =============================================================================

st.subheader("Full period channel performance")
st.caption(
    "Aggregates the entire date range selected. "
    "A channel can have a strong overall ROAS but still be flagged above "
    "if it deteriorated sharply in recent days."
)

channel_summary = df.groupby("channel").agg(
    spend       = ("spend",       "sum"),
    revenue     = ("revenue",     "sum"),
    clicks      = ("clicks",      "sum"),
    leads       = ("leads",       "sum"),
    sales       = ("sales",       "sum"),
    impressions = ("impressions", "sum"),
).reset_index()

channel_summary["roas"]            = safe_div(channel_summary["revenue"].values, channel_summary["spend"].values)
channel_summary["cac"]             = safe_div(channel_summary["spend"].values,   channel_summary["sales"].values)
channel_summary["cpl"]             = safe_div(channel_summary["spend"].values,   channel_summary["leads"].values)
channel_summary["conversion_rate"] = safe_div(channel_summary["sales"].values,   channel_summary["clicks"].values)
channel_summary["profit_proxy"]    = channel_summary["revenue"] - channel_summary["spend"]
channel_summary = channel_summary.replace([np.inf, -np.inf], np.nan)

styled_summary = (
    channel_summary[["channel","spend","revenue","sales","roas","cac","cpl","conversion_rate"]]
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


# =============================================================================
# SECTION 9: Campaign drill-down
# =============================================================================

st.subheader("Campaign drill-down")
st.caption(
    "Channel averages can hide what is really happening. "
    "One strong campaign can mask another dragging the average down."
)

filtered_channels = sorted(df["channel"].dropna().unique())
selected_channel  = st.selectbox("Which channel do you want to explore?", filtered_channels)

campaign_df = (
    df[df["channel"] == selected_channel]
    .groupby("campaign")
    .agg(
        spend   = ("spend",   "sum"),
        revenue = ("revenue", "sum"),
        clicks  = ("clicks",  "sum"),
        leads   = ("leads",   "sum"),
        sales   = ("sales",   "sum"),
    )
    .reset_index()
)
campaign_df["roas"]         = safe_div(campaign_df["revenue"].values, campaign_df["spend"].values)
campaign_df["cac"]          = safe_div(campaign_df["spend"].values,   campaign_df["sales"].values)
campaign_df["cpl"]          = safe_div(campaign_df["spend"].values,   campaign_df["leads"].values)
campaign_df["profit_proxy"] = campaign_df["revenue"] - campaign_df["spend"]
campaign_df = campaign_df.replace([np.inf, -np.inf], np.nan)

if not campaign_df.empty:
    best  = campaign_df.sort_values("roas", ascending=False).iloc[0]
    worst = campaign_df.sort_values("roas", ascending=True).iloc[0]
    st.markdown(
        f"The strongest campaign in **{selected_channel}** is **{best['campaign']}** "
        f"at {fmt_x(best['roas'])} ROAS. "
        f"The one worth reviewing is **{worst['campaign']}** at {fmt_x(worst['roas'])} ROAS."
    )

campaign_styled = (
    campaign_df.style
    .background_gradient(subset=["roas"], cmap="RdYlGn")
    .background_gradient(subset=["cac"],  cmap="RdYlGn_r")
    .format({
        "spend":        "€{:,.0f}",
        "revenue":      "€{:,.0f}",
        "roas":         "{:.2f}x",
        "cac":          "€{:,.0f}",
        "cpl":          "€{:,.0f}",
        "profit_proxy": "€{:,.0f}",
    })
)
st.dataframe(campaign_styled, use_container_width=True)

camp_daily = (
    df[df["channel"] == selected_channel]
    .groupby(["date","campaign"])
    .agg(spend=("spend","sum"), revenue=("revenue","sum"))
    .reset_index()
)
camp_daily["roas"] = safe_div(camp_daily["revenue"].values, camp_daily["spend"].values)
camp_daily = camp_daily.replace([np.inf, -np.inf], np.nan)

fig_camp = px.line(
    camp_daily, x="date", y="roas", color="campaign",
    markers=False,
    title=f"{selected_channel}: daily ROAS by campaign",
    labels={"roas": "ROAS", "date": ""},
)
fig_camp.update_layout(height=320, margin=dict(t=40, b=20))
st.plotly_chart(fig_camp, use_container_width=True)


# =============================================================================
# SECTION 10: PDF export
# =============================================================================

st.markdown("---")
st.subheader("Export this report")
st.markdown(
    "Download a PDF summary of the signals and budget context - "
    "useful for sharing in a Monday morning message or attaching to a client report."
)

if TREND_READY and not trend.empty and so_what:
    if st.button("Generate PDF summary"):
        with st.spinner("Building your PDF..."):
            pdf_bytes = generate_pdf(
                so_what          = so_what,
                trend            = trend,
                spent_to_date    = spent_to_date,
                projected_spend  = projected_spend,
                monthly_budget   = monthly_budget,
                days_elapsed     = days_elapsed,
                days_remaining   = days_remaining,
                projected_roas   = projected_roas,
                daily_spend_rate = daily_spend_rate,
            )
        st.download_button(
            label     = "Download PDF",
            data      = pdf_bytes,
            file_name = "budget_pulse_report.pdf",
            mime      = "application/pdf",
        )
else:
    st.info("PDF export is available once you have at least 14 days of data.")
