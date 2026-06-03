# Store Intelligence Challenge

This repository is configured around the latest provided challenge resources:

- `data/sample_events.jsonl` is the normalized default replay stream from the provided `sample_eventsbe42122.jsonl`.
- `data/store_layout.json` is an inferred `ST1076` layout based on the cameras and zones present in the new sample events.
- The provided POS CSV format is supported, including `order_date` plus `order_time` columns. The supplied POS rows are for `ST1008`, while the latest sample-event stream is for `ST1076`, so the default dashboard uses the event stream as the authoritative demo dataset.

## Part A: Detection Pipeline

Run the detector when raw CCTV clips and a matching layout are available:

```powershell
python -m pipeline.detect --video-zip "<path-to-cctv.zip>" --layout data\store_layout.json --pos-csv "D:\downloads\POS - sample transactionsb1e826f.csv" --out outputs\detected_events.jsonl
```

For a quick smoke run:

```powershell
python -m pipeline.detect --video-zip "<path-to-cctv.zip>" --layout data\store_layout.json --pos-csv "D:\downloads\POS - sample transactionsb1e826f.csv" --out outputs\detected_events_sample.jsonl --max-seconds 15
```

The event stream is newline-delimited JSON and follows the challenge schema:

- `ENTRY`, `EXIT`, and `REENTRY` from entry/exit cameras.
- `ZONE_ENTER`, `ZONE_EXIT`, and periodic `ZONE_DWELL`.
- `BILLING_QUEUE_JOIN` when a tracked visitor enters the billing zone.
- `BILLING_QUEUE_ABANDON` when a visitor leaves billing and no matching POS transaction follows.
- `is_staff=true` for tracks observed in staff or non-customer areas.

The API also accepts the provided sample-event compatibility format (`id_token`, `store_code`, `event_timestamp`, `zone_entered`, `queue_completed`, etc.) and normalizes it into the canonical schema during ingest.

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
- `GET /stores/{store_id}/heatmap` returns zone frequency, average dwell, normalized heat score, and data-confidence status.
- `GET /stores/{store_id}/anomalies` returns queue, conversion, dead-zone, or no-traffic signals with suggested actions.
- `GET /health` returns latest event timestamps per store and stale-feed warnings.

Load the default provided sample events into a running API:

```powershell
$events = Get-Content data\sample_events.jsonl | ForEach-Object { $_ | ConvertFrom-Json }
Invoke-RestMethod -Uri http://127.0.0.1:8000/events/ingest -Method Post -ContentType application/json -Body (@{events=@($events)} | ConvertTo-Json -Depth 20)
```

## Part C: Production Readiness

Run with Docker Compose:

```powershell
docker compose up --build
```

Deploy on Render:

1. Create a new Web Service or Blueprint from this GitHub repository.
2. Keep the Docker defaults from `render.yaml` or the repository `Dockerfile`.
3. Set the health check path to `/healthz`.
4. After the service is live, use `https://<your-render-service>.onrender.com/dashboard` as the demo link.

Five-command local setup:

```powershell
git clone https://github.com/Ishita-2611/store-intelligence.git
cd store-intelligence
pip install -r requirements.txt
python -m pytest --cov=app --cov=pipeline --cov-report=term-missing tests
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Production behaviors currently included:

- Docker entrypoint and Render-friendly `PORT` handling.
- Slim API dependencies in `requirements-api.txt`; local detection/test dependencies in `requirements.txt`.
- Structured JSON request logs with `trace_id`, `store_id`, endpoint, latency, event count, and status code.
- Idempotent ingest by `event_id`.
- Partial-success ingest responses for malformed event batches.
- Structured `503` response if the event store is unavailable.

## Part D: AI Engineering

Documentation for AI-assisted engineering decisions is in:

- `DESIGN.md`
- `CHOICES.md`
- `docs/DESIGN.md`
- `docs/CHOICES.md`

Each test file starts with a `# PROMPT:` and `# CHANGES MADE:` block explaining what AI was asked to draft and what was changed afterward.

## Part E: Live Dashboard

The dashboard is served by the same FastAPI app:

```text
http://127.0.0.1:8000/dashboard
```

The primary action is uploading CCTV footage for analysis. The secondary `Replay sample` action streams the new `ST1076` sample-event resource for a quick reviewer demo.

Run tests:

```powershell
python -m pytest tests -q
```
