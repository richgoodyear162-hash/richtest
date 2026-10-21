"""Builds small made-up lead exports for tests.

Nothing here is real data, and the files are only ever written to a temporary
folder during a test run.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pandas as pd

COLUMNS = [
    "id", "formType", "created_at", "updated_at", "commission", "form_id",
    "cross_sell_id", "session_id", "utm_source", "utm_medium", "campaign_name",
    "match_type", "device", "variation_name", "day_of_week", "provider_name",
    "valid", "region",
]


def row(**values) -> dict:
    base = {c: "" for c in COLUMNS}
    base.update({k: str(v) for k, v in values.items()})
    if not base["updated_at"]:
        base["updated_at"] = base["created_at"]
    return base


def frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLUMNS)


def random_export(seed: int = 1, sessions: int = 400) -> pd.DataFrame:
    """A plausible mix of journeys, routing, cross sells and partials."""
    rng = random.Random(seed)
    rows: list[dict] = []
    next_id = 1000
    start = datetime(2026, 6, 1)
    devices = ["Mobile", "Desktop", "Tablet", ""]
    sources = [("google", "cpc"), ("bing", "cpc"), ("google", "organic"), ("", ""), ("newsletter", "email")]
    buyers = ["Focus Group", "Sky", "O2 Daisy", "British Gas", ""]

    for s in range(sessions):
        session = f"s{s}"
        t = start + timedelta(minutes=rng.randint(0, 60 * 24 * 140))
        src, med = rng.choice(sources)
        common = dict(
            session_id=session, utm_source=src, utm_medium=med,
            campaign_name=rng.choice(["", "Brand UK", "Leased Lines Generic", "Energy Compare"]),
            device=rng.choice(devices), match_type=rng.choice(["exact", "phrase", ""]),
            region=rng.choice(["", "London", "North West"]),
        )
        journey = rng.choice(["ll", "bb", "en"])
        form_id = {"ll": rng.choice([5, 21]), "bb": rng.choice([2, 7]), "en": rng.choice([6, 19])}[journey]
        base_type = {"ll": "leased-lines", "bb": "broadband", "en": "compare-business-energy"}[journey]

        def fmt(dt):
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        # A partial row or two on the way in.
        for _ in range(rng.randint(1, 2)):
            next_id += 1
            rows.append(row(id=next_id, formType=f"partial-{base_type}", created_at=fmt(t), form_id=form_id, **common))
            t += timedelta(minutes=1)
        if rng.random() < 0.3:
            continue  # abandoned

        landed = base_type
        if journey == "ll" and rng.random() < 0.25:
            landed = "broadband"  # routed in form
        if landed == "broadband" and rng.random() < 0.2:
            landed = "broadband-mobile"
        next_id += 1
        parent = next_id
        amount = {"leased-lines": 280, "broadband": 47.5, "broadband-mobile": 52, "compare-business-energy": 180}[landed]
        rows.append(row(
            id=parent, formType=landed, created_at=fmt(t), form_id=form_id,
            commission=rng.choice([amount, amount, ""]), provider_name=rng.choice(buyers),
            valid=rng.choice(["1", "0"]), **common,
        ))
        t += timedelta(minutes=3)

        if landed.startswith("broadband") and rng.random() < 0.4:
            # Partial cross sell carrying the parent's commission (a known quirk),
            # then sometimes the completed cross sell.
            next_id += 1
            rows.append(row(id=next_id, formType="partial-cross-sell-energy", created_at=fmt(t),
                            form_id=form_id, cross_sell_id=parent, commission=30, **common))
            if rng.random() < 0.6:
                next_id += 1
                rows.append(row(id=next_id, formType="cross-sell-energy", created_at=fmt(t + timedelta(minutes=2)),
                                form_id=form_id, cross_sell_id=parent, commission=52.5,
                                provider_name="British Gas", **common))
        if rng.random() < 0.1:
            # A second lead in the same session on another journey, no link.
            next_id += 1
            rows.append(row(id=next_id, formType="compare-business-energy", created_at=fmt(t + timedelta(minutes=9)),
                            form_id=6, commission=150, **common))
    return frame(rows)


def to_excel_format(export: pd.DataFrame) -> pd.DataFrame:
    """Mimic a file re-saved in Excel: DD/MM/YYYY HH:MM dates."""
    out = export.copy()
    for col in ("created_at", "updated_at"):
        parsed = pd.to_datetime(out[col], errors="coerce")
        out[col] = parsed.dt.strftime("%d/%m/%Y %H:%M").fillna("")
    return out
