from sqlalchemy import select

from core.config import get_settings
from models.normalized_record import NormalizedRecord
from models.task_plan import TaskPlan
from models.task_run import TaskRun
from services.executors.visit_real_runner import VisitRealRunOutcome, VisitRealRunner


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


def test_visit_executor_dry_run_writes_task_run_and_detail(client, db_session) -> None:
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": True})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "dry_run_ready"
    assert payload["manual_required"] is False
    assert payload["result_payload"]["execution_mode"] == "dry_run"
    assert payload["result_payload"]["action_trace"]

    task_runs = list(db_session.scalars(select(TaskRun)).all())
    assert len(task_runs) == 1
    assert task_runs[0].run_status == "dry_run_ready"

    detail = client.get(f"/api/task-runs/{payload['task_run_id']}")
    assert detail.status_code == 200
    assert detail.json()["item"]["task_run_id"] == payload["task_run_id"]


def test_precheck_failed_for_non_planned_task(client, db_session) -> None:
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    task.plan_status = "skipped"
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/precheck")
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "precheck_failed"
    assert "plan_status" in (payload["error_message"] or "")

    task_runs = list(db_session.scalars(select(TaskRun)).all())
    assert len(task_runs) == 1
    assert task_runs[0].run_status == "precheck_failed"


def test_visit_execute_returns_simulated_success(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "false")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "false")
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "simulated_success"
    assert payload["manual_required"] is False
    assert payload["final_link"].startswith("https://pts.example.com/simulated/visit/")
    assert payload["result_payload"]["execution_mode"] == "simulated"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "simulated"
    get_settings.cache_clear()


def test_visit_precheck_fails_when_real_execution_enabled_but_config_missing(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("VISIT_REAL_BASE_URL", "")
    monkeypatch.setenv("VISIT_REAL_TOKEN", "")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "")
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: False)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")

    response = client.post(f"/api/tasks/{task.id}/precheck")
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "precheck_failed"
    assert payload["result_payload"]["execution_mode"] == "real_precheck"
    assert payload["result_payload"]["runner_diagnostics"]["config_valid"] is False
    assert "pts_cookie_header" in payload["result_payload"]["runner_diagnostics"]["missing_fields"]
    get_settings.cache_clear()


def test_visit_execute_returns_manual_required_for_unsupported_visit_type(client, db_session) -> None:
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["visit_type"] = "未知回访类型"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "manual_required"
    assert payload["result_payload"]["execution_mode"] == "manual_required"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "simulated"


def test_visit_execute_runs_real_runner_successfully(client, db_session, monkeypatch, visit_real_server) -> None:
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
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: False)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["pts_link"] = f"{visit_real_server['base_url']}/pts/{data['delivery_id']}"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "success"
    assert payload["result_payload"]["execution_mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert len(payload["result_payload"]["action_results"]) == 6
    assert payload["result_payload"]["action_results"][2]["action"] == "assign_owner"
    assert payload["result_payload"]["action_results"][3]["action"] == "mark_visit_target"
    assert payload["result_payload"]["action_results"][4]["action"] == "fill_feedback"
    assert payload["result_payload"]["action_results"][5]["action"] == "complete_visit"
    assert payload["final_link"] == f"{visit_real_server['base_url']}/work-orders/{data['delivery_id']}"
    assert visit_real_server["request_log"][0]["method"] == "GET"
    assert visit_real_server["request_log"][1]["method"] == "POST"
    assert visit_real_server["request_log"][2]["path"].endswith("/assign-owner")
    assert visit_real_server["request_log"][3]["path"].endswith("/mark-target")
    assert visit_real_server["request_log"][4]["path"].endswith("/fill-feedback")
    assert visit_real_server["request_log"][5]["path"].endswith("/complete")
    get_settings.cache_clear()


def test_visit_execute_runs_pts_direct_runner_successfully(client, db_session, monkeypatch, visit_real_server) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "session=visit-real-cookie")
    monkeypatch.setenv("PTS_BASE_URL", visit_real_server["base_url"])
    monkeypatch.delenv("VISIT_REAL_BASE_URL", raising=False)
    monkeypatch.delenv("VISIT_REAL_TOKEN", raising=False)
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: False)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["pts_link"] = f"{visit_real_server['base_url']}/pts/{data['delivery_id']}"
    data["visit_type"] = "客户满意度调研"
    data["satisfaction"] = "十分满意"
    data["feedback_note"] = "来自钉钉文档的备注"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "success"
    assert payload["result_payload"]["execution_mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["transport_mode"] == "pts_direct"
    assert payload["result_payload"]["action_results"][1]["action"] == "create_visit_work_order"
    assert payload["result_payload"]["action_results"][4]["action"] == "fill_feedback"
    assert payload["final_link"].endswith("/return-visit/detail/visit-1")
    query_requests = [item for item in visit_real_server["request_log"] if item["path"] == "/query"]
    assert query_requests
    get_settings.cache_clear()


def test_visit_execute_prefers_browser_session_mode_when_available(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("VISIT_REAL_BASE_URL", raising=False)
    monkeypatch.delenv("VISIT_REAL_TOKEN", raising=False)
    monkeypatch.setenv("PTS_BASE_URL", "https://pts.example.com")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "")
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: True)

    async def fake_browser_run(self, context, actions, diagnostics):
        diagnostics["transport_mode"] = "pts_browser_session"
        diagnostics["config_valid"] = True
        return VisitRealRunOutcome(
            run_status="success",
            final_link="https://pts.example.com/return-visit/detail/browser-mode",
            action_results=[{"action": "open_pts_delivery_link", "status": "success", "http_status": 200}],
            runner_diagnostics=diagnostics,
        )

    monkeypatch.setattr(VisitRealRunner, "_run_pts_browser_mode", fake_browser_run)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "success"
    assert payload["result_payload"]["runner_diagnostics"]["transport_mode"] == "pts_browser_session"
    assert payload["final_link"].endswith("/browser-mode")
    get_settings.cache_clear()


