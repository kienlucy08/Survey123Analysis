"""ArcGIS Online / Enterprise token auth via the REST API (generateToken)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests


@dataclass
class Token:
    value: str
    expires_at_ms: int

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return time.time() * 1000 >= self.expires_at_ms - skew_seconds * 1000


def generate_token(portal_url: str, username: str, password: str, expiration_minutes: int = 60) -> Token:
    """Exchange username/password for a short-lived ArcGIS token.

    `portal_url` is the org root, e.g. https://yourorg.maps.arcgis.com — the
    sharing/rest API lives underneath it.
    """
    resp = requests.post(
        f"{portal_url.rstrip('/')}/sharing/rest/generateToken",
        data={
            "username": username,
            "password": password,
            "referer": portal_url,
            "expiration": expiration_minutes,
            "f": "json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        raise RuntimeError(f"ArcGIS auth failed: {payload['error']}")
    return Token(value=payload["token"], expires_at_ms=payload["expires"])


class TokenManager:
    """Lazily creates and refreshes an ArcGIS token as it's asked for."""

    def __init__(self, portal_url: str, username: str, password: str):
        self.portal_url = portal_url
        self.username = username
        self.password = password
        self._token: Token | None = None

    def get(self) -> str:
        if self._token is None or self._token.is_expired():
            self._token = generate_token(self.portal_url, self.username, self.password)
        return self._token.value
