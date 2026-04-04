import asyncio
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from apps.api.main import app
from core.config import get_settings
from core.db import get_db
from core.exceptions import EnvironmentDependencyError
from core.runtime_state import runtime_state
from models.module_config import ModuleConfig
from models.normalized_record import NormalizedRecord
from models.source_snapshot import SourceSnapshot
from models.task_plan import TaskPlan
from models.task_run import TaskRun
from scheduler.jobs import register_jobs, run_scheduled_sync_job
from services.environment_check import EnvironmentCheckService
from services.collectors.visit_collector import VisitCollector
from services.executors.schemas import ExecutionResult
from services.executors.visit_executor import VisitExecutor
from services.pts_session_service import PtsSessionService


def _get_planned_task(db_session, module_code: str) -> TaskPlan:
    task = db_session.scalars(
        select(TaskPlan)
        .where(TaskPlan.module_code == module_code, TaskPlan.plan_status == "planned")
        .order_by(TaskPlan.created_at.asc())
    ).first()
    assert task is not None
    return task


def test_scheduler_registers_interval_job_and_runs_sync(client, db_session) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    config = db_session.scalars(select(ModuleConfig).where(ModuleConfig.module_code == "visit")).one()
    config.extra_config = {
        **(config.extra_config or {}),
        "schedule_type": "interval",
        "schedule_interval_minutes": 5,
    }
    db_session.commit()

    testing_session_factory = sessionmaker(
        bind=db_session.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    scheduler = BackgroundScheduler()
    job_ids = register_jobs(scheduler, session_factory=testing_session_factory)

    assert "sync:visit" in job_ids
    run_scheduled_sync_job("visit", session_factory=testing_session_factory)

    latest_snapshot = db_session.scalars(
        select(SourceSnapshot).where(SourceSnapshot.module_code == "visit").order_by(SourceSnapshot.sync_time.desc())
    ).first()
    assert latest_snapshot is not None
    assert latest_snapshot.raw_meta["_ops"]["trigger"] == "scheduler"


def test_sync_auto_retry_for_temporary_failure(client, db_session, monkeypatch) -> None:
    original_collect = VisitCollector.collect
    calls = {"count": 0}

    async def flaky_collect(self):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("temporary collector timeout")
        return await original_collect(self)

    monkeypatch.setattr(VisitCollector, "collect", flaky_collect)
    response = client.post("/api/sync/run", json={"module_code": "visit", "force": False})

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot"]["sync_status"] == "success"
    assert payload["run_context"]["retried"] is True
    assert payload["run_context"]["retry_count"] == 1
    assert payload["run_context"]["attempt"] == 2

    snapshots = list(
        db_session.scalars(select(SourceSnapshot).where(SourceSnapshot.module_code == "visit").order_by(SourceSnapshot.sync_time.asc())).all()
    )
    assert len(snapshots) == 2
    assert snapshots[0].sync_status == "failed"
    assert snapshots[0].raw_meta["_ops"]["retryable"] is True
    assert snapshots[-1].sync_status == "success"


def test_sync_conflict_returns_409(client) -> None:
    assert runtime_state.acquire_sync("visit") is True
    response = client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    assert response.status_code == 409
    assert "运行中" in response.json()["detail"]


def test_execute_auto_retry_and_task_rerun(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "false")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "false")
    get_settings.cache_clear()
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    task = _get_planned_task(db_session, "visit")
    original_execute = VisitExecutor.execute
    calls = {"count": 0}

    async def flaky_execute(self, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return ExecutionResult(
                run_status="failed",
                error_message="temporary pts timeout",
                executor_version=self.executor_version,
                retryable=True,
                result_payload={"step": "create_visit_work_order"},
            )
        return await original_execute(self, context)

    monkeypatch.setattr(VisitExecutor, "execute", flaky_execute)

    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 200
    payload = response.json()["item"]
    assert payload["run_status"] == "simulated_success"

    task_runs = list(
        db_session.scalars(select(TaskRun).where(TaskRun.task_plan_id == task.id).order_by(TaskRun.run_time.asc())).all()
    )
    assert len(task_runs) == 2
    assert task_runs[0].run_status == "failed"
    assert task_runs[0].result_payload["_ops"]["retryable"] is True
    assert task_runs[1].result_payload["_ops"]["trigger"] == "retry"

    rerun = client.post(f"/api/tasks/{task.id}/rerun", json={"dry_run": False})
    assert rerun.status_code == 200
    rerun_payload = rerun.json()["item"]
    assert rerun_payload["result_payload"]["_ops"]["trigger"] == "rerun"
    get_settings.cache_clear()


def test_batch_execute_pending_visit_tasks(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "false")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "false")
    get_settings.cache_clear()
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})

    planned_before = client.get("/api/tasks?module_code=visit&status=planned").json()["items"]
    assert len(planned_before) >= 1

    response = client.post("/api/tasks/batch/execute-pending", json={"module_code": "visit", "dry_run": False})
    assert response.status_code == 200
    payload = response.json()
    assert payload["module_code"] == "visit"
    assert payload["total_count"] == len(planned_before)
    assert payload["success_count"] >= 1
    assert len(payload["items"]) == len(planned_before)

    overview_after = client.get("/api/ops/overview")
    visit_after = next(item for item in overview_after.json()["items"] if item["module_code"] == "visit")
    assert visit_after["planned_tasks"] == 0
    get_settings.cache_clear()


