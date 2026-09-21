"""A tiny on-disk parquet cache so the dashboard doesn't re-query ArcGIS on every filter change."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


def save(df: pd.DataFrame, name: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE_DIR / f"{name}.parquet")


def load(name: str, max_age_hours: float | None = None) -> pd.DataFrame | None:
    path = CACHE_DIR / f"{name}.parquet"
    if not path.exists():
        return None
    if max_age_hours is not None:
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours > max_age_hours:
            return None
    return pd.read_parquet(path)
