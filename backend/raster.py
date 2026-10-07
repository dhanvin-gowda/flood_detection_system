from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
import rasterio
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import Resampling, reproject
from rasterio.windows import Window

from backend.clients.cdse import CDSEClient

# CDSE publishes S1 GRDH VV/VH as uint16 amplitude (DN), uncalibrated, so the
# only meaningful comparison is a log-amplitude ratio between two scenes:
#   diff_db = 20 * log10(after) - 20 * log10(before)
# Flooding smooths the surface and drives that ratio down by several dB.
DEFAULT_THRESHOLD_DB = -3.0

# DN is uncalibrated, so an absolute dB gate is not portable across products.
# Instead of hard-coding one, the classifier gates on the after-scene value
# being darker than a percentile of its own valid distribution, which suppresses
# speckle-driven false positives without depending on absolute calibration.
DEFAULT_AFTER_PERCENTILE = 35.0

_EPS = 1e-6


class RasterAnalysisError(RuntimeError):
    pass


@dataclass
class Scene:
    label: str
    path: Path
    feature: Dict[str, Any]


def _dn_to_db(values: np.ndarray) -> np.ndarray:
    """uint16 amplitude DN -> 20*log10 dB."""
    return 20.0 * np.log10(np.maximum(values, _EPS))


def _window_for(bbox: Sequence[float], transform, width: int, height: int) -> Window:
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    inv = ~transform
    c0, r0 = inv * (minx, maxy)
    c1, r1 = inv * (maxx, miny)
    win = Window(col_off=c0, row_off=r0, width=c1 - c0, height=r1 - r0)
    return win.round_offsets().round_lengths()


# CDSE's OData download backend fails as often as it works (502/503/504 during
# incidents, connection resets in between), and GDAL's own GDAL_HTTP_MAX_RETRY
# only respreads attempts over ~2 seconds. These bound an application-level
# retry above it: enough to ride out a blip, short enough that a real outage
# still reports quickly.
FETCH_ATTEMPTS = 4
FETCH_BACKOFF_S = (2.0, 5.0, 10.0)

# Statuses GDAL/CURL can report that are worth retrying: gateway/rate-limit
# failures on CDSE's side, never a client mistake (401/404 are permanent).
_RETRYABLE_STATUS_RE = re.compile(r"HTTP response code: (\d{3})", re.IGNORECASE)
_RETRYABLE_STATUSES = frozenset({"429", "502", "503", "504"})
_RETRYABLE_PATTERNS = (
    "connection",
    "winerror 10054",
    "winerror 10060",
    "curl error",
    "failed to connect",
)


def _is_retryable_fetch_error(exc: BaseException) -> bool:
    """Transient transport failures only — logic errors never retry."""
    msg = str(exc).lower()
    match = _RETRYABLE_STATUS_RE.search(msg)
    if match and match.group(1) in _RETRYABLE_STATUSES:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return any(pattern in msg for pattern in _RETRYABLE_PATTERNS)


def _friendly_fetch_error(label: str, exc: BaseException) -> BaseException:
    """Translate GDAL/libcurl internals into an actionable dashboard message.

    Returns ``exc`` unchanged when it has no recognised transient signature, so
    real errors (AOI misses the product, entirely-nodata scene) keep their detail.
    """
    msg = str(exc)
    match = _RETRYABLE_STATUS_RE.search(msg)
    if match and match.group(1) in _RETRYABLE_STATUSES:
        return RasterAnalysisError(
            f"{label} scene fetch failed: Copernicus download service "
            f"temporarily unavailable (HTTP {match.group(1)}). "
            "Try again in a few minutes."
        )
    if _is_retryable_fetch_error(exc):
        return RasterAnalysisError(
            f"{label} scene fetch failed: could not reach the Copernicus "
            "download service (network error). Try again later."
        )
    return exc


