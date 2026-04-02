from fastapi.testclient import TestClient

from apps.api.main import app
from core.db import get_db
from core.exceptions import EnvironmentDependencyError


def test_console_dashboard_renders_module_overview(client) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    response = client.get("/console")
    assert response.status_code == 200
    text = response.text
    assert "模块总览" in text
    assert "Closed Loop V2" in text
    assert "立即同步" in text
    assert "人工处理清单" in text
    assert "PTS 会话" in text
    assert "应用新 Cookie" in text
    assert "交付转售后待执行回访" in text
    assert "一键创建并闭环" in text


def test_console_snapshots_page_renders_snapshot_list(client) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    response = client.get("/console/snapshots")
    assert response.status_code == 200
    assert "Snapshots" in response.text
    assert "visit" in response.text


def test_console_tasks_page_renders_actions_and_run_detail(client, db_session) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    tasks_response = client.get("/api/tasks")
    task_id = tasks_response.json()["items"][0]["task_plan_id"]

    page = client.get(f"/console/tasks?task_id={task_id}")
    assert page.status_code == 200
    assert "预检查" in page.text
    assert "演练执行" in page.text
    assert "执行" in page.text
    assert "Planned Payload" in page.text
    assert "业务解释" in page.text

    precheck = client.post(f"/api/tasks/{task_id}/precheck")
    run_id = precheck.json()["item"]["task_run_id"]

    detail = client.get(f"/console/task-runs/{run_id}")
    assert detail.status_code == 200
    assert "执行结果详情" in detail.text
    assert "业务解释" in detail.text
    assert "预检查" in detail.text or "预检查失败" in detail.text


def test_console_records_page_renders_low_priority_view(client) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    response = client.get("/console/records")
    assert response.status_code == 200
    assert "Records" in response.text


def test_console_returns_clear_environment_error_when_db_unavailable() -> None:
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
            response = client.get("/console")
        assert response.status_code == 503
        assert "环境依赖未就绪" in response.text
        assert "数据库不可达" in response.text
    finally:
        app.dependency_overrides.clear()
