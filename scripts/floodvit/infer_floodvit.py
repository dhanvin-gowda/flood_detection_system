"""Feed a processed Sentinel-1 before/after pair to FloodViT and run one inference.

Connects the existing Rasterio/NumPy preprocessing (data/<uuid>/before.tif +
after.tif written by backend.pipeline/raster) to the pickled FloodViT model,
then converts the prediction into a binary flood mask GeoTIFF
(data/<uuid>/flood_mask_floodvit.tif) on the scenes' exact grid.

Run from repo root:  python scripts/floodvit/infer_floodvit.py
Optional args:       python scripts/floodvit/infer_floodvit.py <analysis_dir> [checkpoint]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
sys.path.insert(0, str(VENDOR_DIR))
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import rasterio
import torch
import torch.nn.functional as F

from backend.raster import _dn_to_db

DEFAULT_CHECKPOINT = Path(r"C:\Users\lenovo\Downloads\(Copy) floodvit.pt")

# Channel order produced by KuroSiwo's segmentation_trainer:
#   image (post_event) -> pre_event_1 -> pre_event_2, each event (vv, vh).
# fetch_scene only stores the VV band, so the before scene fills both pre slots
# and VV is duplicated into the VH slots (placeholder until VH is stored).
EVENT_ORDER = ("after", "before", "before")

# KuroSiwo label set (see Orion-AI-Lab/KuroSiwo training/segmentation_trainer.py
# CLASS_LABELS): the head emits 3 classes — 0 No water, 1 Permanent Waters,
# 2 Floods. Class 3 (Invalid pixels) exists only in the training labels, so it
# never appears in the prediction. Flood extent = class 2 alone: water that was
# not there before the event, matching the semantics of the classical
# flood_mask.tif (permanent water bodies are excluded).
FLOOD_CLASS = 2


def find_analysis_dir() -> Path:
    """First data/<uuid>/ that contains both before.tif and after.tif."""
    data_root = REPO_ROOT / "data"
    for d in sorted(p for p in data_root.iterdir() if p.is_dir()):
        if (d / "before.tif").is_file() and (d / "after.tif").is_file():
            return d
    raise SystemExit("no analysis dir with before.tif + after.tif found under data/")


def read_scene(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a stored scene; returns (dn float32 array, valid mask)."""
    with rasterio.open(path) as src:
        dn = src.read(1).astype("float32")
    return dn, dn > 0


def stretch_db(dn: np.ndarray, valid: np.ndarray, out_max: float) -> np.ndarray:
    """dB values stretched onto [0, out_max] by the 2-98 percentile of valid data.

    Mirrors the classical pipeline (_dn_to_db) and render_preview's percentile
    stretch, rescaled onto the model's clamp_input range (0.15).
    """
    db = _dn_to_db(dn)
    lo, hi = np.percentile(db[valid], (2.0, 98.0))
    if hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((db - lo) / (hi - lo), 0.0, 1.0) * out_max
    return np.where(valid, scaled, 0.0).astype("float32")


def build_input(before_path: Path, after_path: Path, configs: dict) -> torch.Tensor:
    """(1, 6, 224, 224) tensor: [post vv/vh, pre1 vv/vh, pre2 vv/vh]."""
    scenes: dict[str, np.ndarray] = {}
    for name in dict.fromkeys(EVENT_ORDER):  # unique, order-preserving
        dn, valid = read_scene(before_path if name == "before" else after_path)
        if not valid.any():
            raise SystemExit(f"{name}.tif is entirely nodata")
        clamp = float(configs.get("clamp_input") or 0.15)
        scenes[name] = stretch_db(dn, valid, clamp)

    mean_vv, std_vv = float(configs["data_mean"][0]), float(configs["data_std"][0])
    events = []
    for name in EVENT_ORDER:
        vv = scenes[name]
        pair = np.stack([vv, vv])  # vh slot duplicates vv (VV-only storage)
        norm = (pair - mean_vv) / std_vv
        events.append(torch.from_numpy(norm))

    image = torch.cat(events, dim=0)  # (6, H, W)
    image = F.interpolate(
        image[None], size=(224, 224), mode="bilinear", align_corners=False
    )
    return image  # (1, 6, 224, 224)


def source_grid(before_path: Path, after_path: Path) -> tuple[dict, int, int]:
    """Reference GeoTIFF profile + grid size shared by the stored scene pair."""
    with rasterio.open(before_path) as b_src, rasterio.open(after_path) as a_src:
        if (b_src.height, b_src.width) != (a_src.height, a_src.width):
            raise SystemExit(
                f"grid mismatch: {before_path.name} "
                f"{b_src.height}x{b_src.width} vs {after_path.name} "
                f"{a_src.height}x{a_src.width}"
            )
        if str(b_src.crs) != str(a_src.crs):
            raise SystemExit(f"CRS mismatch: {b_src.crs} vs {a_src.crs}")
        if not b_src.transform.almost_equals(a_src.transform, precision=1e-9):
            raise SystemExit("transform mismatch between scenes")
        profile = b_src.profile.copy()
        height, width = b_src.height, b_src.width
    return profile, height, width


