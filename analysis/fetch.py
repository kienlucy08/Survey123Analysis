"""Query ArcGIS feature layers (Survey123 results) over REST and return DataFrames."""

from __future__ import annotations

import pandas as pd
import requests

PAGE_SIZE = 2000


def query_layer(layer_url: str, token: str, where: str = "1=1", out_fields: str = "*") -> pd.DataFrame:
    """Pull every record from a feature layer's /query endpoint, paginating as needed."""
    all_rows: list[dict] = []
    offset = 0

    while True:
        resp = requests.get(
            f"{layer_url.rstrip('/')}/query",
            params={
                "where": where,
                "outFields": out_fields,
                "f": "json",
                "token": token,
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "orderByFields": "OBJECTID",
            },
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"ArcGIS query failed for {layer_url}: {payload['error']}")

        features = payload.get("features", [])
        all_rows.extend(f["attributes"] for f in features)

        if not payload.get("exceededTransferLimit") or not features:
            break
        offset += len(features)

    df = pd.DataFrame(all_rows)

    # The object-id field's *casing* varies by layer (e.g. "objectid" on
    # PostgreSQL-backed hosted layers vs "OBJECTID" elsewhere) even though
    # ArcGIS's query engine accepts either case in where/orderBy clauses.
    # Normalize it so every downstream consumer can rely on "OBJECTID".
    oid_col = next((c for c in df.columns if c.lower() == "objectid"), None)
    if oid_col and oid_col != "OBJECTID":
        df = df.rename(columns={oid_col: "OBJECTID"})

    for date_col in ("CreationDate", "EditDate"):
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], unit="ms", utc=True, errors="coerce")
    return df


def get_layer_metadata(layer_url: str, token: str) -> dict:
    resp = requests.get(f"{layer_url.rstrip('/')}", params={"f": "json", "token": token}, timeout=30)
    resp.raise_for_status()
    return resp.json()
