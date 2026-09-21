"""Usage & growth metrics over a unified, scope-tagged submissions table."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from analysis.scope import ATTACHMENT_COLUMN, CREATOR_COLUMN, DATE_COLUMN


def _to_utc_timestamp(value: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


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
