from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

from backend import raster


def _write(path: Path, data: np.ndarray, nodata: float = 0) -> None:
    h, w = data.shape
    transform = rasterio.transform.from_bounds(81.0, 28.0, 82.0, 29.0, w, h)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w,
        count=1, dtype=str(data.dtype), crs="EPSG:4326", transform=transform,
        nodata=nodata, compress="deflate",
    ) as dst:
        dst.write(data, 1)


class TestRenderPreview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gray_preview_stretches_and_masks_nodata(self):
        data = np.zeros((4, 6), dtype="float32")
        data[0:2] = 100.0
        data[2, :] = 0.0
        data[3] = 400.0
        tif = self.dir / "before.tif"
        _write(tif, data)

        png = raster.render_preview(tif, self.dir / "before.png", mode="gray")
        self.assertTrue(png.exists())

        arr = np.array(Image.open(png))
        self.assertEqual(arr.shape, (4, 6, 4))
        self.assertTrue((arr[0, :, 3] == 255).all())
        self.assertTrue((arr[2, :, 3] == 0).all(), "nodata row must be transparent")
        self.assertLess(int(arr[0, 0, 0]), int(arr[3, 0, 0]))
        self.assertTrue((arr[3, :, 0] == arr[3, :, 1]).all())
        self.assertTrue((arr[3, :, 1] == arr[3, :, 2]).all())

    def test_mask_preview_is_transparent_red(self):
        data = np.zeros((4, 4), dtype="uint8")
        data[:2] = 1
        tif = self.dir / "flood_mask.tif"
        _write(tif, data)

        png = raster.render_preview(tif, self.dir / "flood_mask.png", mode="mask")
        arr = np.array(Image.open(png))
        self.assertTrue((arr[0, :, 3] > 0).all(), "flooded pixels visible")
        self.assertTrue((arr[3, :, 3] == 0).all(), "unflooded pixels hidden")
        self.assertTrue((arr[0, :, 0] > arr[0, :, 1]).all(), "flood colour is red")

    def test_second_call_serves_the_cache(self):
        tif = self.dir / "before.tif"
        _write(tif, np.full((4, 4), 250.0, dtype="float32"))
        first = raster.render_preview(tif, self.dir / "before.png", mode="gray")
        stamp = first.stat().st_mtime_ns
        second = raster.render_preview(tif, self.dir / "before.png", mode="gray")
        self.assertEqual(first, second)
        self.assertEqual(second.stat().st_mtime_ns, stamp)

    def test_fully_nodata_scene_raises(self):
        tif = self.dir / "empty.tif"
        _write(tif, np.zeros((4, 4), dtype="float32"))
        with self.assertRaises(raster.RasterAnalysisError):
            raster.render_preview(tif, self.dir / "empty.png", mode="gray")


if __name__ == "__main__":
    unittest.main()
