"""Usage & growth metrics over a unified, scope-tagged submissions table."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from analysis.scope import (
    ATTACHMENT_COLUMN,
    CREATOR_COLUMN,
    DATE_COLUMN,
    FAMILY_COLUMN,
    ORG_COLUMN,
    ROLE_COLUMN,
    SITE_ID_COLUMN,
    SITE_NAME_COLUMN,
)


def _to_utc_timestamp(value: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def primary_only(df: pd.DataFrame) -> pd.DataFrame:
    """Excludes QA-role rows, so "submissions" reflects actual field survey
    visits rather than a QA reviewer's extra pass over a site that's already
    counted once. Every "submissions"/usage/growth/contributor metric should
    run on this — the only thing that needs the un-filtered df is
    site_completion(), which explicitly wants both required and qa rows."""
    if df.empty or ROLE_COLUMN not in df.columns:
        return df
    return df[df[ROLE_COLUMN] != "qa"]


def filter_by_timeframe(df: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    start = _to_utc_timestamp(start)
    end = _to_utc_timestamp(end)
    return df[(df[DATE_COLUMN] >= start) & (df[DATE_COLUMN] <= end)]


def usage_by_scope(df: pd.DataFrame) -> pd.DataFrame:
    """Total submissions per scope of work, most active first."""
    if df.empty:
        return pd.DataFrame(columns=["scope", "scope_label", "submissions"])
    return (
        df.groupby(["scope", "scope_label"], as_index=False)
        .size()
        .rename(columns={"size": "submissions"})
        .sort_values("submissions", ascending=False)
        .reset_index(drop=True)
    )


def usage_by_purpose(df: pd.DataFrame, scope_key: str) -> pd.DataFrame:
    """Breakdown of one scope's submissions by its purpose/survey values."""
    subset = df[df["scope"] == scope_key]
    if subset.empty:
        return pd.DataFrame(columns=["purpose", "submissions"])
    return (
        subset.groupby("purpose", as_index=False)
        .size()
        .rename(columns={"size": "submissions"})
        .sort_values("submissions", ascending=False)
        .reset_index(drop=True)
    )


def usage_by_organization(df: pd.DataFrame) -> pd.DataFrame:
    """Total submissions per organization ("the organization this data needs
    to be sent to" — confirm_org), most active first, across every scope."""
    if df.empty:
        return pd.DataFrame(columns=[ORG_COLUMN, "submissions", "unique_contributors", "avg_attachments"])
    return (
        df.groupby(ORG_COLUMN, as_index=False)
        .agg(
            submissions=(DATE_COLUMN, "size"),
            unique_contributors=(CREATOR_COLUMN, "nunique"),
            avg_attachments=(ATTACHMENT_COLUMN, "mean"),
        )
        .sort_values("submissions", ascending=False)
        .reset_index(drop=True)
    )


