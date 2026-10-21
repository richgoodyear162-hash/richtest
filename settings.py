"""Reads config.toml into a small object the rest of the dashboard uses."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.toml")

UNMAPPED = "Unmapped"


@dataclass(frozen=True)
class Event:
    date: date
    label: str


@dataclass
class Settings:
    start_date: date
    dashboard_channels: list[str]
    cross_sell_form_types: set[str]
    partial_prefix: str
    max_cross_sell_depth: int
    columns: dict[str, str]
    form_type_channel: dict[str, str]
    form_id_channel: dict[str, str]
    traffic_sources: dict[str, list[str]]
    brand_terms: list[str]
    events: list[Event] = field(default_factory=list)

    def col(self, key: str) -> str:
        return self.columns.get(key, key)


def normalise_form_id(value) -> str:
    """Form ids arrive as 5, "5" or "5.0" depending on how the file was saved."""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def load_settings(path: Path | str = CONFIG_PATH) -> Settings:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    form_type_channel: dict[str, str] = {}
    form_id_channel: dict[str, str] = {}
    for channel in raw.get("channels", []):
        name = channel["name"]
        for form_type in channel.get("form_types", []):
            form_type_channel[str(form_type).strip()] = name
        for form_id in channel.get("form_ids", []):
            form_id_channel[normalise_form_id(form_id)] = name

    events = [Event(e["date"], e["label"]) for e in raw.get("events", [])]
    start = raw.get("start_date", date(2026, 6, 1))
    if isinstance(start, str):
        start = date.fromisoformat(start)

    return Settings(
        start_date=start,
        dashboard_channels=list(raw.get("dashboard_channels", [])),
        cross_sell_form_types=set(raw.get("cross_sell_form_types", [])),
        partial_prefix=raw.get("partial_prefix", "partial-"),
        max_cross_sell_depth=int(raw.get("max_cross_sell_depth", 5)),
        columns=dict(raw.get("columns", {})),
        form_type_channel=form_type_channel,
        form_id_channel=form_id_channel,
        traffic_sources={k: list(v) for k, v in raw.get("traffic_sources", {}).items()},
        brand_terms=list(raw.get("campaign_types", {}).get("brand_terms", ["brand"])),
        events=sorted(events, key=lambda e: e.date),
    )
