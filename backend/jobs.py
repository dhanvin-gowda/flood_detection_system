from __future__ import annotations
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional, Callable


class JobRegistry:
    def __init__(self):
        self._lock = Lock()
        self._states: Dict[str, Dict[str, Any]] = {}

    def set(self, job_id: str, **kwargs):
        with self._lock:
            state = self._states.setdefault(job_id, {})
            state.update(kwargs)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._states.get(job_id, {}).copy()


class JobRunner:
    def __init__(self, max_workers: int = 4):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.registry = JobRegistry()

    def submit(self, analysis_id: str, fn: Callable[[], Any]):
        def wrapped():
            try:
                self.registry.set(analysis_id, status="running", error=None)
                result = fn()
                self.registry.set(analysis_id, status="completed", result=result)
                return result
            except Exception as e:
                self.registry.set(analysis_id, status="error", error=str(e))
                raise
        self.executor.submit(wrapped)