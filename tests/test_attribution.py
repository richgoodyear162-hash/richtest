import random
from datetime import date

import pandas as pd
import pytest

import loader
import pipeline
from attribution import (
    ROUTE_CROSS_SELL, ROUTE_DIRECT, ROUTE_FOLLOW_ON, ROUTE_IN_FORM,
    attribute_leads, form_starts, prepare_rows,
)
from metrics import FILTERS, apply_filters, breakdown, starts_available, summarise
from settings import load_settings
from synthetic import frame, random_export, row

START, END = date(2026, 6, 1), date(2026, 10, 31)


@pytest.fixture
def settings():
    return load_settings()


def prepared_from(rows, settings):
    df = frame(rows)
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["updated_at"] = df["created_at"]
    df["commission"] = loader.parse_money(df["commission"])
    df["session"] = df["session_id"]
    return prepare_rows(df, settings)


def test_routing_cross_sell_and_partials(settings):
    rows = [
        row(id=1, formType="partial-leased-lines", created_at="2026-09-01 10:00:00", form_id=5, session_id="a"),
        row(id=2, formType="broadband", created_at="2026-09-01 10:05:00", form_id=5, session_id="a", commission=50),
        row(id=3, formType="partial-cross-sell-energy", created_at="2026-09-01 10:06:00", form_id=5,
            session_id="a", cross_sell_id=2, commission=30),
        row(id=4, formType="cross-sell-energy", created_at="2026-09-01 10:07:00", form_id=5,
            session_id="a", cross_sell_id=2, commission=55),
        row(id=5, formType="leased-lines", created_at="2026-09-02 09:00:00", form_id=21, session_id="b", commission=280),
        row(id=6, formType="broadband-mobile", created_at="2026-09-02 09:30:00", form_id="", session_id="c", commission=40),
        row(id=7, formType="broadband", created_at="2026-09-02 09:30:00", form_id=99, session_id="d", commission=0),
    ]
    leads = attribute_leads(prepared_from(rows, settings), settings).set_index("id")
    assert set(leads.index) == {"2", "4", "5", "6", "7"}
    assert leads.loc["2", ["landed", "started_in", "route"]].tolist() == ["Broadband", "Leased Lines", ROUTE_IN_FORM]
    assert leads.loc["4", ["landed", "started_in", "route"]].tolist() == ["Energy", "Leased Lines", ROUTE_CROSS_SELL]
    assert leads.loc["4", "is_addon"]
    assert leads.loc["5", "route"] == ROUTE_DIRECT
    assert leads.loc["6", "started_in"] == "Broadband"  # blank form_id falls back to landed
    assert leads.loc["7", "started_in"] == "Broadband"  # unknown form_id falls back too

    s = summarise(leads.reset_index(), form_starts(prepared_from(rows, settings)), "Leased Lines", START, END)
    assert s["blended_enquiries"] == 2
    assert s["blended_revenue"] == 280 + 50 + 55
    assert s["reported_leads"] == 1
    assert s["routed_out_leads"] == 2


def test_cross_sell_chain_depth_limit(settings):
    rows = [row(id=1, formType="leased-lines", created_at="2026-09-01 10:00:00", form_id=5)]
    for i in range(2, 9):
        rows.append(row(id=i, formType="cross-sell-energy", created_at=f"2026-09-01 10:0{i}:00",
                        form_id=6, cross_sell_id=i - 1))
    leads = attribute_leads(prepared_from(rows, settings), settings).set_index("id")
    # Leads 2 to 6 are within 5 links of lead 1.
    for i in range(2, 7):
        assert leads.loc[str(i), "started_in"] == "Leased Lines"
    # Lead 7 is 6 links away, so it stops at lead 2's own journey.
    assert leads.loc["7", "started_in"] == "Energy"


def test_follow_on_toggle(settings):
    rows = [
        row(id=1, formType="broadband", created_at="2026-09-01 10:00:00", form_id=2, session_id="x", commission=47),
        row(id=2, formType="compare-business-energy", created_at="2026-09-01 10:20:00", form_id=6,
            session_id="x", commission=150),
    ]
    prepared = prepared_from(rows, settings)
    off = attribute_leads(prepared, settings, follow_ons=False).set_index("id")
    on = attribute_leads(prepared, settings, follow_ons=True).set_index("id")
    assert off.loc["2", "started_in"] == "Energy" and not off.loc["2", "is_addon"]
    assert on.loc["2", "started_in"] == "Broadband" and on.loc["2", "is_addon"]
    assert on.loc["2", "route"] == ROUTE_FOLLOW_ON