def test_visit_execute_preserves_final_link_when_writeback_fails(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("VISIT_REAL_BASE_URL", raising=False)
    monkeypatch.delenv("VISIT_REAL_TOKEN", raising=False)
    monkeypatch.setenv("PTS_BASE_URL", "https://pts.example.com")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "")
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: True)

    async def fake_browser_run(self, context, actions):
        diagnostics = self._base_diagnostics()
        diagnostics["transport_mode"] = "pts_browser_session"
        diagnostics["config_valid"] = True
        return VisitRealRunOutcome(
            run_status="failed",
            final_link="https://pts.example.com/return-visit/detail/writeback-failed",
            error_message="回写钉钉文档失败",
            retryable=False,
            action_results=[
                {
                    "action": "writeback_visit_link_to_dingtalk",
                    "status": "failed",
                    "error_message": "回写钉钉文档失败",
                    "error_type": "unknown_error",
                    "retryable": False,
                    "http_status": None,
                }
            ],
            runner_diagnostics=diagnostics,
        )

    monkeypatch.setattr(VisitRealRunner, "run", fake_browser_run)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "failed"
    assert payload["final_link"].endswith("/writeback-failed")
    assert payload["result_payload"]["action_results"][0]["action"] == "writeback_visit_link_to_dingtalk"
    get_settings.cache_clear()


def test_visit_same_source_row_cannot_execute_twice_after_success(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("VISIT_REAL_BASE_URL", raising=False)
    monkeypatch.delenv("VISIT_REAL_TOKEN", raising=False)
    monkeypatch.setenv("PTS_BASE_URL", "https://pts.example.com")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "")
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: True)

    async def fake_browser_run(self, context, actions, diagnostics):
        diagnostics["transport_mode"] = "pts_browser_session"
        diagnostics["config_valid"] = True
        return VisitRealRunOutcome(
            run_status="success",
            final_link="https://pts.example.com/return-visit/detail/once-only",
            action_results=[{"action": "complete_visit", "status": "success", "http_status": 200}],
            runner_diagnostics=diagnostics,
        )

    monkeypatch.setattr(VisitRealRunner, "_run_pts_browser_mode", fake_browser_run)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")

    first = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert first.status_code == 200
    assert first.json()["item"]["run_status"] == "success"

    second = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert second.status_code == 200
    payload = second.json()["item"]
    assert payload["run_status"] == "precheck_failed"
    assert "禁止重复执行" in (payload["error_message"] or "")
    assert payload["final_link"].endswith("/once-only")
    get_settings.cache_clear()


