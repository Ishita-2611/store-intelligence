# PROMPT: Validate that the API can ingest the provided sample_events JSONL and understands the provided POS CSV shape.
# CHANGES MADE: Used the real downloaded sample files and asserted canonical API behavior rather than snapshotting brittle full responses.

from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.ingestion import store
from app.main import app


client = TestClient(app)
SAMPLE_EVENTS_PATH = Path("D:/downloads/sample_eventsbe42122.jsonl")
POS_PATH = Path("D:/downloads/POS - sample transactionsb1e826f.csv")


def setup_function() -> None:
    store.reset()


def test_provided_sample_events_ingest_through_compatibility_normalizer() -> None:
    events = [json.loads(line) for line in SAMPLE_EVENTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

    response = client.post("/events/ingest", json={"events": events})

    assert response.status_code == 200
    assert response.json()["accepted"] == len(events)
    metrics = client.get("/stores/ST1076/metrics").json()
    funnel = client.get("/stores/ST1076/funnel").json()
    assert metrics["unique_visitors"] >= 3
    assert funnel["stages"][0]["count"] >= 3


def test_provided_pos_csv_has_expected_columns() -> None:
    with POS_PATH.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    assert rows
    assert {"order_id", "order_date", "order_time", "store_id", "product_id", "brand_name", "total_amount"} <= set(rows[0])
    assert all(row["store_id"] for row in rows[:10])
