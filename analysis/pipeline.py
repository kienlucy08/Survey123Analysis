"""Top-level orchestration: config -> (live ArcGIS fetch | sample data) -> unify -> cache."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

from analysis import cache, sample_data, scope
from analysis.attachments import fetch_attachment_counts
from analysis.auth import TokenManager
from analysis.fetch import query_layer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
SCOPES_PATH = PROJECT_ROOT / "config" / "scopes.yaml"
CACHE_NAME = "unified_submissions"


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def live_config_available() -> bool:
    load_dotenv(PROJECT_ROOT / ".env")
    has_creds = all(os.getenv(k) for k in ("ARCGIS_PORTAL_URL", "ARCGIS_USERNAME", "ARCGIS_PASSWORD"))
    return has_creds and SCOPES_PATH.exists()


def fetch_live() -> pd.DataFrame:
    load_dotenv(PROJECT_ROOT / ".env")
    settings = load_settings()
    scopes_config = scope.load_scopes_config(SCOPES_PATH)

    token_manager = TokenManager(
        portal_url=os.environ["ARCGIS_PORTAL_URL"],
        username=os.environ["ARCGIS_USERNAME"],
        password=os.environ["ARCGIS_PASSWORD"],
    )
    date_field = settings.get("default_date_field", "CreationDate")
    creator_fields = tuple(settings.get("default_creator_fields", scope.DEFAULT_CREATOR_FIELDS))

    raw_by_survey_key: dict[str, pd.DataFrame] = {}
    for _, _, survey in scope.iter_survey_specs(scopes_config):
        raw = query_layer(survey["layer_url"], token_manager.get())
        raw[scope.ATTACHMENT_COLUMN] = _fetch_attachment_counts_column(raw, survey["layer_url"], token_manager.get())
        raw_by_survey_key[survey["key"]] = raw

    return scope.unify(raw_by_survey_key, scopes_config, date_field=date_field, creator_fields=creator_fields)


def _fetch_attachment_counts_column(raw: pd.DataFrame, layer_url: str, token: str) -> pd.Series:
    """Best-effort per-record attachment count; falls back to NA for a layer
    that errors out rather than failing the whole pull."""
    if raw.empty or "OBJECTID" not in raw.columns:
        return pd.Series(dtype="Int64")
    try:
        object_ids = raw["OBJECTID"].dropna().astype(int).tolist()
        counts = fetch_attachment_counts(layer_url, token, object_ids)
        return raw["OBJECTID"].map(counts)
    except Exception:
        return pd.Series(pd.NA, index=raw.index, dtype="Int64")


def fetch_sample() -> pd.DataFrame:
    raw_by_survey_key = sample_data.generate_sample_dataset()
    scopes_config = sample_data.sample_scopes_config()
    return scope.unify(raw_by_survey_key, scopes_config, date_field="CreationDate", creator_fields=("email", "Creator"))


def get_unified_data(use_sample: bool, cache_max_age_hours: float | None = None, force_refresh: bool = False) -> pd.DataFrame:
    cache_name = f"{CACHE_NAME}_sample" if use_sample else CACHE_NAME

    if not force_refresh:
        cached = cache.load(cache_name, max_age_hours=cache_max_age_hours)
        if cached is not None:
            return cached

    df = fetch_sample() if use_sample else fetch_live()
    cache.save(df, cache_name)
    return df
