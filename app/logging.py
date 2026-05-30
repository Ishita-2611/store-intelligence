from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response


logger = logging.getLogger("store_intelligence")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


async def structured_request_logger(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    start = time.perf_counter()
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    store_id = request.path_params.get("store_id")
    event_count = None
    status_code = 500

    if request.url.path == "/events/ingest":
        body = await request.body()
        request._body = body
        try:
            payload = json.loads(body or b"{}")
            events = payload.get("events", payload if isinstance(payload, list) else [])
            event_count = len(events) if isinstance(events, list) else None
            if isinstance(events, list) and events:
                store_id = events[0].get("store_id")
        except json.JSONDecodeError:
            event_count = None

    response = await call_next(request)
    status_code = response.status_code
    response.headers["x-trace-id"] = trace_id

    log_payload = {
        "trace_id": trace_id,
        "store_id": store_id,
        "endpoint": request.url.path,
        "method": request.method,
        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        "event_count": event_count,
        "status_code": status_code,
    }
    logger.info(json.dumps(log_payload, separators=(",", ":"), sort_keys=True))
    return response

