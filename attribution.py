"""Credits every lead to the journey it started in, and counts form starts.

Rules (brief section 4), applied to completed leads in this order:
1. Landed as is the channel for the lead's formType (table A).
2. Started in is the home channel of the lead's form_id (table B), or Landed
   as when form_id is blank or not in the table.
3. If cross_sell_id points to another lead, take that lead's Started in,
   following chains up to 5 deep. The lead becomes an add-on.
4. Optional: a later lead in a session with no cross sell link, where the
   session's first completed lead started in a different channel, takes the
   first lead's Started in and becomes an add-on.
"""

from __future__ import annotations

import pandas as pd

from settings import UNMAPPED, Settings

UNKNOWN = "Unknown"

ROUTE_DIRECT = "Direct"
ROUTE_IN_FORM = "Routed in form"
ROUTE_CROSS_SELL = "Cross sell"
ROUTE_FOLLOW_ON = "Session follow-on"


# Field preparation ---------------------------------------------------------


def _text(rows: pd.DataFrame, column: str) -> pd.Series:
    if column not in rows.columns:
        return pd.Series("", index=rows.index)
    return rows[column].astype(str).str.strip()


def _with_unknown(values: pd.Series) -> pd.Series:
    return values.where(values != "", UNKNOWN)


def _contains_any(values: pd.Series, terms: list[str]) -> pd.Series:
    result = pd.Series(False, index=values.index)
    for term in terms:
        if term:
            result |= values.str.contains(term, regex=False)
    return result


def source_group(source: pd.Series, medium: pd.Series, settings: Settings) -> pd.Series:
    """Traffic source group from utm_source and utm_medium."""
    t = settings.traffic_sources
    s = source.str.lower().str.strip()
    m = medium.str.lower().str.strip()
    both = s + " " + m
    paid = _contains_any(m, t.get("paid_mediums", []))
    direct_values = set(t.get("direct_values", [""]))

    rules = [
        ("Google Ads", paid & _contains_any(s, t.get("google_sources", []))),
        ("Microsoft Ads", paid & _contains_any(s, t.get("microsoft_sources", []))),
        ("AI assistants", _contains_any(s, t.get("ai_sources", []))),
        ("Email", _contains_any(both, t.get("email_terms", []))),
        (
            "Organic search",
            _contains_any(m, t.get("organic_mediums", []))
            | (~paid & _contains_any(s, t.get("search_engines", []))),
        ),
        ("Partner referral", _contains_any(m, t.get("partner_mediums", []))),
        ("Direct or unknown", s.isin(direct_values) & (m.isin(direct_values))),
    ]
    result = pd.Series("Other", index=source.index)
    assigned = pd.Series(False, index=source.index)
    for label, mask in rules:
        take = mask & ~assigned
        result[take] = label
        assigned |= take
    return result


def campaign_type(campaign: pd.Series, groups: pd.Series, settings: Settings) -> pd.Series:
    """Brand search, Product search, Other campaign or None."""
    lower = campaign.str.lower()
    paid_search = groups.isin(["Google Ads", "Microsoft Ads"])
    result = pd.Series("Other campaign", index=campaign.index)
    result[paid_search] = "Product search"
    result[_contains_any(lower, settings.brand_terms)] = "Brand search"
    result[campaign == ""] = "None"
    return result


