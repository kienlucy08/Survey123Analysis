"""Synthetic Survey123 data, shaped like what fetch.query_layer() returns, so the
dashboard can be built and demoed before real ArcGIS credentials are wired up.

Swap this out for pipeline.fetch_live() once config/settings.yaml and
config/scopes.yaml point at your real org.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CREATORS = [
    "j.rivera", "a.chen", "m.okafor", "s.patel", "d.nguyen",
    "l.garcia", "t.walker", "k.johnson",
]

# key -> (title, purpose value, records/day at the start, growth per day)
SURVEY_PROFILES = {
    "compound_inspection": ("Compound Inspection", "Compound Inspection", 2.0, 0.015),
    "structure_flight_inspection": ("Structure Flight Inspection", "Structure Flight Inspection", 1.4, 0.012),
    "guy_facilities_inspection": ("Guy Facilities Inspection v2", "Guy Facilities Inspection", 1.0, 0.010),
    "plumb_and_twist": ("Plumb & Twist", "Plumb & Twist", 1.2, 0.008),
    "close_out_report": ("Close Out Report", "Close Out", 0.8, 0.020),
}

# Shared site pool for the Inspection surveys, so the demo shows realistic
# partial vs. complete site coverage without live ArcGIS data.
SITE_POOL = [f"SITE-{i:03d}" for i in range(1, 21)]
INSPECTION_SURVEY_KEYS = {"compound_inspection", "structure_flight_inspection", "guy_facilities_inspection", "plumb_and_twist"}

# Weighted so one org dominates, like the real org's data.
ORG_POOL = ["K2Towers", "MurphyTower", "VerticalBridge", "EverestInfrastructure", "TowerCo"]
ORG_WEIGHTS = [0.4, 0.25, 0.15, 0.12, 0.08]


def _simulate_survey(rng: np.random.Generator, n_days: int, base_rate: float, growth_per_day: float, with_site: bool) -> pd.DataFrame:
    rows = []
    object_id = 1
    for day_offset in range(n_days, 0, -1):
        day = pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=day_offset)
        # weekday seasonality: quieter on weekends
        weekday_factor = 0.35 if day.weekday() >= 5 else 1.0
        expected = max(0.0, (base_rate + growth_per_day * (n_days - day_offset)) * weekday_factor)
        count = rng.poisson(expected)
        for _ in range(count):
            creator = rng.choice(CREATORS)
            minute_offset = rng.integers(0, 24 * 60)
            row = {
                "OBJECTID": object_id,
                "CreationDate": day + pd.Timedelta(minutes=int(minute_offset)),
                "Creator": creator,
                "email": f"{creator.replace('.', '')}@example.com",
                "attachment_count": max(0, int(rng.normal(loc=10, scale=4))),
                "confirm_org": rng.choice(ORG_POOL, p=ORG_WEIGHTS),
            }
            if with_site:
                site = rng.choice(SITE_POOL)
                row["customer_site_id"] = site
                row["customer_site_name"] = f"{site} Tower"
            rows.append(row)
            object_id += 1
    return pd.DataFrame(rows)


def generate_sample_dataset(n_days: int = 180, seed: int = 42) -> dict[str, pd.DataFrame]:
    """Returns {survey_key: raw_dataframe} for every survey in SURVEY_PROFILES,
    matching the shape analysis.fetch.query_layer() would return."""
    rng = np.random.default_rng(seed)
    dataset = {}
    for key, (title, purpose_value, base_rate, growth_per_day) in SURVEY_PROFILES.items():
        df = _simulate_survey(rng, n_days, base_rate, growth_per_day, with_site=key in INSPECTION_SURVEY_KEYS)
        df["purpose"] = purpose_value
        dataset[key] = df
    return dataset


def sample_scopes_config() -> list[dict]:
    """A scopes.yaml-equivalent config matching generate_sample_dataset(), so the
    dashboard can run end-to-end with zero setup."""
    return [
        {
            "key": "inspection",
            "label": "Inspection",
            "track_completion": True,
            "required_families": ["compound", "structure"],  # guy/pnt below are optional
            "surveys": [
                {"key": "compound_inspection", "title": "Compound Inspection", "purpose_field": "purpose", "purpose_values": [], "family": "compound", "completion_role": "required"},
                {"key": "structure_flight_inspection", "title": "Structure Flight Inspection", "purpose_field": "purpose", "purpose_values": [], "family": "structure", "completion_role": "required"},
                {"key": "guy_facilities_inspection", "title": "Guy Facilities Inspection v2", "purpose_field": "purpose", "purpose_values": [], "family": "guy", "completion_role": "optional"},
                {"key": "plumb_and_twist", "title": "Plumb & Twist", "purpose_field": "purpose", "purpose_values": [], "family": "pnt", "completion_role": "optional"},
            ],
        },
        {
            "key": "close_out",
            "label": "Close Out",
            "surveys": [
                {"key": "close_out_report", "title": "Close Out Report", "purpose_field": "purpose", "purpose_values": []},
            ],
        },
    ]
