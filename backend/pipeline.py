from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor, wait as futures_wait
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend import config, raster, storage
from backend.clients.cdse import (
    CDSEClient,
    _datetime_of,
    _mode_of,
    _normalise_track,
    _polarizations_of,
)
from backend.clients import osm as osm_client

RASTER_NAMES = ("before.tif", "after.tif", "flood_mask.tif")

OSM_LAYER_NAMES = osm_client.OSM_LAYERS


def _midnight_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def parse_date(value: Optional[str]) -> date:
    if not value:
        return datetime.now(timezone.utc).date()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid date {value!r}, expected YYYY-MM-DD") from exc


def build_windows(
    event_date: date,
) -> Tuple[Tuple[datetime, datetime], Tuple[datetime, datetime]]:
    """Before/after search windows anchored to the event date.

    The before window covers [event - max_before_days, event): the newest
    usable reference is the day before the event, because an acquisition on
    the event day is ambiguous — the flood may already have reached the
    swath. The after window covers [event + 1, event + max_after_days]; the
    two are disjoint by construction, so the old overlap-shifting loop is
    unnecessary. Both limits come from config so they can be widened without
    touching code.
    """
    settings = config.settings
    before_start = _midnight_utc(event_date - timedelta(days=settings.max_before_days))
    before_end = _midnight_utc(event_date)
    after_start = before_end + timedelta(days=1)
    after_end = before_end + timedelta(days=settings.max_after_days + 1)
    return (before_start, before_end), (after_start, after_end)


def _fmt_window(window: Tuple[datetime, datetime]) -> str:
    """Human range for a (start, end-exclusive) window: last covered day last."""
    start, end = window
    return f"{start.date()} -> {(end - timedelta(days=1)).date()}"


def _orbit_sort_key(track: str):
    return (0, int(track)) if track.lstrip("-").isdigit() else (1, track)


def _orbits_text(features: Sequence[Dict[str, Any]]) -> str:
    orbits = {t for t in (_normalise_track(f) for f in features) if t}
    if not orbits:
        return "none"
    return ", ".join(sorted(orbits, key=_orbit_sort_key))


def _timestamp(feature: Dict[str, Any]) -> datetime:
    raw = _datetime_of(feature).replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _mode_pol_compatible(
    before_feature: Dict[str, Any], after_feature: Dict[str, Any]
) -> bool:
    """Soft compatibility: only compare what both scenes actually record."""
    b_mode, a_mode = _mode_of(before_feature), _mode_of(after_feature)
    if b_mode and a_mode and b_mode != a_mode:
        return False
    b_pol, a_pol = _polarizations_of(before_feature), _polarizations_of(after_feature)
    if b_pol and a_pol and b_pol != a_pol:
        return False
    return True


def _no_pair_error(
    event_date: date,
    before_window: Tuple[datetime, datetime],
    after_window: Tuple[datetime, datetime],
    before_features: Sequence[Dict[str, Any]],
    after_features: Sequence[Dict[str, Any]],
) -> RuntimeError:
    before_tracks = {t for t in (_normalise_track(f) for f in before_features) if t}
    after_tracks = {t for t in (_normalise_track(f) for f in after_features) if t}
    lines = [
        "No compatible Sentinel-1 before/after pair found.",
        "",
        f"Event date: {event_date.isoformat()}",
        f"Before search: {_fmt_window(before_window)}",
        f"After search: {_fmt_window(after_window)}",
        "",
        f"Before orbits found: {_orbits_text(before_features)}",
        f"After orbits found: {_orbits_text(after_features)}",
        "",
        (
            "No common relative orbit was found."
            if before_tracks.isdisjoint(after_tracks)
            else "No pair survived the compatibility ranking."
        ),
        (
            f"Search window: before {config.settings.max_before_days} days, "
            f"after {config.settings.max_after_days} days."
        ),
    ]
    if after_window[0].date() > datetime.now(timezone.utc).date():
        lines.append(
            "The after window has not started yet; no post-event acquisition exists."
        )
    lines.append("Try expanding the search window or changing the AOI.")
    return RuntimeError("\n".join(lines))


