# Store Intelligence Challenge

This repository is being built in challenge parts. Part A is implemented first: a CCTV-to-events detection pipeline that emits the required structured JSONL schema.

## Part A: Detection Pipeline

Run the pipeline against the uploaded CCTV zip:

```powershell
python -m pipeline.detect --video-zip "C:\Users\ishit\Downloads\CCTV Footage-20260529T160731Z-3-00144614ea.zip" --layout data\store_layout.json --pos-csv "C:\Users\ishit\Downloads\Brigade_Bangalore_10_April_26 (1)bc6219c.csv" --out outputs\events_part_a.jsonl
```

For a fast smoke test:

```powershell
python -m pipeline.detect --video-zip "C:\Users\ishit\Downloads\CCTV Footage-20260529T160731Z-3-00144614ea.zip" --layout data\store_layout.json --pos-csv "C:\Users\ishit\Downloads\Brigade_Bangalore_10_April_26 (1)bc6219c.csv" --out outputs\events_part_a_sample.jsonl --max-seconds 15
```

The event stream is newline-delimited JSON and follows the challenge schema:

- `ENTRY`, `EXIT`, and `REENTRY` from the entry camera.
- `ZONE_ENTER`, `ZONE_EXIT`, and periodic `ZONE_DWELL`.
- `BILLING_QUEUE_JOIN` from the billing camera when a tracked visitor enters the billing zone.
- `BILLING_QUEUE_ABANDON` when a visitor leaves the billing zone and no POS transaction follows in the next five minutes.
- `is_staff=true` for tracks observed in staff/back-of-house zones.

The current baseline uses OpenCV HOG person detection plus background-subtraction fallback. This avoids external model downloads and gives us a reproducible event contract. A heavier detector such as YOLOv8/RT-DETR can replace `detect_people()` later without changing downstream API work.

## Part B: Intelligence API

Start the API:

```powershell
uvicorn app.main:app --reload
```

Open the interactive docs:

```text
http://127.0.0.1:8000/docs
```

Implemented endpoints:

- `POST /events/ingest` accepts up to 500 events, validates each event, deduplicates by `event_id`, and returns partial-success errors.
- `GET /stores/{store_id}/metrics` returns unique visitors, conversion rate, average dwell by zone, queue depth, and abandonment rate.
- `GET /stores/{store_id}/funnel` returns session-based Entry -> Zone Visit -> Billing Queue -> Purchase counts and drop-off.
- `GET /stores/{store_id}/heatmap` returns zone visit frequency, average dwell, normalized heat score, and a low-confidence flag for small samples.
- `GET /stores/{store_id}/anomalies` returns queue, conversion, dead-zone, or no-traffic anomalies with suggested actions.
- `GET /health` returns latest event timestamps per store and `STALE_FEED` warnings.

Run tests:

```powershell
python -m pytest tests -q
```

## Part C: Production Readiness

Run with Docker Compose:

```powershell
docker compose up --build
```

Five-command local setup:

```powershell
git clone https://github.com/Ishita-2611/store-intelligence.git
cd store-intelligence
pip install -r requirements.txt
python -m pytest --cov=app --cov=pipeline --cov-report=term-missing tests
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Production behaviors currently included:

- Container entrypoint via `docker-compose.yml`.
- Slim API container using `requirements-api.txt`; local `requirements.txt` keeps detection/test dependencies.
- Structured JSON request logs with `trace_id`, `store_id`, `endpoint`, `latency_ms`, `event_count`, and `status_code`.
- Idempotent ingest by `event_id`.
- Partial-success ingest responses for malformed events.
- Structured `503` response if the event store is unavailable.
- Tests for empty/all-staff traffic, zero purchases, re-entry deduplication, idempotent ingest, health, and API analytics.

Load generated Part A events into the running API:

```powershell
$events = Get-Content outputs\events_part_a.jsonl | ForEach-Object { $_ | ConvertFrom-Json }
Invoke-RestMethod -Uri http://127.0.0.1:8000/events/ingest -Method Post -ContentType application/json -Body (@{events=@($events | Select-Object -First 500)} | ConvertTo-Json -Depth 20)
Invoke-RestMethod -Uri http://127.0.0.1:8000/events/ingest -Method Post -ContentType application/json -Body (@{events=@($events | Select-Object -Skip 500)} | ConvertTo-Json -Depth 20)
```

## Part D: AI Engineering

Documentation for the AI-assisted engineering decisions is in:

- `docs/DESIGN.md`
- `docs/CHOICES.md`

Each test file also starts with a `# PROMPT:` and `# CHANGES MADE:` block explaining what AI was asked to draft and what was changed afterward.