def fetch_scene(
    client: CDSEClient,
    feature: Dict[str, Any],
    bbox: Sequence[float],
    label: str,
    out_path: Path,
    bands: Sequence[str] = ("vv", "vh"),
    resolution_m: float = 10.0,
    max_side: int = 2500,
) -> Scene:
    """Read the AOI out of a remote S1 COG and reproject onto a common grid.

    The source COGs carry no CRS or geotransform tags, so the grid is derived
    from the STAC feature bbox and the target grid is defined once for the pair.
    Both scenes must land on the same grid to be compared pixel by pixel.
    """
    from backend.clients.cdse import size_for_bbox

    href = client.asset_href(feature, bands[0])
    src_bbox = feature.get("bbox")
    if not src_bbox or len(src_bbox) != 4:
        raise RasterAnalysisError(f"feature {feature.get('id')} has no bbox")

    width, height = size_for_bbox(bbox, resolution_m=resolution_m, max_side=max_side)
    out_transform = transform_from_bounds(
        float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]), width, height
    )

    def _read_remote_block(token: str):
        """One full remote read: open the COG, locate the AOI window, load it.

        Kept as a closure so the retry loop below can re-run the whole unit
        with a fresh token — a partially-opened dataset must never be reused.
        """
        with rasterio.Env(
            GDAL_HTTP_HEADERS=f"Authorization: {token}",
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            GDAL_HTTP_MAX_RETRY="3",
            GDAL_HTTP_RETRY_DELAY="2",
        ):
            with rasterio.open("/vsicurl/" + href) as src:
                src_transform = transform_from_bounds(
                    float(src_bbox[0]), float(src_bbox[1]),
                    float(src_bbox[2]), float(src_bbox[3]),
                    src.width, src.height,
                )
                win = _window_for(bbox, src_transform, src.width, src.height)
                if win.width <= 0 or win.height <= 0:
                    raise RasterAnalysisError(
                        f"AOI does not intersect product {feature.get('id')}"
                    )
                block = src.read(1, window=win)
                block_transform = src_transform * rasterio.Affine.translation(
                    win.col_off, win.row_off
                )
        return block, block_transform

    for attempt in range(1, FETCH_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(FETCH_BACKOFF_S[attempt - 2])
            # The header is baked into GDAL_HTTP_HEADERS per attempt; a token
            # that expired during the backoff would 401 the next read.
            token = f"Bearer {client._get_token(force=True)}"
        else:
            token = client.auth_header
        try:
            block, block_transform = _read_remote_block(token)
            break
        except Exception as exc:
            if not _is_retryable_fetch_error(exc) or attempt == FETCH_ATTEMPTS:
                friendly = _friendly_fetch_error(label, exc)
                if friendly is exc:
                    raise
                raise friendly from exc
            print(
                f"[sh] {label:<6} fetch attempt {attempt}/{FETCH_ATTEMPTS} "
                f"failed ({exc}) — retrying"
            )

    dst = np.zeros((height, width), dtype="float64")
    reproject(
        source=block.astype("float64"),
        destination=dst,
        src_transform=block_transform,
        src_crs="EPSG:4326",
        dst_transform=out_transform,
        dst_crs="EPSG:4326",
        resampling=Resampling.bilinear,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        out_path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs="EPSG:4326", transform=out_transform,
        nodata=0, compress="deflate",
    ) as dst_file:
        dst_file.write(dst.astype("float32"), 1)

    valid = int(np.count_nonzero(dst))
    print(
        f"[sh] {label:<6} -> {out_path.name}  grid={width}x{height} "
        f"valid={valid:,} ({100 * valid / dst.size:.1f}%)  "
        f"track={feature.get('properties', {}).get('sat:relative_orbit')}"
    )
    if valid == 0:
        raise RasterAnalysisError(
            f"{label} scene is entirely nodata; product may not cover the AOI"
        )
    return Scene(label=label, path=out_path, feature=feature)


def _finite_stats(values: np.ndarray) -> Dict[str, Optional[float]]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {"min": None, "max": None, "mean": None, "std": None}
    return {
        "min": float(np.min(finite)),
        "max": float(np.max(finite)),
        "mean": float(np.mean(finite)),
        "std": float(np.std(finite)),
    }


_M_PER_DEG_LAT = 111_320.0


def _pixel_area_m2(transform, bounds, crs) -> float:
    """Ground area of one pixel in square metres.

    The AOI grid is built in EPSG:4326, so a pixel spans ``transform.a`` degrees
    of longitude by ``transform.e`` degrees of latitude. Converting both with the
    same metres-per-degree factor ignores that a degree of longitude shrinks by
    cos(latitude): at 29N that inflates the area by roughly 15%, which is the
    difference between a plausible and an implausible flood extent. Projected
    coordinates are already metres, so those transforms are used as-is.
    """
    if crs is not None and not crs.is_geographic:
        return abs(transform.a * transform.e)

    lat_mid = max(-90.0, min(90.0, (bounds.bottom + bounds.top) / 2.0))
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(lat_mid))
    return abs(transform.a) * abs(transform.e) * _M_PER_DEG_LAT * m_per_deg_lon


