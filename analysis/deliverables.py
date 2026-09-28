"""Monthly deliverable/report counts exported from the internal database
(not ArcGIS) — the "true" number of finished reports sent to each
organization, as opposed to Survey123's raw submission counts.

Expected CSV columns (per row = one template used by one org in one month):
  organization_name, organizationId, template, templateId, engine, report_count

organization_name matches Survey123's confirm_org values directly (verified
2026-09-25: EverestInfrastructure, K2Towers, MurphyTower, STI, Tarpon,
TestOrg, TowerCo, VerticalBridge all appear in both) — no name-matching
needed when comparing the two data sources.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

DELIVERABLES_DIR = Path(__file__).resolve().parent.parent / "data" / "deliverables"

REQUIRED_COLUMNS = ["organization_name", "template", "report_count"]
ALL_COLUMNS = ["organization_name", "organizationId", "template", "templateId", "engine", "report_count"]

# Longer tokens first so "sept" matches before "sep" would ambiguously — not
# that it matters here since both resolve to month 9, but same principle
# would bite a hypothetical "jun"/"june" collision with different mappings.
_MONTH_TOKENS = [
    ("sept", 9), ("january", 1), ("february", 2), ("march", 3), ("april", 4),
    ("june", 6), ("july", 7), ("august", 8), ("october", 10), ("november", 11), ("december", 12),
    ("jan", 1), ("feb", 2), ("mar", 3), ("apr", 4), ("may", 5), ("jun", 6),
    ("jul", 7), ("aug", 8), ("sep", 9), ("oct", 10), ("nov", 11), ("dec", 12),
]


def guess_period_from_filename(filename: str) -> date:
    """Best-effort month guess from a name like 'PerOrgSept.csv' or
    'deliverables_2025-08.csv' — always shown to the user to confirm/adjust
    before saving, never trusted silently. Falls back to the current month
    if nothing matches."""
    name = filename.lower()

    numeric_match = re.search(r"(20\d{2})[-_]?(0[1-9]|1[0-2])(?!\d)", name)
    if numeric_match:
        return date(int(numeric_match.group(1)), int(numeric_match.group(2)), 1)

    year_match = re.search(r"(20\d{2})", name)
    year = int(year_match.group(1)) if year_match else datetime.now().year
    for token, month in _MONTH_TOKENS:
        if token in name:
            return date(year, month, 1)
    return datetime.now().date().replace(day=1)


def period_bounds(period: str) -> tuple[datetime, datetime]:
    """'YYYY-MM' -> (first instant, last instant) of that calendar month, UTC —
    matches how the monthly report archive defines a month's boundaries."""
    p = pd.Period(period, freq="M")
    start = p.to_timestamp(how="start").tz_localize("UTC").to_pydatetime()
    end = p.to_timestamp(how="end").tz_localize("UTC").to_pydatetime()
    return start, end


def validate_columns(df: pd.DataFrame) -> list[str]:
    """Returns missing required columns, if any — empty list means OK to save."""
    return [c for c in REQUIRED_COLUMNS if c not in df.columns]


def save_deliverables(df: pd.DataFrame, period: str) -> Path:
    """period is 'YYYY-MM'. Overwrites that month's file if it already exists —
    re-uploading the same month is how you correct a bad import."""
    DELIVERABLES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DELIVERABLES_DIR / f"{period}.csv"
    df.to_csv(out_path, index=False)
    return out_path


def list_periods() -> list[str]:
    if not DELIVERABLES_DIR.exists():
        return []
    return sorted(p.stem for p in DELIVERABLES_DIR.glob("*.csv"))


def load_deliverables(period: str | None = None) -> pd.DataFrame:
    """One month's raw rows, or every uploaded month concatenated with a
    'period' column, if period is None."""
    columns = ALL_COLUMNS + ["period"]
    if period is not None:
        path = DELIVERABLES_DIR / f"{period}.csv"
        if not path.exists():
            return pd.DataFrame(columns=ALL_COLUMNS)
        return pd.read_csv(path)

    frames = []
    for p in list_periods():
        df = pd.read_csv(DELIVERABLES_DIR / f"{p}.csv")
        df["period"] = p
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)


def deliverables_by_org(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["organization_name", "report_count"])
    return (
        df.groupby("organization_name", as_index=False)["report_count"]
        .sum()
        .sort_values("report_count", ascending=False)
        .reset_index(drop=True)
    )


def deliverables_by_template(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["template", "engine", "report_count", "org_count"])
    return (
        df.groupby(["template", "engine"], as_index=False, dropna=False)
        .agg(report_count=("report_count", "sum"), org_count=("organization_name", "nunique"))
        .sort_values("report_count", ascending=False)
        .reset_index(drop=True)
    )


def deliverables_by_engine(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["engine", "report_count"])
    display = df.copy()
    display["engine"] = display["engine"].fillna("unknown")
    return (
        display.groupby("engine", as_index=False)["report_count"]
        .sum()
        .sort_values("report_count", ascending=False)
        .reset_index(drop=True)
    )


def compare_to_survey123(deliverables_df: pd.DataFrame, survey123_org_usage: pd.DataFrame) -> pd.DataFrame:
    """Side-by-side per org: DB deliverables vs. Survey123 submissions in the
    same period. Not a strict 1:1 (one deliverable can bundle several
    submissions), but flags where the two diverge a lot."""
    db = deliverables_by_org(deliverables_df).rename(columns={"organization_name": "organization"})
    s123 = survey123_org_usage[["organization", "submissions"]]
    merged = db.merge(s123, on="organization", how="outer").fillna(0)
    merged["report_count"] = merged["report_count"].astype(int)
    merged["submissions"] = merged["submissions"].astype(int)
    return merged.sort_values("report_count", ascending=False).reset_index(drop=True)
