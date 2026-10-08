from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

import httpx

from backend import config

# Each layer is fetched as its own Overpass query so one slow or failing layer
# degrades alone instead of taking the others with it. The `out` mode sits
# outside the union block, which is where Overpass QL requires it: `out geom`
# inlines way geometry so no follow-up node lookup is needed, while
# settlements and hospitals only need a centre point because they render as
# labels/markers.
_LAYERS = {
    "roads": (
        'way["highway"]({bbox});',
        "geom",
    ),
    "buildings": (
        'way["building"]({bbox});',
        "geom",
    ),
    "settlements": (
        'node["place"]({bbox});\nway["place"]({bbox});\nrelation["place"]({bbox});',
        "center",
    ),
    "hospitals": (
        'node["amenity"="hospital"]({bbox});\n'
        'way["amenity"="hospital"]({bbox});\n'
        'relation["amenity"="hospital"]({bbox});',
        "center",
    ),
}

OSM_LAYERS = tuple(_LAYERS)

_LAYER_PROPERTIES = {
    "roads": ("highway", "name", "ref", "surface", "bridge", "tunnel"),
    "buildings": ("building", "name", "building:levels"),
    "settlements": ("name", "place", "population", "name:en"),
    "hospitals": ("name", "amenity", "emergency", "healthcare", "operator"),
}


class OsmFetchError(RuntimeError):
    pass


# Public Overpass instances 504 and time out routinely under load, so a failed
# instance rotates to the next mirror instead of failing the layer. The whole
# chain shares the single osm_timeout_s budget, which keeps the pipeline's
# join deadline and this loop in agreement.
_MIRROR_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

MAX_ROUNDS = 3


def _candidate_urls() -> List[str]:
    urls = [config.settings.osm_overpass_url]
    for url in _MIRROR_URLS:
        if url not in urls:
            urls.append(url)
    return urls


def bbox_query_param(bbox: Sequence[float]) -> str:
    """Overpass bbox order is (south, west, north, east), not GeoJSON order."""
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    return f"{miny},{minx},{maxy},{maxx}"


def build_query(bbox: Sequence[float], layer: str) -> str:
    if layer not in _LAYERS:
        raise ValueError(f"unknown OSM layer {layer!r}, expected one of {OSM_LAYERS}")
    statements, out_mode = _LAYERS[layer]
    body = statements.format(bbox=bbox_query_param(bbox))
    return f"[out:json][timeout:60];\n(\n{body}\n);\nout {out_mode};"


def _keep_properties(tags: Dict[str, Any], layer: str) -> Dict[str, Any]:
    return {k: tags[k] for k in _LAYER_PROPERTIES[layer] if k in tags}


def _line_coords(geometry: Optional[List[Dict[str, Any]]]) -> Optional[List[List[float]]]:
    if not geometry or len(geometry) < 2:
        return None
    coords = [[float(p["lon"]), float(p["lat"])] for p in geometry if "lon" in p and "lat" in p]
    return coords if len(coords) >= 2 else None


def elements_to_geojson(elements: Sequence[Dict[str, Any]], layer: str) -> Dict[str, Any]:
    """Convert Overpass elements to a GeoJSON FeatureCollection.

    Roads become LineStrings, closed building ways become Polygons (an unclosed
    ``building`` way has no interior to fill and is skipped), and settlements
    and hospitals collapse to their centre point (label/marker rendering).
    """
    if layer not in _LAYERS:
        raise ValueError(f"unknown OSM layer {layer!r}, expected one of {OSM_LAYERS}")

    features: List[Dict[str, Any]] = []
    for el in elements:
        tags = el.get("tags") or {}
        geom: Optional[Dict[str, Any]] = None

        if layer in ("settlements", "hospitals"):
            if el.get("type") == "node" and "lat" in el and "lon" in el:
                geom = {
                    "type": "Point",
                    "coordinates": [float(el["lon"]), float(el["lat"])],
                }
            else:
                centre = el.get("center") or {}
                if "lat" in centre and "lon" in centre:
                    geom = {
                        "type": "Point",
                        "coordinates": [float(centre["lon"]), float(centre["lat"])],
                    }
        elif el.get("type") == "way":
            coords = _line_coords(el.get("geometry"))
            if coords is None:
                continue
            if layer == "buildings":
                if coords[0] != coords[-1] or len(coords) < 4:
                    continue
                geom = {"type": "Polygon", "coordinates": [coords]}
            else:
                geom = {"type": "LineString", "coordinates": coords}

        if geom is None:
            continue
        features.append(
            {
                "type": "Feature",
                "id": f"{el.get('type', '?')}/{el.get('id', 0)}",
                "properties": _keep_properties(tags, layer),
                "geometry": geom,
            }
        )

    return {"type": "FeatureCollection", "features": features}


def _post_overpass(url: str, query: str, timeout_s: float) -> List[Dict[str, Any]]:
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            r = client.post(
                url,
                data={"data": query},
                headers={"User-Agent": "FloodScope/1.0 (flood impact analysis)"},
            )
    except httpx.HTTPError as exc:
        raise OsmFetchError(f"request failed: {exc}") from exc

    if r.status_code != 200:
        raise OsmFetchError(f"HTTP {r.status_code}")
    try:
        payload = r.json()
    except ValueError as exc:
        raise OsmFetchError("non-JSON body") from exc
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise OsmFetchError("response has no elements list")
    return elements


def fetch_layer(bbox: Sequence[float], layer: str) -> Dict[str, Any]:
    """Query Overpass for one layer and return it as GeoJSON.

    The public mirrors 429 and hang routinely, so the chain runs up to
    MAX_ROUNDS passes over every candidate, each instance getting an even
    share of the osm_timeout_s budget per round. Raises OsmFetchError only
    once the budget is exhausted; callers decide whether that is fatal (the
    pipeline treats it as a per-layer degradation).
    """
    query = build_query(bbox, layer)
    deadline = time.monotonic() + config.settings.osm_timeout_s
    candidates = _candidate_urls()
    attempts: List[str] = []

    for round_no in range(1, MAX_ROUNDS + 1):
        if deadline - time.monotonic() < 5.0:
            break
        share = (deadline - time.monotonic()) / len(candidates)
        for url in candidates:
            remaining = deadline - time.monotonic()
            if remaining < 5.0:
                break
            budget = min(share, remaining)
            try:
                elements = _post_overpass(url, query, budget)
            except OsmFetchError as exc:
                attempts.append(f"{url} (round {round_no}: {exc})")
                print(f"[osm] {url} failed (round {round_no}): {exc}")
                continue

            fc = elements_to_geojson(elements, layer)
            fc["properties"] = {
                "layer": layer,
                "bbox": [float(v) for v in bbox],
                "source": "OpenStreetMap contributors",
                "copyright": "https://www.openstreetmap.org/copyright",
                "featureCount": len(fc["features"]),
            }
            print(f"[osm] {layer:<12} -> {len(fc['features']):,} features ({url})")
            return fc

        if round_no < MAX_ROUNDS and deadline - time.monotonic() >= 5.0:
            time.sleep(2.0)

    raise OsmFetchError(
        "all Overpass instances failed: " + ("; ".join(attempts) or "no time left")
    )
