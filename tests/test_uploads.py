import threading
import time
import zipfile
from io import BytesIO

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app.main import app
from app import uploads
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


def test_reset_returns_immediately_while_detector_is_stopping(monkeypatch) -> None:
    stopped = threading.Event()

    class SlowProcess:
        def poll(self) -> None:
            return None

    def slow_terminate(_process) -> None:
        time.sleep(0.2)
        stopped.set()

    monkeypatch.setattr("app.uploads.terminate_process", slow_terminate)
    with upload_controller._lock:
        upload_controller._jobs["job-slow"] = UploadJob(job_id="job-slow", filename="slow.mp4", status="processing")
        upload_controller._active_process = SlowProcess()

    started = time.perf_counter()
    response = upload_controller.reset()

    assert response == {"status": "idle"}
    assert time.perf_counter() - started < 0.1
    assert upload_controller.latest() is None
    assert stopped.wait(timeout=1)


def test_uploaded_detector_uses_bounded_analysis_window(monkeypatch, tmp_path) -> None:
    captured = {}
    source_zip = tmp_path / "clip.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("CCTV Footage/CAM 3.mp4", b"fake-video")

    def fake_run_detector(command, _controller):
        captured["command"] = command
        out_path = command[command.index("--out") + 1]
        open(out_path, "w", encoding="utf-8").close()
        return uploads.subprocess.CompletedProcess(command, 0, stdout="wrote 0 events", stderr="")

    monkeypatch.setattr(uploads, "run_detector", fake_run_detector)
    with upload_controller._lock:
        upload_controller._jobs["bounded"] = UploadJob(job_id="bounded", filename="clip.zip")
        generation = upload_controller._generation

    upload_controller._process("bounded", source_zip, generation)

    assert captured["command"][captured["command"].index("--sample-stride") + 1] == "20"
    assert captured["command"][captured["command"].index("--max-seconds") + 1] == "60.0"