def logits_to_mask(
    out: torch.Tensor, height: int, width: int, flood_class: int = FLOOD_CLASS
) -> np.ndarray:
    """(1, 3, 224, 224) logits -> (height, width) uint8 {0,1} flood mask.

    Logits are bilinearly resized to the source grid *before* argmax (the
    reference semantic-segmentation eval recipe): upscaling hard labels with
    nearest neighbour would turn each 224x224 cell into ~11x11 blocky steps on
    the ~2500px analysis grid.
    """
    if out.ndim != 4 or out.shape[0] != 1:
        raise SystemExit(f"unexpected model output shape: {tuple(out.shape)}")
    if out.shape[1] <= flood_class:
        raise SystemExit(
            f"model emitted {out.shape[1]} classes, need index {flood_class}"
        )
    logits = F.interpolate(
        out, size=(height, width), mode="bilinear", align_corners=False
    )
    pred = logits.argmax(dim=1)[0]  # (height, width)
    return (pred == flood_class).to(torch.uint8).numpy()


def write_flood_mask(
    mask: np.ndarray, before_path: Path, after_path: Path, out_path: Path
) -> dict:
    """Save the binary mask as a GeoTIFF on the scenes' exact grid.

    CRS, affine transform, resolution and extent are copied verbatim from the
    reference profile (before.tif, which after.tif is verified to share), so
    the mask overlays the stored scenes pixel for pixel. Nodata pixels in
    either scene are forced to 0. The layout mirrors the classical
    flood_mask.tif written by backend.raster.analyze_pair: uint8, nodata 0,
    deflate-compressed.
    """
    profile, height, width = source_grid(before_path, after_path)
    if mask.shape != (height, width):
        raise SystemExit(
            f"mask shape {mask.shape} does not match grid {height}x{width}"
        )

    with rasterio.open(before_path) as b_src, rasterio.open(after_path) as a_src:
        valid = (b_src.read(1) > 0) & (a_src.read(1) > 0)

    flood = (mask > 0) & valid
    out = flood.astype("uint8")

    profile.update(
        driver="GTiff", height=height, width=width, count=1, dtype="uint8",
        nodata=0, compress="deflate",
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(out, 1)
    return {
        "path": out_path,
        "crs": str(profile["crs"]),
        "transform": profile["transform"],
        "width": width,
        "height": height,
        "floodPixels": int(np.count_nonzero(out)),
        "validPixels": int(np.count_nonzero(valid)),
        "totalPixels": int(valid.size),
    }


def main() -> int:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else find_analysis_dir()
    ckpt = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CHECKPOINT
    before_path, after_path = data_dir / "before.tif", data_dir / "after.tif"
    for p in (before_path, after_path, ckpt):
        if not p.is_file():
            print(f"FAIL: file not found: {p}")
            return 1

    print(f"data       : {data_dir}")
    print(f"checkpoint : {ckpt} ({ckpt.stat().st_size / 1e6:.1f} MB)")

    t0 = time.perf_counter()
    model = torch.load(ckpt, map_location="cpu", weights_only=False)
    model.eval()
    print(f"model load : {time.perf_counter() - t0:.1f}s")

    x = build_input(before_path, after_path, model.configs)

    t0 = time.perf_counter()
    with torch.inference_mode():
        out = model(x)
    print(f"inference  : {time.perf_counter() - t0:.1f}s")

    print()
    print(f"input shape          : {tuple(x.shape)}")
    print(f"model output shape   : {tuple(out.shape)}")
    print(f"output data type     : {out.dtype}")
    print(f"prediction min/max   : {out.min().item():.6f} / {out.max().item():.6f}")

    profile, height, width = source_grid(before_path, after_path)
    mask = logits_to_mask(out, height, width)
    mask_path = data_dir / "flood_mask_floodvit.tif"
    info = write_flood_mask(mask, before_path, after_path, mask_path)

    with rasterio.open(before_path) as ref:
        ref_crs, ref_transform = str(ref.crs), ref.transform
    grid_ok = (
        info["crs"] == ref_crs
        and info["transform"].almost_equals(ref_transform, precision=1e-12)
        and (info["width"], info["height"]) == (ref.width, ref.height)
    )

    print()
    print(f"flood rule           : argmax(logits) == {FLOOD_CLASS} "
          f"(KuroSiwo 'Floods'; 1=permanent water is excluded)")
    print(f"grid                 : {width}x{height} crs={info['crs']}")
    print(f"grid matches {before_path.name:<13}: {'yes' if grid_ok else 'NO'}")
    print(f"valid pixels         : {info['validPixels']:,} / {info['totalPixels']:,}")
    print(f"flood pixels         : {info['floodPixels']:,} "
          f"({100.0 * info['floodPixels'] / max(info['validPixels'], 1):.2f}% of valid)")
    print(f"mask written         : {mask_path}")
    print("\nOK: FloodViT inference completed.")
    if not grid_ok:
        print("FAIL: output grid does not match the reference scene")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
