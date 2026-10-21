"""Acceptance checks from the brief (section 10).

These run against real lead exports covering 1 June to 21 October 2026. The
exports never go into the repository: point RPE_EXPORTS_DIR at the folder that
holds them, or leave them in data/exports/ where the dashboard keeps uploads.

    RPE_EXPORTS_DIR=/path/to/exports pytest tests/test_acceptance.py
"""

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import loader
import pipeline
from metrics import apply_filters, in_range, starts_available, summarise
from settings import load_settings

EXPORTS = Path(os.environ.get("RPE_EXPORTS_DIR", loader.EXPORTS_DIR))

pytestmark = pytest.mark.skipif(
    not loader.export_files(EXPORTS),
    reason=f"No lead exports in {EXPORTS}. Set RPE_EXPORTS_DIR to run the acceptance checks.",
)

SEP = (date(2026, 9, 1), date(2026, 9, 30))
OCT_1_20 = (date(2026, 10, 1), date(2026, 10, 20))
ALL = (date(2026, 6, 1), date(2026, 10, 21))


@pytest.fixture(scope="module")
def model():
    return pipeline.build(load_settings(), EXPORTS, cache_dir=None)


def money(x):
    return pytest.approx(x, abs=0.005)


def test_completed_leads_and_revenue(model):
    leads = in_range(model.leads, *ALL)
    assert len(leads) == 23_723
    assert leads["commission"].sum() == money(2_118_521.33)


def test_leased_lines_september(model):
    s = summarise(model.leads, model.starts, "Leased Lines", *SEP)
    assert (s["blended_enquiries"], s["blended_revenue"]) == (168, money(38_529.00))
    assert round(s["rpe"], 2) == 229.34
    assert (s["reported_leads"], s["reported_revenue"]) == (132, money(36_750.00))
    assert round(s["reported_rpe"], 2) == 278.41
    assert (s["form_starts"], s["form_starts_completed"]) == (509, 165)


def test_leased_lines_september_routed_to_broadband(model):
    sep = in_range(model.leads, *SEP)
    routed = sep[(sep["started_in"] == "Leased Lines") & (sep["landed"] == "Broadband")]
    assert len(routed) == 36
    assert routed["commission"].sum() == money(1_779.00)


def test_broadband_september(model):
    s = summarise(model.leads, model.starts, "Broadband", *SEP)
    assert (s["reported_leads"], s["reported_revenue"]) == (1_792, money(85_401.50))
    assert round(s["reported_rpe"], 2) == 47.66
    assert (s["flattened_leads"], s["flattened_revenue"]) == (1_756, money(83_622.50))
    assert round(s["flattened_rpe"], 2) == 47.62


def test_broadband_october(model):
    s = summarise(model.leads, model.starts, "Broadband", *OCT_1_20)
    assert (s["blended_enquiries"], s["blended_revenue"]) == (1_110, money(56_889.50))
    assert round(s["rpe"], 2) == 51.25
    oct_leads = in_range(model.leads, *OCT_1_20)
    cross = oct_leads[(oct_leads["started_in"] == "Broadband") & (oct_leads["landed"] == "Energy")]
    assert len(cross) == 83
    assert cross["commission"].sum() == money(4_357.50)


def test_energy_october(model):
    s = summarise(model.leads, model.starts, "Energy", *OCT_1_20)
    assert (s["reported_leads"], s["reported_revenue"]) == (777, money(141_823.38))
    assert round(s["reported_rpe"], 2) == 182.53
    assert (s["blended_enquiries"], s["blended_revenue"]) == (694, money(137_465.88))
    assert round(s["rpe"], 2) == 198.08


def test_leased_lines_september_mobile(model):
    selection = {"f_device": {"Mobile"}}
    leads = apply_filters(model.leads, selection)
    starts = apply_filters(model.starts, selection)
    s = summarise(leads, starts, "Leased Lines", *SEP, starts_ok=starts_available(selection))
    assert (s["blended_enquiries"], s["blended_revenue"]) == (51, money(10_274.50))
    sep = in_range(leads, *SEP)
    assert ((sep["started_in"] == "Leased Lines") & (sep["landed"] == "Broadband")).sum() == 18
    assert s["form_starts"] == 102


def test_leased_lines_september_focus_group(model):
    selection = {"f_buyer": {"Focus Group"}}
    leads = apply_filters(model.leads, selection)
    s = summarise(leads, model.starts, "Leased Lines", *SEP, starts_ok=starts_available(selection))
    assert (s["blended_leads"], s["blended_revenue"]) == (83, money(25_730.00))
    assert s["form_starts"] is None and s["form_conversion"] is None


def test_reported_total_equals_blended_total(model):
    channels = set(model.leads["landed"]) | set(model.leads["started_in"])
    for selection in ({}, {"f_device": {"Mobile"}}, {"f_buyer": {"Focus Group"}}):
        leads = apply_filters(model.leads, selection)
        for rng in (SEP, OCT_1_20, ALL):
            rows = [summarise(leads, model.starts, c, *rng) for c in channels]
            assert sum(r["reported_revenue"] for r in rows) == money(sum(r["blended_revenue"] for r in rows))


def test_uploading_same_file_twice_changes_nothing(model, tmp_path):
    first = loader.export_files(EXPORTS)[0]
    for f in loader.export_files(EXPORTS):
        loader.save_upload(f.name, f.read_bytes(), tmp_path)
    again = loader.save_upload("copy-" + first.name, first.read_bytes(), tmp_path)
    assert again.status == "duplicate"
    twice = pipeline.build(load_settings(), tmp_path, cache_dir=None)
    pd.testing.assert_frame_equal(
        model.leads.sort_values("id").reset_index(drop=True),
        twice.leads.sort_values("id").reset_index(drop=True),
    )


def test_non_export_rejected(tmp_path):
    result = loader.save_upload("notes.csv", b"name,email\nRich,x\n", tmp_path)
    assert result.status == "rejected"
    assert result.message == "This doesn't look like a lead export"
    assert not any(tmp_path.iterdir())
