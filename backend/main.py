from __future__ import annotations

import sys

# Windows consoles default to a legacy codepage (cp1252 on Western locales)
# that cannot encode characters the pipeline logs, so print() would raise
# UnicodeEncodeError and fail the analysis job. Force UTF-8 with a lossy
# fallback so any stray non-ASCII output degrades instead of crashing.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import time
import uvicorn

from backend import config, jobs, models, pipeline, raster, storage
from backend.clients import maptiler


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

job_runner = jobs.JobRunner()


@app.get("/health")
@app.get("/backend/health")
def health():
    # No network call here: /health is polled by the frontend and a token
    # exchange per request would be both slow and needlessly noisy. Presence of
    # the OAuth client is enough to say "configured"; the first real search
    # surfaces an authentication failure.
    s = config.settings
    return {
        "status": "ok",
        "credentials_configured": bool(s.sentinel_hub_id and s.sentinel_hub_api_key),
        "token_url": s.cdse_oauth_token_url,
        "stac_url": s.cdse_stac_url,
        "flood_threshold_db": s.flood_threshold_db,
        "flood_after_percentile": s.flood_after_percentile,
    }


@app.post("/backend/analyze", response_model=models.AnalysisResponse)
def analyze(req: models.AnalysisRequest):
    st = storage.AnalysisState.create()
    st.update(phase="queued", request=req.model_dump())
    data = st.get()

    job_runner.submit(
        st.analysis_id,
        lambda: _run_and_report(st.analysis_id, req),
    )

    return models.AnalysisResponse(
        analysisId=st.analysis_id,
        phase="queued",
        createdAt=data["createdAt"],
        updatedAt=data["updatedAt"],
    )


def _run_and_report(analysis_id: str, req: models.AnalysisRequest):
    started = time.time()
    results = pipeline.run_analysis(
        analysis_id=analysis_id,
        bbox=req.bbox,
        date_str=req.date,
    )
    pipeline.summarize(results, time.time() - started)
    return results


@app.get("/backend/analyze/{analysis_id}")
def get_analyze(analysis_id: str):
    try:
        st = storage.AnalysisState.load(analysis_id)
        return st.get()
    except Exception:
        raise HTTPException(404, "Not found")


@app.get("/backend/geocode")
def geocode(q: str):
    try:
        mc = maptiler.MapTilerClient()
        return mc.geocode(q)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/backend/rasters/{analysis_id}/{name}")
def get_raster(analysis_id: str, name: str):
    # `name` is a URL segment, so it is resolved through a fixed allow-list
    # instead of being joined onto the analysis directory. `.png` names are
    # rendered on demand from the matching `.tif` (the masks and scenes are
    # immutable once the job completes, so the rendered file is cached).
    if name in pipeline.RASTER_NAMES:
        tif_name, media_type = name, "image/tiff"
    elif name.endswith(".png") and (name[:-4] + ".tif") in pipeline.RASTER_NAMES:
        tif_name, media_type = name[:-4] + ".tif", "image/png"
    else:
        raise HTTPException(404, "Not found")
    try:
        st = storage.AnalysisState.load(analysis_id)
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    tif_path = st.path / tif_name
    if not tif_path.exists():
        raise HTTPException(404, "Not found")
    if media_type == "image/tiff":
        return FileResponse(tif_path, media_type="image/tiff")
    try:
        png_path = raster.render_preview(
            tif_path, st.path / name,
            mode="mask" if tif_name.startswith("flood_mask") else "gray",
        )
    except raster.RasterAnalysisError as exc:
        raise HTTPException(422, str(exc))
    return FileResponse(png_path, media_type="image/png")


@app.get("/backend/osm/{analysis_id}/{layer}")
def get_osm(analysis_id: str, layer: str):
    # OSM layers are fetched by the pipeline alongside the Sentinel path and
    # land on disk as `osm_{layer}.geojson`; this route only reads the cache,
    # so Overpass is never hit from a request thread.
    if layer not in pipeline.OSM_LAYER_NAMES:
        raise HTTPException(404, "Unknown layer")
    try:
        st = storage.AnalysisState.load(analysis_id)
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    path = st.path / f"osm_{layer}.geojson"
    if not path.exists():
        raise HTTPException(404, "Layer not available for this analysis")
    return FileResponse(path, media_type="application/geo+json")


# FloodViT vector outputs written offline under `data/<id>/` by the
# scripts in `scripts/floodvit/` (polygonize -> affected roads/bridges ->
# disconnected routes/settlements). Each is reachable through
# `GET /backend/floodvit/{analysis_id}/{name}` where `name` is the key
# below; the filenames are fixed, so no path ever comes from the request.
FLOODVIT_FILES = {
    "polygons": "flood_polygons.geojson",
    "affected_roads": "affected_roads.geojson",
    "affected_bridges": "affected_bridges.geojson",
    "disconnected_routes": "disconnected_routes.geojson",
    "disconnected_settlements": "disconnected_settlements.geojson",
}


@app.get("/backend/floodvit/{analysis_id}/{name}")
def get_floodvit_layer(analysis_id: str, name: str):
    # These files only exist once the offline scripts have been run for an
    # analysis, so a 404 is a normal "not generated yet" answer. The route
    # only reads the cache (never Overpass/CDSE), and `AnalysisState.load`
    # rejects anything that is not a stored UUID before any path is touched.
    if name not in FLOODVIT_FILES:
        raise HTTPException(404, "Unknown layer")
    try:
        st = storage.AnalysisState.load(analysis_id)
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    path = st.path / FLOODVIT_FILES[name]
    if not path.exists():
        raise HTTPException(404, "FloodViT layer not available for this analysis")
    return FileResponse(path, media_type="application/geo+json")


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)