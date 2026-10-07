"""Find roads and settlements cut off from the main road network by flooding.

Builds a NetworkX graph from data/<uuid>/osm_roads.geojson. Overpass `out geom`
returns no node ids, so graph nodes are the ways' exact shared vertices (rounded
to 7 decimals — a shared OSM node yields identical coordinates in every way that
uses it). Edges are the vertex-to-vertex segments between them.

Flood cutting is segment-level: only segments belonging to roads listed in
affected_roads.geojson (Task 6) that also geometrically intersect a FloodViT
flood polygon are removed from the graph — a way flagged 4% affected keeps its
unflooded 96%. The largest remaining connected component is the "main" network;
every surviving road fragment and every settlement node outside it is reported.

Outputs (in the same analysis dir):
  disconnected_routes.geojson      surviving road fragments outside the main
                                   component (original OSM id/tags plus
                                   component membership stats).
  disconnected_settlements.geojson settlements whose nearest road-network node
                                   lies outside the main component.

`newlyDisconnected` distinguishes flood-caused cut-offs (the node was inside the
largest component of the uncut graph) from islands that were already disconnected
before the flood. Classification is connectivity only: it is NOT a claim that a
road is damaged or a settlement unreachable in the real world.

Run from repo root:  python scripts/floodvit/disconnected_routes.py
Optional arg:        python scripts/floodvit/disconnected_routes.py <analysis_dir>
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import networkx as nx
from pyproj import Geod
from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

OSM_ROADS_FILE = "osm_roads.geojson"
SETTLEMENTS_FILE = "osm_settlements.geojson"
AFFECTED_ROADS_FILE = "affected_roads.geojson"
FLOOD_POLYGONS_FILE = "flood_polygons.geojson"
ROUTES_OUT_FILE = "disconnected_routes.geojson"
SETTLEMENTS_OUT_FILE = "disconnected_settlements.geojson"

REQUIRED_INPUTS = (
    OSM_ROADS_FILE,
    SETTLEMENTS_FILE,
    AFFECTED_ROADS_FILE,
    FLOOD_POLYGONS_FILE,
)

GEOD = Geod(ellps="WGS84")

# Vertex coordinates come from the same Overpass response, so a shared OSM node
# arrives with identical text in every way → identical float. Rounding to 7
# decimals (~1 cm) guards against float-repr noise without merging real nodes.
COORD_PRECISION = 7


def node_key(coord) -> tuple[float, float]:
    return (
        round(float(coord[0]), COORD_PRECISION),
        round(float(coord[1]), COORD_PRECISION),
    )


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


def load_geojson(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def geodesic_length_m(a: tuple, b: tuple) -> float:
    return GEOD.line_length([a[0], b[0]], [a[1], b[1]])


def scaled_point(lon: float, lat: float) -> Point:
    """Equirectangular point: lon scaled by cos(lat) so planar nearest-neighbour
    distance approximates ground distance (raw degrees overweigh east–west)."""
    return Point(lon * math.cos(math.radians(lat)), lat)


class Way:
    """One OSM road way split into vertex-to-vertex segments."""

    __slots__ = ("id", "properties", "coords", "keys", "cut", "lengths")

    def __init__(self, feature: dict, candidate: bool, flood_tree: STRtree | None):
        self.id = feature.get("id")
        self.properties = feature.get("properties") or {}
        self.coords = [[float(x), float(y)] for x, y in feature["geometry"]["coordinates"]]
        self.keys = [node_key(c) for c in self.coords]
        n_seg = max(len(self.coords) - 1, 0)
        self.lengths = [
            geodesic_length_m(self.coords[i], self.coords[i + 1]) for i in range(n_seg)
        ]
        # A segment is cut only when its way is Task-6 affected AND the segment
        # itself intersects a flood polygon. Ways outside the affected set cannot
        # contain a cut segment (Task 6 used the same intersects predicate over
        # whole ways), so the candidate test skips them entirely.
        if candidate and flood_tree is not None:
            self.cut = []
            for i in range(n_seg):
                if self.keys[i] == self.keys[i + 1]:
                    self.cut.append(False)
                    continue
                seg = LineString([self.coords[i], self.coords[i + 1]])
                self.cut.append(bool(flood_tree.query(seg, predicate="intersects").size))
        else:
            self.cut = [False] * n_seg

    def fragments(self) -> list[tuple[int, int]]:
        """Maximal runs of consecutive surviving segments as (start, end) index
        pairs over the segment list; each run is a connected chain in the graph."""
        runs: list[tuple[int, int]] = []
        i, n = 0, len(self.cut)
        while i < n:
            if self.cut[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and not self.cut[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        return runs


def build_graph(ways: list[Way]) -> tuple[nx.Graph, int, int]:
    """Post-cut graph: every node kept (an isolate whose only road was cut is
    itself the result), edge alive iff at least one covering segment survives —
    parallel ways sharing identical segment geometry keep the link if any of
    them is open. Returns (graph, segments_total, segments_cut)."""
    edge_alive: dict[tuple, bool] = {}
    edge_attrs: dict[tuple, dict] = {}
    nodes: set = set()
    seg_total = seg_cut = 0
    for w in ways:
        nodes.update(w.keys)
        for i, is_cut in enumerate(w.cut):
            seg_total += 1
            if is_cut:
                seg_cut += 1
            u, v = w.keys[i], w.keys[i + 1]
            if u == v:
                continue
            pair = (u, v) if u < v else (v, u)
            if is_cut:
                edge_alive.setdefault(pair, False)
            else:
                edge_alive[pair] = True
                edge_attrs.setdefault(
                    pair,
                    {
                        "lengthM": w.lengths[i],
                        "wayId": w.id,
                        "highway": w.properties.get("highway"),
                        "name": w.properties.get("name"),
                    },
                )
    g = nx.Graph()
    g.add_nodes_from(nodes)
    for pair, alive in edge_alive.items():
        if alive:
            g.add_edge(pair[0], pair[1], **edge_attrs[pair])
    return g, seg_total, seg_cut


def build_uncut_graph(ways: list[Way]) -> nx.Graph:
    """Same nodes, every segment present — the pre-flood reference network."""
    g = nx.Graph()
    for w in ways:
        g.add_nodes_from(w.keys)
        for i in range(len(w.cut)):
            u, v = w.keys[i], w.keys[i + 1]
            if u != v:
                g.add_edge(u, v)
    return g


def component_ranks(g: nx.Graph) -> tuple[list[set], dict]:
    """Components ordered by size (rank 0 = main) and node → rank map."""
    comps = sorted(nx.connected_components(g), key=len, reverse=True)
    rank_of: dict = {}
    for r, comp in enumerate(comps):
        for n in comp:
            rank_of[n] = r
    return comps, rank_of


def fragment_inventory(ways: list[Way], rank_of: dict) -> tuple[list[dict], dict, dict]:
    """Every surviving fragment with its component membership.

    Returns (fragments, comp_road_count, comp_length_m); aggregates are complete
    before any feature is emitted, so component stats are never partial.
    """
    fragments: list[dict] = []
    comp_road_count: dict[int, int] = defaultdict(int)
    comp_len: dict[int, float] = defaultdict(float)
    for w in ways:
        runs = w.fragments()
        for idx, (a, b) in enumerate(runs):
            rank = rank_of.get(w.keys[a], 0)
            length = sum(w.lengths[a : b + 1])
            comp_road_count[rank] += 1
            comp_len[rank] += length
            fragments.append(
                {"way": w, "idx": idx, "n": len(runs), "a": a, "b": b,
                 "rank": rank, "lengthM": length}
            )
    return fragments, comp_road_count, comp_len


def route_features(
    fragments: list[dict],
    comp_node_count: dict,
    comp_road_count: dict,
    comp_len: dict,
    main0_nodes: set,
) -> tuple[list[dict], float, int]:
    """One feature per surviving fragment outside the main component.
    Returns (features, disconnected_km, newly_disconnected_count)."""
    features: list[dict] = []
    for fr in fragments:
        if fr["rank"] == 0:
            continue  # reachable from the main network
        w: Way = fr["way"]
        props = dict(w.properties)
        props.update(
            status="disconnected",
            fragmentIndex=fr["idx"],
            fragmentCount=fr["n"],
            fragmentLengthM=round(fr["lengthM"], 1),
            componentId=fr["rank"],
            componentNodeCount=comp_node_count.get(fr["rank"], 0),
            componentRoadCount=comp_road_count.get(fr["rank"], 0),
            componentLengthM=round(comp_len.get(fr["rank"], 0.0), 1),
            newlyDisconnected=w.keys[fr["a"]] in main0_nodes,
        )
        features.append(
            {
                "type": "Feature",
                "id": w.id,
                "properties": props,
                "geometry": {
                    "type": "LineString",
                    "coordinates": w.coords[fr["a"] : fr["b"] + 2],
                },
            }
        )
    km = sum(f["properties"]["fragmentLengthM"] for f in features) / 1000.0
    newly = sum(1 for f in features if f["properties"]["newlyDisconnected"])
    return features, km, newly


def settlement_features(
    sett_features: list[dict],
    ways: list[Way],
    rank_of: dict,
    comp_node_count: dict,
    comp_road_count: dict,
    comp_len: dict,
    main0_nodes: set,
) -> tuple[list[dict], int, int]:
    """Snap each settlement to the nearest road-network node and keep those
    outside the main component. Isolated nodes are included: a village whose
    only access road was cut snaps to that road's now-isolated node. Returns
    (features, settlement_total, unsnapped_count)."""
    nodes = sorted({k for w in ways for k in w.keys})
    if not nodes:
        return [], len(sett_features), len(sett_features)
    tree = STRtree([scaled_point(*n) for n in nodes])

    features: list[dict] = []
    for f in sett_features:
        geom = f.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        lon, lat = (float(geom["coordinates"][0]), float(geom["coordinates"][1]))
        node = nodes[int(tree.query_nearest(scaled_point(lon, lat))[0])]
        rank = rank_of.get(node, 0)
        if rank == 0:
            continue  # connected to the main network
        _, _, snap_m = GEOD.inv(lon, lat, node[0], node[1])
        props = dict(f.get("properties") or {})
        props.update(
            status="disconnected",
            snapDistanceM=round(snap_m, 1),
            componentId=rank,
            componentNodeCount=comp_node_count.get(rank, 0),
            componentRoadCount=comp_road_count.get(rank, 0),
            componentLengthM=round(comp_len.get(rank, 0.0), 1),
            newlyDisconnected=node in main0_nodes,
        )
        features.append(
            {
                "type": "Feature",
                "id": f.get("id"),
                "properties": props,
                "geometry": geom,
            }
        )
    return features, len(sett_features), 0


def collection(features: list[dict], layer: str, method: str, base: dict,
               extra: dict) -> dict:
    props = {
        "layer": layer,
        "bbox": base.get("bbox"),
        "source": base.get("source"),
        "copyright": base.get("copyright"),
        "method": method,
        "featureCount": len(features),
    }
    props.update(extra)
    return {"type": "FeatureCollection", "properties": props, "features": features}


def main() -> int:
    t0 = time.perf_counter()
    analysis_dir = resolve_analysis_dir(sys.argv[1] if len(sys.argv) > 1 else None)

    roads_fc = load_geojson(analysis_dir / OSM_ROADS_FILE)
    sett_fc = load_geojson(analysis_dir / SETTLEMENTS_FILE)
    affected_fc = load_geojson(analysis_dir / AFFECTED_ROADS_FILE)
    flood_fc = load_geojson(analysis_dir / FLOOD_POLYGONS_FILE)

    road_features = [
        f
        for f in roads_fc.get("features", [])
        if (f.get("geometry") or {}).get("type") == "LineString"
    ]
    sett_features = [
        f
        for f in sett_fc.get("features", [])
        if (f.get("geometry") or {}).get("type") == "Point"
    ]
    candidate_ids = {f.get("id") for f in affected_fc.get("features", [])}
    flood_geoms: list[BaseGeometry] = [
        shape(f["geometry"]) for f in flood_fc.get("features", []) if f.get("geometry")
    ]
    flood_tree = STRtree(flood_geoms) if flood_geoms else None

    ways = [
        Way(f, f.get("id") in candidate_ids, flood_tree) for f in road_features
    ]
    g, seg_total, seg_cut = build_graph(ways)
    g0 = build_uncut_graph(ways)

    comps, rank_of = component_ranks(g)
    comp_node_count = {r: len(c) for r, c in enumerate(comps)}
    comps0 = sorted(nx.connected_components(g0), key=len, reverse=True)
    main0 = set(comps0[0]) if comps0 else set()

    fragments, comp_road_count, comp_len = fragment_inventory(ways, rank_of)
    routes, routes_km, routes_newly = route_features(
        fragments, comp_node_count, comp_road_count, comp_len, main0
    )
    setts, sett_total, unsnapped = settlement_features(
        sett_features, ways, rank_of, comp_node_count, comp_road_count, comp_len, main0
    )

    osm_props = roads_fc.get("properties") or {}
    common_extra = {
        "graphNodes": g.number_of_nodes(),
        "graphEdges": g.number_of_edges(),
        "segmentsTotal": seg_total,
        "segmentsCut": seg_cut,
        "componentsBefore": len(comps0),
        "componentsAfter": len(comps),
        "mainComponentNodes": comp_node_count.get(0, 0),
    }

    routes_fc = collection(
        routes,
        "disconnected_routes",
        (
            "NetworkX graph of OSM centerlines (nodes = exact shared way "
            "vertices); segments of affected_roads.geojson ways that intersect "
            "FloodViT flood polygons removed; features are surviving road "
            "fragments outside the largest remaining connected component "
            "(connectivity only, not a damage or impassability assessment)"
        ),
        osm_props,
        dict(
            common_extra,
            disconnectedComponents=len(
                {f["properties"]["componentId"] for f in routes}
            ),
            newlyDisconnectedFeatures=routes_newly,
        ),
    )
    setts_fc = collection(
        setts,
        "disconnected_settlements",
        (
            "settlements snapped to the nearest node of the flood-cut road "
            "graph; features are those outside the largest remaining connected "
            "component (connectivity only, not a real-world reachability claim)"
        ),
        sett_fc.get("properties") or {},
        dict(
            common_extra,
            settlementsTotal=sett_total,
            unsnappedSettlements=unsnapped,
            newlyDisconnectedFeatures=sum(
                1 for f in setts if f["properties"]["newlyDisconnected"]
            ),
        ),
    )

    routes_path = analysis_dir / ROUTES_OUT_FILE
    setts_path = analysis_dir / SETTLEMENTS_OUT_FILE
    routes_path.write_text(json.dumps(routes_fc, separators=(",", ":")), encoding="utf-8")
    setts_path.write_text(json.dumps(setts_fc, separators=(",", ":")), encoding="utf-8")

    n_affected_ways = sum(1 for w in ways if any(w.cut))
    print(f"analysis dir        : {analysis_dir}")
    print(f"roads               : {len(road_features)} ways "
          f"({n_affected_ways} with cut segments)")
    print(f"segments cut        : {seg_cut} of {seg_total}")
    print(f"graph               : {g.number_of_nodes()} nodes, "
          f"{g.number_of_edges()} edges")
    print(f"components          : {len(comps0)} before -> {len(comps)} after "
          f"(main = {comp_node_count.get(0, 0)} nodes)")
    print(f"disconnected routes : {len(routes)} fragments "
          f"({routes_km:.3f} km, {routes_newly} newly disconnected) "
          f"in {routes_fc['properties']['disconnectedComponents']} components")
    print(f"disconnected places : {len(setts)} of {sett_total} settlements "
          f"({setts_fc['properties']['newlyDisconnectedFeatures']} newly)")
    print(f"geojson written     : {routes_path}")
    print(f"                      {setts_path}")
    print(f"elapsed             : {time.perf_counter() - t0:.2f}s")
    print("\nOK: disconnected routes/settlements derived (connectivity only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
