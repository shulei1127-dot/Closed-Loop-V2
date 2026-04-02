from __future__ import annotations

from sqlalchemy import select

from core.config import get_settings
from models.normalized_record import NormalizedRecord
from models.task_plan import TaskPlan


CONTRACT_RUNNER_KEYS = {
    "module_code",
    "runner",
    "mode",
    "config_valid",
    "missing_fields",
    "http_statuses",
    "attempted_actions",
    "failed_action",
    "last_error",
    "error_type",
}


def _run_sync(client, module_code: str) -> None:
    response = client.post("/api/sync/run", json={"module_code": module_code, "force": False})
    assert response.status_code == 200


def _get_planned_task(db_session, module_code: str) -> TaskPlan:
    task = db_session.scalars(
        select(TaskPlan)
        .where(TaskPlan.module_code == module_code, TaskPlan.plan_status == "planned")
        .order_by(TaskPlan.created_at.asc())
    ).first()
    assert task is not None
    return task


def _write_file(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("dummy", encoding="utf-8")


def _assert_result_contract(payload: dict, *, expected_mode: str, module_code: str) -> None:
    result_payload = payload["result_payload"]
    assert result_payload["execution_mode"] == expected_mode
    assert isinstance(result_payload["action_trace"], list)
    assert isinstance(result_payload["action_results"], list)
    diagnostics = result_payload["runner_diagnostics"]
    assert CONTRACT_RUNNER_KEYS.issubset(diagnostics.keys())
    assert diagnostics["module_code"] == module_code


def _assert_action_results_are_normalized(payload: dict) -> None:
    for item in payload["result_payload"]["action_results"]:
        assert "action" in item
        assert "status" in item
        assert "http_status" in item
        assert "retryable" in item
        assert "error_message" in item
        assert "error_type" in item


def test_execution_contract_simulated_payloads_are_uniform(client, db_session, monkeypatch, tmp_path) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    for module_code in ("visit", "inspection", "proactive"):
        _run_sync(client, module_code)
        task = _get_planned_task(db_session, module_code)
        response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
        assert response.status_code == 200
        payload = response.json()["item"]
        assert payload["run_status"] == "simulated_success"
        _assert_result_contract(payload, expected_mode="simulated", module_code=module_code)

        diagnostics = payload["result_payload"]["runner_diagnostics"]
        assert diagnostics["config_valid"] is None
        assert diagnostics["missing_fields"] == []
        assert diagnostics["attempted_actions"] == []
        assert diagnostics["failed_action"] is None
        assert diagnostics["last_error"] is None
        assert diagnostics["error_type"] is None

    get_settings.cache_clear()


def test_execution_contract_precheck_failed_config_payloads_are_uniform(
    client,
    db_session,
    monkeypatch,
    tmp_path,
) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")

    module_assertions = {
        "visit": {
            "enable_env": "VISIT_REAL_EXECUTION_ENABLED",
            "prepare": lambda: (
                monkeypatch.setenv("VISIT_REAL_BASE_URL", ""),
                monkeypatch.setenv("VISIT_REAL_TOKEN", ""),
                monkeypatch.setenv("PTS_COOKIE_HEADER", ""),
            ),
            "missing_field": "pts_cookie_header",
        },
        "inspection": {
            "enable_env": "INSPECTION_REAL_EXECUTION_ENABLED",
            "prepare": lambda: (
                monkeypatch.delenv("INSPECTION_REAL_BASE_URL", raising=False),
                monkeypatch.delenv("INSPECTION_REAL_TOKEN", raising=False),
            ),
            "missing_field": "inspection_real_base_url",
        },
        "proactive": {
            "enable_env": "PROACTIVE_REAL_EXECUTION_ENABLED",
            "prepare": lambda: (
                monkeypatch.delenv("PROACTIVE_REAL_BASE_URL", raising=False),
                monkeypatch.delenv("PROACTIVE_REAL_TOKEN", raising=False),
            ),
            "missing_field": "proactive_real_base_url",
        },
    }

    for module_code, assertion in module_assertions.items():
        monkeypatch.setenv(assertion["enable_env"], "true")
        assertion["prepare"]()
        get_settings.cache_clear()

        _run_sync(client, module_code)
        task = _get_planned_task(db_session, module_code)
        response = client.post(f"/api/tasks/{task.id}/precheck")
        assert response.status_code == 200
        payload = response.json()["item"]

        assert payload["run_status"] == "precheck_failed"
        _assert_result_contract(payload, expected_mode="real_precheck", module_code=module_code)
        diagnostics = payload["result_payload"]["runner_diagnostics"]
        assert diagnostics["config_valid"] is False
        assert diagnostics["missing_fields"]
        assert assertion["missing_field"] in diagnostics["missing_fields"]
        assert diagnostics["error_type"] == "config_missing"
        assert diagnostics["failed_action"] is None
        assert diagnostics["last_error"] is None

    get_settings.cache_clear()


def test_execution_contract_real_success_payloads_are_uniform(
    client,
    db_session,
    monkeypatch,
    tmp_path,
    visit_real_server,
    inspection_real_server,
    proactive_real_server,
) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")

    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "session=visit-real-cookie")
    monkeypatch.setenv("VISIT_REAL_BASE_URL", visit_real_server["base_url"])
    monkeypatch.setenv("VISIT_REAL_TOKEN", "visit-real-token")
    monkeypatch.setenv("VISIT_REAL_CREATE_ENDPOINT", "/visit-work-orders")
    monkeypatch.setenv("VISIT_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/assign-owner")
    monkeypatch.setenv("VISIT_REAL_MARK_TARGET_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/mark-target")
    monkeypatch.setenv("VISIT_REAL_FILL_FEEDBACK_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/fill-feedback")
    monkeypatch.setenv("VISIT_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/complete")
    monkeypatch.setenv("VISIT_REAL_FINAL_LINK_PATH", "data.final_link")

    monkeypatch.setenv("INSPECTION_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("INSPECTION_REAL_BASE_URL", inspection_real_server["base_url"])
    monkeypatch.setenv("INSPECTION_REAL_TOKEN", "inspection-real-token")
    monkeypatch.setenv("INSPECTION_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/assign-owner")
    monkeypatch.setenv("INSPECTION_REAL_ADD_MEMBER_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/add-member")
    monkeypatch.setenv("INSPECTION_REAL_UPLOAD_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/upload-reports")
    monkeypatch.setenv("INSPECTION_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/complete")
    monkeypatch.setenv("INSPECTION_REAL_FINAL_LINK_PATH", "data.final_link")

    monkeypatch.setenv("PROACTIVE_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_REAL_BASE_URL", proactive_real_server["base_url"])
    monkeypatch.setenv("PROACTIVE_REAL_TOKEN", "proactive-real-token")
    monkeypatch.setenv("PROACTIVE_REAL_CREATE_ENDPOINT", "/proactive-work-orders")
    monkeypatch.setenv("PROACTIVE_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/proactive-work-orders/{work_order_id}/assign-owner")
    monkeypatch.setenv("PROACTIVE_REAL_FEEDBACK_ENDPOINT_TEMPLATE", "/proactive-work-orders/{work_order_id}/fill-feedback")
    monkeypatch.setenv("PROACTIVE_REAL_FINAL_LINK_PATH", "data.final_link")

    get_settings.cache_clear()

    _run_sync(client, "visit")
    visit_task = _get_planned_task(db_session, "visit")
    visit_record = db_session.get(NormalizedRecord, visit_task.normalized_record_id)
    assert visit_record is not None
    visit_data = dict(visit_record.normalized_data)
    visit_data["pts_link"] = f"{visit_real_server['base_url']}/pts/{visit_data['delivery_id']}"
    visit_record.normalized_data = visit_data

    _run_sync(client, "inspection")
    inspection_task = _get_planned_task(db_session, "inspection")
    inspection_record = db_session.get(NormalizedRecord, inspection_task.normalized_record_id)
    assert inspection_record is not None
    inspection_data = dict(inspection_record.normalized_data)
    inspection_data["work_order_id"] = "WO-REAL-001"
    inspection_data["work_order_link"] = (
        f"{inspection_real_server['base_url']}/inspection-work-orders/WO-REAL-001"
    )
    inspection_record.normalized_data = inspection_data

    _run_sync(client, "proactive")
    proactive_task = _get_planned_task(db_session, "proactive")
    db_session.commit()

    for module_code, task_id in (
        ("visit", visit_task.id),
        ("inspection", inspection_task.id),
        ("proactive", proactive_task.id),
    ):
        response = client.post(f"/api/tasks/{task_id}/execute", json={"dry_run": False})
        assert response.status_code == 200
        payload = response.json()["item"]
        assert payload["run_status"] == "success"
        assert payload["final_link"]
        _assert_result_contract(payload, expected_mode="real", module_code=module_code)
        diagnostics = payload["result_payload"]["runner_diagnostics"]
        assert diagnostics["config_valid"] is True
        assert diagnostics["missing_fields"] == []
        assert diagnostics["error_type"] is None
        assert diagnostics["failed_action"] is None
        assert diagnostics["last_error"] is None
        _assert_action_results_are_normalized(payload)

    get_settings.cache_clear()


def test_execution_contract_retryable_http_failures_are_uniform(
    client,
    db_session,
    monkeypatch,
    tmp_path,
    visit_real_server,
    inspection_real_server,
    proactive_real_server,
) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")

    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "session=visit-real-cookie")
    monkeypatch.setenv("VISIT_REAL_BASE_URL", visit_real_server["base_url"])
    monkeypatch.setenv("VISIT_REAL_TOKEN", "visit-real-token")
    monkeypatch.setenv("VISIT_REAL_CREATE_ENDPOINT", "/visit-work-orders")
    monkeypatch.setenv("VISIT_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/assign-owner-fail")
    monkeypatch.setenv("VISIT_REAL_MARK_TARGET_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/mark-target")
    monkeypatch.setenv("VISIT_REAL_FILL_FEEDBACK_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/fill-feedback")
    monkeypatch.setenv("VISIT_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/complete")
    monkeypatch.setenv("VISIT_REAL_FINAL_LINK_PATH", "data.final_link")

    monkeypatch.setenv("INSPECTION_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("INSPECTION_REAL_BASE_URL", inspection_real_server["base_url"])
    monkeypatch.setenv("INSPECTION_REAL_TOKEN", "inspection-real-token")
    monkeypatch.setenv("INSPECTION_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/assign-owner")
    monkeypatch.setenv("INSPECTION_REAL_ADD_MEMBER_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/add-member")
    monkeypatch.setenv("INSPECTION_REAL_UPLOAD_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/upload-reports-fail")
    monkeypatch.setenv("INSPECTION_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/complete")
    monkeypatch.setenv("INSPECTION_REAL_FINAL_LINK_PATH", "data.final_link")

    monkeypatch.setenv("PROACTIVE_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_REAL_BASE_URL", proactive_real_server["base_url"])
    monkeypatch.setenv("PROACTIVE_REAL_TOKEN", "proactive-real-token")
    monkeypatch.setenv("PROACTIVE_REAL_CREATE_ENDPOINT", "/proactive-work-orders")
    monkeypatch.setenv("PROACTIVE_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/proactive-work-orders/{work_order_id}/assign-owner-fail")
    monkeypatch.setenv("PROACTIVE_REAL_FEEDBACK_ENDPOINT_TEMPLATE", "/proactive-work-orders/{work_order_id}/fill-feedback")
    monkeypatch.setenv("PROACTIVE_REAL_FINAL_LINK_PATH", "data.final_link")

    get_settings.cache_clear()

    _run_sync(client, "visit")
    visit_task = _get_planned_task(db_session, "visit")
    visit_record = db_session.get(NormalizedRecord, visit_task.normalized_record_id)
    assert visit_record is not None
    visit_data = dict(visit_record.normalized_data)
    visit_data["pts_link"] = f"{visit_real_server['base_url']}/pts/{visit_data['delivery_id']}"
    visit_record.normalized_data = visit_data

    _run_sync(client, "inspection")
    inspection_task = _get_planned_task(db_session, "inspection")
    inspection_record = db_session.get(NormalizedRecord, inspection_task.normalized_record_id)
    assert inspection_record is not None
    inspection_data = dict(inspection_record.normalized_data)
    inspection_data["work_order_id"] = "WO-REAL-001"
    inspection_data["work_order_link"] = (
        f"{inspection_real_server['base_url']}/inspection-work-orders/WO-REAL-001"
    )
    inspection_record.normalized_data = inspection_data

    _run_sync(client, "proactive")
    proactive_task = _get_planned_task(db_session, "proactive")
    db_session.commit()

    for module_code, task_id, failed_action in (
        ("visit", visit_task.id, "assign_owner"),
        ("inspection", inspection_task.id, "upload_report_files"),
        ("proactive", proactive_task.id, "assign_owner"),
    ):
        response = client.post(f"/api/tasks/{task_id}/execute", json={"dry_run": False})
        assert response.status_code == 200
        payload = response.json()["item"]
        assert payload["run_status"] == "failed"
        _assert_result_contract(payload, expected_mode="real_attempted", module_code=module_code)
        diagnostics = payload["result_payload"]["runner_diagnostics"]
        assert diagnostics["error_type"] == "http_error"
        assert diagnostics["failed_action"] == failed_action
        failed = payload["result_payload"]["action_results"][-1]
        assert failed["retryable"] is True
        assert failed["error_type"] == "http_error"

    get_settings.cache_clear()