def prepare_rows(rows: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Add channel and filter fields to every row (partial and completed)."""
    c = settings.col
    out = pd.DataFrame(index=rows.index)
    out["id"] = rows["id"]
    out["created_at"] = rows["created_at"]
    out["date"] = rows["created_at"].dt.normalize()
    out["commission"] = rows["commission"]
    out["formType"] = rows["formType"]
    out["form_id"] = rows["form_id"]
    out["cross_sell_id"] = rows["cross_sell_id"]
    out["session"] = rows["session"]

    prefix = settings.partial_prefix
    out["is_partial"] = out["formType"].str.startswith(prefix)
    out["base_type"] = out["formType"].where(
        ~out["is_partial"], out["formType"].str.slice(len(prefix))
    )
    out["is_cross_sell_type"] = out["base_type"].isin(settings.cross_sell_form_types)
    out["landed"] = out["base_type"].map(settings.form_type_channel).fillna(UNMAPPED)
    out["form_home"] = out["form_id"].map(settings.form_id_channel)
    out["journey"] = out["form_home"].fillna(out["landed"])

    source = _text(rows, c("utm_source"))
    medium = _text(rows, c("utm_medium"))
    campaign = _text(rows, c("campaign"))
    out["f_source"] = source_group(source, medium, settings)
    out["f_campaign_type"] = campaign_type(campaign, out["f_source"], settings)
    out["f_campaign"] = _with_unknown(campaign)
    out["f_match_type"] = _with_unknown(_text(rows, c("match_type")))
    out["f_device"] = _with_unknown(_text(rows, c("device")))
    out["f_form"] = _with_unknown(out["form_id"])
    out["f_test_arm"] = _with_unknown(_text(rows, c("test_arm")))
    if c("day_of_week") in rows.columns:
        out["f_day_of_week"] = _with_unknown(_text(rows, c("day_of_week")))
    else:
        out["f_day_of_week"] = rows["created_at"].dt.day_name().fillna(UNKNOWN)
    out["f_lead_type"] = _with_unknown(out["formType"])
    out["f_buyer"] = _with_unknown(_text(rows, c("buyer")))
    out["f_valid"] = _with_unknown(_text(rows, c("valid")))
    out["f_region"] = _with_unknown(_text(rows, c("region")))
    return out


# Leads ---------------------------------------------------------------------


def attribute_leads(prepared: pd.DataFrame, settings: Settings, follow_ons: bool = False) -> pd.DataFrame:
    """Completed leads with landed, started_in, is_addon and route."""
    leads = prepared[~prepared["is_partial"]].copy()
    leads["started_in"] = leads["journey"]
    leads["is_addon"] = False
    leads["route"] = ROUTE_DIRECT
    leads.loc[leads["started_in"] != leads["landed"], "route"] = ROUTE_IN_FORM

    # Rule 3: cross sell links, followed up to max depth.
    base_started = dict(zip(leads["id"], leads["journey"]))
    parent_of = dict(zip(leads["id"], leads["cross_sell_id"]))
    linked = leads["cross_sell_id"].isin(base_started.keys()) & (leads["cross_sell_id"] != leads["id"])
    for idx in leads.index[linked]:
        lead_id = leads.at[idx, "id"]
        ancestor = leads.at[idx, "cross_sell_id"]
        seen = {lead_id}
        for _ in range(settings.max_cross_sell_depth - 1):
            parent = parent_of.get(ancestor, "")
            if parent in base_started and parent not in seen and parent != ancestor:
                seen.add(ancestor)
                ancestor = parent
            else:
                break
        leads.at[idx, "started_in"] = base_started[ancestor]
    leads.loc[linked, "is_addon"] = True
    leads.loc[linked, "route"] = ROUTE_CROSS_SELL

    # Rule 4: same-session follow-ons (off by default).
    if follow_ons:
        in_session = leads[leads["session"] != ""].sort_values(["created_at", "id"], kind="mergesort")
        first = in_session.groupby("session").head(1)
        first_started = dict(zip(first["session"], first["started_in"]))
        first_ids = set(first["id"])
        candidates = (
            (leads["session"] != "")
            & ~linked
            & ~leads["id"].isin(first_ids)
        )
        session_start = leads["session"].map(first_started)
        follow = candidates & session_start.notna() & (session_start != leads["started_in"])
        leads.loc[follow, "started_in"] = session_start[follow]
        leads.loc[follow, "is_addon"] = True
        leads.loc[follow, "route"] = ROUTE_FOLLOW_ON

    leads["f_route"] = leads["route"]
    return leads.reset_index(drop=True)


# Form starts ---------------------------------------------------------------


def form_starts(prepared: pd.DataFrame) -> pd.DataFrame:
    """One form start per session per journey.

    Uses every row (partial and completed) except cross sell types. A start is
    dated to, and filtered on, the session's first row on that journey.
    """
    rows = prepared[~prepared["is_cross_sell_type"]].copy()
    rows["session_key"] = rows["session"].where(rows["session"] != "", "row:" + rows["id"])
    rows = rows.sort_values(["created_at", "id"], kind="mergesort")
    completed = (
        rows.assign(done=~rows["is_partial"])
        .groupby(["session_key", "journey"])["done"]
        .any()
        .rename("completed")
    )
    starts = rows.groupby(["session_key", "journey"], sort=False).head(1)
    starts = starts.join(completed, on=["session_key", "journey"])
    keep = ["session_key", "journey", "created_at", "date", "completed"] + [
        col for col in starts.columns if col.startswith("f_")
    ]
    return starts[keep].reset_index(drop=True)


# Data health ---------------------------------------------------------------


def mapping_health(prepared: pd.DataFrame, settings: Settings) -> dict:
    """Form ids and form types missing from config, and partials with commission."""
    ids = prepared[(prepared["form_id"] != "") & prepared["form_home"].isna()]
    unmapped_ids = (
        ids.groupby("form_id")
        .agg(rows=("id", "size"), first_seen=("created_at", "min"))
        .reset_index()
        .rename(columns={"form_id": "Form id", "rows": "Rows", "first_seen": "First seen"})
        .sort_values("Rows", ascending=False)
    )
    types = prepared[prepared["landed"] == UNMAPPED]
    unmapped_types = (
        types.groupby("base_type")
        .agg(rows=("id", "size"), first_seen=("created_at", "min"))
        .reset_index()
        .rename(columns={"base_type": "Form type", "rows": "Rows", "first_seen": "First seen"})
        .sort_values("Rows", ascending=False)
    )
    partial_commission = prepared[prepared["is_partial"] & (prepared["commission"] != 0)]
    return {
        "completed_leads": int((~prepared["is_partial"]).sum()),
        "partial_rows": int(prepared["is_partial"].sum()),
        "unmapped_form_ids": unmapped_ids,
        "unmapped_form_types": unmapped_types,
        "partial_with_commission": int(len(partial_commission)),
        "partial_commission_total": float(partial_commission["commission"].sum()),
    }
