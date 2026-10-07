from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import httpx

from backend.config import settings

# Each S1 product is a full-swath COG of roughly 700 MB; reading it over HTTP
# needs a token in a GDAL header, so the client caches one token per instance.
_EPS = 1e-6


class CDSEError(RuntimeError):
    pass


def iso_utc(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def size_for_bbox(
    bbox: Sequence[float],
    resolution_m: float = 10.0,
    max_side: int = 2500,
    min_side: int = 64,
) -> Tuple[int, int]:
    """Grid size for the bbox that keeps pixels square at its own latitude."""
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    lat_mid = (miny + maxy) / 2.0
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * max(math.cos(math.radians(lat_mid)), 1e-6)

    width = int(round(abs(maxx - minx) * m_per_deg_lon / resolution_m))
    height = int(round(abs(maxy - miny) * m_per_deg_lat / resolution_m))
    width = max(min_side, min(max_side, width))
    height = max(min_side, min(max_side, height))
    return width, height


def _normalise_track(feature: Dict[str, Any]) -> Optional[str]:
    """A relative orbit identifies the acquisition geometry."""
    props = feature.get("properties") or {}
    for key in ("sat:relative_orbit", "relativeOrbit", "relative_orbit",
                "sat:orbit_number", "orbitNumber"):
        value = props.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _datetime_of(feature: Dict[str, Any]) -> str:
    props = feature.get("properties") or {}
    return str(props.get("datetime") or props.get("start_datetime") or "")


def _mode_of(feature: Dict[str, Any]) -> Optional[str]:
    """Acquisition mode (IW, EW, ...) — one half of pair compatibility."""
    props = feature.get("properties") or {}
    for key in ("sar:instrument_mode", "sat:instrument_mode", "instrument_mode"):
        value = props.get(key)
        if value not in (None, ""):
            return str(value).upper()
    return None


def _polarizations_of(feature: Dict[str, Any]) -> Optional[Tuple[str, ...]]:
    """Sorted polarisation set, e.g. ("VH", "VV"); None when not recorded."""
    props = feature.get("properties") or {}
    for key in ("sar:polarizations", "polarisations", "polarisation"):
        value = props.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, str):
            value = [v for v in value.replace(",", " ").split() if v]
        if not isinstance(value, (list, tuple, set)):
            value = [value]
        return tuple(sorted({str(v).upper() for v in value}))
    return None


class CDSEClient:
    """Sentinel-1 access via the CDSE STAC API and HTTP range reads.

    Sentinel Hub's Process API rejects these credentials, and CDSE's openEO
    backend rejects the resulting token, but STAC search plus cloud-optimised
    GeoTIFF range reads serve real Sentinel-1 GRDH VV/VH imagery, so this client
    targets those.
    """

    def __init__(
        self,
        token_url: Optional[str] = None,
        stac_url: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.token_url = token_url or settings.cdse_oauth_token_url
        self.stac_url = (stac_url or settings.cdse_stac_url).rstrip("/")
        self.timeout = timeout
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    @property
    def auth_host(self) -> str:
        return urlparse(self.token_url).netloc

    def credentials_configured(self) -> bool:
        return bool(settings.sentinel_hub_id and settings.sentinel_hub_api_key)

    def _get_token(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and now < self._expires_at - 60:
            return self._token

        if not self.credentials_configured():
            raise CDSEError("SENTINEL_HUB_ID / SENTINEL_HUB_API_KEY are not set")

        form = {
            "grant_type": "client_credentials",
            "client_id": settings.sentinel_hub_id,
            "client_secret": settings.sentinel_hub_api_key,
        }
        with httpx.Client(timeout=60) as client:
            r = client.post(self.token_url, data=form)
        if r.status_code != 200:
            raise CDSEError(
                f"token request to {self.token_url} failed ({r.status_code}): {r.text[:400]}"
            )

        body = r.json()
        token = body.get("access_token")
        if not token:
            raise CDSEError(f"no access_token in response: {body}")

        self._token = token
        self._expires_at = now + float(body.get("expires_in", 1800))
        print(
            f"[sh] auth host={self.auth_host} token ok "
            f"(expires {int(body.get('expires_in', 1800))}s)"
        )
        return token

    def verify_credentials(self) -> str:
        return self._get_token(force=True)

    @property
    def auth_header(self) -> str:
        return f"Bearer {self._get_token()}"

    def search(
        self,
        bbox: Sequence[float],
        time_from,
        time_to,
        collection: str = "sentinel-1-grd",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return STAC features intersecting the bbox within the time window."""
        if limit is None:
            limit = settings.cdse_search_limit
        minx, miny, maxx, maxy = (float(v) for v in bbox)
        params = {
            "collections": collection,
            "bbox": f"{minx},{miny},{maxx},{maxy}",
            "datetime": f"{iso_utc(time_from)}/{iso_utc(time_to)}",
            "limit": str(limit),
        }
        url = f"{self.stac_url}/search"
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(url, params=params, headers={"Authorization": self.auth_header})
        if r.status_code != 200:
            raise CDSEError(f"STAC search failed ({r.status_code}): {r.text[:400]}")

        features = r.json().get("features") or []
        features = [f for f in features if _datetime_of(f)]
        features.sort(key=_datetime_of)

        bbox_txt = ",".join(f"{v:.4f}" for v in (minx, miny, maxx, maxy))
        window = f"{iso_utc(time_from)}/{iso_utc(time_to)}"
        print(
            f"[sh] search  bbox={bbox_txt} window={window} -> "
            f"{len(features)} feature(s)"
        )
        for f in features:
            print(
                f"[sh]          {_datetime_of(f)}  track={_normalise_track(f)}  "
                f"{f.get('id', '')[:44]}"
            )
        return features

    def asset_href(self, feature: Dict[str, Any], band: str = "vv") -> str:
        """Prefer the HTTPS alternate so the COG can be read with /vsicurl."""
        asset = (feature.get("assets") or {}).get(band)
        if not asset:
            raise CDSEError(f"feature {feature.get('id')} has no '{band}' asset")

        alternate = asset.get("alternate") or {}
        https = alternate.get("https") or {}
        href = https.get("href") or asset.get("href")
        if not href:
            raise CDSEError(f"'{band}' asset of {feature.get('id')} has no href")
        if href.startswith("s3://"):
            raise CDSEError(
                f"'{band}' asset only exposes an s3:// href ({href}); "
                "HTTP range reads require the https alternate"
            )
        return href

    def describe(self, feature: Dict[str, Any]) -> Dict[str, Any]:
        props = feature.get("properties") or {}
        return {
            "productId": feature.get("id"),
            "beginPosition": _datetime_of(feature),
            "endPosition": _datetime_of(feature),
            "platform": props.get("platform") or props.get("constellation"),
            "productType": "GRD",
            "relativeOrbitNumber": _normalise_track(feature),
            "orbitDirection": props.get("sat:orbit_state"),
            "polarisation": props.get("sar:polarizations"),
            "footprint": {"bbox": feature.get("bbox")},
            "sameTrackProxy": True,
        }