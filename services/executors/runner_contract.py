from __future__ import annotations

from typing import Any


def classify_execution_error(
    *,
    status: str | None = None,
    http_status: int | None = None,
    error_type: str | None = None,
) -> str | None:
    if error_type is not None:
        return error_type
    if http_status == 403:
        return "permission_denied"
    if status == "manual_required":
        return "manual_required"
    if status == "member_missing":
        return "business_rejected"
    if http_status is not None and http_status >= 500:
        return "http_error"
    if http_status is not None and http_status >= 400:
        return "business_rejected"
    return None


def is_retryable_error(*, error_type: str | None, http_status: int | None = None) -> bool:
    if error_type == "timeout":
        return True
    if error_type == "http_error" and http_status is not None and http_status >= 500:
        return True
    return False


def normalize_action_result(action_result: dict[str, Any]) -> dict[str, Any]:
    result = dict(action_result)
    result.setdefault("http_status", None)
    result.setdefault("error_message", None)
    resolved_error_type = classify_execution_error(
        status=result.get("status"),
        http_status=result.get("http_status"),
        error_type=result.get("error_type"),
    )
    result["error_type"] = resolved_error_type
    result["retryable"] = bool(
        result.get(
            "retryable",
            is_retryable_error(
                error_type=resolved_error_type,
                http_status=result.get("http_status"),
            ),
        )
    )
    return result


def normalize_action_results(action_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_action_result(item) for item in action_results]


def build_runner_diagnostics(
    *,
    module_code: str,
    runner: str,
    mode: str,
    **extra: Any,
) -> dict[str, Any]:
    diagnostics = {
        "module_code": module_code,
        "runner": runner,
        "mode": mode,
        "config_valid": None,
        "missing_fields": [],
        "http_statuses": [],
        "attempted_actions": [],
        "failed_action": None,
        "last_error": None,
        "error_type": None,
    }
    diagnostics.update(extra)
    return diagnostics


def build_simulated_runner_diagnostics(
    *,
    module_code: str,
    runner: str,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    return build_runner_diagnostics(
        module_code=module_code,
        runner=runner,
        mode="simulated",
        reason=reason,
        **extra,
    )


def apply_validation_result(diagnostics: dict[str, Any], missing_fields: list[str]) -> dict[str, Any]:
    diagnostics["missing_fields"] = list(missing_fields)
    diagnostics["config_valid"] = not missing_fields
    diagnostics["error_type"] = "config_missing" if missing_fields else None
    diagnostics["failed_action"] = None
    diagnostics["last_error"] = None
    return diagnostics


def refresh_runner_diagnostics(
    diagnostics: dict[str, Any],
    action_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized = normalize_action_results(action_results)
    diagnostics["http_statuses"] = [item.get("http_status") for item in normalized]
    diagnostics["attempted_actions"] = [item.get("action") for item in normalized]
    return normalized


def mark_runner_failure(
    diagnostics: dict[str, Any],
    *,
    action_result: dict[str, Any] | None = None,
    error_type: str | None = None,
    failed_action: str | None = None,
    last_error: str | None = None,
) -> None:
    normalized = normalize_action_result(action_result) if action_result is not None else None
    diagnostics["failed_action"] = failed_action or (normalized or {}).get("action")
    diagnostics["last_error"] = last_error or (normalized or {}).get("error_message")
    diagnostics["error_type"] = (
        error_type
        or (normalized or {}).get("error_type")
        or classify_execution_error()
    )


def mark_runner_success(diagnostics: dict[str, Any]) -> None:
    diagnostics["failed_action"] = None
    diagnostics["last_error"] = None
    diagnostics["error_type"] = None
