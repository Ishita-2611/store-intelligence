import zipfile
from io import BytesIO
import json
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app.main import app
from app import uploads
from app.uploads import (
    UploadJob,
    camera_id_for_filename,
    ensure_zip,
    should_use_precomputed_events,
    write_layout_fallback_events,
    upload_controller,
    write_precomputed_events_for_upload,
)
from pipeline.layouts import camera_key_for_name, layout_path_for_camera_name, layout_path_for_zip


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


def test_uploaded_mp4_preserves_original_name_for_layout_detection(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    upload = UploadFile(filename="entry 1.mp4", file=BytesIO(b"fake-video"))

    job = upload_controller.create_job(upload)
    saved_path = tmp_path / job.job_id / "entry 1.mp4"
    zip_path = ensure_zip(saved_path)

    assert saved_path.exists()
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == ["CCTV Footage/entry 1.mp4"]
    assert layout_path_for_zip(zip_path).name == "store_2.json"


def test_mp4_upload_is_wrapped_for_detector(tmp_path) -> None:
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake mp4 bytes")

    zip_path = ensure_zip(video_path)

    assert zip_path.suffix == ".zip"
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == ["CCTV Footage/clip.mp4"]


def test_store_zip_layouts_are_detected_from_archive_members(tmp_path) -> None:
    store_1_zip = tmp_path / "store-1.zip"
    with zipfile.ZipFile(store_1_zip, "w") as archive:
        archive.writestr("Store 1/CAM 5 - billing.mp4", b"fake-video")
    store_2_zip = tmp_path / "store-2.zip"
    with zipfile.ZipFile(store_2_zip, "w") as archive:
        archive.writestr("Store 2/billing_area.mp4", b"fake-video")

    assert camera_key_for_name("CAM 5 - billing.mp4") == "CAM_5_BILLING"
    assert layout_path_for_zip(store_1_zip).name == "store_1.json"
    assert layout_path_for_zip(store_2_zip).name == "store_2.json"
    assert camera_id_for_filename("CAM 5 - billing.mp4") == "STORE_1_CAM_5_BILLING"


def test_single_mp4_upload_layout_is_detected_from_camera_name(tmp_path) -> None:
    wrapped_mp4_zip = tmp_path / "single-camera.zip"
    with zipfile.ZipFile(wrapped_mp4_zip, "w") as archive:
        archive.writestr("CCTV Footage/CAM 1 - zone.mp4", b"fake-video")

    assert layout_path_for_camera_name("CAM 1 - zone.mp4").name == "store_1.json"
    assert layout_path_for_zip(wrapped_mp4_zip).name == "store_1.json"


def test_store_layouts_use_floor_plan_zone_names() -> None:
    store_1 = json.loads(Path("data/store_layouts/store_1.json").read_text(encoding="utf-8"))
    store_2 = json.loads(Path("data/store_layouts/store_2.json").read_text(encoding="utf-8"))
    store_1_zone_ids = {zone["zone_id"] for camera in store_1["cameras"].values() for zone in camera["zones"]}
    store_2_zone_ids = {zone["zone_id"] for camera in store_2["cameras"].values() for zone in camera["zones"]}

    assert "STORE_1_SALM_TFS_FRAGRANCE" in store_1_zone_ids
    assert "STORE_1_CASH_COUNTER_QUEUE" in store_1_zone_ids
    assert "STORE_2_LEFT_WALL_AND_GONDOLA" in store_2_zone_ids
    assert "STORE_2_CASH_COUNTER_QUEUE" in store_2_zone_ids


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


def test_uploaded_detector_analyzes_complete_footage_by_default(monkeypatch, tmp_path) -> None:
    captured = {}
    source_zip = tmp_path / "clip.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("CCTV Footage/CAM 3.mp4", b"fake-video")
    monkeypatch.setattr(uploads, "UPLOAD_USE_SAMPLE_EVENTS", False)

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
    assert upload_controller.status("bounded")["analysis_window_seconds"] is None


def test_store_upload_status_reports_selected_store(monkeypatch, tmp_path) -> None:
    source_zip = tmp_path / "store-1.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("Store 1/CAM 3 - entry.mp4", b"fake-video")
    monkeypatch.setattr(uploads, "UPLOAD_USE_SAMPLE_EVENTS", False)

    def fake_run_detector(_zip_path, events_path, _controller, _job_id, _generation):
        events_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(uploads, "run_detector", fake_run_detector)
    with upload_controller._lock:
        upload_controller._jobs["store1"] = UploadJob(job_id="store1", filename="store-1.zip")
        generation = upload_controller._generation

    upload_controller._process("store1", source_zip, generation)

    assert upload_controller.status("store1")["store_id"] == "STORE_1"


def test_uploaded_files_run_detector_by_default(tmp_path) -> None:
    source_zip = tmp_path / "store-upload.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("CCTV Footage/unknown-camera.mp4", b"fake-video")

    assert should_use_precomputed_events(source_zip, source_zip) is False


def test_uploaded_files_can_opt_into_committed_sample_events(monkeypatch, tmp_path) -> None:
    source_zip = tmp_path / "store-upload.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("CCTV Footage/unknown-camera.mp4", b"fake-video")
    monkeypatch.setattr(uploads, "UPLOAD_USE_SAMPLE_EVENTS", True)
    monkeypatch.setattr(uploads, "UPLOAD_DIRECT_DETECT_MAX_BYTES", 1)

    assert should_use_precomputed_events(source_zip, source_zip) is True


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


def test_layout_fallback_events_are_written_for_zero_cv_upload(tmp_path) -> None:
    source_zip = tmp_path / "store-1-single.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("CCTV Footage/CAM 3 - entry.mp4", b"fake-video")
    layout = json.loads(Path("data/store_layouts/store_1.json").read_text(encoding="utf-8"))
    events_path = tmp_path / "fallback.jsonl"

    write_layout_fallback_events(source_zip, layout, events_path)
    rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]

    assert rows[0]["store_id"] == "STORE_1"
    assert rows[0]["camera_id"] == "STORE_1_CAM_3_ENTRY"
    assert rows[0]["event_type"] == "ENTRY"
    assert rows[0]["confidence"] == 0.35
