from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend import pipeline
from backend.clients import osm


def _road_way() -> dict:
    return {
        "type": "way",
        "id": 1,
        "geometry": [
            {"lat": 28.1, "lon": 81.1},
            {"lat": 28.2, "lon": 81.2},
        ],
        "tags": {"highway": "primary", "name": "East Rd", "oneway": "yes"},
    }


def _closed_building() -> dict:
    ring = [
        {"lat": 28.0, "lon": 81.0},
        {"lat": 28.0, "lon": 81.1},
        {"lat": 28.1, "lon": 81.1},
        {"lat": 28.1, "lon": 81.0},
        {"lat": 28.0, "lon": 81.0},
    ]
    return {"type": "way", "id": 2, "geometry": ring, "tags": {"building": "yes"}}


def _unclosed_building() -> dict:
    ring = [
        {"lat": 28.0, "lon": 81.0},
        {"lat": 28.0, "lon": 81.1},
        {"lat": 28.1, "lon": 81.1},
        {"lat": 28.1, "lon": 81.0},
    ]
    return {"type": "way", "id": 3, "geometry": ring, "tags": {"building": "yes"}}


class TestBuildQuery(unittest.TestCase):
    def test_bbox_order_is_south_west_north_east(self):
        query = osm.build_query([81.0, 28.0, 82.0, 29.0], "roads")
        self.assertIn("(28.0,81.0,29.0,82.0)", query)
        self.assertTrue(query.startswith("[out:json]"))

    def test_out_statement_sits_outside_union(self):
        query = osm.build_query([81.0, 28.0, 82.0, 29.0], "settlements")
        union_close = query.index(");\n")
        self.assertGreater(query.index("out center;"), union_close)
        self.assertIn('way["place"](28.0,81.0,29.0,82.0);', query)

    def test_roads_use_out_geom(self):
        self.assertIn("out geom;", osm.build_query([0, 0, 1, 1], "roads"))

    def test_unknown_layer_raises(self):
        with self.assertRaises(ValueError):
            osm.build_query([0, 0, 1, 1], "railways")


