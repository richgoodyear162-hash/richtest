"""Checks dashboard.html gives exactly the same figures as the Python code.

The engine inside dashboard.html is run with Node on made-up exports, and its
results are compared with the Python pipeline on the same files.
"""

import json
import random
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

import loader
import pipeline
from metrics import FILTERS, apply_filters, breakdown, in_range, starts_available, summarise
from settings import load_settings
from synthetic import frame, random_export, row, to_excel_format

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "dashboard.html"
RUNNER = Path(__file__).with_name("engine_runner.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node isn't installed")


def run_engine(files, cases, tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"files": [str(f) for f in files], "cases": cases}))
    out = subprocess.run(["node", str(RUNNER), str(HTML), str(spec)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def write_exports(tmp_path):
    exports = tmp_path / "exports"
    export = random_export(seed=31, sessions=1500)
    half = len(export) // 2
    # Overlapping files, an Excel re-save, and an unmapped form id and type.
    extra = frame([
        row(id=900001, formType="broadband", created_at="2026-10-13 09:00:00", form_id=35, session_id="z1", commission=47.5),
        row(id=900002, formType="invoice", created_at="2026-08-02 10:00:00", form_id=40, session_id="z2", commission=95),
        row(id=900003, formType="leased-lines", created_at="2026-05-20 10:00:00", form_id=5, session_id="z3", commission=240),
    ])
    for name, part in [
        ("enquiries-master-anon-1.csv", export.iloc[: half + 200]),
        ("enquiries-master-anon-2.csv", export.iloc[half - 200 :]),
        ("enquiries-master-anon-3-excel.csv", to_excel_format(export.iloc[half - 600 : half + 600])),
        ("enquiries-master-anon-4.csv", extra),
    ]:
        assert loader.save_upload(name, part.to_csv(index=False).encode(), exports).status == "saved"
    return exports


def test_config_matches_python_settings():
    text = HTML.read_text()
    settings = load_settings()
    for channel in ("Leased Lines", "Broadband", "Energy"):
        block = re.search(r'name: "%s"(.*?)\}' % channel, text, re.S).group(1)
        ids = sorted(re.search(r"formIds: \[([^\]]*)\]", block).group(1).replace(" ", "").split(","))
        assert ids == sorted(k for k, v in settings.form_id_channel.items() if v == channel)
        types = sorted(re.findall(r'"([a-z-]+)"', re.search(r"formTypes: \[([^\]]*)\]", block).group(1)))
        assert types == sorted(k for k, v in settings.form_type_channel.items() if v == channel)


def test_html_engine_matches_python(tmp_path):
    settings = load_settings()
    exports = write_exports(tmp_path)
    model = pipeline.build(settings, exports, cache_dir=None)

    rng = random.Random(5)
    cases = []
    for i in range(60):
        selection = {}
        for spec in rng.sample(FILTERS, rng.randint(0, 3)):
            values = sorted(model.leads_with_follow_ons[spec.key].unique())
            selection[spec.key] = sorted(rng.sample(values, rng.randint(1, len(values))))
        start = date(2026, rng.randint(6, 9), rng.randint(1, 28))
        end = date(2026, rng.choice([9, 10]), rng.randint(1, 30))
        if end < start:
            start, end = end, start
        cases.append({
            "channel": rng.choice(["Leased Lines", "Broadband", "Energy", "Unmapped"]),
            "start": start.isoformat(), "end": end.isoformat(),
            "gran": rng.choice(["Day", "Week", "Month"]),
            "followOns": i % 3 == 0, "selection": selection,
        })

    js = run_engine(loader.export_files(exports), cases, tmp_path)

    h = model.health
    assert js["health"]["rowsAfterDedupe"] == h["rows_after_dedupe"]
    assert js["health"]["duplicatesRemoved"] == h["duplicates_removed"]
    assert js["health"]["completedLeads"] == h["completed_leads"]
    assert js["health"]["partialWithCommission"] == h["partial_with_commission"]
    assert js["health"]["unmappedFormIds"] == sorted(h["unmapped_form_ids"]["Form id"])
    assert js["health"]["unmappedFormTypes"] == sorted(h["unmapped_form_types"]["Form type"])
    assert js["checks"] == 15

    for case, got in zip(cases, js["cases"]):
        selection = {k: set(v) for k, v in case["selection"].items()}
        ok = starts_available(selection)
        leads = apply_filters(model.leads_for(case["followOns"]), selection)
        starts = apply_filters(model.starts, {k: v for k, v in selection.items()
                                              if next(f for f in FILTERS if f.key == k).filters_starts})
        s, e = date.fromisoformat(case["start"]), date.fromisoformat(case["end"])
        want = summarise(leads, starts, case["channel"], s, e, ok)
        for key, value in want.items():
            if value is None:
                assert got["summary"][key] is None, (case, key)
            else:
                assert got["summary"][key] == pytest.approx(value), (case, key)
        check_routed(leads, case, s, e, got["routed"])
        table = breakdown(leads, starts, case["channel"], s, e, case["gran"], ok)
        assert [r["label"] for r in got["table"]] == table["label"].tolist()
        assert [r["isPart"] for r in got["table"]] == table["is_part"].tolist()
        assert [r["blended_revenue"] for r in got["table"]] == pytest.approx(table["blended_revenue"].tolist())
        assert [r["blended_enquiries"] for r in got["table"]] == table["blended_enquiries"].tolist()


def check_routed(leads, case, start, end, got):
    """The routed customers panel, worked out independently with pandas."""
    src = "Leased Lines" if case["channel"] == "Broadband" else case["channel"]
    to = "Broadband"
    period = in_range(leads, start, end)
    started = period[period["started_in"] == src]
    routed = started[started["landed"] == to]
    kept = started[started["landed"] == src]
    reported = period[period["landed"] == to]
    assert got["from_enquiries"] == int((~started["is_addon"]).sum())
    assert got["from_revenue"] == pytest.approx(started["commission"].sum())
    assert got["routed_leads"] == len(routed)
    assert got["routed_revenue"] == pytest.approx(routed["commission"].sum())
    assert got["kept_leads"] == len(kept)
    assert got["to_reported_leads"] == len(reported)
    assert got["to_without_leads"] == len(reported) - len(routed)
    assert got["to_without_revenue"] == pytest.approx(reported["commission"].sum() - routed["commission"].sum())
    if src == "Leased Lines":
        # Broadband without routed leased lines customers is its flattened figure
        # when leased lines is the only journey that routes into broadband.
        others = reported[(reported["started_in"] != to) & (reported["started_in"] != src)]
        if others.empty and got["to_without_leads"]:
            flat = reported[reported["started_in"] == to]
            assert got["to_without_rpe"] == pytest.approx(flat["commission"].sum() / len(flat))
