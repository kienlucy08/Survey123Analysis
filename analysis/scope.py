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
SITE_ID_COLUMN = "site_id"
SITE_NAME_COLUMN = "site_name"
FAMILY_COLUMN = "family"
ROLE_COLUMN = "completion_role"
ORG_COLUMN = "organization"
DEFAULT_CREATOR_FIELDS = ("email", "Creator")
DEFAULT_SITE_ID_FIELD = "customer_site_id"
DEFAULT_SITE_NAME_FIELD = "customer_site_name"
# "confirm_org" is a human-readable confirmation of organization_id (whose
# alias is literally "Organization this data needs to be sent to") — present
# and consistently populated on every FieldSync survey layer checked 2026-09-25.
DEFAULT_ORG_FIELD = "confirm_org"


def load_scopes_config(path: str | Path) -> list[dict]:
    with open(path) as f:
        config = yaml.safe_load(f)
    return config["scopes"]


def iter_survey_specs(scopes_config: list[dict]):
    """Yield (scope_key, scope_label, survey_spec) for every survey in the config."""
    for scope in scopes_config:
        for survey in scope["surveys"]:
            yield scope["key"], scope["label"], survey


def get_completion_config(scopes_config: list[dict]) -> dict[str, list[str]]:
    """{scope_key: [required family, ...]} for every scope that opted into
    site-level completion tracking (config/scopes.yaml: track_completion: true
    + required_families). A scope's "site" is complete once it has at least
    one required-role submission in every listed family."""
    return {
        scope["key"]: scope["required_families"]
        for scope in scopes_config
        if scope.get("track_completion") and scope.get("required_families")
    }


def get_family_order(scopes_config: list[dict]) -> dict[str, list[str]]:
    """{scope_key: [family, ...]} in first-seen config order — every family
    a scope has (required and optional alike), for stable chart coloring and
    labeling. Don't use required_families for this: that's only the subset
    that drives site completeness, not the full set of families to display."""
    order: dict[str, list[str]] = {}
    for scope_key, _, survey in iter_survey_specs(scopes_config):
        family = survey.get("family", survey["key"])
        order.setdefault(scope_key, [])
        if family not in order[scope_key]:
            order[scope_key].append(family)
    return order


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


def _resolve_site(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(pd.NA, index=index, dtype="object")
    # Site IDs are typed by field techs (not selected from a list), so the
    # same site shows up as "ND-1531", "nd-1531 ", etc. Normalize so a join
    # on this column isn't defeated by whitespace/casing alone — it won't
    # fix a site name typed into the ID field, but it fixes the cheap part.
    return series.astype("object").str.strip().str.lower().replace("", pd.NA)


def tag_survey_records(
    df: pd.DataFrame,
    scope_key: str,
    scope_label: str,
    survey: dict,
    date_field: str = "CreationDate",
    creator_fields: tuple[str, ...] = DEFAULT_CREATOR_FIELDS,
    site_id_field: str = DEFAULT_SITE_ID_FIELD,
    site_name_field: str = DEFAULT_SITE_NAME_FIELD,
    org_field: str = DEFAULT_ORG_FIELD,
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

    site_id_field = survey.get("site_id_field", site_id_field)
    site_name_field = survey.get("site_name_field", site_name_field)
    org_field = survey.get("org_field", org_field)

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
            SITE_ID_COLUMN: _resolve_site(df.get(site_id_field), df.index),
            SITE_NAME_COLUMN: df.get(site_name_field),
            FAMILY_COLUMN: survey.get("family", survey["key"]),
            ROLE_COLUMN: survey.get("completion_role", "required"),
            ORG_COLUMN: df.get(org_field, pd.Series(pd.NA, index=df.index, dtype="object")).fillna("Unknown"),
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

    columns = [
        "scope", "scope_label", "survey_key", "survey_title", "purpose", DATE_COLUMN, CREATOR_COLUMN, ORG_COLUMN,
        "object_id", ATTACHMENT_COLUMN, SITE_ID_COLUMN, SITE_NAME_COLUMN, FAMILY_COLUMN, ROLE_COLUMN,
    ]
    if not tagged:
        return pd.DataFrame(columns=columns)
    return pd.concat(tagged, ignore_index=True)