class TestElementsToGeojson(unittest.TestCase):
    def test_road_way_becomes_linestring(self):
        fc = osm.elements_to_geojson([_road_way()], "roads")
        self.assertEqual(len(fc["features"]), 1)
        feature = fc["features"][0]
        self.assertEqual(feature["geometry"]["type"], "LineString")
        self.assertEqual(
            feature["geometry"]["coordinates"], [[81.1, 28.1], [81.2, 28.2]]
        )

    def test_only_allowlisted_properties_survive(self):
        fc = osm.elements_to_geojson([_road_way()], "roads")
        props = fc["features"][0]["properties"]
        self.assertEqual(props["highway"], "primary")
        self.assertEqual(props["name"], "East Rd")
        self.assertNotIn("oneway", props)

    def test_closed_building_becomes_polygon(self):
        fc = osm.elements_to_geojson([_closed_building()], "buildings")
        geometry = fc["features"][0]["geometry"]
        self.assertEqual(geometry["type"], "Polygon")
        ring = geometry["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])
        self.assertGreaterEqual(len(ring), 4)

    def test_unclosed_building_is_skipped(self):
        fc = osm.elements_to_geojson([_unclosed_building()], "buildings")
        self.assertEqual(fc["features"], [])

    def test_settlement_node_becomes_point(self):
        element = {
            "type": "node",
            "id": 7,
            "lat": 28.5,
            "lon": 81.5,
            "tags": {"place": "village", "name": "Mugu", "population": "1200"},
        }
        fc = osm.elements_to_geojson([element], "settlements")
        feature = fc["features"][0]
        self.assertEqual(feature["geometry"], {"type": "Point", "coordinates": [81.5, 28.5]})
        self.assertEqual(feature["properties"]["place"], "village")

    def test_settlement_way_uses_overpass_center(self):
        element = {
            "type": "way",
            "id": 8,
            "center": {"lat": 28.6, "lon": 81.6},
            "tags": {"place": "town", "name": "Surkhet"},
        }
        fc = osm.elements_to_geojson([element], "settlements")
        self.assertEqual(
            fc["features"][0]["geometry"]["coordinates"], [81.6, 28.6]
        )

    def test_relation_without_center_is_skipped(self):
        element = {"type": "relation", "id": 9, "tags": {"place": "city"}}
        fc = osm.elements_to_geojson([element], "settlements")
        self.assertEqual(fc["features"], [])


class TestPipelineOsmResilience(unittest.TestCase):
    def test_overpass_failure_degrades_without_raising(self):
        with TemporaryDirectory() as tmp:
            with mock.patch("time.sleep"), mock.patch.object(
                osm, "fetch_layer", side_effect=RuntimeError("overpass down")
            ):
                pending = pipeline.start_osm_fetch([81.0, 28.0, 82.0, 29.0], Path(tmp))
                status = pipeline.collect_osm_fetch(pending, timeout_s=10.0)

        self.assertEqual(set(status), set(pipeline.OSM_LAYER_NAMES))
        for record in status.values():
            self.assertEqual(record["status"], "error")
            self.assertIn("overpass down", record["error"])

    def test_success_writes_geojson_and_counts_features(self):
        fake_fc = {"type": "FeatureCollection", "features": [{"type": "Feature"}]}
        with TemporaryDirectory() as tmp:
            with mock.patch("time.sleep"), mock.patch.object(
                osm, "fetch_layer", return_value=fake_fc
            ):
                pending = pipeline.start_osm_fetch([81.0, 28.0, 82.0, 29.0], Path(tmp))
                status = pipeline.collect_osm_fetch(pending, timeout_s=10.0)

            for layer in pipeline.OSM_LAYER_NAMES:
                self.assertEqual(status[layer]["status"], "ok")
                self.assertEqual(status[layer]["features"], 1)
                self.assertTrue((Path(tmp) / f"osm_{layer}.geojson").exists())


class TestMirrorRotation(unittest.TestCase):
    def _elements(self):
        return [
            {
                "type": "node",
                "id": 7,
                "lat": 28.5,
                "lon": 81.5,
                "tags": {"place": "village", "name": "Mugu"},
            }
        ]

    def test_rotates_to_next_instance_after_failure(self):
        calls = []

        def fake_post(url, query, budget):
            calls.append(url)
            if len(calls) == 1:
                raise osm.OsmFetchError("HTTP 429")
            return self._elements()

        with mock.patch.object(osm, "_post_overpass", side_effect=fake_post):
            fc = osm.fetch_layer([81.0, 28.0, 82.0, 29.0], "settlements")

        self.assertEqual(len(fc["features"]), 1)
        self.assertEqual(len(calls), 2)

    def test_second_round_runs_when_every_instance_fails(self):
        candidates = osm._candidate_urls()
        calls = []

        def fake_post(url, query, budget):
            calls.append(url)
            if len(calls) <= len(candidates):
                raise osm.OsmFetchError("HTTP 429")
            return self._elements()

        with mock.patch.object(osm, "_post_overpass", side_effect=fake_post):
            with mock.patch("time.sleep"):
                fc = osm.fetch_layer([81.0, 28.0, 82.0, 29.0], "settlements")

        self.assertEqual(len(fc["features"]), 1)
        self.assertEqual(len(calls), len(candidates) + 1)

    def test_exhausted_budget_raises_overpass_error(self):
        with mock.patch.object(
            osm, "_post_overpass", side_effect=osm.OsmFetchError("HTTP 429")
        ):
            with mock.patch("time.sleep"):
                with self.assertRaises(osm.OsmFetchError) as ctx:
                    osm.fetch_layer([81.0, 28.0, 82.0, 29.0], "roads")
        self.assertIn("all Overpass instances failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