def test_pending_visit_list_excludes_rows_with_existing_successful_run(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "true")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.delenv("VISIT_REAL_BASE_URL", raising=False)
    monkeypatch.delenv("VISIT_REAL_TOKEN", raising=False)
    monkeypatch.setenv("PTS_BASE_URL", "https://pts.example.com")
    monkeypatch.setenv("PTS_COOKIE_HEADER", "")
    monkeypatch.setattr("services.executors.visit_real_runner.VisitRealRunner._browser_session_available", lambda self: True)
    get_settings.cache_clear()
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    task = _get_planned_task(db_session, "visit")

    from services.executors.visit_real_runner import VisitRealRunOutcome, VisitRealRunner

    async def fake_browser_run(self, context, actions, diagnostics):
        diagnostics["transport_mode"] = "pts_browser_session"
        diagnostics["config_valid"] = True
        return VisitRealRunOutcome(
            run_status="success",
            final_link="https://pts.example.com/return-visit/detail/pending-hidden",
            action_results=[{"action": "complete_visit", "status": "success", "http_status": 200}],
            runner_diagnostics=diagnostics,
        )

    monkeypatch.setattr(VisitRealRunner, "_run_pts_browser_mode", fake_browser_run)
    execute_response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert execute_response.status_code == 200
    assert execute_response.json()["item"]["run_status"] == "success"

    pending = client.get("/api/ops/overview").json()["items"]
    visit_item = next(item for item in pending if item["module_code"] == "visit")
    assert visit_item["planned_tasks"] == 0
    get_settings.cache_clear()


def test_execute_conflict_returns_409(client, db_session) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    task = _get_planned_task(db_session, "visit")
    assert runtime_state.acquire_task(str(task.id)) is True
    response = client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})
    assert response.status_code == 409
    assert "运行中" in response.json()["detail"]


def test_ops_api_and_console_render_failure_and_manual_required(client, db_session) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    client.post("/api/sync/run", json={"module_code": "proactive", "force": False})

    proactive_task = _get_planned_task(db_session, "proactive")
    proactive_record = db_session.get(NormalizedRecord, proactive_task.normalized_record_id)
    assert proactive_record is not None
    proactive_data = dict(proactive_record.normalized_data)
    proactive_data["contact_name"] = None
    proactive_data["contact_phone"] = None
    proactive_record.normalized_data = proactive_data
    db_session.commit()
    client.post(f"/api/tasks/{proactive_task.id}/execute", json={"dry_run": False})

    db_session.add(
        SourceSnapshot(
            module_code="inspection",
            source_url="https://example.com",
            source_doc_key="doc",
            source_view_key="view",
            data_source="fixture",
            sync_status="failed",
            sync_error="temporary transport error",
            raw_columns=[],
            raw_rows=[],
            raw_meta={"_ops": {"trigger": "scheduler", "retryable": True}},
            row_count=0,
        )
    )
    db_session.commit()

    overview = client.get("/api/ops/overview")
    failures = client.get("/api/ops/failures")
    manual_required = client.get("/api/ops/manual-required")
    console = client.get("/console")
    tasks_page = client.get("/console/tasks")

    assert overview.status_code == 200
    assert failures.status_code == 200
    assert manual_required.status_code == 200
    failure_items = failures.json()["items"]
    manual_items = manual_required.json()["items"]
    assert any(item["module_code"] == "inspection" for item in failure_items)
    assert any(item["module_code"] == "proactive" for item in manual_items)
    assert any(item["display_status"] == "失败" for item in failure_items)
    assert any(item["business_explanation"] for item in failure_items)
    assert any(item["display_status"] == "需人工处理" for item in manual_items)
    assert any(item["customer_name"] for item in manual_items)
    assert console.status_code == 200
    assert "失败任务" in console.text
    assert "人工处理清单" in console.text
    assert "重跑同步" in console.text
    assert tasks_page.status_code == 200
    assert "业务解释" in tasks_page.text
    assert "人工处理清单" in tasks_page.text


def test_ops_overview_counts_only_pending_planned_tasks(client, db_session) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    task = _get_planned_task(db_session, "visit")
    overview_before = client.get("/api/ops/overview")
    assert overview_before.status_code == 200
    visit_before = next(item for item in overview_before.json()["items"] if item["module_code"] == "visit")
    assert visit_before["planned_tasks"] >= 1

    db_session.add(
        TaskRun(
            task_plan_id=task.id,
            run_status="success",
            manual_required=False,
            result_payload={"execution_mode": "real"},
            final_link="https://pts.example.com/return-visit/detail/success-1",
            error_message=None,
            executor_version="test",
        )
    )
    db_session.commit()

    overview_after = client.get("/api/ops/overview")
    assert overview_after.status_code == 200
    visit_after = next(item for item in overview_after.json()["items"] if item["module_code"] == "visit")
    assert visit_after["planned_tasks"] == visit_before["planned_tasks"] - 1


