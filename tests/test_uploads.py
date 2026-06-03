import zipfile
from io import BytesIO

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app.main import app
from app import uploads
from app.uploads import (
    UploadJob,
    camera_id_for_filename,
    ensure_zip,
    upload_controller,
    write_precomputed_events_for_upload,
)


client = TestClient(app)


def setup_function() -> None:
    upload_controller.reset()


def test_latest_upload_is_idle_before_any_cctv_job() -> None:
    response = client.get("/uploads/cctv/latest")

    assert response.status_code == 200
    assert response.json() == {"status": "idle"}


def test_upload_endpoint_accepts_cctv_file(monkeypatch) -> None:
    def fake_create_job(upload) -> UploadJob:
        assert upload.filename == "sample.mp4"
        return UploadJob(job_id="job123", filename=upload.filename, status="queued")

    monkeypatch.setattr(upload_controller, "create_job", fake_create_job)

    response = client.post("/uploads/cctv", files={"file": ("sample.mp4", b"video", "video/mp4")})

    assert response.status_code == 200
    assert response.json()["job_id"] == "job123"
    assert response.json()["status"] == "queued"


def test_mp4_upload_is_wrapped_for_detector(tmp_path) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    zip_path = ensure_zip(video_path)

    assert zip_path.suffix == ".zip"
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == ["CCTV Footage/clip.mp4"]


def test_upload_rejects_unsupported_file_type(tmp_path) -> None:
    text_path = tmp_path / "notes.txt"
    text_path.write_text("not footage", encoding="utf-8")

    try:
        ensure_zip(text_path)
    except ValueError as exc:
        assert ".zip" in str(exc)
        assert ".mp4" in str(exc)
    else:
        raise AssertionError("Expected unsupported upload type to fail")


def test_reset_cancels_queued_upload_before_processing() -> None:
    upload = UploadFile(filename="queued.mp4", file=BytesIO(b"fake-video"))

    job = upload_controller.create_job(upload)
    generation = upload_controller._generation
    upload_controller.reset()

    assert upload_controller._is_cancelled(job.job_id, generation) is True
    assert upload_controller.latest() is None


def test_uploaded_detector_uses_bounded_analysis_window(monkeypatch, tmp_path) -> None:
    captured = {}
    source_zip = tmp_path / "clip.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("CCTV Footage/CAM 3.mp4", b"fake-video")

    def fake_run_detector(zip_path, events_path, _controller, job_id, generation):
        captured["zip_path"] = zip_path
        captured["job_id"] = job_id
        captured["generation"] = generation
        events_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(uploads, "run_detector", fake_run_detector)
    with upload_controller._lock:
        upload_controller._jobs["bounded"] = UploadJob(job_id="bounded", filename="clip.zip")
        generation = upload_controller._generation

    upload_controller._process("bounded", source_zip, generation)

    assert captured["zip_path"] == source_zip
    assert captured["job_id"] == "bounded"
    assert captured["generation"] == generation
    assert upload_controller.status("bounded")["status"] == "completed"
    assert upload_controller.status("bounded")["analysis_window_seconds"] == 60.0


def test_challenge_camera_upload_can_use_precomputed_events(tmp_path) -> None:
    events_path = tmp_path / "cam1-events.jsonl"

    write_precomputed_events_for_upload("CAM 1.mp4", events_path)
    rows = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert rows
    assert camera_id_for_filename("CAM 1.mp4") == "CAM1"
    assert all('"camera_id": "CAM1"' in row for row in rows)


def test_challenge_sample_endpoint_loads_events() -> None:
    response = client.post("/uploads/challenge-sample", json={"filename": "CAM 1.mp4"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["accepted_events"] > 0
