"""Find the nearest hospital to the flooded area and a passable route to it.

Reads data/<uuid>/flood_polygons.geojson + affected_roads.geojson + osm_roads.geojson
(Tasks 4/6), rebuilds the Task-7 flood-cut road graph (segments of affected ways
that intersect a flood polygon removed), then:

  * fetches hospitals from Overpass for the analysis bbox (reusing the pipeline's
    mirror-rotation retry logic; cached as osm_hospitals.geojson so the query runs
    at most once per analysis),
  * picks the road node nearest any flood polygon inside the largest surviving
    connected component as the start ("way out" of the flooded zone),
  * snaps every hospital to the road network and measures a Dijkstra route
    (edge weight = geodesic segment length) from the start node,
  * selects the straight-line-nearest hospital that actually has a passable route.

Outputs (in the same analysis dir):
  hospitals.geojson       every hospital in the (padded) AOI with distance to the
                          flood, network snap distance, reachability, route length
                          and a `selected` flag on the chosen one.
  hospital_route.geojson  0 or 1 LineString: start -> selected hospital over
                          surviving roads, tagged with the road names used.

Wording follows Tasks 6/7: "passable" means only that the route stays on road
segments that do not intersect the mapped flood polygons — a connectivity result,
not a real-world guarantee that the road or hospital is undamaged/open.

Run from repo root:  python scripts/floodvit/nearest_hospital.py
Optional arg:        python scripts/floodvit/nearest_hospital.py <analysis_dir>
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import networkx as nx
import numpy as np
import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points
from shapely.strtree import STRtree

from backend.clients.osm import OsmFetchError, fetch_layer
from disconnected_routes import (
    GEOD,
    Way,
    build_graph,
    collection,
    component_ranks,
    load_geojson,
    scaled_point,
)

OSM_ROADS_FILE = "osm_roads.geojson"
FLOOD_POLYGONS_FILE = "flood_polygons.geojson"
AFFECTED_ROADS_FILE = "affected_roads.geojson"
HOSPITALS_CACHE_FILE = "osm_hospitals.geojson"
HOSPITALS_OUT_FILE = "hospitals.geojson"
ROUTE_OUT_FILE = "hospital_route.geojson"

REQUIRED_INPUTS = (FLOOD_POLYGONS_FILE, AFFECTED_ROADS_FILE, OSM_ROADS_FILE)

# Hospitals just outside the AOI still serve the flooded area, so the query box
# is padded (~5.5 km at mid latitudes) before hitting Overpass.
BBOX_PAD_DEG = 0.05
# A hospital whose nearest network node sits in an isolated fragment cannot be
# routed to; retry with the nearest main-component node when it is within this
# range (a building entrance is routinely offset from the nearest through road).
SNAP_MAX_M = 500.0


def resolve_analysis_dir(arg: str | None) -> Path:
    """Analysis dir holding every input; default to the first match under data/."""
    if arg is None:
        data_root = REPO_ROOT / "data"
        for d in sorted(p for p in data_root.iterdir() if p.is_dir()):
            if all((d / name).is_file() for name in REQUIRED_INPUTS):
                return d
        raise SystemExit("no data/<uuid>/ with all of " + ", ".join(REQUIRED_INPUTS))
    p = Path(arg)
    if p.is_file():  # accept a path to any input file
        p = p.parent
    if not p.is_dir():
        raise SystemExit(f"directory not found: {p}")
    for name in REQUIRED_INPUTS:
        if not (p / name).is_file():
            raise SystemExit(f"missing {name} in {p}")
    return p


def read_bbox(analysis_dir: Path, roads_fc: dict) -> list[float]:
    """AOI bbox in GeoJSON order, padded for the hospital query."""
    bbox = None
    meta_path = analysis_dir / "metadata.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        bbox = ((meta.get("request") or {}).get("bbox")) or None
    if not bbox:
        bbox = (roads_fc.get("properties") or {}).get("bbox") or None
    if not bbox or len(bbox) != 4:
        raise SystemExit("no bbox found in metadata.json or osm_roads.geojson")
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    return [
        max(-180.0, minx - BBOX_PAD_DEG),
        max(-90.0, miny - BBOX_PAD_DEG),
        min(180.0, maxx + BBOX_PAD_DEG),
        min(90.0, maxy + BBOX_PAD_DEG),
    ]


def load_hospitals(analysis_dir: Path, bbox: list[float]) -> dict:
    """Hospital FeatureCollection from the pipeline cache or a fresh Overpass query."""
    cache_path = analysis_dir / HOSPITALS_CACHE_FILE
    if cache_path.is_file():
        print(f"hospitals           : using cached {HOSPITALS_CACHE_FILE}")
        return load_geojson(cache_path)
    fc = fetch_layer(bbox, "hospitals")
    cache_path.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    return fc


def scale_geom(geom: BaseGeometry) -> BaseGeometry:
    """Cos-lat scaled geometry (same projection as disconnected_routes.scaled_point)."""
    def _scale(coords):
        out = np.array(coords, dtype="float64")
        out[:, 0] *= np.cos(np.radians(out[:, 1]))
        return out

    return shapely.transform(geom, _scale)


def flood_distance_m(lon: float, lat: float, tree: STRtree,
                     flood_scaled: list) -> tuple[float, float]:
    """(geodesic metres from the point to the nearest flood polygon, scaled-degrees
    distance used only for cheap deterministic ranking)."""
    pt = scaled_point(lon, lat)
    idx = int(tree.query_nearest(pt)[0])
    near = nearest_points(pt, flood_scaled[idx])[1]
    # Invert the cos-lat scale at the query latitude: the nearest point sits on
    # the flood outline, so recovering its lon gives a geodesic pair for Geod.
    d_m = GEOD.inv(lon, lat, near.x / math.cos(math.radians(lat)), near.y)[2]
    return d_m, float(pt.distance(flood_scaled[idx]))


def pick_start_node(g: nx.Graph, main_nodes: set, flood_tree: STRtree,
                    flood_scaled: list) -> tuple[tuple, float]:
    """Main-component node nearest any flood polygon (deterministic tie-break).

    Restricted to nodes that still have an edge, so the start can never be an
    isolate. Returns (node, distanceToFloodM)."""
    best = None
    for node in main_nodes:
        if g.degree(node) == 0:
            continue
        d_m, d_scaled = flood_distance_m(node[0], node[1], flood_tree, flood_scaled)
        key = (d_scaled, node)
        if best is None or key < best[0]:
            best = (key, node, d_m)
    if best is None:
        raise SystemExit("road graph has no node with an edge in the main component")
    return best[1], best[2]


def build_graphs(analysis_dir: Path):
    roads_fc = load_geojson(analysis_dir / OSM_ROADS_FILE)
    affected_fc = load_geojson(analysis_dir / AFFECTED_ROADS_FILE)
    flood_fc = load_geojson(analysis_dir / FLOOD_POLYGONS_FILE)

    road_features = [
        f for f in roads_fc.get("features", [])
        if (f.get("geometry") or {}).get("type") == "LineString"
    ]
    flood_geoms = [
        shape(f["geometry"]) for f in flood_fc.get("features", []) if f.get("geometry")
    ]
    if not road_features:
        raise SystemExit(f"{OSM_ROADS_FILE} has no road LineStrings")
    if not flood_geoms:
        raise SystemExit(f"{FLOOD_POLYGONS_FILE} has no polygons")

    candidate_ids = {f.get("id") for f in affected_fc.get("features", [])}
    flood_tree = STRtree(flood_geoms)
    flood_scaled = [scale_geom(g) for g in flood_geoms]

    ways = [Way(f, f.get("id") in candidate_ids, flood_tree) for f in road_features]
    g, seg_total, seg_cut = build_graph(ways)
    comps, _ = component_ranks(g)
    main_nodes = set(comps[0]) if comps else set()
    return roads_fc, g, main_nodes, flood_tree, flood_scaled, seg_total, seg_cut


def snap_hospital(lon: float, lat: float, nodes: list, node_tree: STRtree,
                  main_nodes: list, main_tree: STRtree | None,
                  lengths: dict) -> tuple[tuple, float, bool]:
    """(node, snapDistanceM, reachable) — nearest network node overall, but moved
    to the nearest main-component node when the first snap lands in an isolated
    fragment close by (otherwise reachability would hinge on which side of a cut
    a building's entrance happens to fall)."""
    pt = scaled_point(lon, lat)
    node = nodes[int(node_tree.query_nearest(pt)[0])]
    d_m = GEOD.inv(lon, lat, node[0], node[1])[2]
    if node in lengths:
        return node, d_m, True

    if main_tree is None:
        return node, d_m, False
    cand = main_nodes[int(main_tree.query_nearest(pt)[0])]
    cand_d = GEOD.inv(lon, lat, cand[0], cand[1])[2]
    if cand_d <= SNAP_MAX_M:
        return cand, cand_d, True
    return node, d_m, False


def main() -> int:
    t0 = time.perf_counter()
    analysis_dir = resolve_analysis_dir(sys.argv[1] if len(sys.argv) > 1 else None)

    roads_fc, g, main_nodes, flood_tree, flood_scaled, seg_total, seg_cut = build_graphs(
        analysis_dir
    )
    bbox = read_bbox(analysis_dir, roads_fc)
    hospitals_fc = load_hospitals(analysis_dir, bbox)

    start, start_dist_m = pick_start_node(g, main_nodes, flood_tree, flood_scaled)
    lengths = nx.single_source_dijkstra_path_length(g, start, weight="lengthM")

    hospital_features = [
        f for f in hospitals_fc.get("features", [])
        if (f.get("geometry") or {}).get("type") == "Point"
    ]
    nodes = sorted(g.nodes)
    node_tree = STRtree([scaled_point(*n) for n in nodes])
    main_list = sorted(n for n in nodes if n in lengths)
    main_tree = STRtree([scaled_point(*n) for n in main_list]) if main_list else None

    hospitals: list[dict] = []
    for f in sorted(hospital_features, key=lambda f: str(f.get("id"))):
        lon, lat = float(f["geometry"]["coordinates"][0]), float(f["geometry"]["coordinates"][1])
        node, snap_m, reachable = snap_hospital(
            lon, lat, nodes, node_tree, main_list, main_tree, lengths
        )
        flood_m, _ = flood_distance_m(lon, lat, flood_tree, flood_scaled)
        props = dict(f.get("properties") or {})
        props.update(
            hospitalId=f.get("id"),
            distanceToFloodM=round(flood_m, 1),
            snapDistanceM=round(snap_m, 1),
            reachable=reachable,
            routeLengthM=round(lengths[node], 1) if reachable else None,
            selected=False,
        )
        hospitals.append({"feature": f, "props": props, "dist": flood_m, "node": node})

    route_status = "route found"
    route_feature = None
    selected = None
    if not hospitals:
        route_status = "no hospitals found"
    else:
        by_dist = sorted(hospitals, key=lambda h: (h["dist"], str(h["feature"].get("id"))))
        reachable = [h for h in by_dist if h["props"]["reachable"]]
        if reachable:
            selected = reachable[0]
            selected["props"]["selected"] = True
        else:
            route_status = "no passable route"

    if selected is not None:
        end_lon, end_lat = selected["feature"]["geometry"]["coordinates"]
        path = nx.dijkstra_path(g, start, selected["node"], weight="lengthM")
        road_names: list[str] = []
        way_ids: list = []
        for u, v in zip(path, path[1:]):
            attrs = g[u][v]
            name = attrs.get("name")
            if name and name not in road_names:
                road_names.append(name)
            wid = attrs.get("wayId")
            if wid is not None and wid not in way_ids:
                way_ids.append(wid)
        straight_m = GEOD.inv(start[0], start[1], end_lon, end_lat)[2]
        route_feature = {
            "type": "Feature",
            "id": selected["props"]["hospitalId"],
            "properties": {
                "status": "passable route",
                "hospitalId": selected["props"]["hospitalId"],
                "hospitalName": selected["props"].get("name"),
                "startFloodDistanceM": round(start_dist_m, 1),
                "straightLineM": round(straight_m, 1),
                "routeLengthM": round(lengths[selected["node"]], 1),
                "segments": len(path) - 1,
                "roadNames": road_names,
                "wayIds": way_ids,
            },
            "geometry": {"type": "LineString", "coordinates": [list(n) for n in path]},
        }

    out_hospitals = [
        {
            "type": "Feature",
            "id": h["feature"].get("id"),
            "properties": h["props"],
            "geometry": h["feature"]["geometry"],
        }
        for h in sorted(hospitals, key=lambda h: (h["dist"], str(h["feature"].get("id"))))
    ]

    osm_props = hospitals_fc.get("properties") or {}
    hospitals_fc_out = collection(
        out_hospitals,
        "hospitals",
        (
            "hospitals (amenity=hospital) from Overpass, snapped to the flood-cut "
            "road graph; `selected` marks the straight-line-nearest hospital that "
            "has a route over surviving (non-flood-intersecting) road segments "
            "(connectivity only, not a real-world accessibility claim)"
        ),
        osm_props,
        {
            "hospitalCount": len(out_hospitals),
            "reachableCount": sum(1 for h in out_hospitals if h["properties"]["reachable"]),
            "selectedId": selected["props"]["hospitalId"] if selected else None,
            "selectedName": selected["props"].get("name") if selected else None,
            "routeStatus": route_status,
            "graphNodes": g.number_of_nodes(),
            "graphEdges": g.number_of_edges(),
            "segmentsCut": seg_cut,
        },
    )
    route_fc_out = collection(
        [route_feature] if route_feature else [],
        "hospital_route",
        (
            "shortest path (geodesic edge lengths) on the flood-cut road graph "
            "from the main-component node nearest the flood polygons to the "
            "selected hospital; edges are road segments not intersecting any "
            "flood polygon (connectivity only, not a guarantee of passability)"
        ),
        osm_props,
        {
            "routeStatus": route_status,
            "startNode": [round(start[0], 7), round(start[1], 7)],
            "startFloodDistanceM": round(start_dist_m, 1),
        },
    )

    (analysis_dir / HOSPITALS_OUT_FILE).write_text(
        json.dumps(hospitals_fc_out, separators=(",", ":")), encoding="utf-8"
    )
    (analysis_dir / ROUTE_OUT_FILE).write_text(
        json.dumps(route_fc_out, separators=(",", ":")), encoding="utf-8"
    )

    print(f"analysis dir        : {analysis_dir}")
    print(f"bbox (padded)       : {bbox}")
    print(f"graph               : {g.number_of_nodes()} nodes, "
          f"{g.number_of_edges()} edges ({seg_cut} of {seg_total} segments cut)")
    print(f"start node          : {start} ({start_dist_m:.1f} m from flood)")
    print(f"hospitals           : {len(out_hospitals)} "
          f"({hospitals_fc_out['properties']['reachableCount']} reachable)")
    print(f"selected            : "
          f"{selected['props'].get('name') if selected else '-'} "
          f"({route_status})")
    if route_feature:
        p = route_feature["properties"]
        print(f"route               : {p['routeLengthM']} m "
              f"({p['straightLineM']} m straight, {p['segments']} segments)")
        print(f"roads used          : {', '.join(p['roadNames']) or '(unnamed)'}")
    print(f"geojson written     : {analysis_dir / HOSPITALS_OUT_FILE}")
    print(f"                      {analysis_dir / ROUTE_OUT_FILE}")
    print(f"elapsed             : {time.perf_counter() - t0:.2f}s")
    print("\nOK: nearest hospital + passable route derived (connectivity only).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OsmFetchError as exc:
        raise SystemExit(f"hospital fetch failed: {exc}")
