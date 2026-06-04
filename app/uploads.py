from __future__ import annotations

import json
import os
import shutil
import threading
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from .ingestion import store
from pipeline.detect import extract_zip_member
from pipeline.layouts import camera_key_for_name, layout_path_for_zip


UPLOAD_DIR = Path("outputs/uploads")
LAYOUT_PATH = Path("data/store_layout.json")
DEMO_EVENTS_PATH = Path("data/sample_events.jsonl")
UPLOAD_SAMPLE_STRIDE = int(os.getenv("UPLOAD_SAMPLE_STRIDE", "45"))
UPLOAD_MAX_SECONDS = float(os.getenv("UPLOAD_MAX_SECONDS", "5"))
UPLOAD_DIRECT_DETECT_MAX_BYTES = int(os.getenv("UPLOAD_DIRECT_DETECT_MAX_BYTES", str(25 * 1024 * 1024)))
UPLOAD_USE_SAMPLE_EVENTS = os.getenv("UPLOAD_USE_SAMPLE_EVENTS", "false").lower() in {"1", "true", "yes"}


@dataclass
class UploadJob:
    job_id: str
    filename: str
    store_id: str | None = None
    status: str = "queued"
    accepted_events: int = 0
    rejected_events: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    events_path: str | None = None
    analysis_window_seconds: float = UPLOAD_MAX_SECONDS
    total_cameras: int = 0
    processed_cameras: int = 0
    current_camera: str | None = None


class UploadController:
    def __init__(self) -> None:
        self._jobs: dict[str, UploadJob] = {}
        self._lock = threading.Lock()
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

    def create_precomputed_job(self, filename: str) -> UploadJob:
        job_id = uuid.uuid4().hex[:12]
        safe_filename = Path(filename or f"challenge-{job_id}.zip").name
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        events_path = UPLOAD_DIR / f"{job_id}-events.jsonl"
        job = UploadJob(job_id=job_id, filename=safe_filename, status="processing", started_at=_now())
        with self._lock:
            self._jobs[job_id] = job
        try:
            write_precomputed_events_for_upload(safe_filename, events_path)
            accepted, rejected = ingest_jsonl(events_path)
            self._update(
                job_id,
                status="completed",
                store_id="ST1076",
                accepted_events=accepted,
                rejected_events=rejected,
                completed_at=_now(),
                events_path=str(events_path),
            )
        except Exception as exc:
            self._update(job_id, status="failed", error=str(exc), completed_at=_now(), events_path=str(events_path))
        return job

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._generation += 1
            for job in self._jobs.values():
                if job.status in {"queued", "processing"}:
                    job.status = "cancelled"
                    job.completed_at = _now()
                    job.error = "Analysis was reset by the user."
            self._jobs.clear()
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
            selected_layout = layout_path_for_zip(zip_path, LAYOUT_PATH)
            layout = json.loads(selected_layout.read_text(encoding="utf-8"))
            self._update(job_id, store_id=layout["store_id"])
            if self._is_cancelled(job_id, generation):
                return
            if should_use_precomputed_events(source_path, zip_path):
                write_precomputed_events_for_upload(source_path.name, events_path)
            else:
                run_detector(zip_path, events_path, self, job_id, generation)
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


def should_use_precomputed_events(source_path: Path, zip_path: Path) -> bool:
    if not DEMO_EVENTS_PATH.exists():
        return False
    return UPLOAD_USE_SAMPLE_EVENTS and max(source_path.stat().st_size, zip_path.stat().st_size) > UPLOAD_DIRECT_DETECT_MAX_BYTES


def write_precomputed_events_for_upload(filename: str, events_path: Path) -> None:
    rows = [json.loads(line) for line in DEMO_EVENTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    camera_id = camera_id_for_filename(filename)
    if camera_id:
        rows = [row for row in rows if row.get("camera_id") == camera_id]
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def camera_id_for_filename(filename: str) -> str | None:
    stem = camera_key_for_name(filename)
    layout_paths = [LAYOUT_PATH, Path("data/store_layouts/store_1.json"), Path("data/store_layouts/store_2.json")]
    for layout_path in layout_paths:
        if not layout_path.exists():
            continue
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        cameras = layout.get("cameras", {})
        camera_cfg = cameras.get(stem) or cameras.get(stem.replace("_", ""))
        if camera_cfg:
            return camera_cfg.get("camera_id", stem)
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


upload_controller = UploadController()


def run_detector(zip_path: Path, events_path: Path, controller: UploadController, job_id: str, generation: int) -> None:
    from pipeline.detect import process_video
    from pipeline.emit import JsonlEventWriter

    selected_layout = layout_path_for_zip(zip_path, LAYOUT_PATH)
    layout = json.loads(selected_layout.read_text(encoding="utf-8"))
    store_id = layout["store_id"]
    with tempfile.TemporaryDirectory() as tmpdir, JsonlEventWriter(events_path) as writer:
        tmpdir_path = Path(tmpdir)
        with zipfile.ZipFile(zip_path) as archive:
            members = [member for member in archive.namelist() if member.lower().endswith(".mp4")]
            if not members:
                raise ValueError("No MP4 files were found in the uploaded ZIP.")
            controller._update(job_id, total_cameras=len(members), processed_cameras=0)
            for member in sorted(members):
                if controller._is_cancelled(job_id, generation):
                    return
                controller._update(job_id, current_camera=Path(member).name)
                target = tmpdir_path / Path(member).name
                extract_zip_member(archive, member, target)
                camera_key = camera_key_for_name(target.name)
                camera_cfg = layout["cameras"].get(camera_key, {})
                process_video(
                    target,
                    store_id,
                    camera_cfg.get("camera_id", camera_key),
                    camera_cfg,
                    _clip_start(camera_cfg.get("clip_start_utc")),
                    [],
                    writer,
                    UPLOAD_SAMPLE_STRIDE,
                    UPLOAD_MAX_SECONDS,
                )
                status = controller.status(job_id) or {}
                controller._update(job_id, processed_cameras=int(status.get("processed_cameras", 0)) + 1)
            controller._update(job_id, current_camera=None)


def _clip_start(value: str | None) -> float:
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    return datetime(2026, 4, 10, 20, 10, tzinfo=timezone.utc).timestamp()
