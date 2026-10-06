from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS

from backend import raster
from backend.clients.cdse import CDSEClient, CDSEError


class _Bbox:
    """Minimal stand-in for rasterio.coords.BoundingBox."""

    def __init__(self, values):
        self.left, self.bottom, self.right, self.top = values


def _write(path: Path, data: np.ndarray) -> None:
    transform = rasterio.transform.from_bounds(81.0, 28.9, 81.1, 29.0, 10, 10)
    with rasterio.open(
        path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1],
        count=1, dtype="float32", crs="EPSG:4326", transform=transform,
        nodata=0, compress="deflate",
    ) as dst:
        dst.write(data.astype("float32"), 1)


class TestAnalyzePair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # bright land at 400 DN drops to 50 DN over half the grid = open water
        before = np.full((10, 10), 400.0)
        after = np.full((10, 10), 400.0)
        after[:5, :] = 50.0
        self.before, self.after = self.dir / "before.tif", self.dir / "after.tif"
        _write(self.before, before)
        _write(self.after, after)

    def tearDown(self):
        self.tmp.cleanup()

    def test_counts_flooded_half_grid(self):
        results = raster.analyze_pair(self.before, self.after)
        self.assertEqual(results["floodPixels"], 50)
        self.assertEqual(results["validPixels"], 100)
        self.assertAlmostEqual(results["floodPctOfValid"], 50.0, places=1)

    def test_diff_is_negative_where_brightness_drops(self):
        results = raster.analyze_pair(self.before, self.after)
        self.assertLess(results["diffDb"]["mean"], 0)

    def test_no_flood_when_brightness_rises(self):
        rising = np.full((10, 10), 900.0)
        path = self.dir / "rising.tif"
        _write(path, rising)
        results = raster.analyze_pair(self.before, path)
        self.assertEqual(results["floodPixels"], 0)

    def test_area_derived_from_geotransform(self):
        results = raster.analyze_pair(self.before, self.after)
        expected = 50 * results["grid"]["pixelAreaM2"] / 1e6
        self.assertAlmostEqual(results["floodAreaKm2"], expected, places=3)
        self.assertGreater(results["grid"]["pixelAreaM2"], 0)

    def test_writes_mask_raster(self):
        mask = self.dir / "flood_mask.tif"
        results = raster.analyze_pair(self.before, self.after, mask_out_path=mask)
        self.assertTrue(mask.exists())
        with rasterio.open(mask) as src:
            data = src.read(1)
            self.assertEqual(src.dtypes[0], "uint8")
            self.assertEqual(int(data.sum()), 50)

    def test_rejects_grid_mismatch(self):
        odd = self.dir / "odd.tif"
        _write(odd, np.full((8, 8), 400.0))
        with self.assertRaises(raster.RasterAnalysisError):
            raster.analyze_pair(self.before, odd)

    def test_rejects_missing_file(self):
        with self.assertRaises(raster.RasterAnalysisError):
            raster.analyze_pair(self.before, self.dir / "nope.tif")

    def test_rejects_fully_nodata(self):
        empty = self.dir / "empty.tif"
        _write(empty, np.zeros((10, 10)))
        with self.assertRaises(raster.RasterAnalysisError):
            raster.analyze_pair(self.before, empty)

    def test_cutoff_is_percentile_of_after_scene(self):
        results = raster.analyze_pair(self.before, self.after, after_percentile=35.0)
        # half the after grid sits at 50 DN and half at 400 DN, so the 35th
        # percentile must land in the dark (flooded) half
        self.assertAlmostEqual(results["afterDbCutoff"], 20 * np.log10(50), places=3)

    def test_dn_to_db_uses_amplitude_factor(self):
        db = raster._dn_to_db(np.array([100.0]))
        self.assertAlmostEqual(float(db[0]), 40.0, places=6)


class TestPixelArea(unittest.TestCase):
    def test_geographic_area_applies_cos_latitude(self):
        # A 0.01 x 0.1 deg box at ~29N as a single pixel: a degree of longitude
        # is ~0.875x a degree of latitude, so ignoring cos(lat) would overstate
        # the area by ~14%.
        transform = rasterio.transform.from_bounds(81.0, 28.9, 81.01, 29.0, 1, 1)
        bounds = rasterio.transform.array_bounds(1, 1, transform)
        area = raster._pixel_area_m2(transform, _Bbox(bounds), CRS.from_epsg(4326))
        expected = (
            0.01 * 111320.0 * 0.1 * 111320.0 * math.cos(math.radians(28.95))
        )
        self.assertAlmostEqual(area, expected, places=3)
        self.assertLess(area, 0.01 * 0.1 * 111320.0 ** 2)

    def test_geographic_area_shrinks_toward_the_poles(self):
        transform = rasterio.transform.from_bounds(0.0, 0.0, 0.01, 0.01, 1, 1)
        equator = raster._pixel_area_m2(
            transform, _Bbox((0.0, 0.0, 0.01, 0.01)), CRS.from_epsg(4326)
        )
        polar = raster._pixel_area_m2(
            transform, _Bbox((0.0, 79.99, 0.01, 80.0)), CRS.from_epsg(4326)
        )
        self.assertLess(polar, equator * 0.2)

    def test_projected_area_is_not_rescaled(self):
        # UTM transforms are already in metres; multiplying by metres-per-degree
        # would inflate the area by nine orders of magnitude.
        transform = rasterio.transform.from_bounds(500000, 3000000, 500010, 3000010, 10, 10)
        area = raster._pixel_area_m2(
            transform, _Bbox((500000, 3000000, 500010, 3000010)), CRS.from_epsg(32643)
        )
        self.assertAlmostEqual(area, 1.0, places=6)


class TestAssetHref(unittest.TestCase):
    def test_prefers_https_alternate(self):
        client = CDSEClient(token_url="http://x/token", stac_url="http://x/stac")
        feature = {
            "id": "p1",
            "assets": {"vv": {
                "href": "s3://eodata/a/vv.tif",
                "alternate": {"https": {"href": "https://download.example/vv.tif"}},
            }},
        }
        self.assertEqual(client.asset_href(feature), "https://download.example/vv.tif")

    def test_raises_when_only_s3_available(self):
        client = CDSEClient(token_url="http://x/token", stac_url="http://x/stac")
        with self.assertRaises(CDSEError):
            client.asset_href({"id": "p1", "assets": {"vv": {"href": "s3://eodata/a/vv.tif"}}})

    def test_raises_when_band_missing(self):
        client = CDSEClient(token_url="http://x/token", stac_url="http://x/stac")
        with self.assertRaises(CDSEError):
            client.asset_href({"id": "p1", "assets": {}})


if __name__ == "__main__":
    unittest.main()