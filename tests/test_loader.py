import pandas as pd
import pytest

import loader
import pipeline
from metrics import summarise
from settings import load_settings
from synthetic import frame, random_export, row, to_excel_format


@pytest.fixture
def settings():
    return load_settings()


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


def test_rejects_csv_that_is_not_an_export(tmp_path):
    bad = pd.DataFrame({"name": ["a"], "email": ["b"]})
    result = loader.save_upload("contacts.csv", csv_bytes(bad), tmp_path)
    assert result.status == "rejected"
    assert result.message == "This doesn't look like a lead export"
    assert list(tmp_path.iterdir()) == []


def test_rejects_unreadable_file(tmp_path):
    result = loader.save_upload("junk.csv", b"\x00\x01\x02not,a\ncsv\x00", tmp_path)
    assert result.status == "rejected"
    assert list(tmp_path.iterdir()) == []


def test_same_file_twice_is_not_saved_again(tmp_path):
    content = csv_bytes(random_export(seed=3, sessions=50))
    first = loader.save_upload("enquiries-master-anon-1.csv", content, tmp_path)
    second = loader.save_upload("enquiries-master-anon-1 (1).csv", content, tmp_path)
    assert first.status == "saved"
    assert second.status == "duplicate"
    assert len(loader.export_files(tmp_path)) == 1


def test_overlapping_exports_do_not_change_figures(tmp_path, settings):
    export = random_export(seed=4, sessions=300)
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    loader.save_upload("all.csv", csv_bytes(export), a_dir)

    # Same leads split into two overlapping files, plus the whole file again
    # under a different name with a trivial byte difference.
    half = len(export) // 2
    loader.save_upload("first.csv", csv_bytes(export.iloc[: half + 50]), b_dir)
    loader.save_upload("second.csv", csv_bytes(export.iloc[half - 50 :]), b_dir)
    loader.save_upload("again.csv", csv_bytes(export) + b"\n", b_dir)

    m_a = pipeline.build(settings, a_dir, cache_dir=None)
    m_b = pipeline.build(settings, b_dir, cache_dir=None)
    assert m_b.health["duplicates_removed"] > 0
    start, end = pd.Timestamp("2026-06-01").date(), pd.Timestamp("2026-10-31").date()
    for channel in ("Leased Lines", "Broadband", "Energy"):
        assert summarise(m_a.leads, m_a.starts, channel, start, end) == summarise(
            m_b.leads, m_b.starts, channel, start, end
        )


def test_dedupe_keeps_latest_updated_at(tmp_path, settings):
    rows = [
        row(id=1, formType="leased-lines", created_at="2026-09-01 10:00:00",
            updated_at="2026-09-01 10:00:00", commission=220, form_id=5),
        row(id=1, formType="leased-lines", created_at="2026-09-01 10:00:00",
            updated_at="2026-09-03 09:00:00", commission=280, form_id=5),
    ]
    loader.save_upload("a.csv", csv_bytes(frame(rows[:1])), tmp_path)
    loader.save_upload("b.csv", csv_bytes(frame(rows[1:])), tmp_path)
    loaded = loader.load_leads(settings, tmp_path, cache_dir=None)
    assert len(loaded.rows) == 1
    assert loaded.rows["commission"].iloc[0] == 280
    assert loaded.health["duplicates_removed"] == 1


def test_untouched_file_beats_excel_resave(tmp_path, settings):
    original = frame([
        row(id=7, formType="broadband", created_at="2026-09-02 11:15:42",
            updated_at="2026-09-02 11:15:42", commission=47.5, form_id=2),
    ])
    resaved = to_excel_format(original)
    resaved["updated_at"] = "05/09/2026 08:00"  # later, but from an Excel file
    resaved["commission"] = "99"
    loader.save_upload("excel.csv", csv_bytes(resaved), tmp_path)
    loader.save_upload("orig.csv", csv_bytes(original), tmp_path)
    loaded = loader.load_leads(settings, tmp_path, cache_dir=None)
    assert loaded.rows["commission"].tolist() == [47.5]


def test_parses_both_date_formats(settings, tmp_path):
    export = random_export(seed=5, sessions=40)
    loader.save_upload("excel.csv", csv_bytes(to_excel_format(export)), tmp_path)
    loaded = loader.load_leads(settings, tmp_path, cache_dir=None)
    assert loaded.health["unreadable_dates"] == 0
    assert len(loaded.rows) == len(export)
    assert bool(loaded.health["files"]["Re-saved in Excel"].iloc[0])


def test_blank_commission_is_zero_and_before_start_dropped(tmp_path, settings):
    rows = frame([
        row(id=1, formType="leased-lines", created_at="2026-05-31 23:59:00", commission=200, form_id=5),
        row(id=2, formType="leased-lines", created_at="2026-06-01 00:00:00", commission="", form_id=5),
        row(id=3, formType="leased-lines", created_at="2026-06-02 09:00:00", commission="£1,250.50", form_id=5),
    ])
    loader.save_upload("a.csv", csv_bytes(rows), tmp_path)
    loaded = loader.load_leads(settings, tmp_path, cache_dir=None)
    assert loaded.rows["id"].tolist() == ["2", "3"]
    assert loaded.rows["commission"].tolist() == [0.0, 1250.5]


def test_cache_reused_until_files_change(tmp_path, settings):
    exports, cache = tmp_path / "exports", tmp_path / "cache"
    loader.save_upload("a.csv", csv_bytes(random_export(seed=6, sessions=30)), exports)
    first = loader.load_leads(settings, exports, cache)
    assert len(list(cache.glob("*.pkl"))) == 1
    second = loader.load_leads(settings, exports, cache)
    assert len(second.rows) == len(first.rows)
    loader.save_upload("b.csv", csv_bytes(random_export(seed=7, sessions=30)), exports)
    third = loader.load_leads(settings, exports, cache)
    assert len(third.rows) > len(first.rows)
    assert len(list(cache.glob("*.pkl"))) == 1
    loader.remove_export("b.csv", exports)
    assert len(loader.load_leads(settings, exports, cache).rows) == len(first.rows)
