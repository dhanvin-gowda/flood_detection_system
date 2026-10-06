from __future__ import annotations

import math
import unittest
from datetime import date

from backend import pipeline
from backend.clients.cdse import size_for_bbox


def _feature(dt: str, track: str) -> dict:
    return {
        "id": f"prod-{dt}-{track}",
        "bbox": [81.0, 28.0, 82.0, 30.0],
        "properties": {"datetime": dt, "sat:relative_orbit": track},
    }


class TestBuildWindows(unittest.TestCase):
    def test_reference_precedes_after(self):
        ref, aft = pipeline.build_windows(date(2026, 8, 26), today=date(2026, 10, 5))
        self.assertLess(ref[1], aft[0])
        self.assertEqual(ref[1].date(), date(2026, 8, 27))
        self.assertEqual(aft[1].date(), date(2026, 10, 6))

    def test_windows_never_overlap_near_today(self):
        ref, aft = pipeline.build_windows(date(2026, 10, 4), today=date(2026, 10, 5))
        self.assertLessEqual(ref[1], aft[0])

    def test_windows_never_overlap_same_day(self):
        ref, aft = pipeline.build_windows(date(2026, 10, 5), today=date(2026, 10, 5))
        self.assertLessEqual(ref[1], aft[0])

    def test_parse_date_defaults_to_today(self):
        self.assertIsInstance(pipeline.parse_date(None), date)

    def test_parse_date_rejects_garbage(self):
        with self.assertRaises(ValueError):
            pipeline.parse_date("26-08-2026")


class TestChoosePair(unittest.TestCase):
    def test_prefers_shared_relative_orbit(self):
        before = [
            _feature("2026-08-22T00:34:56Z", "165"),
            _feature("2026-08-26T12:38:13Z", "56"),
        ]
        after = [
            _feature("2026-10-01T12:38:14Z", "56"),
            _feature("2026-10-04T00:26:51Z", "92"),
        ]
        b, a = pipeline.choose_pair(before, after)
        self.assertEqual(b["properties"]["sat:relative_orbit"], "56")
        self.assertEqual(a["properties"]["sat:relative_orbit"], "56")

    def test_picks_latest_in_window(self):
        before = [
            _feature("2026-08-20T12:00:00Z", "56"),
            _feature("2026-08-26T12:00:00Z", "56"),
        ]
        after = [_feature("2026-10-01T12:00:00Z", "56")]
        b, _ = pipeline.choose_pair(before, after)
        self.assertEqual(b["properties"]["datetime"], "2026-08-26T12:00:00Z")

    def test_raises_when_no_shared_track(self):
        # A cross-track pair aligns on the grid but not in incidence angle or
        # radiometric calibration, so the difference map would be meaningless.
        before = [_feature("2026-08-26T12:00:00Z", "56")]
        after = [_feature("2026-10-04T00:00:00Z", "92")]
        with self.assertRaises(RuntimeError) as ctx:
            pipeline.choose_pair(before, after)
        self.assertIn("relative orbit", str(ctx.exception))

    def test_raises_on_empty_side(self):
        with self.assertRaises(RuntimeError):
            pipeline.choose_pair([], [])
        with self.assertRaises(RuntimeError):
            pipeline.choose_pair([_feature("2026-08-26T12:00:00Z", "56")], [])


class TestSizeForBbox(unittest.TestCase):
    def test_capped_at_max_side(self):
        width, height = size_for_bbox([81.0, 28.0, 82.5, 29.5], max_side=2500)
        self.assertLessEqual(width, 2500)
        self.assertLessEqual(height, 2500)

    def test_respects_min_side(self):
        width, height = size_for_bbox([81.0, 28.9, 81.001, 28.901], min_side=64)
        self.assertGreaterEqual(width, 64)
        self.assertGreaterEqual(height, 64)

    def test_pixels_stay_square_at_latitude(self):
        # at ~29N a degree of longitude is shorter than a degree of latitude,
        # so a 0.25 x 0.25 deg box must produce fewer columns than rows to keep
        # ground pixels square
        span = 0.1  # keep both sides well under max_side
        bbox = [81.0, 28.9, 81.0 + span, 28.9 + span]
        width, height = size_for_bbox(bbox, resolution_m=10.0)
        lat_mid = (28.9 + 28.9 + span) / 2.0
        m_per_deg_lon = 111320.0 * math.cos(math.radians(lat_mid))
        expected_w = int(round(span * m_per_deg_lon / 10.0))
        expected_h = int(round(span * 111320.0 / 10.0))
        self.assertAlmostEqual(width, expected_w, delta=1)
        self.assertAlmostEqual(height, expected_h, delta=1)
        self.assertGreater(height, width)


if __name__ == "__main__":
    unittest.main()