def analyze_pair(
    before_path: Path,
    after_path: Path,
    threshold_db: float = DEFAULT_THRESHOLD_DB,
    after_percentile: float = DEFAULT_AFTER_PERCENTILE,
    mask_out_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compare a reference and present S1 scene and classify flooded pixels."""
    before_path, after_path = Path(before_path), Path(after_path)
    for p, name in ((before_path, "reference"), (after_path, "present")):
        if not p.exists():
            raise RasterAnalysisError(f"missing {name} raster: {p}")

    with rasterio.open(before_path) as b_src, rasterio.open(after_path) as a_src:
        if (b_src.height, b_src.width) != (a_src.height, a_src.width):
            raise RasterAnalysisError(
                f"grid mismatch: {before_path.name} "
                f"{b_src.height}x{b_src.width} vs {after_path.name} "
                f"{a_src.height}x{a_src.width}"
            )
        if str(b_src.crs) != str(a_src.crs):
            raise RasterAnalysisError(f"CRS mismatch: {b_src.crs} vs {a_src.crs}")
        if not b_src.transform.almost_equals(a_src.transform, precision=1e-9):
            raise RasterAnalysisError("transform mismatch between scenes")

        before_dn = b_src.read(1).astype("float64")
        after_dn = a_src.read(1).astype("float64")
        transform = b_src.transform
        bounds = b_src.bounds
        src_crs = b_src.crs
        crs = str(b_src.crs)
        shape = (b_src.height, b_src.width)

    valid = (before_dn > 0) & (after_dn > 0)
    valid_pixels = int(np.count_nonzero(valid))
    if valid_pixels == 0:
        raise RasterAnalysisError(
            "both scenes are entirely nodata; the products may not overlap the AOI"
        )

    before_db = _dn_to_db(before_dn)
    after_db = _dn_to_db(after_dn)
    diff_db = after_db - before_db

    # Gate on the after-scene brightness relative to itself: open water sits in
    # the dark tail, so anything above the percentile is land cover that merely
    # changed radiometrically.
    after_db_valid = np.where(valid, after_db, np.nan)
    cutoff = float(np.nanpercentile(after_db_valid, after_percentile))

    flooded = valid & (diff_db <= threshold_db) & (after_db <= cutoff)
    flood_pixels = int(np.count_nonzero(flooded))

    pixel_area_m2 = _pixel_area_m2(transform, bounds, src_crs)
    flood_area_km2 = flood_pixels * pixel_area_m2 / 1e6
    aoi_area_km2 = valid_pixels * pixel_area_m2 / 1e6

    diff_valid = np.where(valid, diff_db, np.nan)
    results: Dict[str, Any] = {
        "thresholdDb": threshold_db,
        "afterPercentile": after_percentile,
        "afterDbCutoff": round(cutoff, 3),
        "crs": crs,
        "grid": {
            "width": shape[1],
            "height": shape[0],
            "resM": math.sqrt(pixel_area_m2),
            "pixelAreaM2": pixel_area_m2,
        },
        "bounds": {
            "minx": bounds.left, "miny": bounds.bottom,
            "maxx": bounds.right, "maxy": bounds.top,
        },
        "validPixels": valid_pixels,
        "totalPixels": int(valid.size),
        "coveragePct": round(100.0 * valid_pixels / valid.size, 2),
        "aoiAreaKm2": round(aoi_area_km2, 4),
        "floodPixels": flood_pixels,
        "floodAreaKm2": round(flood_area_km2, 4),
        "floodPctOfValid": round(100.0 * flood_pixels / valid_pixels, 2),
        "diffDb": _finite_stats(diff_valid),
        "beforeDb": _finite_stats(np.where(valid, before_db, np.nan)),
        "afterDb": _finite_stats(after_db_valid),
    }

    if mask_out_path is not None:
        mask_out_path = Path(mask_out_path)
        mask_out_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            mask_out_path, "w", driver="GTiff", height=shape[0], width=shape[1],
            count=1, dtype="uint8", crs=crs, transform=transform,
            nodata=0, compress="deflate",
        ) as dst:
            dst.write(flooded.astype("uint8"), 1)
        results["floodMaskPath"] = str(mask_out_path)
        results["floodMaskFile"] = mask_out_path.name

    print(
        f"[raster] crs={crs} shape={shape[1]}x{shape[0]} "
        f"res={math.sqrt(pixel_area_m2):.1f}m "
        f"before={results['beforeDb']['mean']:.2f} dB after={results['afterDb']['mean']:.2f} dB"
    )
    return results


def render_preview(tif_path: Path, png_path: Path, mode: str = "gray") -> Path:
    """Stretch a scene into an 8-bit RGBA PNG that MapLibre can overlay.

    ``mode="gray"`` applies a 2-98 percentile stretch to the valid DN values so
    the SAR scene reads visually regardless of absolute calibration, with
    alpha 0 on nodata so the basemap shows through the AOI edges. ``mode="mask"``
    paints flooded pixels as translucent red for the flood overlay. The PNG is
    cached next to the source raster; the analysis folders are immutable once
    the job completes, so the stretch never needs recomputing.
    """
    from PIL import Image

    tif_path, png_path = Path(tif_path), Path(png_path)
    if png_path.exists():
        return png_path

    with rasterio.open(tif_path) as src:
        data = src.read(1)

    height, width = data.shape
    rgba = np.zeros((height, width, 4), dtype="uint8")

    if mode == "mask":
        flooded = data > 0
        rgba[flooded] = (255, 64, 64, 190)
    else:
        valid = data > 0
        if not np.any(valid):
            raise RasterAnalysisError(f"{tif_path.name} is entirely nodata")
        lo, hi = (float(v) for v in np.percentile(data[valid], (2.0, 98.0)))
        if hi <= lo:
            hi = lo + 1.0
        scaled = np.clip((data.astype("float64") - lo) / (hi - lo), 0.0, 1.0)
        gray = (scaled * 255.0).astype("uint8")
        rgba[..., 0] = gray
        rgba[..., 1] = gray
        rgba[..., 2] = gray
        rgba[..., 3] = np.where(valid, 255, 0)

    png_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(png_path, format="PNG", optimize=True)
    print(f"[raster] preview {tif_path.name} -> {png_path.name} ({width}x{height}, {mode})")
    return png_path


def console_report(results: Dict[str, Any], elapsed_s: float) -> None:
    diff = results["diffDb"]
    print(
        f"[flood] diff_db min={diff['min']:.2f} max={diff['max']:.2f} "
        f"mean={diff['mean']:.2f}"
    )
    print(
        f"[flood] rule: diff_db <= {results['thresholdDb']} dB and "
        f"after_db <= {results['afterDbCutoff']} dB "
        f"(p{results['afterPercentile']:.0f} of after scene)"
    )
    print(
        f"[flood] flood pixels={results['floodPixels']:,} / "
        f"{results['validPixels']:,} valid ({results['floodPctOfValid']:.1f}%) "
        f"-> {results['floodAreaKm2']:.3f} km2"
    )
    print(f"[flood] done in {elapsed_s:.2f}s")