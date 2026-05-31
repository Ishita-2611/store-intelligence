from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .errors import StoreUnavailableError
from .ingestion import store
from .logging import configure_logging, structured_request_logger
from .metrics import compute_anomalies, compute_funnel, compute_heatmap, compute_metrics, health_snapshot
from .models import IngestRequest, IngestResponse
from .replay import replay_controller
from .uploads import upload_controller


app = FastAPI(title="Store Intelligence API", version="0.2.0")
configure_logging()
app.middleware("http")(structured_request_logger)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.exception_handler(StoreUnavailableError)
async def store_unavailable_handler(_request: Request, exc: StoreUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": {"code": "STORE_UNAVAILABLE", "message": str(exc)}},
    )


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard")
def dashboard() -> FileResponse:
    return FileResponse("app/static/dashboard.html")


@app.post("/events/ingest", response_model=IngestResponse)
def ingest_events(payload: IngestRequest | list[dict[str, Any]]) -> dict:
    raw_events = payload.events if isinstance(payload, IngestRequest) else payload
    if len(raw_events) > 500:
        raise HTTPException(status_code=413, detail={"message": "batch size cannot exceed 500 events"})
    return store.ingest(raw_events)


@app.get("/stores/{store_id}/metrics")
def get_metrics(store_id: str) -> dict:
    events = store.by_store(store_id)
    return {"store_id": store_id, **compute_metrics(events)}


@app.get("/Metrics")
@app.get("/metrics")
def get_default_metrics() -> dict:
    return get_metrics("ST1008")


@app.get("/stores/{store_id}/funnel")
def get_funnel(store_id: str) -> dict:
    events = store.by_store(store_id)
    return {"store_id": store_id, **compute_funnel(events)}


@app.get("/stores/{store_id}/heatmap")
def get_heatmap(store_id: str) -> dict:
    events = store.by_store(store_id)
    return {"store_id": store_id, **compute_heatmap(events)}


@app.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str) -> dict:
    events = store.by_store(store_id)
    return {"store_id": store_id, "anomalies": compute_anomalies(events)}


@app.get("/health")
def get_health() -> dict:
    return health_snapshot(store.all_events())


@app.post("/demo/replay/start")
def start_demo_replay(batch_size: int = 25, interval_ms: int = 700) -> dict:
    upload_controller.reset()
    return replay_controller.start(batch_size=batch_size, interval_ms=interval_ms)


@app.post("/demo/replay/reset")
def reset_demo_replay() -> dict:
    upload_controller.reset()
    return replay_controller.reset()


@app.get("/demo/replay/status")
def get_demo_replay_status() -> dict:
    return replay_controller.status()


@app.post("/uploads/cctv")
def upload_cctv(file: UploadFile = File(...)) -> dict:
    replay_controller.stop()
    store.reset()
    job = upload_controller.create_job(file)
    return job.__dict__.copy()


@app.get("/uploads/cctv/latest")
def get_latest_upload() -> dict:
    return upload_controller.latest() or {"status": "idle"}


@app.get("/uploads/cctv/{job_id}")
def get_upload_status(job_id: str) -> dict:
    status = upload_controller.status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail={"message": "upload job not found"})
    return status
