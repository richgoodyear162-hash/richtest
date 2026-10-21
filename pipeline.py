"""Ties loader and attribution together. Used by the app and the tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import loader
from attribution import attribute_leads, form_starts, mapping_health, prepare_rows
from settings import Settings


@dataclass
class Model:
    leads: pd.DataFrame
    leads_with_follow_ons: pd.DataFrame
    starts: pd.DataFrame
    health: dict

    def leads_for(self, follow_ons: bool) -> pd.DataFrame:
        return self.leads_with_follow_ons if follow_ons else self.leads


def build(settings: Settings, exports_dir: Path = loader.EXPORTS_DIR, cache_dir: Path | None = loader.CACHE_DIR) -> Model:
    loaded = loader.load_leads(settings, exports_dir, cache_dir)
    prepared = prepare_rows(loaded.rows, settings)
    health = dict(loaded.health)
    health.update(mapping_health(prepared, settings))
    return Model(
        leads=attribute_leads(prepared, settings, follow_ons=False),
        leads_with_follow_ons=attribute_leads(prepared, settings, follow_ons=True),
        starts=form_starts(prepared),
        health=health,
    )
