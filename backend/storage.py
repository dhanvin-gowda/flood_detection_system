from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import re
import uuid
from backend.config import DATA_DIR

# Analysis ids reach this module straight from URL path segments, so they are
# constrained to the exact shape create() produces. Anything else ("..",
# absolute paths, nested separators) would escape DATA_DIR.
_ANALYSIS_ID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def valid_analysis_id(analysis_id: str) -> bool:
    return bool(analysis_id and _ANALYSIS_ID_RE.match(analysis_id))


class AnalysisState:
    def __init__(self, analysis_id: str, path: Path):
        self.analysis_id = analysis_id
        self.path = path
        self.metadata_path = path / "metadata.json"

    @classmethod
    def create(cls) -> "AnalysisState":
        analysis_id = str(uuid.uuid4())
        path = DATA_DIR / analysis_id
        path.mkdir(parents=True, exist_ok=True)
        state = cls(analysis_id, path)
        state._write({
            "analysisId": analysis_id,
            "phase": "queued",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        return state

    @classmethod
    def load(cls, analysis_id: str) -> "AnalysisState":
        if not valid_analysis_id(analysis_id):
            raise FileNotFoundError(analysis_id)
        path = DATA_DIR / analysis_id
        metadata_path = path / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(analysis_id)
        return cls(analysis_id, path)

    def _write(self, data: dict):
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        data["updatedAt"] = datetime.now(timezone.utc).isoformat()
        with open(self.metadata_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get(self) -> dict:
        with open(self.metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def set_phase(self, phase: str):
        data = self.get()
        data["phase"] = phase
        self._write(data)

    def set_error(self, error: str):
        data = self.get()
        data["phase"] = "error"
        data["error"] = error
        self._write(data)

    def update(self, **kwargs):
        data = self.get()
        data.update(kwargs)
        self._write(data)