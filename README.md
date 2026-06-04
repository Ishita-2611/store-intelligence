# Store Intelligence Challenge

This repository is configured around the latest provided challenge resources:

- `data/provided_sample_events.jsonl` is the raw sample event resource supplied with the new problem statement package.
- `data/sample_events.jsonl` is the normalized default replay stream derived from that provided sample event file.
- `data/pos_transactions.csv` is the supplied POS transaction resource.
- `data/store_layout.json` is an inferred `ST1076` layout based on the cameras and zones present in the new sample events.
- `data/store_layouts/store_1.json` and `data/store_layouts/store_2.json` map the actual Store 1 and Store 2 CCTV zip camera names to detector zones.
- The supplied POS rows are for `ST1008`, while the supplied sample-event stream is for `ST1076`. The API and parser support both files, but the default live demo uses the `ST1076` event stream as the authoritative dataset because there is no matching `ST1076` POS file in the provided resources.
- The detector uses YOLOv8 automatically when `ultralytics` is available and selects CUDA when PyTorch reports a GPU; otherwise it falls back to OpenCV HOG and motion detection.

## Part A: Detection Pipeline

Run the detector against the provided Store 1 or Store 2 CCTV zip:

```powershell
python -m pipeline.detect --video-zip "D:\downloads\Store 2-20260602T101819Z-3-001099f208.zip" --pos-csv data\pos_transactions.csv --out outputs\detected_events.jsonl
```

For a quick smoke run:

```powershell
python -m pipeline.detect --video-zip "D:\downloads\Store 2-20260602T101819Z-3-001099f208.zip" --pos-csv data\pos_transactions.csv --out outputs\detected_events_sample.jsonl --sample-stride 45 --max-seconds 3
```

For the committed Store 1 demo replay, preprocess the large Store 1 zip once into a small JSONL stream:

```powershell
python -m pipeline.detect --video-zip "D:\downloads\Store 1-20260602T101818Z-3-001ec38db8.zip" --pos-csv data\pos_transactions.csv --out data\store_1_events.jsonl --sample-stride 45 --max-seconds 5
```

When the zip contains `Store 1/` or `Store 2/`, the detector automatically selects the matching layout from `data/store_layouts/`. Store 2 has two entry cameras, so `ENTRY_1` is the authoritative customer-count camera and `ENTRY_2` contributes zone observation without double-counting visitor entries. Large CCTV zip files are not committed to Git; keep them in the provided download location and pass their local path to the command.

The event stream is newline-delimited JSON and follows the challenge schema:

- `ENTRY`, `EXIT`, and `REENTRY` from entry/exit cameras.
- `ZONE_ENTER`, `ZONE_EXIT`, and periodic `ZONE_DWELL`.
- `BILLING_QUEUE_JOIN` when a tracked visitor enters the billing zone.
- `BILLING_QUEUE_ABANDON` when a visitor leaves billing and no matching POS transaction follows; if no POS reference data exists for that store, the pipeline does not guess abandonment.
- `is_staff=true` for tracks observed in staff or non-customer areas.

The API also accepts the provided sample-event compatibility format (`id_token`, `store_code`, `event_timestamp`, `zone_entered`, `queue_completed`, etc.) and normalizes it into the canonical schema during ingest. The committed `data/provided_sample_events.jsonl` file exercises that path.

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
- `GET /healthz` returns a lightweight deployment health check for Render and Docker health probes.

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
python -m pytest
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Optional stronger local detector:

```powershell
pip install -r requirements-ml.txt
python -m pipeline.detect --video-zip "D:\downloads\Store 1-20260602T101818Z-3-001ec38db8.zip" --pos-csv data\pos_transactions.csv --out outputs\detected_events.jsonl
```

Production behaviors currently included:

- Docker entrypoint and Render-friendly `PORT` handling.
- Docker Compose can build with ML dependencies and request GPU devices for complete-footage detection jobs.
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

The dashboard has reliable simulated real-time demos for the hosted environment: `Replay sample` streams the provided `ST1076` sample-event resource, `Replay Store 1 all` streams the complete `data/store_1_events.jsonl` file, and `Replay camera` streams one preprocessed Store 1 camera at a time from that same file. The Store 1 JSONL was preprocessed offline from the supplied Store 1 CCTV zip because that raw zip is too large for hosted browser upload. Raw CCTV upload still supports smaller Store 1 and Store 2 ZIP/MP4 files; uploaded events report `STORE_1` or `STORE_2`, and the dashboard switches analytics to that uploaded store automatically. Uploaded raw footage is analyzed completely by default with stride 45; set `UPLOAD_MAX_SECONDS` to a positive value only when you intentionally want a shorter smoke run, and set `UPLOAD_SAMPLE_STRIDE` for deeper or faster local analysis.

Run tests:

```powershell
python -m pytest
```
