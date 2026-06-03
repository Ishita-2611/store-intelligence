# PROMPT: Validate that the API can ingest the provided sample_events JSONL and understands the provided POS CSV shape.
# CHANGES MADE: Committed the new provided resource files into data/ and asserted canonical API behavior rather than snapshotting brittle full responses.

from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.ingestion import store
from app.main import app


client = TestClient(app)
SAMPLE_EVENTS_PATH = Path("data/provided_sample_events.jsonl")
NORMALIZED_EVENTS_PATH = Path("data/sample_events.jsonl")
POS_PATH = Path("data/pos_transactions.csv")


def setup_function() -> None:
    store.reset()


def test_provided_sample_events_ingest_through_compatibility_normalizer() -> None:
    events = [json.loads(line) for line in SAMPLE_EVENTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

    response = client.post("/events/ingest", json={"events": events})

    assert response.status_code == 200
    assert response.json()["accepted"] == len(events)
    metrics = client.get("/stores/ST1076/metrics").json()
    funnel = client.get("/stores/ST1076/funnel").json()
    health = client.get("/health").json()
    assert metrics["unique_visitors"] >= 3
    assert funnel["stages"][0]["count"] >= 3
    assert health["stores"]["ST1076"]["warning"] == "STALE_FEED"


def test_repo_default_replay_file_uses_latest_provided_resource() -> None:
    replay_rows = [json.loads(line) for line in NORMALIZED_EVENTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert replay_rows
    assert {row["store_id"] for row in replay_rows} == {"ST1076"}
    assert all(row["event_type"].isupper() for row in replay_rows)


def test_provided_pos_csv_has_expected_columns() -> None:
    with POS_PATH.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    assert rows
    assert {"order_id", "order_date", "order_time", "store_id", "product_id", "brand_name", "total_amount"} <= set(rows[0])
    assert all(row["store_id"] for row in rows[:10])
