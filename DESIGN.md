# Store Intelligence API Design

## Goal

This project converts the latest provided challenge resources into structured behavioral events and exposes those events through a Store Intelligence API. The north star metric is offline conversion rate: the number of customer sessions that reach purchase-like behavior divided by total observed customer sessions. The code is intentionally split into a detection layer and an API layer so that the computer-vision approach can improve without changing the downstream event contract.

## Architecture

The Part A pipeline lives in `pipeline/`. `pipeline.detect` reads a CCTV zip, extracts each camera clip into a temporary directory, processes sampled frames, tracks moving people, maps each track to configured store zones, and writes newline-delimited JSON events. `pipeline.emit` owns the event dataclass and schema validation. `pipeline.tracker` provides a centroid tracker that keeps a stable `visitor_id` while a person remains near the previous frame position. `pipeline.zones` contains deterministic polygon logic for mapping the lower center of a bounding box to a store zone.

The default sample-event layout is encoded in `data/store_layout.json`. The actual Store 1 and Store 2 CCTV zips use `data/store_layouts/store_1.json` and `data/store_layouts/store_2.json`; `pipeline.layouts` detects the matching layout from zip member paths such as `Store 1/CAM 5 - billing.mp4` or `Store 2/billing_area.mp4`. Staff handling is implemented as a zone-based flag: tracks observed in staff or non-customer areas become `is_staff=true`, so the API can exclude those events from customer metrics.

The Part B API lives in `app/`. It is a FastAPI service with an in-memory `EventStore`. `POST /events/ingest` accepts batches of up to 500 events, validates each event with Pydantic, deduplicates by `event_id`, and returns partial-success errors instead of failing the whole request. The analytics endpoints read from the same store and compute metrics at request time. For production, the `EventStore` boundary is the place where SQLite or PostgreSQL would be added.

The Part C production layer adds Docker Compose, a healthcheck, structured JSON request logging, and graceful degradation. The API logs `trace_id`, `store_id`, endpoint, latency, event count for ingest, and status code. A simulated store-unavailable path returns a structured HTTP 503 rather than a raw stack trace. The Docker image uses `requirements-api.txt` so the production API container stays small; the heavier local `requirements.txt` keeps OpenCV, pytest, and coverage tools for detection and testing.

## Data Flow

1. `python -m pipeline.detect` reads a CCTV zip and layout config.
2. Each frame sample is processed by person detection plus motion fallback.
3. Tracks are converted into event types such as `ENTRY`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, and `BILLING_QUEUE_ABANDON`.
4. Events are written to a configured JSONL path, defaulting to `outputs/detected_events.jsonl`.
5. The API ingests JSONL-derived batches through `/events/ingest`.
6. Metrics, funnel, heatmap, anomalies, and health endpoints compute business views from stored events.

## Important Assumptions

The latest usable resources include two large CCTV zips for Store 1 and Store 2, the raw provided sample events, the normalized `ST1076` replay stream, the supplied POS CSV, and inferred layouts for all supported stores. The CCTV zips remain outside Git because they are large challenge assets, while the layout/config files needed to process them are committed. The default replay demo uses the `ST1076` sample events as the authoritative sample stream. The POS parser supports the supplied CSV format, but the shipped POS rows do not directly join to the `ST1076`, `STORE_1`, or `STORE_2` streams because they reference `ST1008`.

The detection pipeline is a reproducible baseline, not a perfect CV system. It uses OpenCV HOG detection and background subtraction fallback because the challenge environment may not have model weights or GPU access. Confidence values are retained instead of suppressing low-confidence events, matching the problem statement's expectation that uncertainty should be visible downstream.

## AI-Assisted Decisions

The first AI-assisted decision was to separate the event contract from detector quality. A model suggested starting with YOLOv8 plus ByteTrack, which is a better production direction, but I chose the OpenCV baseline for this submission because model downloads and GPU availability were risky in a take-home environment. The compromise was to build `detect_people()` as a replaceable function while keeping event generation, schema validation, and API logic independent of the model.

The second AI-assisted decision was around funnel counting. A generic answer would count only `ENTRY` events as total visitors. When real generated events were tested, zone visits exceeded entry counts because entry detection can be imperfect. I adjusted the API to use all observed non-staff visitor IDs as the session base while still preserving `ENTRY` and `REENTRY` event types. That makes the funnel logically consistent and transparent under imperfect detection.

The third AI-assisted decision was Docker packaging. Initially the Docker image installed the full local requirements, including OpenCV and test tooling. During the actual Docker build, this made the image heavy enough to stress the local Docker Desktop setup. I changed the design to use a separate `requirements-api.txt` for the API container.

## Operational Notes

The service can be started with `docker compose up --build`. `/health` returns latest event timestamps per store and marks stale feeds with `STALE_FEED`. `/healthz` is a lightweight deployment health endpoint for Render. The system logs enough request context for an on-call engineer to answer which endpoint was called, for which store, how many events were ingested, whether it succeeded, and how long it took.

The test suite covers schema validation, idempotent ingest, partial-success handling, all-staff traffic, zero-purchase scenarios, re-entry deduplication, health, logging, graceful degradation, provided resource normalization, and deterministic pipeline helpers. The video processing loop remains validated by smoke runs rather than statement-level unit coverage, because deterministic correctness is better tested at the event and API boundaries.

## Live Dashboard

Part E adds a first-screen operational dashboard at `/dashboard`. It is served by the same FastAPI process, so Docker Compose starts the API and dashboard together. The dashboard's replay path streams `data/sample_events.jsonl` into the in-memory event store in timed batches. The upload path runs detection on Store 1 or Store 2 CCTV zips, records the selected `store_id` on the upload job, and switches the dashboard analytics calls to that uploaded store. While replay or upload processing is running, the browser polls the same production endpoints used by external clients: `/metrics`, `/funnel`, `/heatmap`, `/anomalies`, `/health`, and job status endpoints.

This makes the dashboard a connected system demo rather than a static mock. The KPI strip shows visitors, conversion, queue depth, and data confidence. The funnel visualizes session drop-off. The heatmap ranks zone activity by visit count and dwell. The anomaly rail shows active operational signals or an all-clear state.
