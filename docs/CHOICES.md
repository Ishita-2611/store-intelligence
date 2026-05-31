# Engineering Choices

## 1. Detection Model and Tracking Choice

Options considered were YOLOv8 or RT-DETR with ByteTrack, a VLM-assisted frame classifier, and a lightweight OpenCV baseline. The strongest production option is YOLOv8/RT-DETR plus ByteTrack because it provides stronger person detection under occlusion and group entry. An LLM initially recommended that route, and I agree with it as the long-term direction. However, for this challenge submission I chose OpenCV HOG person detection plus background subtraction fallback and centroid tracking.

The main reason was reproducibility. The local environment did not already include `ultralytics` or `torch`, and the challenge needs to run with minimal setup. Relying on model downloads or GPU acceleration would create a fragile demo. The current baseline works with normal Python dependencies and produces the required schema from the provided CCTV clips. It also keeps confidence values visible rather than dropping weak detections, which helps the API and reviewer understand uncertainty.

The trade-off is accuracy. HOG and motion detection are not as robust as a modern detector, especially with crowded billing, partial occlusion, and unusual camera angles. I accepted that trade-off because the scoring framework values a working end-to-end system and clear reasoning. The code keeps `detect_people()` isolated, so YOLOv8, RT-DETR, or another detector can replace it without changing tracking, event emission, ingest, or metrics.

For staff detection, I considered uniform classification from visual appearance. I chose a simpler zone-based approach for this iteration: people seen in back-of-house or staff stock zones are flagged as staff. A VLM could help classify uniforms in ambiguous frames, but using one would introduce latency, cost, and privacy questions. The current method is explainable and deterministic, even though it can miss staff who never enter a staff-only area.

## 2. Event Schema Design

The problem statement provided the required event shape, so the key choice was not whether to invent a schema, but where to enforce it. I chose to enforce schema twice: once in `pipeline.emit.StoreEvent` when writing JSONL, and again in `app.models.StoreEvent` when ingesting events through the API. This catches both pipeline bugs and malformed external inputs.

The event schema keeps `event_id` as the idempotency key, `visitor_id` as the session identity, and `metadata.session_seq` as an event order marker within the visitor session. `zone_id` is nullable because `ENTRY`, `EXIT`, and `REENTRY` are threshold-level events, while zone events use concrete layout labels. `confidence` is required and bounded between 0 and 1. I intentionally do not suppress low-confidence events in the pipeline because the scoring rubric calls out confidence calibration; hiding uncertainty would make metrics look cleaner but less honest.

An LLM suggested adding extra fields like bounding boxes and detector names to every event. I chose not to include those as top-level fields because the challenge schema is explicit and automated tests may expect that shape. If needed later, those details can go inside `metadata` without breaking the API. This keeps the event stream compatible with the prompt while leaving room for detector-specific diagnostics.

For billing conversion, I used the challenge's time-window idea but adapted it to the available data. The pipeline can emit `BILLING_QUEUE_ABANDON` when a visitor leaves the billing zone and no POS transaction follows within five minutes. The API treats billing visitors minus abandoners as purchase-like converted visitors. This is not a perfect POS reconciliation model, but it gives a session-level conversion signal from the available anonymized events.

## 3. API Architecture Choice

Options considered were FastAPI with in-memory storage, FastAPI with SQLite, and a fuller service with PostgreSQL or Redis. I chose FastAPI with an in-memory `EventStore` for Parts B and C. FastAPI matches the challenge FAQ recommendation and gives automatic docs at `/docs`, Pydantic validation, simple testing with `TestClient`, and clear endpoint definitions.

The in-memory store is the biggest deliberate simplification. It makes the service easy to run in Docker and fast to test. It also makes idempotency straightforward: the store keeps a dictionary keyed by `event_id` and a per-store list for analytics. For a real deployment across 40 stores, this would be replaced by a durable database and probably a streaming ingest path. The current `EventStore` is intentionally isolated so that replacement is a contained change rather than a rewrite.

An LLM recommended adding PostgreSQL immediately for production readiness. I disagreed for this submission because database setup would add operational surface area without improving the core scoring signals as much as correct endpoints, idempotency, and edge-case tests. Instead, I added a simulated store-unavailable mode and a structured 503 response to show how the API boundary should behave when persistence fails.

The API computes metrics at request time rather than maintaining cached aggregates. This is simpler and safer for the small challenge event volume. It also avoids stale dashboard values during evaluation. The trade-off is that for high-volume production use, pre-aggregated tables or streaming materialized views would become necessary.

The Docker choice changed during implementation. The first image installed the full local requirements, including OpenCV and test tools. That worked conceptually but was too heavy for the local Docker Desktop environment and even caused a failed build. I split runtime dependencies into `requirements-api.txt`, leaving `requirements.txt` for local detection and testing. The final Dockerized API starts cleanly with Compose and exposes `/health`, `/metrics`, `/funnel`, `/heatmap`, and `/anomalies`.

## 4. AI Usage Summary

AI helped structure the implementation order, identify likely scoring risks, and generate the first draft of tests. I did not accept AI output blindly. I changed the detector plan for reproducibility, corrected funnel logic after seeing real event distributions, and slimmed the Docker build after observing actual local failure. The submitted code is therefore not just generated boilerplate; it includes decisions made from the uploaded files, the real Docker run, and the metrics produced by the current event stream.

