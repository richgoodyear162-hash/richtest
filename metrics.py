"""Filters, periods and the figures shown on cards, trend and breakdown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

# Filters -------------------------------------------------------------------


@dataclass(frozen=True)
class FilterSpec:
    key: str
    label: str
    filters_starts: bool


FILTERS = [
    FilterSpec("f_source", "Traffic source", True),
    FilterSpec("f_campaign_type", "Campaign type", True),
    FilterSpec("f_campaign", "Campaign", True),
    FilterSpec("f_match_type", "Match type", True),
    FilterSpec("f_device", "Device", True),
    FilterSpec("f_form", "Form", True),
    FilterSpec("f_test_arm", "Test arm", True),
    FilterSpec("f_day_of_week", "Day of week", True),
    FilterSpec("f_lead_type", "Lead type", False),
    FilterSpec("f_route", "Route", False),
    FilterSpec("f_buyer", "Buyer", False),
    FilterSpec("f_valid", "Valid lead", False),
    FilterSpec("f_region", "Region", False),
]
FILTERS_BY_KEY = {f.key: f for f in FILTERS}

# A selection maps a filter key to the values kept. A missing key, or None,
# means every value is selected.
Selection = dict[str, "set[str] | None"]


def active_filters(selection: Selection) -> dict[str, set[str]]:
    return {k: v for k, v in selection.items() if v is not None}


def starts_available(selection: Selection) -> bool:
    """Form starts can't be filtered on fields partial rows don't carry."""
    return all(FILTERS_BY_KEY[k].filters_starts for k in active_filters(selection))


def apply_filters(frame: pd.DataFrame, selection: Selection) -> pd.DataFrame:
    mask = pd.Series(True, index=frame.index)
    for key, values in active_filters(selection).items():
        if key in frame.columns:
            mask &= frame[key].isin(values)
    return frame[mask]


def describe_filters(selection: Selection, max_values: int = 3) -> str:
    parts = []
    for key, values in active_filters(selection).items():
        label = FILTERS_BY_KEY[key].label
        shown = sorted(values)
        if not shown:
            parts.append(f"{label}: none")
            continue
        text = ", ".join(shown[:max_values])
        if len(shown) > max_values:
            text += f" and {len(shown) - max_values} more"
        parts.append(f"{label}: {text}")
    return "; ".join(parts)


# Periods -------------------------------------------------------------------


def last_full_day(last_timestamp: pd.Timestamp) -> date:
    """The last day the data fully covers."""
    if last_timestamp.time() >= pd.Timestamp("23:59:00").time():
        return last_timestamp.date()
    return last_timestamp.date() - timedelta(days=1)


def comparison_range(start: date, end: date, mode: str) -> tuple[date, date] | None:
    if mode == "Previous period":
        length = (end - start).days + 1
        return start - timedelta(days=length), start - timedelta(days=1)
    if mode == "Same days last month":
        shift = pd.DateOffset(months=1)
        return (pd.Timestamp(start) - shift).date(), (pd.Timestamp(end) - shift).date()
    return None


def period_start(dates: pd.Series, granularity: str) -> pd.Series:
    if granularity == "Week":
        return dates - pd.to_timedelta(dates.dt.weekday, unit="D")
    if granularity == "Month":
        return dates.dt.to_period("M").dt.to_timestamp()
    return dates


def period_end(start: pd.Timestamp, granularity: str) -> pd.Timestamp:
    if granularity == "Week":
        return start + pd.Timedelta(days=6)
    if granularity == "Month":
        return start + pd.offsets.MonthEnd(0)
    return start


def _day_month(d: pd.Timestamp) -> str:
    return f"{d.day} {d:%b}"


def period_label(start: pd.Timestamp, granularity: str, range_start: date, range_end: date) -> tuple[str, bool]:
    """Label a period, marking part periods, for example "1 to 20 Oct"."""
    end = period_end(start, granularity)
    shown_start = max(start, pd.Timestamp(range_start))
    shown_end = min(end, pd.Timestamp(range_end))
    is_part = shown_start > start or shown_end < end
    if granularity == "Day":
        return f"{start:%a} {_day_month(start)} {start:%Y}", False
    if is_part:
        first = str(shown_start.day) if shown_start.month == shown_end.month else _day_month(shown_start)
        return f"{first} to {_day_month(shown_end)} {shown_end:%Y}", True
    if granularity == "Week":
        return f"w/c {_day_month(start)} {start:%Y}", False
    return f"{start:%b %Y}", False


def all_periods(range_start: date, range_end: date, granularity: str) -> list[pd.Timestamp]:
    days = pd.Series(pd.date_range(range_start, range_end, freq="D"))
    return sorted(period_start(days, granularity).unique())


def in_range(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    return frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]


# Figures -------------------------------------------------------------------


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _channel_flags(leads: pd.DataFrame, channel: str) -> pd.DataFrame:
    landed = leads["landed"] == channel
    started = leads["started_in"] == channel
    rev = leads["commission"]
    return pd.DataFrame(
        {
            "reported_leads": landed.astype(int),
            "reported_revenue": rev.where(landed, 0.0),
            "flattened_leads": (landed & started).astype(int),
            "flattened_revenue": rev.where(landed & started, 0.0),
            "blended_enquiries": (started & ~leads["is_addon"]).astype(int),
            "blended_leads": started.astype(int),
            "blended_revenue": rev.where(started, 0.0),
            "routed_in_leads": (landed & ~started).astype(int),
            "routed_in_revenue": rev.where(landed & ~started, 0.0),
            "routed_out_leads": (started & ~landed).astype(int),
            "routed_out_revenue": rev.where(started & ~landed, 0.0),
        },
        index=leads.index,
    )


SUM_COLUMNS = [
    "reported_leads", "reported_revenue", "flattened_leads", "flattened_revenue",
    "blended_enquiries", "blended_leads", "blended_revenue",
    "routed_in_leads", "routed_in_revenue", "routed_out_leads", "routed_out_revenue",
]


def _add_ratios(row: dict, starts_ok: bool) -> dict:
    row["reported_rpe"] = _ratio(row["reported_revenue"], row["reported_leads"])
    row["flattened_rpe"] = _ratio(row["flattened_revenue"], row["flattened_leads"])
    row["rpe"] = _ratio(row["blended_revenue"], row["blended_enquiries"])
    if starts_ok:
        row["form_conversion"] = _ratio(row["form_starts_completed"], row["form_starts"])
    else:
        row["form_starts"] = None
        row["form_starts_completed"] = None
        row["form_conversion"] = None
    # Parked: spend and site visits join here as extra columns, for example
    # row["spend"], row["cost_per_enquiry"], row["site_visits"].
    return row


def summarise(
    leads: pd.DataFrame,
    starts: pd.DataFrame,
    channel: str,
    start: date,
    end: date,
    starts_ok: bool = True,
) -> dict:
    """Totals for one channel over a date range (already filtered)."""
    period_leads = in_range(leads, start, end)
    flags = _channel_flags(period_leads, channel)
    row = {k: (float(v) if "revenue" in k else int(v)) for k, v in flags.sum().items()}
    for k in SUM_COLUMNS:
        row.setdefault(k, 0)
    period_starts = in_range(starts, start, end)
    journey = period_starts[period_starts["journey"] == channel]
    row["form_starts"] = int(len(journey))
    row["form_starts_completed"] = int(journey["completed"].sum())
    return _add_ratios(row, starts_ok)


def routing_detail(leads: pd.DataFrame, channel: str, start: date, end: date) -> dict:
    """Leads that crossed between channels, for the card's routing line."""
    period = in_range(leads, start, end)
    out = period[(period["started_in"] == channel) & (period["landed"] != channel)]
    inward = period[(period["landed"] == channel) & (period["started_in"] != channel)]
    return {
        "out": out.groupby("landed")["is_addon"].agg(["size", "sum"]).rename(
            columns={"size": "leads", "sum": "addons"}
        ),
        "in": inward.groupby("started_in").size().rename("leads"),
    }


