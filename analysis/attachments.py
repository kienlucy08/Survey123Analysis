"""Photo/attachment counts per submission, via the ArcGIS attachments REST API.

Survey123 stores photos as feature attachments, not a field on the record —
getting a count per submission means a separate call per layer.
"""

from __future__ import annotations

import requests

from analysis.fetch import get_layer_metadata

BATCH_SIZE = 500


def layer_has_attachments(layer_url: str, token: str) -> bool:
    return bool(get_layer_metadata(layer_url, token).get("hasAttachments"))


def fetch_attachment_counts(layer_url: str, token: str, object_ids: list[int]) -> dict[int, int]:
    """Returns {object_id: attachment_count} for every id in object_ids.
    IDs with no attachments are included with a count of 0."""
    counts = {oid: 0 for oid in object_ids}
    if not object_ids:
        return counts

    if not layer_has_attachments(layer_url, token):
        return counts

    for i in range(0, len(object_ids), BATCH_SIZE):
        batch = object_ids[i : i + BATCH_SIZE]
        resp = requests.post(
            f"{layer_url.rstrip('/')}/queryAttachments",
            data={"objectIds": ",".join(str(o) for o in batch), "f": "json", "token": token},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"queryAttachments failed for {layer_url}: {payload['error']}")
        for group in payload.get("attachmentGroups", []):
            counts[group["parentObjectId"]] = len(group.get("attachmentInfos", []))

    return counts
