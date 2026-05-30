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
