# Store Intelligence API Design

## Goal

This project converts the provided Brigade Bangalore CCTV clips into structured behavioral events and exposes those events through a Store Intelligence API. The north star metric is offline conversion rate: the number of customer sessions that reach purchase-like behavior divided by total observed customer sessions. The code is intentionally split into a detection layer and an API layer so that the computer-vision approach can improve without changing the downstream event contract.

## Architecture

The Part A pipeline lives in `pipeline/`. `pipeline.detect` reads the CCTV zip, extracts each camera clip into a temporary directory, processes sampled frames, tracks moving people, maps each track to configured store zones, and writes newline-delimited JSON events. `pipeline.emit` owns the event dataclass and schema validation. `pipeline.tracker` provides a simple centroid tracker that keeps a stable `visitor_id` while a person remains near the previous frame position. `pipeline.zones` contains deterministic polygon logic for mapping the lower center of a bounding box to a store zone.

The store layout is encoded in `data/store_layout.json`. It maps the five provided camera files to semantic camera IDs such as `CAM_ENTRY_01`, `CAM_BILLING_01`, and `CAM_SKINCARE_01`, and it defines polygons for zones like `MAKEUP_UNIT`, `BILLING_COUNTER`, and `BACK_OF_HOUSE`. Staff handling is implemented as a zone-based flag: tracks observed in back-of-house or stock areas become `is_staff=true`, so the API can exclude those events from customer metrics.

The Part B API lives in `app/`. It is a FastAPI service with an in-memory `EventStore`. `POST /events/ingest` accepts batches of up to 500 events, validates each event with Pydantic, deduplicates by `event_id`, and returns partial-success errors instead of failing the whole request. The analytics endpoints read from the same store and compute metrics at request time. This keeps the implementation simple, transparent, and easy to test for the challenge. For production, the `EventStore` boundary is the place where SQLite or PostgreSQL would be added.

The Part C production layer adds Docker Compose, a healthcheck, structured JSON request logging, and graceful degradation. The API logs `trace_id`, `store_id`, endpoint, latency, event count for ingest, and status code. A simulated store-unavailable path returns a structured HTTP 503 rather than a raw stack trace. The Docker image uses `requirements-api.txt` so the production API container stays small; the heavier local `requirements.txt` keeps OpenCV, pytest, and coverage tools for detection and testing.

## Data Flow

1. `python -m pipeline.detect` reads the CCTV zip and layout config.
2. Each frame sample is processed by person detection plus motion fallback.
3. Tracks are converted into event types such as `ENTRY`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, and `BILLING_QUEUE_ABANDON`.
4. Events are written to `outputs/events_part_a.jsonl`.
5. The API ingests JSONL-derived batches through `/events/ingest`.
6. Metrics, funnel, heatmap, anomalies, and health endpoints compute business views from stored events.

## Important Assumptions

The provided dataset differs from the generic problem statement. Instead of fifteen clips for five stores, it contains five camera clips for one Brigade Bangalore store. I mapped this to a single store, `ST1008`, using the uploaded POS CSV and the layout image embedded in the workbook. The source clips are only a few minutes long, so the system treats the event stream as the scoring input rather than pretending it covers a full trading day.

The detection pipeline is a reproducible baseline, not a perfect CV system. It uses OpenCV HOG detection and background subtraction fallback because the challenge environment may not have model weights or GPU access. Confidence values are retained instead of suppressing low-confidence events, matching the problem statement's expectation that uncertainty should be visible downstream.

## AI-Assisted Decisions

The first AI-assisted decision was to separate the event contract from detector quality. An LLM suggested starting with YOLOv8 plus ByteTrack, which is a better production direction, but I overrode that for the initial submission because model downloads and GPU availability were risky in a take-home environment. The compromise was to build `detect_people()` as a replaceable function while keeping event generation, schema validation, and API logic independent of the model.

The second AI-assisted decision was around funnel counting. A generic answer would count only `ENTRY` events as total visitors. When we tested the real generated events, zone visits exceeded entry counts because the entry camera was imperfect. I adjusted the API to use all observed non-staff visitor IDs as the session base while still preserving `ENTRY` and `REENTRY` event types. That makes the funnel logically consistent and transparent under imperfect detection.

The third AI-assisted decision was Docker packaging. Initially the Docker image installed the full local requirements, including OpenCV and test tooling. During the actual Docker build, this made the image heavy enough to stress the local Docker Desktop setup. I changed the design to use a separate `requirements-api.txt` for the API container. This was an engineering correction based on observed behavior, not a theoretical optimization.

## Operational Notes

The service can be started with `docker compose up --build`. `/health` returns latest event timestamps per store and marks stale feeds with `STALE_FEED`. The system logs enough request context for an on-call engineer to answer: which endpoint was called, for which store, how many events were ingested, whether it succeeded, and how long it took.

The test suite covers schema validation, idempotent ingest, partial-success handling, all-staff traffic, zero-purchase scenarios, re-entry deduplication, health, logging, graceful degradation, and deterministic pipeline helpers. The video processing loop remains validated by smoke runs rather than statement-level unit coverage, because deterministic correctness is better tested at the event and API boundaries.

