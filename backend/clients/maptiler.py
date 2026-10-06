from __future__ import annotations
from typing import Any, Dict
from urllib.parse import quote_plus

import httpx
from backend.config import settings

# The query is part of the path, not a query parameter, so it is percent-encoded
# before being appended.
GEOCODING_URL = "https://api.maptiler.com/geocoding/{query}.json"


class MapTilerClient:
    def __init__(self, timeout: float = 30.0):
        self.key = settings.next_public_maptiler_key
        self.timeout = timeout

    def geocode(self, q: str, limit: int = 5) -> Dict[str, Any]:
        if not self.key:
            raise RuntimeError("MapTiler key not configured")
        params = {"key": self.key, "limit": str(limit)}
        url = GEOCODING_URL.format(query=quote_plus(q))
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            return r.json()