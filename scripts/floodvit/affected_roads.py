"""Find OSM roads and bridges that intersect the FloodViT flood polygons.

Reads data/<uuid>/flood_polygons.geojson (written by mask_to_polygons.py) and
data/<uuid>/osm_roads.geojson (written by the pipeline's Overpass fetch), then
keeps every road LineString that geometrically intersects at least one flood
polygon. Both inputs are EPSG:4326, so no reprojection is needed — shapely
operates directly on lon/lat and pyproj's geodesic gives metric lengths.

Bridges are not a separate OSM layer: they are highway ways carrying a
`bridge` tag, so affected_bridges.geojson is the bridge-tagged subset of the
affected roads (affected_roads.geojson keeps ALL affected highways, bridges
included).

Outputs (in the same analysis dir):
  affected_roads.geojson    FeatureCollection, original OSM feature id +
                            tags plus status / length metrics.
  affected_bridges.geojson  same schema, bridge-tagged subset.

Classification is deliberately weak wording: `status = "potentially affected"`
means only that the mapped centerline overlaps the mapped flood area — it is
NOT a claim that the road is damaged, impassable, or destroyed.

Run from repo root:  python scripts/floodvit/affected_roads.py
Optional arg:        python scripts/floodvit/affected_roads.py <analysis_dir>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from pyproj import Geod
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.strtree import STRtree

FLOOD_POLYGONS_FILE = "flood_polygons.geojson"
OSM_ROADS_FILE = "osm_roads.geojson"
ROADS_OUT_FILE = "affected_roads.geojson"
BRIDGES_OUT_FILE = "affected_bridges.geojson"

# Geodesic calculator for line lengths on the WGS84 ellipsoid; planar degree
# lengths would understate east–west segments by cos(latitude).
GEOD = Geod(ellps="WGS84")

# A road counts as a bridge when OSM tags it with any bridge value except
# "no" (keeps yes/abandoned/aqueduct/viaduct... with their original tag).
def is_bridge(properties: dict) -> bool:
    value = properties.get("bridge")
    return value is not None and str(value).lower() != "no"


def resolve_analysis_dir(arg: str | None) -> Path:
    """Analysis dir holding both inputs; default to the first match under data/."""
    if arg is None:
        data_root = REPO_ROOT / "data"
        for d in sorted(p for p in data_root.iterdir() if p.is_dir()):
            if (d / FLOOD_POLYGONS_FILE).is_file() and (d / OSM_ROADS_FILE).is_file():
                return d
        raise SystemExit(
            f"no data/<uuid>/ with both {FLOOD_POLYGONS_FILE} and {OSM_ROADS_FILE}"
        )
    p = Path(arg)
    if p.is_file():
        p = p.parent
    if not p.is_dir():
        raise SystemExit(f"directory not found: {p}")
    for name in (FLOOD_POLYGONS_FILE, OSM_ROADS_FILE):
        if not (p / name).is_file():
            raise SystemExit(f"missing {name} in {p}")
    return p


def load_geojson(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def line_parts(geom: BaseGeometry):
    """All LineString/LinearRing pieces of an intersection result.

    line ∩ polygon can be a GeometryCollection (lines plus touch points) or a
    MultiLineString; points carry no length and are skipped.
    """
    if geom.is_empty:
        return
    if geom.geom_type in ("LineString", "LinearRing"):
        yield geom
    elif geom.geom_type in ("MultiLineString", "GeometryCollection", "MultiPoint"):
        for part in geom.geoms:
            yield from line_parts(part)
    # Polygon/MultiPolygon cannot come out of a line intersection


def geodesic_length_m(geom: BaseGeometry) -> float:
    return sum(
        GEOD.line_length(*zip(*part.coords)) for part in line_parts(geom)
    )


def find_affected(
    road_features: list[dict], flood_geoms: list[BaseGeometry]
) -> list[dict]:
    """Road features that intersect a flood polygon, with length metrics.

    Uses an STRtree over the flood polygons for candidate lookup; `intersects`
    counts boundary-touching roads too (a road clipping the flood edge is
    still potentially affected). The hit polygons are unioned before the
    length intersection so overlapping polygons cannot double-count length.
    """
    if not flood_geoms:
        return []
    tree = STRtree(flood_geoms)
    out: list[dict] = []
    for feature in road_features:
        geom = shape(feature.get("geometry"))
        if geom.is_empty or geom.geom_type != "LineString":
            continue
        hits = [flood_geoms[i] for i in tree.query(geom, predicate="intersects")]
        if not hits:
            continue
        total_m = geodesic_length_m(geom)
        affected_m = geodesic_length_m(geom.intersection(unary_union(hits)))
        fraction = affected_m / total_m if total_m > 0 else 0.0
        props = dict(feature.get("properties") or {})
        props.update(
            status="potentially affected",
            totalLengthM=round(total_m, 1),
            affectedLengthM=round(affected_m, 1),
            affectedFraction=round(fraction, 4),
        )
        out.append(
            {
                "type": "Feature",
                "id": feature.get("id"),
                "properties": props,
                "geometry": feature["geometry"],
            }
        )
    return out


def collection(features: list[dict], layer: str, osm_props: dict) -> dict:
    return {
        "type": "FeatureCollection",
        "properties": {
            "layer": layer,
            "bbox": osm_props.get("bbox"),
            "source": osm_props.get("source"),
            "copyright": osm_props.get("copyright"),
            "status": "potentially affected",
            "method": (
                "geometric intersection of OSM centerlines with FloodViT "
                "flood polygons; overlap only, not a damage assessment"
            ),
            "featureCount": len(features),
        },
        "features": features,
    }


def total_km(features: list[dict]) -> tuple[float, float]:
    affected = sum(f["properties"]["affectedLengthM"] for f in features)
    total = sum(f["properties"]["totalLengthM"] for f in features)
    return affected / 1000.0, total / 1000.0


def main() -> int:
    t0 = time.perf_counter()
    analysis_dir = resolve_analysis_dir(sys.argv[1] if len(sys.argv) > 1 else None)

    flood_fc = load_geojson(analysis_dir / FLOOD_POLYGONS_FILE)
    osm_fc = load_geojson(analysis_dir / OSM_ROADS_FILE)
    flood_geoms = [
        shape(f["geometry"]) for f in flood_fc.get("features", []) if f.get("geometry")
    ]
    road_features = [
        f
        for f in osm_fc.get("features", [])
        if (f.get("geometry") or {}).get("type") == "LineString"
    ]

    affected = find_affected(road_features, flood_geoms)
    affected_bridges = [f for f in affected if is_bridge(f["properties"])]

    osm_props = osm_fc.get("properties") or {}
    roads_fc = collection(affected, "affected_roads", osm_props)
    bridges_fc = collection(affected_bridges, "affected_bridges", osm_props)

    roads_path = analysis_dir / ROADS_OUT_FILE
    bridges_path = analysis_dir / BRIDGES_OUT_FILE
    roads_path.write_text(json.dumps(roads_fc, separators=(",", ":")), encoding="utf-8")
    bridges_path.write_text(
        json.dumps(bridges_fc, separators=(",", ":")), encoding="utf-8"
    )

    scanned_bridges = sum(1 for f in road_features if is_bridge(f["properties"] or {}))
    roads_aff_km, roads_km = total_km(affected)
    bridges_aff_km, bridges_km = total_km(affected_bridges)

    print(f"analysis dir        : {analysis_dir}")
    print(f"flood polygons      : {len(flood_geoms)}")
    print(f"roads scanned       : {len(road_features)} ({scanned_bridges} bridge-tagged)")
    print(f"roads affected      : {len(affected)} "
          f"({roads_aff_km:.3f} of {roads_km:.3f} km)")
    print(f"bridges affected    : {len(affected_bridges)} "
          f"({bridges_aff_km:.3f} of {bridges_km:.3f} km)")
    print(f"geojson written     : {roads_path}")
    print(f"                      {bridges_path}")
    print(f"elapsed             : {time.perf_counter() - t0:.2f}s")
    print("\nOK: affected roads/bridges derived (overlap only, not damage).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
