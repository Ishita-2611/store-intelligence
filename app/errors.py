from __future__ import annotations


class StoreUnavailableError(RuntimeError):
    """Raised when the backing event store cannot serve a request."""