def choose_pair(
    before_features: Sequence[Dict[str, Any]],
    after_features: Sequence[Dict[str, Any]],
    *,
    event_date: date,
    before_window: Optional[Tuple[datetime, datetime]] = None,
    after_window: Optional[Tuple[datetime, datetime]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Rank every same-orbit before/after pair and return the best one.

    Cross-track Sentinel-1 products are georeferenced to different frames:
    the reprojection in ``raster.fetch_scene`` aligns the grid, but neither
    the incidence angle nor the radiometric calibration, so a cross-orbit
    difference map would be dominated by that mismatch rather than by water.
    Same relative orbit is therefore mandatory — never relaxed in favour of
    date proximity. The ranking, best first:

    1. same relative orbit (hard filter);
    2. same acquisition mode and polarisation when both scenes record them;
    3. smallest max(event - before, after - event) — closest to the event;
    4. smallest total span (after - before) — tightest bracket;
    5. earliest timestamps, only to stay deterministic for slice products.

    If nothing matches on mode/pol, the best same-orbit pair is still
    returned (tier 2) with the relaxation logged: the orbit match is what
    makes change detection meaningful, the rest is preference.
    """
    if before_window is None or after_window is None:
        before_window, after_window = build_windows(event_date)
    before_sorted = sorted(before_features, key=_timestamp)
    after_sorted = sorted(after_features, key=_timestamp)
    event_ts = _midnight_utc(event_date)

    total_pairs = len(before_sorted) * len(after_sorted)
    same_orbit_pairs = 0
    compatible_pairs = 0
    ranked: List[Tuple[Any, ...]] = []
    for after in after_sorted:
        after_track = _normalise_track(after)
        if not after_track:
            continue
        after_ts = _timestamp(after)
        for before in before_sorted:
            if _normalise_track(before) != after_track:
                continue
            before_ts = _timestamp(before)
            same_orbit_pairs += 1
            tier = 0 if _mode_pol_compatible(before, after) else 1
            if tier == 0:
                compatible_pairs += 1
            max_gap = max(event_ts - before_ts, after_ts - event_ts)
            span = after_ts - before_ts
            ranked.append((tier, max_gap, span, before_ts, after_ts, before, after))

    print(
        f"[cdse] candidates: before={len(before_features)} "
        f"(orbits {_orbits_text(before_features)}) "
        f"after={len(after_features)} (orbits {_orbits_text(after_features)})"
    )
    print(
        f"[cdse] pairs: {total_pairs} total -> {same_orbit_pairs} same-orbit -> "
        f"{compatible_pairs} mode/pol compatible"
    )
    if not ranked:
        raise _no_pair_error(
            event_date, before_window, after_window, before_features, after_features
        )

    ranked.sort(key=lambda r: r[:5])
    tier, max_gap, span, _before_ts, _after_ts, before, after = ranked[0]

    print(f"[cdse] SELECTED event={event_date.isoformat()}")
    print(
        f"[cdse]   BEFORE {_datetime_of(before)} "
        f"orbit={_normalise_track(before)} mode={_mode_of(before) or '?'} "
        f"pol={','.join(_polarizations_of(before) or ()) or '?'}"
    )
    print(
        f"[cdse]   AFTER  {_datetime_of(after)} "
        f"orbit={_normalise_track(after)} mode={_mode_of(after) or '?'} "
        f"pol={','.join(_polarizations_of(after) or ()) or '?'}"
    )
    print(
        f"[cdse]   rank: tier={'mode+pol' if tier == 0 else 'orbit-only (relaxation)'} "
        f"max_gap={max_gap.total_seconds() / 86400:.1f}d "
        f"span={span.total_seconds() / 86400:.1f}d "
        f"considered={total_pairs} pairs "
        f"(candidates: {len(before_sorted)} before, {len(after_sorted)} after)"
    )
    return before, after


def _fetch_one_osm_layer(
    bbox: Sequence[float], layer: str, out_path, stagger_s: float = 0.0
) -> Dict[str, Any]:
    """Fetch one OSM layer to disk. Never raises: a dead Overpass instance
    must not be able to fail an analysis whose Sentinel path is healthy.

    ``stagger_s`` offsets the sibling layer threads so they do not hit the
    same Overpass mirror in one burst — the public instances rate-limit that.
    """
    if stagger_s:
        time.sleep(stagger_s)
    try:
        fc = osm_client.fetch_layer(bbox, layer)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(fc, f, separators=(",", ":"))
        return {"status": "ok", "features": len(fc["features"]), "file": out_path.name}
    except Exception as exc:
        print(f"[osm] {layer} unavailable: {exc}")
        return {"status": "error", "error": str(exc)}


def start_osm_fetch(
    bbox: Sequence[float], out_dir
) -> Tuple[ThreadPoolExecutor, Dict[str, "Future[Dict[str, Any]]"]]:
    """Kick off all OSM layers concurrently with the Sentinel path.

    The two are deliberately independent: this returns immediately with
    futures the pipeline joins later, so Overpass latency only overlaps with
    the CDSE search/download instead of adding to it.
    """
    executor = ThreadPoolExecutor(max_workers=len(OSM_LAYER_NAMES))
    futures = {
        layer: executor.submit(
            _fetch_one_osm_layer,
            bbox,
            layer,
            out_dir / f"osm_{layer}.geojson",
            index * 2.0,
        )
        for index, layer in enumerate(OSM_LAYER_NAMES)
    }
    return executor, futures


def collect_osm_fetch(
    pending: Tuple[ThreadPoolExecutor, Dict[str, "Future[Dict[str, Any]]"]],
    timeout_s: float,
) -> Dict[str, Dict[str, Any]]:
    """Join the OSM futures with a hard deadline; every layer reports a record.

    A future that misses the deadline is recorded as an error and abandoned
    (its own httpx timeout still bounds the thread), so completion is never
    held hostage by Overpass.
    """
    executor, futures = pending
    status: Dict[str, Dict[str, Any]] = {}
    try:
        done, not_done = futures_wait(futures.values(), timeout=timeout_s)
        del done
        for layer, fut in futures.items():
            if fut in not_done:
                status[layer] = {"status": "error", "error": f"timed out after {timeout_s:.0f}s"}
                continue
            try:
                status[layer] = fut.result()
            except Exception as exc:  # belt and braces: _fetch_one swallows already
                status[layer] = {"status": "error", "error": str(exc)}
    finally:
        executor.shutdown(wait=False)
    return status


def run_analysis(
    analysis_id: str,
    bbox: Sequence[float],
    date_str: Optional[str],
    threshold_db: Optional[float] = None,
    after_percentile: Optional[float] = None,
) -> Dict[str, Any]:
    """Full pipeline: select -> download -> compare -> store."""
    st = storage.AnalysisState.load(analysis_id)
    bbox = [float(v) for v in bbox]
    threshold_db = (
        config.settings.flood_threshold_db if threshold_db is None else threshold_db
    )
    after_percentile = (
        config.settings.flood_after_percentile
        if after_percentile is None
        else after_percentile
    )
    event_date = parse_date(date_str)
    client = CDSEClient()

    try:
        try:
            osm_pending = start_osm_fetch(bbox, st.path)
        except Exception as exc:
            print(f"[osm] could not start fetch: {exc}")
            osm_pending = None

        st.set_phase("selecting")
        ref_win, after_win = build_windows(event_date)
        print(
            f"[cdse] event date={event_date.isoformat()} "
            f"before={_fmt_window(ref_win)} ({config.settings.max_before_days}d) "
            f"after={_fmt_window(after_win)} ({config.settings.max_after_days}d)"
        )

        before_features = client.search(bbox, *ref_win)
        after_features = client.search(bbox, *after_win)
        before_feature, after_feature = choose_pair(
            before_features,
            after_features,
            event_date=event_date,
            before_window=ref_win,
            after_window=after_win,
        )

        before_acq = client.describe(before_feature)
        after_acq = client.describe(after_feature)
        st.update(before=before_acq, after=after_acq)

        st.set_phase("submitting_jobs")
        before_scene = raster.fetch_scene(
            client, before_feature, bbox, "before", st.path / "before.tif"
        )

        st.set_phase("downloading")
        after_scene = raster.fetch_scene(
            client, after_feature, bbox, "after", st.path / "after.tif"
        )

        results = raster.analyze_pair(
            before_scene.path,
            after_scene.path,
            threshold_db=threshold_db,
            after_percentile=after_percentile,
            mask_out_path=st.path / "flood_mask.tif",
        )
        results["aoi"] = {"bbox": bbox, "date": event_date.isoformat()}
        results["acquisitions"] = {"before": before_acq, "after": after_acq}
        results["rasters"] = {
            "before": "before.tif",
            "after": "after.tif",
            "floodMask": "flood_mask.tif",
        }

        if osm_pending is not None:
            osm_records = collect_osm_fetch(
                osm_pending, config.settings.osm_timeout_s + 15.0
            )
        else:
            osm_records = {
                layer: {"status": "error", "error": "fetch not started"}
                for layer in OSM_LAYER_NAMES
            }
        results["osm"] = osm_records

        # Results land before the phase flips, so a poller that sees "completed"
        # is guaranteed to also see the payload it is about to render.
        st.update(results=results, rasters=results["rasters"], osm=osm_records)
        st.set_phase("completed")
        return results
    except Exception as exc:
        st.set_error(str(exc))
        traceback.print_exc()
        raise


def summarize(results: Dict[str, Any], elapsed_s: float) -> None:
    raster.console_report(results, elapsed_s)
    print(
        f"[flood] AOI {results['aoi']['bbox']} "
        f"date={results['aoi']['date']}"
    )