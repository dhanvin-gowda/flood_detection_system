from __future__ import annotations

import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend import config, raster, storage
from backend.clients.cdse import CDSEClient, _datetime_of, _normalise_track

# Sentinel-1 IW revisits every 12 days on a given track, so a 7-day window is
# wide enough to catch one acquisition in most cases while staying tight enough
# to keep the reference scene close to the event date.
WINDOW_DAYS = 7

# Written by fetch_scene/analyze_pair and served by the raster download route.
RASTER_NAMES = ("before.tif", "after.tif", "flood_mask.tif")


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
    event_date: date, today: Optional[date] = None
) -> Tuple[Tuple[datetime, datetime], Tuple[datetime, datetime]]:
    """Reference and after windows, guaranteed not to overlap.

    The reference window precedes the event date. If the user picks a date close
    to today the two windows would overlap, which makes change detection
    meaningless because both scenes would be the same acquisition, so the
    reference is pushed backwards until it clears the after window.
    """
    today = today or datetime.now(timezone.utc).date()
    after_end = _midnight_utc(today) + timedelta(days=1)
    after_start = after_end - timedelta(days=WINDOW_DAYS)

    ref_end = _midnight_utc(event_date) + timedelta(days=1)
    ref_start = ref_end - timedelta(days=WINDOW_DAYS)
    while ref_end > after_start:
        ref_end -= timedelta(days=WINDOW_DAYS)
        ref_start = ref_end - timedelta(days=WINDOW_DAYS)

    return (ref_start, ref_end), (after_start, after_end)


def choose_pair(
    before_features: Sequence[Dict[str, Any]],
    after_features: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Pick scenes sharing a relative orbit; geometry must match to compare.

    S1 products from different tracks are georeferenced to different frames, so
    a cross-track pair cannot be compared pixel by pixel: the reprojection in
    ``raster.fetch_scene`` aligns the grid, but not the incidence angle or the
    radiometric calibration, and the resulting difference map would be dominated
    by that mismatch rather than by water. An empty window or a missing shared
    orbit is therefore a hard failure, not something to paper over with a
    fallback.
    """
    if not before_features or not after_features:
        raise RuntimeError(
            "no Sentinel-1 acquisitions on one side of the comparison; widen the "
            "date range or move the AOI"
        )

    before_tracks = {_normalise_track(f) for f in before_features}
    after_tracks = {_normalise_track(f) for f in after_features}
    shared = {t for t in before_tracks & after_tracks if t is not None}

    if not shared:
        raise RuntimeError(
            "no shared Sentinel-1 relative orbit between the reference and after "
            f"windows (before={sorted(map(str, before_tracks))}, "
            f"after={sorted(map(str, after_tracks))}); change the event date or "
            "move the AOI so both windows fall on the same track"
        )

    track = sorted(shared)[0]
    if len(shared) > 1:
        print(f"[cdse] {len(shared)} shared orbits {sorted(shared)}; using {track}")
    b_candidates = [f for f in before_features if _normalise_track(f) == track]
    a_candidates = [f for f in after_features if _normalise_track(f) == track]
    print(f"[cdse] shared relative orbit {track}; using same-track pair")

    return b_candidates[-1], a_candidates[-1]


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
        st.set_phase("selecting")
        ref_win, after_win = build_windows(event_date)
        print(
            f"[cdse] event date={event_date.isoformat()} "
            f"reference={ref_win[0].date()}..{ref_win[1].date()} "
            f"after={after_win[0].date()}..{after_win[1].date()}"
        )

        before_features = client.search(bbox, *ref_win)
        after_features = client.search(bbox, *after_win)
        if not before_features:
            raise RuntimeError(
                "no Sentinel-1 acquisition in the reference window "
                f"{ref_win[0].date()}..{ref_win[1].date()}"
            )
        if not after_features:
            raise RuntimeError(
                "no Sentinel-1 acquisition in the after window "
                f"{after_win[0].date()}..{after_win[1].date()}"
            )
        before_feature, after_feature = choose_pair(before_features, after_features)

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

        # Results land before the phase flips, so a poller that sees "completed"
        # is guaranteed to also see the payload it is about to render.
        st.update(results=results, rasters=results["rasters"])
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