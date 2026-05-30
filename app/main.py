from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from .ingestion import store
from .metrics import compute_anomalies, compute_funnel, compute_heatmap, compute_metrics, health_snapshot
from .models import IngestRequest, IngestResponse


app = FastAPI(title="Store Intelligence API", version="0.2.0")


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

