"""
Persistent job store: every generated firmware batch survives restarts.

Jobs are kept in memory for fast access and mirrored to data/jobs/<id>.json,
so a generated (but not yet flashed) firmware is still flashable after the
server restarts - the old in-memory dict lost everything on each restart.
"""

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from config import JOBS_DIR

log = logging.getLogger(__name__)


class JobStore:
    def __init__(self, directory: Path = JOBS_DIR):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: Dict[str, dict] = {}
        self._load_all()

    # -------------------------------------------------------------- CRUD
    def create(
        self,
        command: str,
        code: str,
        hardware_state: dict,
        context: str = "",
        status: str = "generated",
    ) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        job = {
            "id": str(uuid.uuid4()),
            "command": command,
            "status": status,
            "code": code,
            "hardware_state": hardware_state,
            "context": context,
            "logs": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job["id"]] = job
            self._persist(job)
        return dict(job)

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
        return dict(job) if job else None

    def update(self, job_id: str, **fields: Any) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.update(fields)
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._persist(job)
            return dict(job)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
        return [dict(job) for job in jobs]

    # ---------------------------------------------------------- persistence
    def _path(self, job_id: str) -> Path:
        return self.directory / f"{job_id}.json"

    def _persist(self, job: dict) -> None:
        try:
            self._path(job["id"]).write_text(
                json.dumps(job, indent=2), encoding="utf-8"
            )
        except OSError as e:
            log.warning("Could not persist job %s: %s", job["id"], e)

    def _load_all(self) -> None:
        loaded = 0
        for path in self.directory.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                self._jobs[job["id"]] = job
                loaded += 1
            except (json.JSONDecodeError, OSError, KeyError) as e:
                log.warning("Skipping unreadable job file %s: %s", path.name, e)
        if loaded:
            log.info("Restored %d job(s) from %s", loaded, self.directory)