def test_ops_overview_deduplicates_pending_tasks_across_repeated_syncs(client) -> None:
    first = client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    assert first.status_code == 200
    overview_first = client.get("/api/ops/overview")
    assert overview_first.status_code == 200
    visit_first = next(item for item in overview_first.json()["items"] if item["module_code"] == "visit")

    second = client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    assert second.status_code == 200
    overview_second = client.get("/api/ops/overview")
    assert overview_second.status_code == 200
    visit_second = next(item for item in overview_second.json()["items"] if item["module_code"] == "visit")

    assert visit_second["planned_tasks"] == visit_first["planned_tasks"]


def test_ops_overview_uses_latest_task_plan_status_for_same_business_key(client, db_session) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    overview_before = client.get("/api/ops/overview")
    assert overview_before.status_code == 200
    visit_before = next(item for item in overview_before.json()["items"] if item["module_code"] == "visit")
    task = _get_planned_task(db_session, "visit")
    record = db_session.get(NormalizedRecord, task.normalized_record_id)
    assert record is not None

    newer_record = NormalizedRecord(
        snapshot_id=record.snapshot_id,
        module_code=record.module_code,
        source_row_id=record.source_row_id,
        customer_name=record.customer_name,
        normalized_data={
            **(record.normalized_data or {}),
            "visit_link": "https://pts.example.com/return-visit/detail/already-closed",
        },
        field_mapping=record.field_mapping or {},
        field_confidence=record.field_confidence or {},
        field_evidence=record.field_evidence or {},
        field_samples=record.field_samples or {},
        unresolved_fields=record.unresolved_fields or [],
        recognition_status=record.recognition_status,
    )
    db_session.add(newer_record)
    db_session.flush()

    newer_task = TaskPlan(
        module_code=task.module_code,
        normalized_record_id=newer_record.id,
        task_type=task.task_type,
        eligibility=False,
        plan_status="skipped",
        skip_reason="visit_link 已存在",
        planner_version=task.planner_version,
        planned_payload=task.planned_payload,
    )
    db_session.add(newer_task)
    db_session.commit()

    overview = client.get("/api/ops/overview")
    assert overview.status_code == 200
    visit_item = next(item for item in overview.json()["items"] if item["module_code"] == "visit")
    assert visit_item["planned_tasks"] == visit_before["planned_tasks"] - 1


def test_ops_overview_returns_clear_environment_error_when_db_unavailable() -> None:
    def broken_db():
        raise EnvironmentDependencyError(
            error_type="database_unavailable",
            public_message="数据库不可达",
            hint="请检查 DATABASE_URL、PostgreSQL 服务和数据库初始化状态。",
        )
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/ops/overview")
        assert response.status_code == 503
        payload = response.json()
        assert payload["ok"] is False
        assert payload["error_type"] == "database_unavailable"
        assert payload["message"] == "数据库不可达"
    finally:
        app.dependency_overrides.clear()


def test_health_readiness_returns_environment_report(client, monkeypatch) -> None:
    monkeypatch.setattr(
        EnvironmentCheckService,
        "build_report",
        lambda self: {
            "ok": False,
            "app_env": "development",
            "app_debug": False,
            "database": {"ok": False, "message": "数据库不可达"},
            "real_execution": {
                "enabled": False,
                "modules": {"visit": {"ok": False, "missing_fields": ["pts_cookie_header"], "browser_session_available": False}},
            },
            "scheduler": {"enabled": True, "ok": False, "message": "scheduler 读取 module config 失败"},
        },
    )
    response = client.get("/health/readiness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["database"]["message"] == "数据库不可达"
    assert payload["real_execution"]["modules"]["visit"]["missing_fields"] == ["pts_cookie_header"]


def test_pts_session_api_updates_local_env(client, monkeypatch, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("PTS_BASE_URL=https://pts.chaitin.net\nPTS_COOKIE_HEADER=\nPTS_VERIFY_SSL=true\n", encoding="utf-8")
    monkeypatch.setattr("services.pts_session_service.DEFAULT_ENV_PATH", env_path)

    status = client.get("/api/ops/pts-session")
    assert status.status_code == 200
    assert status.json()["configured"] is False

    update = client.post("/api/ops/pts-session", json={"cookie_header": "session=fake-pts-cookie"})
    assert update.status_code == 200
    payload = update.json()
    assert payload["configured"] is True
    assert payload["message"] == "PTS Cookie 已更新"
    assert "session=fake-pts-cookie" in env_path.read_text(encoding="utf-8")

    refreshed = PtsSessionService(env_path=env_path).get_status()
    assert refreshed["configured"] is True
