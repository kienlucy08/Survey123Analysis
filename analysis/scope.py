"""Map raw per-survey records onto the org's "scope of work" categories.

config/scopes.yaml is the single source of truth for which surveys make up
which scope (e.g. Inspection = Compound Inspection + Structure Flight
Inspection + Guy Facilities + Plumb & Twist; Close Out = Close Out Report).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

DATE_COLUMN = "submitted_date"
CREATOR_COLUMN = "creator"
ATTACHMENT_COLUMN = "attachment_count"
DEFAULT_CREATOR_FIELDS = ("email", "Creator")


def load_scopes_config(path: str | Path) -> list[dict]:
    with open(path) as f:
        config = yaml.safe_load(f)
    return config["scopes"]


def iter_survey_specs(scopes_config: list[dict]):
    """Yield (scope_key, scope_label, survey_spec) for every survey in the config."""
    for scope in scopes_config:
        for survey in scope["surveys"]:
            yield scope["key"], scope["label"], survey


def _resolve_creator(df: pd.DataFrame, creator_fields: tuple[str, ...]) -> pd.Series:
    """First matching field wins, per-row: e.g. prefer the human "email" field
    over the ArcGIS login "Creator" field, falling back row-by-row if one is
    blank on a record that has the other."""
    result = pd.Series(pd.NA, index=df.index, dtype="object")
    for field in creator_fields:
        if field in df.columns:
            result = result.fillna(df[field])
    # Emails/usernames are case-insensitive identifiers; normalize so the same
    # person doesn't get split into two contributors by inconsistent casing.
    return result.fillna("unknown").str.strip().str.lower()


def tag_survey_records(
    df: pd.DataFrame,
    scope_key: str,
    scope_label: str,
    survey: dict,
    date_field: str = "CreationDate",
    creator_fields: tuple[str, ...] = DEFAULT_CREATOR_FIELDS,
) -> pd.DataFrame:
    """Filter a survey's raw records to the ones that belong to `scope_key`
    (via its purpose field, if configured) and normalize the shared columns
    every scope-of-work record needs downstream."""
    if df.empty:
        return df

    purpose_field = survey.get("purpose_field")
    purpose_values = survey.get("purpose_values") or []
    if purpose_field and purpose_values and purpose_field in df.columns:
        df = df[df[purpose_field].isin(purpose_values)]

    out = pd.DataFrame(
        {
            "scope": scope_key,
            "scope_label": scope_label,
            "survey_key": survey["key"],
            "survey_title": survey["title"],
            "purpose": df[purpose_field] if purpose_field in df.columns else survey["title"],
            DATE_COLUMN: pd.to_datetime(df.get(date_field), utc=True, errors="coerce"),
            CREATOR_COLUMN: _resolve_creator(df, creator_fields),
            "object_id": df.get("OBJECTID"),
            ATTACHMENT_COLUMN: df.get(ATTACHMENT_COLUMN),
        }
    )
    return out.dropna(subset=[DATE_COLUMN])


def unify(raw_by_survey_key: dict[str, pd.DataFrame], scopes_config: list[dict], **tag_kwargs) -> pd.DataFrame:
    """Combine every survey's raw records into one long, scope-tagged DataFrame."""
    tagged = []
    for scope_key, scope_label, survey in iter_survey_specs(scopes_config):
        raw = raw_by_survey_key.get(survey["key"])
        if raw is None:
            continue
        tagged.append(tag_survey_records(raw, scope_key, scope_label, survey, **tag_kwargs))

    if not tagged:
        return pd.DataFrame(
            columns=["scope", "scope_label", "survey_key", "survey_title", "purpose", DATE_COLUMN, CREATOR_COLUMN, "object_id"]
        )
    return pd.concat(tagged, ignore_index=True)