def breakdown(
    leads: pd.DataFrame,
    starts: pd.DataFrame,
    channel: str,
    start: date,
    end: date,
    granularity: str,
    starts_ok: bool = True,
) -> pd.DataFrame:
    """One row per period for one channel."""
    period_leads = in_range(leads, start, end)
    flags = _channel_flags(period_leads, channel)
    flags["period"] = period_start(period_leads["date"], granularity)
    sums = flags.groupby("period")[SUM_COLUMNS].sum()

    period_starts = in_range(starts, start, end)
    journey = period_starts[period_starts["journey"] == channel]
    fs = journey.assign(period=period_start(journey["date"], granularity)).groupby("period").agg(
        form_starts=("completed", "size"), form_starts_completed=("completed", "sum")
    )

    rows = []
    for p in all_periods(start, end, granularity):
        row = {k: 0 for k in SUM_COLUMNS}
        if p in sums.index:
            row.update(sums.loc[p].to_dict())
        row["form_starts"] = int(fs.loc[p, "form_starts"]) if p in fs.index else 0
        row["form_starts_completed"] = int(fs.loc[p, "form_starts_completed"]) if p in fs.index else 0
        label, is_part = period_label(p, granularity, start, end)
        row = _add_ratios(row, starts_ok)
        row.update({"period": p, "label": label, "is_part": is_part})
        rows.append(row)
    return pd.DataFrame(rows)


def rolling_rpe(leads: pd.DataFrame, channel: str, start: date, end: date, window: int = 7) -> pd.DataFrame:
    """Daily blended RPE and its rolling average over the previous `window` days."""
    lead_in = start - timedelta(days=window - 1)
    period = in_range(leads, lead_in, end)
    flags = _channel_flags(period, channel)
    flags["date"] = period["date"]
    daily = flags.groupby("date")[["blended_revenue", "blended_enquiries"]].sum()
    days = pd.date_range(lead_in, end, freq="D")
    daily = daily.reindex(days, fill_value=0)
    rolled = daily.rolling(window, min_periods=window).sum()
    result = pd.DataFrame(
        {
            "date": days,
            "rolling_rpe": (rolled["blended_revenue"] / rolled["blended_enquiries"].replace(0, pd.NA)).astype(float),
        }
    )
    return result[result["date"] >= pd.Timestamp(start)].reset_index(drop=True)


# Changes -------------------------------------------------------------------


def pct_change(current, previous) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / previous * 100


def pp_change(current, previous) -> float | None:
    if current is None or previous is None:
        return None
    return (current - previous) * 100