def test_visit_execute_returns_session_expired_when_pts_cookie_is_invalid(
    client, db_session, monkeypatch, visit_real_server
) -> None:
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
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: False)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["pts_link"] = f"{visit_real_server['base_url']}/pts-login/{data['delivery_id']}"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "failed"
    assert payload["error_message"] == "PTS 会话已失效，请重新登录 PTS 或更新 Cookie"
    assert payload["result_payload"]["runner_diagnostics"]["error_type"] == "session_expired"
    assert payload["result_payload"]["runner_diagnostics"]["failed_action"] == "open_pts_delivery_link"
    assert payload["result_payload"]["action_results"][0]["error_type"] == "session_expired"
    get_settings.cache_clear()


def test_visit_execute_assign_owner_failure_records_diagnostics(client, db_session, monkeypatch, visit_real_server) -> None:
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
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: False)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["pts_link"] = f"{visit_real_server['base_url']}/pts/{data['delivery_id']}"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "failed"
    assert payload["result_payload"]["execution_mode"] == "real_attempted"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["failed_action"] == "assign_owner"
    assert payload["result_payload"]["runner_diagnostics"]["last_error"] is not None
    assert payload["final_link"] is None
    get_settings.cache_clear()


def test_visit_execute_mark_visit_target_failure_records_diagnostics(client, db_session, monkeypatch, visit_real_server) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "session=visit-real-cookie")
    monkeypatch.setenv("VISIT_REAL_BASE_URL", visit_real_server["base_url"])
    monkeypatch.setenv("VISIT_REAL_TOKEN", "visit-real-token")
    monkeypatch.setenv("VISIT_REAL_CREATE_ENDPOINT", "/visit-work-orders")
    monkeypatch.setenv("VISIT_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/assign-owner")
    monkeypatch.setenv("VISIT_REAL_MARK_TARGET_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/mark-target-fail")
    monkeypatch.setenv("VISIT_REAL_FILL_FEEDBACK_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/fill-feedback")
    monkeypatch.setenv("VISIT_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/complete")
    monkeypatch.setenv("VISIT_REAL_FINAL_LINK_PATH", "data.final_link")
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: False)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["pts_link"] = f"{visit_real_server['base_url']}/pts/{data['delivery_id']}"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "failed"
    assert payload["result_payload"]["execution_mode"] == "real_attempted"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["failed_action"] == "mark_visit_target"
    assert payload["result_payload"]["runner_diagnostics"]["last_error"] is not None
    assert payload["final_link"] is None
    get_settings.cache_clear()


def test_visit_execute_fill_feedback_failure_records_diagnostics(client, db_session, monkeypatch, visit_real_server) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "session=visit-real-cookie")
    monkeypatch.setenv("VISIT_REAL_BASE_URL", visit_real_server["base_url"])
    monkeypatch.setenv("VISIT_REAL_TOKEN", "visit-real-token")
    monkeypatch.setenv("VISIT_REAL_CREATE_ENDPOINT", "/visit-work-orders")
    monkeypatch.setenv("VISIT_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/assign-owner")
    monkeypatch.setenv("VISIT_REAL_MARK_TARGET_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/mark-target")
    monkeypatch.setenv("VISIT_REAL_FILL_FEEDBACK_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/fill-feedback-fail")
    monkeypatch.setenv("VISIT_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/complete")
    monkeypatch.setenv("VISIT_REAL_FINAL_LINK_PATH", "data.final_link")
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: False)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["pts_link"] = f"{visit_real_server['base_url']}/pts/{data['delivery_id']}"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "failed"
    assert payload["result_payload"]["execution_mode"] == "real_attempted"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["failed_action"] == "fill_feedback"
    assert payload["result_payload"]["runner_diagnostics"]["last_error"] is not None
    assert payload["final_link"] is None
    get_settings.cache_clear()


