import zipfile
from io import BytesIO

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app.main import app
from app.uploads import UploadJob, ensure_zip, upload_controller


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