def test_form_starts_count_sessions_not_rows(settings):
    rows = [
        row(id=1, formType="partial-leased-lines", created_at="2026-09-01 10:00:00", form_id=5, session_id="a", device="Mobile"),
        row(id=2, formType="partial-broadband", created_at="2026-09-01 10:01:00", form_id=5, session_id="a", device="Desktop"),
        row(id=3, formType="broadband", created_at="2026-09-01 10:02:00", form_id=5, session_id="a"),
        row(id=4, formType="partial-cross-sell-energy", created_at="2026-09-01 10:03:00", form_id=5, session_id="a"),
        row(id=5, formType="partial-leased-lines", created_at="2026-09-02 10:00:00", form_id=21, session_id="b"),
    ]
    starts = form_starts(prepared_from(rows, settings))
    ll = starts[starts["journey"] == "Leased Lines"].set_index("session_key")
    assert len(starts) == 2
    assert bool(ll.loc["a", "completed"]) and not bool(ll.loc["b", "completed"])
    assert ll.loc["a", "f_device"] == "Mobile"  # values from the first row


def test_reported_total_equals_blended_total_under_any_filter(tmp_path, settings):
    loader.save_upload("e.csv", random_export(seed=11, sessions=800).to_csv(index=False).encode(), tmp_path)
    model = pipeline.build(settings, tmp_path, cache_dir=None)
    channels = sorted(set(model.leads["landed"]) | set(model.leads["started_in"]))
    rng = random.Random(0)
    for trial in range(40):
        selection = {}
        for spec in rng.sample(FILTERS, rng.randint(0, 4)):
            values = sorted(model.leads[spec.key].unique()) if spec.key in model.leads else []
            if values:
                selection[spec.key] = set(rng.sample(values, rng.randint(1, len(values))))
        follow = trial % 2 == 1
        leads = apply_filters(model.leads_for(follow), selection)
        start = date(2026, rng.randint(6, 9), rng.randint(1, 28))
        end = date(2026, 10, rng.randint(1, 31))
        reported = sum(summarise(leads, model.starts, c, start, end)["reported_revenue"] for c in channels)
        blended = sum(summarise(leads, model.starts, c, start, end)["blended_revenue"] for c in channels)
        assert reported == pytest.approx(blended)
        leads_rep = sum(summarise(leads, model.starts, c, start, end)["reported_leads"] for c in channels)
        leads_bl = sum(summarise(leads, model.starts, c, start, end)["blended_leads"] for c in channels)
        assert leads_rep == leads_bl


def test_breakdown_matches_summary_and_flags_part_periods(tmp_path, settings):
    loader.save_upload("e.csv", random_export(seed=12, sessions=500).to_csv(index=False).encode(), tmp_path)
    model = pipeline.build(settings, tmp_path, cache_dir=None)
    start, end = date(2026, 7, 10), date(2026, 10, 20)
    for gran in ("Day", "Week", "Month"):
        table = breakdown(model.leads, model.starts, "Broadband", start, end, gran)
        total = summarise(model.leads, model.starts, "Broadband", start, end)
        assert table["blended_revenue"].sum() == pytest.approx(total["blended_revenue"])
        assert table["blended_enquiries"].sum() == total["blended_enquiries"]
        assert table["form_starts"].sum() == total["form_starts"]
    months = breakdown(model.leads, model.starts, "Broadband", start, end, "Month")
    assert months["label"].tolist() == ["10 to 31 Jul 2026", "Aug 2026", "Sep 2026", "1 to 20 Oct 2026"]
    assert months["is_part"].tolist() == [True, False, False, True]


def test_form_starts_unavailable_with_lead_only_filters():
    assert starts_available({"f_device": {"Mobile"}})
    assert not starts_available({"f_buyer": {"Focus Group"}})
    assert starts_available({"f_buyer": None})
