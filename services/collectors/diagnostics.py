from __future__ import annotations

from typing import Any


def build_attempt_diagnostic(
    *,
    step: str,
    attempted: bool,
    success: bool,
    row_count: int = 0,
    error: str | None = None,
    error_type: str | None = None,
    http_status: int | None = None,
    data_source: str | None = None,
    transport_mode: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "step": step,
        "attempted": attempted,
        "success": success,
        "row_count": row_count,
        "error": error,
        "error_type": error_type,
        "http_status": http_status,
        "data_source": data_source,
        "transport_mode": transport_mode,
        "meta": meta or {},
    }
