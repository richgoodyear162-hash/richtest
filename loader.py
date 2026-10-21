"""Loads lead exports from CSV uploads.

This is the only module that knows the data arrives as CSV files. Everything
else works with the DataFrame returned by `load_leads`, so a database
connection can replace the uploads later by providing a function with the same
return value.
"""

from __future__ import annotations

import hashlib
import io
import os
import pickle
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from settings import Settings, normalise_form_id

ROOT = Path(__file__).parent
# Uploads live in data/ next to the app unless RPE_DATA_DIR points elsewhere.
DATA_DIR = Path(os.environ.get("RPE_DATA_DIR", ROOT / "data"))
EXPORTS_DIR = DATA_DIR / "exports"
CACHE_DIR = DATA_DIR / "cache"

REQUIRED_COLUMNS = ("id", "formType", "created_at", "commission")
NOT_AN_EXPORT = "This doesn't look like a lead export"

# Bump when the processing below changes, so old caches are ignored.
CACHE_VERSION = 1

_EXCEL_DATE = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{4}")


@dataclass
class UploadResult:
    name: str
    status: str  # "saved", "duplicate" or "rejected"
    message: str
    path: Path | None = None


@dataclass
class LoadedData:
    rows: pd.DataFrame
    health: dict = field(default_factory=dict)


# Upload handling -----------------------------------------------------------


def read_export(source) -> pd.DataFrame:
    """Read a CSV as text, keeping blanks as empty strings."""
    return pd.read_csv(
        source, dtype=str, keep_default_na=False, encoding="utf-8-sig", low_memory=False
    )


def is_lead_export(frame: pd.DataFrame) -> bool:
    return set(REQUIRED_COLUMNS).issubset(frame.columns)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_name(name: str) -> str:
    stem = Path(name).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    if not stem.lower().endswith(".csv"):
        stem += ".csv"
    return stem


def save_upload(name: str, content: bytes, exports_dir: Path = EXPORTS_DIR) -> UploadResult:
    """Check an uploaded file is a lead export and keep it in exports_dir.

    Files that aren't lead exports are not saved. A file whose contents are
    already saved is skipped, so uploading the same file twice changes nothing.
    """
    try:
        frame = read_export(io.BytesIO(content))
    except Exception:
        return UploadResult(name, "rejected", NOT_AN_EXPORT)
    if not is_lead_export(frame):
        return UploadResult(name, "rejected", NOT_AN_EXPORT)

    exports_dir.mkdir(parents=True, exist_ok=True)
    digest = _sha256(content)
    for existing in exports_dir.glob("*.csv"):
        if existing.stat().st_size == len(content) and _sha256(existing.read_bytes()) == digest:
            return UploadResult(
                name, "duplicate", f"Already uploaded as {existing.name}", existing
            )

    target = exports_dir / _safe_name(name)
    if target.exists():
        target = target.with_name(f"{target.stem}-{digest[:8]}{target.suffix}")
    target.write_bytes(content)
    return UploadResult(name, "saved", f"Saved {len(frame):,} rows", target)


def export_files(exports_dir: Path = EXPORTS_DIR) -> list[Path]:
    if not exports_dir.exists():
        return []
    return sorted(p for p in exports_dir.iterdir() if p.suffix.lower() == ".csv")


def remove_export(name: str, exports_dir: Path = EXPORTS_DIR) -> bool:
    target = exports_dir / Path(name).name
    if target.exists() and target.parent == exports_dir:
        target.unlink()
        return True
    return False


# Parsing -------------------------------------------------------------------


def is_excel_resaved(created_at: pd.Series) -> bool:
    """True if the file's dates look like DD/MM/YYYY HH:MM (re-saved in Excel)."""
    values = created_at[created_at.str.strip() != ""]
    if values.empty:
        return False
    return values.str.match(_EXCEL_DATE).mean() > 0.5


def parse_dates(values: pd.Series) -> pd.Series:
    """Parse both YYYY-MM-DD HH:MM:SS and DD/MM/YYYY HH:MM, value by value."""
    text = values.astype(str).str.strip()
    parsed = pd.to_datetime(text, format="%Y-%m-%d %H:%M:%S", errors="coerce")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        missing = parsed.isna() & (text != "")
        if not missing.any():
            break
        parsed[missing] = pd.to_datetime(text[missing], format=fmt, errors="coerce")
    return parsed


