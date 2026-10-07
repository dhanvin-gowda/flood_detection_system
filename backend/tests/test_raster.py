from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import rasterio
from rasterio.crs import CRS

from backend import raster
from backend.clients.cdse import CDSEClient, CDSEError

_ERR_502 = (
    "HTTP response code: 502 - Failure writing output to destination, "
    "passed 150 returned 0"
)


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


class _FakeClient:
    """CDSE stand-in with no network: href + token plumbing only."""

    def __init__(self):
        self.token_refreshes = 0

    def asset_href(self, feature, band="vv"):
        return f"https://download.example/{feature['id']}/{band}.tif"

    @property
    def auth_header(self):
        return "Bearer tok0"

    def _get_token(self, force=False):
        if force:
            self.token_refreshes += 1
        return "tok-refreshed"


class _FakeRemoteSrc:
    def __init__(self, data):
        self.data = data
        self.height, self.width = data.shape

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, index, window=None):
        return self.data


class TestFetchSceneRetry(unittest.TestCase):
    """fetch_scene must ride out CDSE's 502 blips and explain real outages."""

    _FEATURE = {"id": "p1", "bbox": [81.0, 28.9, 81.1, 29.0], "properties": {}}
    _AOI = [81.0, 28.9, 81.1, 29.0]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "before.tif"
        self.client = _FakeClient()
        self.real_open = rasterio.open

    def tearDown(self):
        self.tmp.cleanup()

    def _open_router(self, results):
        """vsicurl reads replay `results`; local writes use the real opener."""
        calls = {"n": 0}

        def fake_open(path, *args, **kwargs):
            if str(path).startswith("/vsicurl/"):
                item = results[min(calls["n"], len(results) - 1)]
                calls["n"] += 1
                if isinstance(item, BaseException):
                    raise item
                return item
            return self.real_open(path, *args, **kwargs)

        self.open_calls = calls
        return fake_open

    def _fetch(self, results):
        with mock.patch.object(
            raster.rasterio, "open", side_effect=self._open_router(results)
        ), mock.patch.object(raster.time, "sleep") as sleep:
            scene = raster.fetch_scene(
                self.client, self._FEATURE, self._AOI, "before", self.out
            )
        return scene, sleep

    def test_recovers_after_transient_502(self):
        err = RuntimeError(_ERR_502)
        scene, sleep = self._fetch([err, err, _FakeRemoteSrc(np.full((10, 10), 400.0))])
        self.assertEqual(scene.path, self.out)
        self.assertEqual(self.open_calls["n"], 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.0, 5.0])
        self.assertEqual(self.client.token_refreshes, 2)
        self.assertTrue(self.out.exists())

    def test_gives_up_with_friendly_message_after_exhaustion(self):
        err = RuntimeError(_ERR_502)
        with mock.patch.object(
            raster.rasterio, "open", side_effect=self._open_router([err])
        ), mock.patch.object(raster.time, "sleep"):
            with self.assertRaises(raster.RasterAnalysisError) as ctx:
                raster.fetch_scene(
                    self.client, self._FEATURE, self._AOI, "before", self.out
                )
        self.assertEqual(self.open_calls["n"], raster.FETCH_ATTEMPTS)
        self.assertIn("temporarily unavailable (HTTP 502)", str(ctx.exception))
        self.assertIn("before", str(ctx.exception))
        # the raw libcurl text must never reach the dashboard
        self.assertNotIn("Failure writing output", str(ctx.exception))

    def test_network_error_maps_to_network_message(self):
        err = ConnectionError("[WinError 10060] connection attempt failed")
        with mock.patch.object(
            raster.rasterio, "open", side_effect=self._open_router([err])
        ), mock.patch.object(raster.time, "sleep"):
            with self.assertRaises(raster.RasterAnalysisError) as ctx:
                raster.fetch_scene(
                    self.client, self._FEATURE, self._AOI, "after", self.out
                )
        self.assertIn("network error", str(ctx.exception))

    def test_permanent_http_error_fails_immediately(self):
        err = RuntimeError("HTTP response code: 404 - Not Found")
        with mock.patch.object(
            raster.rasterio, "open", side_effect=self._open_router([err])
        ), mock.patch.object(raster.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError) as ctx:
                raster.fetch_scene(
                    self.client, self._FEATURE, self._AOI, "before", self.out
                )
        self.assertIs(ctx.exception, err)
        self.assertEqual(self.open_calls["n"], 1)
        sleep.assert_not_called()
        self.assertEqual(self.client.token_refreshes, 0)

    def test_logic_error_never_retries(self):
        err = RuntimeError("feature has no bbox")
        with mock.patch.object(
            raster.rasterio, "open", side_effect=self._open_router([err])
        ), mock.patch.object(raster.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError) as ctx:
                raster.fetch_scene(
                    self.client, self._FEATURE, self._AOI, "before", self.out
                )
        self.assertIs(ctx.exception, err)
        self.assertEqual(self.open_calls["n"], 1)
        sleep.assert_not_called()


class TestFriendlyFetchError(unittest.TestCase):
    def test_502_503_504_429_become_unavailable_message(self):
        for code in ("429", "502", "503", "504"):
            exc = RuntimeError(f"HTTP response code: {code} - gateway said no")
            friendly = raster._friendly_fetch_error("before", exc)
            self.assertIsInstance(friendly, raster.RasterAnalysisError)
            self.assertIn(f"HTTP {code}", str(friendly))
            self.assertIn("before scene fetch failed", str(friendly))

    def test_permanent_status_is_passed_through(self):
        exc = RuntimeError("HTTP response code: 401 - Unauthorized")
        self.assertIs(raster._friendly_fetch_error("before", exc), exc)

    def test_unrelated_error_is_passed_through(self):
        exc = raster.RasterAnalysisError("AOI does not intersect product p1")
        self.assertIs(raster._friendly_fetch_error("before", exc), exc)

    def test_connection_style_errors_become_network_message(self):
        for exc in (
            ConnectionError("reset"),
            RuntimeError("[WinError 10054] an existing connection was forcibly closed"),
            RuntimeError("CURL error: Timeout was reached"),
        ):
            friendly = raster._friendly_fetch_error("after", exc)
            self.assertIsInstance(friendly, raster.RasterAnalysisError)
            self.assertIn("network error", str(friendly))


class TestRetryableClassification(unittest.TestCase):
    def test_retryable(self):
        for exc in (
            RuntimeError(_ERR_502),
            RuntimeError("HTTP response code: 429 - too many requests"),
            ConnectionError("reset"),
            TimeoutError("timed out"),
            RuntimeError("[WinError 10054] connection forcibly closed by remote host"),
        ):
            self.assertTrue(raster._is_retryable_fetch_error(exc), exc)

    def test_not_retryable(self):
        for exc in (
            RuntimeError("HTTP response code: 401 - Unauthorized"),
            RuntimeError("HTTP response code: 404 - Not Found"),
            raster.RasterAnalysisError("band data is entirely nodata"),
        ):
            self.assertFalse(raster._is_retryable_fetch_error(exc), exc)


if __name__ == "__main__":
    unittest.main()
