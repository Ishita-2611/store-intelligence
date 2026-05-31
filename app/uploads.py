from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .ingestion import store


UPLOAD_DIR = Path("outputs/uploads")
LAYOUT_PATH = Path("data/store_layout.json")


@dataclass
class UploadJob:
    job_id: str
    filename: str
    status: str = "queued"
    accepted_events: int = 0
    rejected_events: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    events_path: str | None = None


class UploadController:
    def __init__(self) -> None:
        self._jobs: dict[str, UploadJob] = {}
        self._lock = threading.Lock()
        self._active_process: subprocess.Popen[str] | None = None
        self._generation = 0

    def create_job(self, upload: UploadFile) -> UploadJob:
        job_id = uuid.uuid4().hex[:12]
        filename = Path(upload.filename or f"upload-{job_id}.zip").name
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        source_path = UPLOAD_DIR / f"{job_id}-{filename}"
        with source_path.open("wb") as fh:
            shutil.copyfileobj(upload.file, fh)

        job = UploadJob(job_id=job_id, filename=filename)
        with self._lock:
            self._jobs[job_id] = job
            generation = self._generation

        thread = threading.Thread(target=self._process, args=(job_id, source_path, generation), daemon=True)
        thread.start()
        return job

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._generation += 1
            process = self._active_process
            for job in self._jobs.values():
                if job.status in {"queued", "processing"}:
                    job.status = "cancelled"
                    job.completed_at = _now()
                    job.error = "Analysis was reset by the user."
            self._jobs.clear()
            self._active_process = None

        if process and process.poll() is None:
            threading.Thread(target=terminate_process, args=(process,), daemon=True).start()
        return {"status": "idle"}

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.__dict__.copy() if job else None

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._jobs:
                return None
            job = next(reversed(self._jobs.values()))
            return job.__dict__.copy()

    def _process(self, job_id: str, source_path: Path, generation: int) -> None:
        if self._is_cancelled(job_id, generation):
            return
        self._update(job_id, status="processing", started_at=_now())
        events_path = UPLOAD_DIR / f"{job_id}-events.jsonl"
        try:
            zip_path = ensure_zip(source_path)
            if self._is_cancelled(job_id, generation):
                return
            command = [
                sys.executable,
                "-m",
                "pipeline.detect",
                "--video-zip",
                str(zip_path),
                "--layout",
                str(LAYOUT_PATH),
                "--out",
                str(events_path),
                "--sample-stride",
                "10",
            ]
            completed = run_detector(command, self)
            if self._is_cancelled(job_id, generation):
                return

            accepted, rejected = ingest_jsonl(events_path)
            self._update(
                job_id,
                status="completed",
                accepted_events=accepted,
                rejected_events=rejected,
                completed_at=_now(),
                events_path=str(events_path),
            )
        except Exception as exc:
            self._update(job_id, status="failed", error=str(exc), completed_at=_now(), events_path=str(events_path))

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in changes.items():
                setattr(job, key, value)

    def _is_cancelled(self, job_id: str, generation: int) -> bool:
        with self._lock:
            return generation != self._generation or job_id not in self._jobs


def ensure_zip(path: Path) -> Path:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return path
    if suffix == ".mp4":
        zip_path = path.with_suffix(".zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(path, arcname=f"CCTV Footage/{path.name}")
        return zip_path
    raise ValueError("Upload a .zip containing CCTV MP4 files or a single .mp4 clip.")


def ingest_jsonl(path: Path) -> tuple[int, int]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    store.reset()
    accepted = 0
    rejected = 0
    for index in range(0, len(rows), 500):
        result = store.ingest(rows[index : index + 500])
        accepted += int(result["accepted"]) + int(result["duplicates"])
        rejected += int(result["rejected"])
    return accepted, rejected


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


upload_controller = UploadController()


def run_detector(command: list[str], controller: UploadController) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **process_group_kwargs())
    with controller._lock:
        controller._active_process = process
    try:
        stdout, stderr = process.communicate(timeout=900)
    finally:
        with controller._lock:
            if controller._active_process is process:
                controller._active_process = None

    completed = subprocess.CompletedProcess(command, process.returncode, stdout=stdout, stderr=stderr)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "detection pipeline failed").strip())
    return completed


def terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        terminate_process_group(process)
        process.wait(timeout=5)
    except Exception:
        kill_process_group(process)


def process_group_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_process_group(process: subprocess.Popen[str]) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, text=True)
        return
    os.killpg(process.pid, signal.SIGTERM)


def kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, text=True)
            return
        os.killpg(process.pid, signal.SIGKILL)
    except Exception:
        process.kill()