def organization_trend(df: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """Submissions over time per organization, for a line chart."""
    if df.empty:
        return pd.DataFrame(columns=["period", ORG_COLUMN, "submissions"])
    grouped = (
        df.set_index(DATE_COLUMN)
        .groupby(ORG_COLUMN)
        .resample(freq)
        .size()
        .rename("submissions")
        .reset_index()
        .rename(columns={DATE_COLUMN: "period"})
    )
    return grouped


def organization_growth(df: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    """Like period_over_period_growth but per organization instead of scope."""
    start_ts = _to_utc_timestamp(start)
    end_ts = _to_utc_timestamp(end)
    period_length = end_ts - start_ts
    prior_start = start_ts - period_length

    current = usage_by_organization(filter_by_timeframe(df, start_ts, end_ts)).set_index(ORG_COLUMN)
    prior = usage_by_organization(filter_by_timeframe(df, prior_start, start_ts)).set_index(ORG_COLUMN)

    orgs = sorted(set(current.index) | set(prior.index))
    rows = []
    for org in orgs:
        current_count = int(current["submissions"].get(org, 0))
        prior_count = int(prior["submissions"].get(org, 0))
        pct_change = None
        if prior_count > 0:
            pct_change = (current_count - prior_count) / prior_count * 100
        elif current_count > 0:
            pct_change = float("inf")
        rows.append({"organization": org, "current_count": current_count, "prior_count": prior_count, "pct_change": pct_change})
    if not rows:
        return pd.DataFrame(columns=["organization", "current_count", "prior_count", "pct_change"])
    return pd.DataFrame(rows).sort_values("current_count", ascending=False).reset_index(drop=True)


def trend(df: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """Submissions over time per scope, for a line chart. freq: 'D'/'W'/'M'."""
    if df.empty:
        return pd.DataFrame(columns=["period", "scope", "scope_label", "submissions"])
    grouped = (
        df.set_index(DATE_COLUMN)
        .groupby(["scope", "scope_label"])
        .resample(freq)
        .size()
        .rename("submissions")
        .reset_index()
        .rename(columns={DATE_COLUMN: "period"})
    )
    return grouped


def period_over_period_growth(df: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    """Compare each scope's submission count in [start, end] against the
    immediately preceding period of equal length."""
    start_ts = _to_utc_timestamp(start)
    end_ts = _to_utc_timestamp(end)
    period_length = end_ts - start_ts
    prior_start = start_ts - period_length
    prior_end = start_ts

    current = usage_by_scope(filter_by_timeframe(df, start_ts, end_ts)).set_index("scope")
    prior = usage_by_scope(filter_by_timeframe(df, prior_start, prior_end)).set_index("scope")

    scopes = sorted(set(current.index) | set(prior.index))
    rows = []
    for scope_key in scopes:
        current_count = int(current["submissions"].get(scope_key, 0))
        prior_count = int(prior["submissions"].get(scope_key, 0))
        label = current["scope_label"].get(scope_key) or prior["scope_label"].get(scope_key)
        pct_change = None
        if prior_count > 0:
            pct_change = (current_count - prior_count) / prior_count * 100
        elif current_count > 0:
            pct_change = float("inf")
        rows.append(
            {
                "scope": scope_key,
                "scope_label": label,
                "current_count": current_count,
                "prior_count": prior_count,
                "pct_change": pct_change,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["scope", "scope_label", "current_count", "prior_count", "pct_change"])
    return pd.DataFrame(rows).sort_values("current_count", ascending=False).reset_index(drop=True)


def top_contributors(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Submission counts and average photos/attachments per contributor."""
    if df.empty:
        return pd.DataFrame(columns=[CREATOR_COLUMN, "submissions", "avg_attachments"])
    grouped = df.groupby(CREATOR_COLUMN, as_index=False).agg(
        submissions=(DATE_COLUMN, "size"),
        avg_attachments=(ATTACHMENT_COLUMN, "mean"),
    )
    return grouped.sort_values("submissions", ascending=False).head(n).reset_index(drop=True)


def contributor_activity(df: pd.DataFrame, start: datetime, end: datetime) -> dict:
    """New vs. returning contributors in [start, end], where "new" means no
    submission anywhere in df before `start`."""
    start_ts = _to_utc_timestamp(start)
    end_ts = _to_utc_timestamp(end)

    period_creators = set(filter_by_timeframe(df, start_ts, end_ts)[CREATOR_COLUMN].unique())
    prior_creators = set(df[df[DATE_COLUMN] < start_ts][CREATOR_COLUMN].unique())

    new_creators = period_creators - prior_creators
    returning_creators = period_creators & prior_creators
    period_count = filter_by_timeframe(df, start_ts, end_ts).shape[0]

    return {
        "total_contributors": len(period_creators),
        "new_contributors": len(new_creators),
        "returning_contributors": len(returning_creators),
        "avg_submissions_per_contributor": (period_count / len(period_creators)) if period_creators else 0.0,
    }


def attachment_stats(df: pd.DataFrame) -> dict:
    """Overall photo/attachment-count stats: mean, median, and the single most
    common count ("mode") — the number management is likeliest to ask about."""
    counts = df[ATTACHMENT_COLUMN].dropna()
    if counts.empty:
        return {"mean": None, "median": None, "mode": None, "coverage": 0.0}
    mode = counts.mode()
    return {
        "mean": float(counts.mean()),
        "median": float(counts.median()),
        "mode": int(mode.iloc[0]) if not mode.empty else None,
        "coverage": len(counts) / len(df),
    }


def attachment_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """How many submissions had 0, 1, 2, ... attachments — for a histogram."""
    counts = df[ATTACHMENT_COLUMN].dropna()
    if counts.empty:
        return pd.DataFrame(columns=["attachment_count", "submissions"])
    return (
        counts.astype(int)
        .value_counts()
        .rename_axis("attachment_count")
        .reset_index(name="submissions")
        .sort_values("attachment_count")
        .reset_index(drop=True)
    )


def monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per calendar month, per scope: submissions, unique submitters, and
    average attachments — the core table for a monthly management report."""
    if df.empty:
        return pd.DataFrame(columns=["month", "scope", "scope_label", "submissions", "unique_submitters", "avg_attachments"])
    grouped = (
        df.assign(month=df[DATE_COLUMN].dt.tz_convert(None).dt.to_period("M").dt.to_timestamp())
        .groupby(["month", "scope", "scope_label"], as_index=False)
        .agg(
            submissions=(DATE_COLUMN, "size"),
            unique_submitters=(CREATOR_COLUMN, "nunique"),
            avg_attachments=(ATTACHMENT_COLUMN, "mean"),
        )
    )
    return grouped.sort_values(["month", "submissions"], ascending=[True, False]).reset_index(drop=True)


def site_completion(df: pd.DataFrame, scope_key: str, required_families: list[str]) -> pd.DataFrame:
    """For one scope made of several surveys (e.g. Inspection = compound +
    structure, required; guy + pnt, optional — guy only applies to guyed
    towers, pnt isn't always done), which sites are missing a *required*
    family vs. a partial set. Pass an already time-frame-filtered df — this
    reflects "sites worked in this period," not all-time.

    families_present/submissions count every non-QA family (required and
    optional alike) so an optional survey still shows up as useful context —
    it just never makes a site "incomplete" on its own. Only required_families
    drives missing_families/is_complete.

    Site IDs are free text typed by field techs, not selected from a list, so
    treat this as directionally right rather than exact until that data-entry
    issue is cleaned up (see config/scopes.yaml's note on the Inspection scope).
    """
    subset = df[(df["scope"] == scope_key) & df[SITE_ID_COLUMN].notna()]
    non_qa = subset[subset[ROLE_COLUMN] != "qa"]
    if non_qa.empty:
        return pd.DataFrame(
            columns=["site_id", "site_name", "families_present", "missing_families", "is_complete", "submissions", "last_submitted", "qa_reviewed"]
        )

    rows = []
    for site_id, group in non_qa.groupby(SITE_ID_COLUMN):
        families_present = sorted(set(group[FAMILY_COLUMN]))
        missing = [f for f in required_families if f not in families_present]
        names = group[SITE_NAME_COLUMN].dropna()
        qa_group = subset[(subset[SITE_ID_COLUMN] == site_id) & (subset[ROLE_COLUMN] == "qa")]
        rows.append(
            {
                "site_id": site_id,
                "site_name": names.mode().iloc[0] if not names.empty else site_id,
                "families_present": ", ".join(families_present),
                "missing_families": ", ".join(missing) if missing else "—",
                "is_complete": len(missing) == 0,
                "submissions": len(group),
                "last_submitted": group[DATE_COLUMN].max(),
                "qa_reviewed": not qa_group.empty,
            }
        )
    return pd.DataFrame(rows).sort_values(["is_complete", "last_submitted"], ascending=[True, False]).reset_index(drop=True)


def usage_by_family(df: pd.DataFrame, scope_key: str) -> pd.DataFrame:
    """Submissions per family within one scope (e.g. Inspection's compound /
    structure / guy / pnt). Pass a primary_only() df — this doesn't filter
    QA out itself, since family_growth() needs qa-inclusive intermediates
    for other purposes; the dashboard/report callers pass primary_only(df)."""
    subset = df[df["scope"] == scope_key]
    if subset.empty:
        return pd.DataFrame(columns=[FAMILY_COLUMN, "submissions"])
    return (
        subset.groupby(FAMILY_COLUMN, as_index=False)
        .size()
        .rename(columns={"size": "submissions"})
        .sort_values("submissions", ascending=False)
        .reset_index(drop=True)
    )


def family_trend(df: pd.DataFrame, scope_key: str, freq: str = "D") -> pd.DataFrame:
    """Submissions over time per family within one scope, for a line chart."""
    subset = df[df["scope"] == scope_key]
    if subset.empty:
        return pd.DataFrame(columns=["period", FAMILY_COLUMN, "submissions"])
    grouped = (
        subset.set_index(DATE_COLUMN)
        .groupby(FAMILY_COLUMN)
        .resample(freq)
        .size()
        .rename("submissions")
        .reset_index()
        .rename(columns={DATE_COLUMN: "period"})
    )
    return grouped


def family_growth(df: pd.DataFrame, scope_key: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Like period_over_period_growth but per family within one scope. Pass
    the full-history (not time-filtered) df already restricted to primary_only()."""
    start_ts = _to_utc_timestamp(start)
    end_ts = _to_utc_timestamp(end)
    period_length = end_ts - start_ts
    prior_start = start_ts - period_length

    current = usage_by_family(filter_by_timeframe(df, start_ts, end_ts), scope_key).set_index(FAMILY_COLUMN)
    prior = usage_by_family(filter_by_timeframe(df, prior_start, start_ts), scope_key).set_index(FAMILY_COLUMN)

    families = sorted(set(current.index) | set(prior.index))
    rows = []
    for family in families:
        current_count = int(current["submissions"].get(family, 0))
        prior_count = int(prior["submissions"].get(family, 0))
        pct_change = None
        if prior_count > 0:
            pct_change = (current_count - prior_count) / prior_count * 100
        elif current_count > 0:
            pct_change = float("inf")
        rows.append({"family": family, "current_count": current_count, "prior_count": prior_count, "pct_change": pct_change})
    if not rows:
        return pd.DataFrame(columns=["family", "current_count", "prior_count", "pct_change"])
    return pd.DataFrame(rows).sort_values("current_count", ascending=False).reset_index(drop=True)


def survey_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Every individual survey/layer as its own row — the flat, org-wide view
    that isn't rolled up by scope of work. Pass the full df (QA surveys
    appear as their own rows here rather than being excluded, since each
    survey is independent — there's no double-counting risk the way there is
    when summing multiple surveys into one scope total)."""
    columns = ["survey_key", "survey_title", "scope_label", "submissions", "unique_contributors", "avg_attachments", "last_submitted"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    grouped = df.groupby(["survey_key", "survey_title", "scope_label"], as_index=False).agg(
        submissions=(DATE_COLUMN, "size"),
        unique_contributors=(CREATOR_COLUMN, "nunique"),
        avg_attachments=(ATTACHMENT_COLUMN, "mean"),
        last_submitted=(DATE_COLUMN, "max"),
    )
    return grouped.sort_values("submissions", ascending=False).reset_index(drop=True)


def completion_summary(site_df: pd.DataFrame) -> dict:
    if site_df.empty:
        return {"total_sites": 0, "complete_sites": 0, "partial_sites": 0, "pct_complete": None}
    total = len(site_df)
    complete = int(site_df["is_complete"].sum())
    return {
        "total_sites": total,
        "complete_sites": complete,
        "partial_sites": total - complete,
        "pct_complete": complete / total * 100 if total else None,
    }
