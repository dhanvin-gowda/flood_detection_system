from __future__ import annotations
from datetime import datetime
from typing import Optional, Literal, List, Dict, Any
from pydantic import BaseModel, Field


AnalysisPhase = Literal["queued", "selecting", "submitting_jobs", "downloading", "completed", "error"]


class AnalysisRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    bbox: List[float] = Field(..., min_length=4, max_length=4)  # [minx, miny, maxx, maxy]
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class Acquisition(BaseModel):
    productId: str
    beginPosition: datetime
    endPosition: datetime
    platform: Optional[str] = None
    productType: Optional[str] = None
    orbitNumber: Optional[int] = None
    relativeOrbitNumber: Optional[int] = None
    datatakeID: Optional[str] = None
    orbitDirection: Optional[str] = None
    polarisation: Optional[str] = None
    footprint: Optional[Dict[str, Any]] = None
    sameTrackProxy: bool = False
    url: Optional[str] = None


class AnalysisResponse(BaseModel):
    analysisId: str
    phase: AnalysisPhase
    before: Optional[Acquisition] = None
    after: Optional[Acquisition] = None
    rasters: Optional[Dict[str, str]] = None
    results: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime