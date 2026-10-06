from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import time
import uvicorn

from backend import config, jobs, models, pipeline, storage
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
    # `name` is a URL segment, so it is matched against the exact set the
    # pipeline writes instead of being joined onto the analysis directory.
    if name not in pipeline.RASTER_NAMES:
        raise HTTPException(404, "Not found")
    try:
        st = storage.AnalysisState.load(analysis_id)
    except FileNotFoundError:
        raise HTTPException(404, "Not found")
    path = st.path / name
    if not path.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type="image/tiff")


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)