def test_visit_execute_complete_visit_failure_records_diagnostics(client, db_session, monkeypatch, visit_real_server) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "session=visit-real-cookie")
    monkeypatch.setenv("VISIT_REAL_BASE_URL", visit_real_server["base_url"])
    monkeypatch.setenv("VISIT_REAL_TOKEN", "visit-real-token")
    monkeypatch.setenv("VISIT_REAL_CREATE_ENDPOINT", "/visit-work-orders")
    monkeypatch.setenv("VISIT_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/assign-owner")
    monkeypatch.setenv("VISIT_REAL_MARK_TARGET_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/mark-target")
    monkeypatch.setenv("VISIT_REAL_FILL_FEEDBACK_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/fill-feedback")
    monkeypatch.setenv("VISIT_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/visit-work-orders/{delivery_id}/complete-fail")
    monkeypatch.setenv("VISIT_REAL_FINAL_LINK_PATH", "data.final_link")
    monkeypatch.setattr(VisitRealRunner, "_browser_session_available", lambda self: False)
    get_settings.cache_clear()
    _run_sync(client, "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["pts_link"] = f"{visit_real_server['base_url']}/pts/{data['delivery_id']}"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "failed"
    assert payload["result_payload"]["execution_mode"] == "real_attempted"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["failed_action"] == "complete_visit"
    assert payload["result_payload"]["runner_diagnostics"]["last_error"] is not None
    assert payload["final_link"] is None
    get_settings.cache_clear()


def test_inspection_execute_returns_manual_required_without_reports(client, db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    _run_sync(client, "inspection")
    task = _get_planned_task(db_session, "inspection")

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "manual_required"
    assert payload["manual_required"] is True
    assert payload["result_payload"]["report_match"]["match_strategy"] == "no_match"
    assert payload["result_payload"]["execution_mode"] == "manual_required"
    get_settings.cache_clear()


def test_inspection_execute_returns_simulated_success_with_reports(client, db_session, monkeypatch, tmp_path) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    _run_sync(client, "inspection")
    task = _get_planned_task(db_session, "inspection")

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "simulated_success"
    assert payload["manual_required"] is False
    assert payload["result_payload"]["report_match"]["matched"] is True
    assert payload["result_payload"]["execution_mode"] == "simulated"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "simulated"
    assert payload["result_payload"]["upload_candidates"]["word"]
    assert payload["result_payload"]["upload_candidates"]["pdf"]
    get_settings.cache_clear()


def test_inspection_precheck_fails_when_real_execution_enabled_but_config_missing(
    client,
    db_session,
    monkeypatch,
    tmp_path,
) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("INSPECTION_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("INSPECTION_REAL_BASE_URL", raising=False)
    monkeypatch.delenv("INSPECTION_REAL_TOKEN", raising=False)
    get_settings.cache_clear()
    _run_sync(client, "inspection")
    task = _get_planned_task(db_session, "inspection")

    response = client.post(f"/api/tasks/{task.id}/precheck")
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "precheck_failed"
    assert payload["result_payload"]["execution_mode"] == "real_precheck"
    assert payload["result_payload"]["runner_diagnostics"]["config_valid"] is False
    assert "inspection_real_base_url" in payload["result_payload"]["runner_diagnostics"]["missing_fields"]
    get_settings.cache_clear()


def test_inspection_execute_runs_real_runner_successfully(
    client,
    db_session,
    monkeypatch,
    tmp_path,
    inspection_real_server,
) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("INSPECTION_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("INSPECTION_REAL_BASE_URL", inspection_real_server["base_url"])
    monkeypatch.setenv("INSPECTION_REAL_TOKEN", "inspection-real-token")
    monkeypatch.setenv("INSPECTION_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/assign-owner")
    monkeypatch.setenv("INSPECTION_REAL_ADD_MEMBER_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/add-member")
    monkeypatch.setenv("INSPECTION_REAL_UPLOAD_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/upload-reports")
    monkeypatch.setenv("INSPECTION_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/complete")
    monkeypatch.setenv("INSPECTION_REAL_FINAL_LINK_PATH", "data.final_link")
    get_settings.cache_clear()
    _run_sync(client, "inspection")
    task = _get_planned_task(db_session, "inspection")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["work_order_id"] = "WO-REAL-001"
    data["work_order_link"] = f"{inspection_real_server['base_url']}/inspection-work-orders/WO-REAL-001"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "success"
    assert payload["result_payload"]["execution_mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert len(payload["result_payload"]["action_results"]) == 4
    assert payload["result_payload"]["action_results"][0]["action"] == "open_inspection_work_order"
    assert payload["result_payload"]["action_results"][1]["action"] == "assign_owner"
    assert payload["result_payload"]["action_results"][2]["action"] == "upload_report_files"
    assert payload["result_payload"]["action_results"][3]["action"] == "complete_inspection"
    assert payload["final_link"] == f"{inspection_real_server['base_url']}/inspection-work-orders/WO-REAL-001/completed"
    assert inspection_real_server["request_log"][0]["method"] == "GET"
    assert inspection_real_server["request_log"][1]["path"].endswith("/assign-owner")
    assert inspection_real_server["request_log"][2]["path"].endswith("/upload-reports")
    assert inspection_real_server["request_log"][3]["path"].endswith("/complete")
    get_settings.cache_clear()


def test_inspection_execute_upload_failure_records_diagnostics(
    client,
    db_session,
    monkeypatch,
    tmp_path,
    inspection_real_server,
) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("INSPECTION_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("INSPECTION_REAL_BASE_URL", inspection_real_server["base_url"])
    monkeypatch.setenv("INSPECTION_REAL_TOKEN", "inspection-real-token")
    monkeypatch.setenv("INSPECTION_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/assign-owner")
    monkeypatch.setenv("INSPECTION_REAL_ADD_MEMBER_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/add-member")
    monkeypatch.setenv("INSPECTION_REAL_UPLOAD_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/upload-reports-fail")
    monkeypatch.setenv("INSPECTION_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/complete")
    monkeypatch.setenv("INSPECTION_REAL_FINAL_LINK_PATH", "data.final_link")
    get_settings.cache_clear()
    _run_sync(client, "inspection")
    task = _get_planned_task(db_session, "inspection")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["work_order_id"] = "WO-REAL-001"
    data["work_order_link"] = f"{inspection_real_server['base_url']}/inspection-work-orders/WO-REAL-001"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "failed"
    assert payload["result_payload"]["execution_mode"] == "real_attempted"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["failed_action"] == "upload_report_files"
    assert payload["result_payload"]["runner_diagnostics"]["last_error"] is not None
    assert payload["final_link"] is None
    get_settings.cache_clear()


def test_inspection_execute_assign_owner_add_member_then_success(
    client,
    db_session,
    monkeypatch,
    tmp_path,
    inspection_real_server,
) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("INSPECTION_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("INSPECTION_REAL_BASE_URL", inspection_real_server["base_url"])
    monkeypatch.setenv("INSPECTION_REAL_TOKEN", "inspection-real-token")
    monkeypatch.setenv("INSPECTION_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/assign-owner")
    monkeypatch.setenv("INSPECTION_REAL_ADD_MEMBER_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/add-member")
    monkeypatch.setenv("INSPECTION_REAL_UPLOAD_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/upload-reports")
    monkeypatch.setenv("INSPECTION_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/complete")
    monkeypatch.setenv("INSPECTION_REAL_FINAL_LINK_PATH", "data.final_link")
    get_settings.cache_clear()
    _run_sync(client, "inspection")
    task = _get_planned_task(db_session, "inspection")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["work_order_id"] = "WO-MEMBER-001"
    data["work_order_link"] = f"{inspection_real_server['base_url']}/inspection-work-orders/WO-MEMBER-001"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "success"
    assert payload["result_payload"]["execution_mode"] == "real"
    assert [item["action"] for item in payload["result_payload"]["action_results"]] == [
        "open_inspection_work_order",
        "assign_owner",
        "add_member_if_missing",
        "assign_owner",
        "upload_report_files",
        "complete_inspection",
    ]
    assert payload["result_payload"]["action_results"][1]["status"] == "member_missing"
    assert payload["result_payload"]["action_results"][2]["status"] == "success"
    assert payload["result_payload"]["runner_diagnostics"]["attempted_actions"][-1] == "complete_inspection"
    get_settings.cache_clear()


def test_inspection_execute_permission_denied_returns_manual_required(
    client,
    db_session,
    monkeypatch,
    tmp_path,
    inspection_real_server,
) -> None:
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.docx")
    _write_file(tmp_path / "南京真实客户雷池巡检报告-2026.03.27.pdf")
    monkeypatch.setenv("INSPECTION_REPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("INSPECTION_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("INSPECTION_REAL_BASE_URL", inspection_real_server["base_url"])
    monkeypatch.setenv("INSPECTION_REAL_TOKEN", "inspection-real-token")
    monkeypatch.setenv("INSPECTION_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/assign-owner")
    monkeypatch.setenv("INSPECTION_REAL_ADD_MEMBER_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/add-member")
    monkeypatch.setenv("INSPECTION_REAL_UPLOAD_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/upload-reports")
    monkeypatch.setenv("INSPECTION_REAL_COMPLETE_ENDPOINT_TEMPLATE", "/inspection-work-orders/{work_order_id}/complete")
    monkeypatch.setenv("INSPECTION_REAL_FINAL_LINK_PATH", "data.final_link")
    get_settings.cache_clear()
    _run_sync(client, "inspection")
    task = _get_planned_task(db_session, "inspection")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["work_order_id"] = "WO-DENIED-001"
    data["work_order_link"] = f"{inspection_real_server['base_url']}/inspection-work-orders/WO-DENIED-001"
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "manual_required"
    assert payload["manual_required"] is True
    assert payload["result_payload"]["execution_mode"] == "real_attempted"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["failed_action"] == "assign_owner"
    assert "权限" in (payload["error_message"] or "")
    get_settings.cache_clear()


def test_proactive_execute_returns_simulated_success(client, db_session) -> None:
    _run_sync(client, "proactive")
    task = _get_planned_task(db_session, "proactive")

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "simulated_success"
    assert payload["manual_required"] is False
    assert payload["final_link"].startswith("https://pts.example.com/simulated/proactive/")
    assert payload["result_payload"]["execution_mode"] == "simulated"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "simulated"


def test_proactive_execute_returns_manual_required_without_contact(client, db_session) -> None:
    _run_sync(client, "proactive")
    task = _get_planned_task(db_session, "proactive")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None
    data = dict(record.normalized_data)
    data["contact_name"] = None
    data["contact_phone"] = None
    record.normalized_data = data
    db_session.commit()

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "manual_required"
    assert payload["manual_required"] is True
    assert payload["result_payload"]["execution_mode"] == "manual_required"
    assert "联系人" in (payload["error_message"] or "")


def test_proactive_precheck_fails_when_real_execution_enabled_but_config_missing(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("PROACTIVE_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("PROACTIVE_REAL_BASE_URL", raising=False)
    monkeypatch.delenv("PROACTIVE_REAL_TOKEN", raising=False)
    get_settings.cache_clear()
    _run_sync(client, "proactive")
    task = _get_planned_task(db_session, "proactive")

    response = client.post(f"/api/tasks/{task.id}/precheck")
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "precheck_failed"
    assert payload["result_payload"]["execution_mode"] == "real_precheck"
    assert payload["result_payload"]["runner_diagnostics"]["config_valid"] is False
    assert "proactive_real_base_url" in payload["result_payload"]["runner_diagnostics"]["missing_fields"]
    get_settings.cache_clear()


def test_proactive_execute_runs_real_runner_successfully(client, db_session, monkeypatch, proactive_real_server) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("PROACTIVE_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_REAL_BASE_URL", proactive_real_server["base_url"])
    monkeypatch.setenv("PROACTIVE_REAL_TOKEN", "proactive-real-token")
    monkeypatch.setenv("PROACTIVE_REAL_CREATE_ENDPOINT", "/proactive-work-orders")
    monkeypatch.setenv("PROACTIVE_REAL_ASSIGN_ENDPOINT_TEMPLATE", "/proactive-work-orders/{work_order_id}/assign-owner")
    monkeypatch.setenv("PROACTIVE_REAL_FEEDBACK_ENDPOINT_TEMPLATE", "/proactive-work-orders/{work_order_id}/fill-feedback")
    monkeypatch.setenv("PROACTIVE_REAL_FINAL_LINK_PATH", "data.final_link")
    get_settings.cache_clear()
    _run_sync(client, "proactive")
    task = _get_planned_task(db_session, "proactive")

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]

    assert payload["run_status"] == "success"
    assert payload["result_payload"]["execution_mode"] == "real"
    assert payload["result_payload"]["runner_diagnostics"]["mode"] == "real"
    assert len(payload["result_payload"]["action_results"]) == 3
    assert payload["result_payload"]["action_results"][0]["action"] == "create_proactive_work_order"
    assert payload["result_payload"]["action_results"][1]["action"] == "assign_owner"
    assert payload["result_payload"]["action_results"][2]["action"] == "fill_feedback"
    assert payload["final_link"].startswith(f"{proactive_real_server['base_url']}/proactive-work-orders/")
    assert proactive_real_server["request_log"][0]["path"] == "/proactive-work-orders"
    assert proactive_real_server["request_log"][1]["path"].endswith("/assign-owner")
    assert proactive_real_server["request_log"][2]["path"].endswith("/fill-feedback")
    get_settings.cache_clear()