def parse_money(values: pd.Series) -> pd.Series:
    """Commission as a number. Blanks are zero."""
    cleaned = values.astype(str).str.replace(r"[£,\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def _normalise_id(values: pd.Series) -> pd.Series:
    return values.astype(str).map(normalise_form_id)


def _read_file(path: Path, settings: Settings) -> pd.DataFrame:
    frame = read_export(path)
    if not is_lead_export(frame):
        return pd.DataFrame()
    c = settings.col
    for key in ("updated_at", "form_id", "cross_sell_id", "session"):
        if c(key) not in frame.columns:
            frame[c(key)] = ""
    frame["_file"] = path.name
    frame["_excel"] = is_excel_resaved(frame[c("created_at")])
    frame["created_at"] = parse_dates(frame[c("created_at")])
    frame["updated_at"] = parse_dates(frame[c("updated_at")])
    frame["commission"] = parse_money(frame[c("commission")])
    frame["id"] = _normalise_id(frame[c("id")])
    frame["formType"] = frame[c("form_type")].astype(str).str.strip()
    frame["form_id"] = _normalise_id(frame[c("form_id")])
    frame["cross_sell_id"] = _normalise_id(frame[c("cross_sell_id")])
    frame["session"] = frame[c("session")].astype(str).str.strip()
    return frame


# Loading -------------------------------------------------------------------


def _manifest(files: list[Path]) -> str:
    parts = [f"v{CACHE_VERSION}"]
    for p in files:
        stat = p.stat()
        parts.append(f"{p.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def data_version(exports_dir: Path = EXPORTS_DIR) -> str:
    """Changes whenever a file is added or removed."""
    return _manifest(export_files(exports_dir))


def dedupe(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per lead id.

    Untouched files win over files re-saved in Excel (which lose seconds and
    can't be compared reliably), then the latest updated_at wins.
    """
    ordered = rows.sort_values(
        ["_excel", "updated_at", "created_at"],
        ascending=[True, False, False],
        na_position="last",
        kind="mergesort",
    )
    return ordered.drop_duplicates("id", keep="first")


def _file_summary(frame: pd.DataFrame, name: str) -> dict:
    dates = frame["created_at"].dropna() if not frame.empty else pd.Series(dtype="datetime64[ns]")
    return {
        "File": name,
        "Rows": len(frame),
        "From": dates.min().date() if len(dates) else None,
        "To": dates.max().date() if len(dates) else None,
        "Re-saved in Excel": bool(frame["_excel"].iloc[0]) if len(frame) else False,
    }


def load_leads(
    settings: Settings,
    exports_dir: Path = EXPORTS_DIR,
    cache_dir: Path | None = CACHE_DIR,
) -> LoadedData:
    """Every lead export in exports_dir, cleaned and deduplicated.

    Processed data is cached on disk and only rebuilt when a file is added or
    removed.
    """
    files = export_files(exports_dir)
    key = _manifest(files) + f"-{settings.start_date.isoformat()}"
    cache_file = cache_dir / f"leads-{key}.pkl" if cache_dir else None
    if cache_file and cache_file.exists():
        try:
            with open(cache_file, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass

    frames, summaries = [], []
    for path in files:
        frame = _read_file(path, settings)
        summaries.append(_file_summary(frame, path.name))
        if not frame.empty:
            frames.append(frame)

    if frames:
        raw = pd.concat(frames, ignore_index=True)
    else:
        raw = _read_file_empty(settings)
    bad_dates = int(raw["created_at"].isna().sum())
    raw = raw[raw["created_at"].notna()]
    rows = dedupe(raw)
    before_start = int((rows["created_at"] < pd.Timestamp(settings.start_date)).sum())
    rows = rows[rows["created_at"] >= pd.Timestamp(settings.start_date)]
    rows = rows.sort_values(["created_at", "id"], kind="mergesort").reset_index(drop=True)

    health = {
        "files": pd.DataFrame(summaries, columns=["File", "Rows", "From", "To", "Re-saved in Excel"]),
        "rows_read": int(len(raw) + bad_dates),
        "rows_after_dedupe": int(len(rows)),
        "duplicates_removed": int(len(raw) - len(rows) - before_start),
        "rows_before_start": before_start,
        "unreadable_dates": bad_dates,
        "coverage": (
            (rows["created_at"].min(), rows["created_at"].max()) if len(rows) else (None, None)
        ),
    }
    result = LoadedData(rows=rows, health=health)

    if cache_file:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for old in cache_dir.glob("leads-*.pkl"):
            old.unlink(missing_ok=True)
        with open(cache_file, "wb") as fh:
            pickle.dump(result, fh)
    return result


def _read_file_empty(settings: Settings) -> pd.DataFrame:
    columns = list(REQUIRED_COLUMNS) + [
        "updated_at", "form_id", "cross_sell_id", "session", "_file", "_excel",
    ]
    frame = pd.DataFrame({c: pd.Series(dtype=str) for c in columns})
    frame["created_at"] = pd.Series(dtype="datetime64[ns]")
    frame["updated_at"] = pd.Series(dtype="datetime64[ns]")
    frame["commission"] = pd.Series(dtype=float)
    frame["_excel"] = pd.Series(dtype=bool)
    return frame


def coverage_dates(loaded: LoadedData) -> tuple[date | None, date | None]:
    first, last = loaded.health.get("coverage", (None, None))
    return (first.date() if first is not None else None, last.date() if last is not None else None)
