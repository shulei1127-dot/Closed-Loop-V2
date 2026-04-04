from __future__ import annotations

from typing import Any


STATUS_LABELS = {
    "success": "成功",
    "partial": "部分成功",
    "failed": "失败",
    "planned": "待执行",
    "skipped": "已跳过",
    "precheck_failed": "预检查失败",
    "precheck_passed": "预检查通过",
    "dry_run_ready": "演练就绪",
    "simulated_success": "模拟执行成功",
    "manual_required": "需人工处理",
    "unknown": "未知状态",
}

STATUS_TONES = {
    "success": "success",
    "partial": "warning",
    "planned": "success",
    "precheck_passed": "success",
    "dry_run_ready": "success",
    "simulated_success": "success",
    "skipped": "warning",
    "manual_required": "manual",
    "failed": "failed",
    "precheck_failed": "failed",
    "unknown": "unknown",
}

ERROR_COPY = {
    "config_missing": "执行配置缺失，需要先补齐配置。",
    "session_expired": "PTS 会话已失效，请重新登录 PTS 或更新 Cookie。",
    "http_error": "外部系统请求失败，可稍后重试。",
    "timeout": "请求超时，通常可稍后重试。",
    "response_invalid": "外部返回异常，需排查接口返回。",
    "business_rejected": "业务条件不满足，无法自动继续。",
    "permission_denied": "权限不足，需要人工处理。",
    "manual_required": "需要人工介入处理。",
    "unknown_error": "发生未知异常，需要进一步排查。",
}


def status_label(status: str | None) -> str:
    if not status:
        return "未同步"
    return STATUS_LABELS.get(status, status)


def status_tone(status: str | None) -> str:
    if not status:
        return "unknown"
    return STATUS_TONES.get(status, "unknown")


def extract_error_type(
    *,
    run_status: str | None = None,
    result_payload: dict[str, Any] | None = None,
    manual_required: bool = False,
) -> str | None:
    payload = result_payload or {}
    diagnostics = payload.get("runner_diagnostics") or {}
    error_type = diagnostics.get("error_type")
    if error_type:
        return str(error_type)
    if manual_required or run_status == "manual_required":
        return "manual_required"
    if run_status == "precheck_failed" and diagnostics.get("config_valid") is False:
        return "config_missing"
    return None


def explain_error(
    *,
    error_type: str | None,
    retryable: bool = False,
    manual_required: bool = False,
    error_message: str | None = None,
) -> str:
    if manual_required and error_type is None:
        error_type = "manual_required"
    if error_type in ERROR_COPY:
        base = ERROR_COPY[error_type]
    else:
        base = error_message or "暂无业务解释。"
    if retryable and "可稍后重试" not in base:
        return f"{base.rstrip('。')}，可稍后重试。"
    return base


def build_run_view(
    *,
    run_status: str | None,
    result_payload: dict[str, Any] | None = None,
    manual_required: bool = False,
    retryable: bool = False,
    error_message: str | None = None,
    customer_name: str | None = None,
    task_plan_id: str | None = None,
    task_run_id: str | None = None,
) -> dict[str, Any]:
    payload = result_payload or {}
    diagnostics = payload.get("runner_diagnostics") or {}
    derived_customer = customer_name or payload.get("customer_name")
    derived_error_type = extract_error_type(
        run_status=run_status,
        result_payload=payload,
        manual_required=manual_required,
    )
    return {
        "display_status": status_label(run_status),
        "status_tone": status_tone(run_status),
        "error_type": derived_error_type,
        "business_explanation": explain_error(
            error_type=derived_error_type,
            retryable=retryable,
            manual_required=manual_required,
            error_message=error_message,
        ),
        "retryable_label": "可重试" if retryable else "不可重试",
        "manual_required_label": "需人工处理" if manual_required else "自动处理",
        "rerun_label": "可重跑" if task_plan_id else "",
        "customer_name": derived_customer or "未知客户",
        "execution_mode": payload.get("execution_mode"),
        "failed_action": diagnostics.get("failed_action"),
        "last_error": diagnostics.get("last_error"),
        "task_plan_id": task_plan_id,
        "task_run_id": task_run_id,
        "final_link": payload.get("final_link"),
        "detail_url": f"/console/task-runs/{task_run_id}" if task_run_id else None,
    }
