"""Polygonize the FloodViT flood mask into georeferenced GeoJSON polygons.

Reads data/<uuid>/flood_mask_floodvit.tif (written by infer_floodvit.py),
drops connected flood regions smaller than a noise floor, and converts the
remaining regions into polygons using the raster's own affine transform and
CRS, so every vertex lands in the right place on Earth.

Output: data/<uuid>/flood_polygons.geojson (FeatureCollection, EPSG:4326,
one Polygon/MultiPolygon per connected flood region, staircase outlines that
follow the mask pixels exactly).

Run from repo root:  python scripts/floodvit/mask_to_polygons.py
Optional args:       python scripts/floodvit/mask_to_polygons.py <mask_or_dir> [--min-area-m2 N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import rasterio
from rasterio import features
from scipy import ndimage

from backend.raster import _pixel_area_m2

# Connected regions below this ground area are treated as noise speckle and
# removed before polygonizing (~9 px at the ~111 m2/pixel Sentinel-1 grid).
DEFAULT_MIN_AREA_M2 = 1000.0


def find_mask_path() -> Path:
    """First data/<uuid>/flood_mask_floodvit.tif."""
    data_root = REPO_ROOT / "data"
    for d in sorted(p for p in data_root.iterdir() if p.is_dir()):
        candidate = d / "flood_mask_floodvit.tif"
        if candidate.is_file():
            return candidate
    raise SystemExit("no data/<uuid>/flood_mask_floodvit.tif found under data/")


def resolve_mask_path(arg: str | None) -> Path:
    """Accept a mask GeoTIFF path or an analysis dir; default to the first one."""
    if arg is None:
        return find_mask_path()
    p = Path(arg)
    if p.is_dir():
        p = p / "flood_mask_floodvit.tif"
    if not p.is_file():
        raise SystemExit(f"file not found: {p}")
    return p


def drop_small_regions(
    mask: np.ndarray, pixel_area_m2: float, min_area_m2: float
) -> tuple[np.ndarray, int, int]:
    """Zero out 8-connected flood components smaller than min_area_m2.

    Returns (cleaned mask, raw component count, kept component count).
    """
    labels, n = ndimage.label(mask > 0, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return mask.astype("uint8"), 0, 0
    min_pixels = max(1, int(np.ceil(min_area_m2 / pixel_area_m2)))
    counts = np.bincount(labels.ravel())
    counts[0] = 0  # background
    keep = counts >= min_pixels
    keep[0] = False
    cleaned = keep[labels].astype("uint8")
    return cleaned, int(n), int(np.count_nonzero(keep))


def polygonize(
    mask: np.ndarray, transform, crs
) -> list[tuple[dict, float]]:
    """(geometry, value) pairs for the nonzero regions, in CRS coordinates.

    rasterio.features.shapes runs GDAL's polygonizer with the raster's own
    affine transform, so the output GeoJSON geometry carries the correct
    longitude/latitude already — no reprojection, no shapely needed.
    """
    out: list[tuple[dict, float]] = []
    if not mask.any():
        return out
    for geom, value in features.shapes(
        mask, mask=mask.astype(bool), transform=transform, connectivity=8
    ):
        out.append((geom, float(value)))
    return out


def geometry_area_m2(geom: dict, pixel_area_crs2: float, pixel_area_m2: float) -> float:
    """Ground area of a pixel-aligned polygon in m2.

    Geometry coordinates are in CRS units; for this project's EPSG:4326 grids
    that is degrees. A pixel-aligned outline's area in CRS units divided by
    one pixel's area in CRS units is exactly its pixel count, which we then
    scale by the pixel's ground area (cos-latitude corrected by
    backend.raster._pixel_area_m2, same as the dashboard stats).
    """
    return _ring_area(geom) / pixel_area_crs2 * pixel_area_m2


def _ring_area(geom: dict) -> float:
    """Absolute area of a (Multi)Polygon in CRS units (shoelace, holes subtracted)."""
    if geom["type"] == "Polygon":
        rings = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        rings = geom["coordinates"]
    else:
        raise ValueError(f"unsupported geometry type: {geom['type']}")
    total = 0.0
    for polygon in rings:
        for i, ring in enumerate(polygon):
            a = abs(_shoelace(ring))
            total += a if i == 0 else -a  # first ring = exterior, rest = holes
    return total


def _shoelace(ring: list) -> float:
    x = [p[0] for p in ring]
    y = [p[1] for p in ring]
    return 0.5 * sum(
        x[i] * y[i + 1] - x[i + 1] * y[i] for i in range(len(ring) - 1)
    )


def main() -> int:
    argv = sys.argv[1:]
    min_area_m2 = DEFAULT_MIN_AREA_M2
    if "--min-area-m2" in argv:
        i = argv.index("--min-area-m2")
        try:
            min_area_m2 = float(argv[i + 1])
        except (IndexError, ValueError):
            print("FAIL: --min-area-m2 requires a number")
            return 1
        del argv[i : i + 2]
    mask_path = resolve_mask_path(argv[0] if argv else None)

    t0 = time.perf_counter()
    with rasterio.open(mask_path) as src:
        mask = src.read(1)
        transform, crs = src.transform, src.crs
        bounds, res = src.bounds, src.res
        height, width = src.height, src.width

    pixel_area_m2 = _pixel_area_m2(transform, bounds, crs)
    pixel_area_crs2 = abs(res[0] * res[1])

    raw_flood_px = int(np.count_nonzero(mask))
    cleaned, n_raw, n_kept = drop_small_regions(mask, pixel_area_m2, min_area_m2)
    kept_flood_px = int(np.count_nonzero(cleaned))

    shapes_list = polygonize(cleaned, transform, crs)

    features_out = []
    total_area_m2 = 0.0
    for geom, _value in shapes_list:
        area_m2 = geometry_area_m2(geom, pixel_area_crs2, pixel_area_m2)
        pixel_count = int(round(area_m2 / pixel_area_m2))
        total_area_m2 += area_m2
        features_out.append(
            {
                "type": "Feature",
                "properties": {
                    "pixelCount": pixel_count,
                    "areaM2": round(area_m2, 2),
                    "areaKm2": round(area_m2 / 1e6, 6),
                },
                "geometry": geom,
            }
        )

    fc = {"type": "FeatureCollection", "features": features_out}
    out_path = mask_path.with_name("flood_polygons.geojson")
    out_path.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")

    # Cross-check: polygons must cover exactly the pixels that survived the
    # noise filter (pixel-aligned outlines => areas agree to rounding).
    expected_m2 = kept_flood_px * pixel_area_m2
    drift = abs(total_area_m2 - expected_m2) / max(expected_m2, 1.0)

    print(f"mask                 : {mask_path}")
    print(f"grid                 : {width}x{height} crs={crs}")
    print(f"pixel area           : {pixel_area_m2:.2f} m2")
    print(f"noise floor          : {min_area_m2:.0f} m2 "
          f"(~{int(np.ceil(min_area_m2 / pixel_area_m2))} px)")
    print(f"regions raw/kept     : {n_raw} / {n_kept} "
          f"({n_raw - n_kept} removed as noise)")
    print(f"pixels raw/kept      : {raw_flood_px:,} / {kept_flood_px:,}")
    print(f"polygons written     : {len(features_out)}")
    print(f"total flood area     : {total_area_m2 / 1e6:.4f} km2 "
          f"({total_area_m2:,.0f} m2)")
    print(f"area cross-check     : expected {expected_m2 / 1e6:.4f} km2, "
          f"drift {drift:.6%}")
    print(f"geojson written      : {out_path}")
    print(f"elapsed              : {time.perf_counter() - t0:.2f}s")

    if drift > 1e-4:
        print("FAIL: polygon area does not match the cleaned mask")
        return 1
    print("\nOK: flood mask polygonized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
