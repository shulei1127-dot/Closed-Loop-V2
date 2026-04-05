from fastapi.testclient import TestClient
from models.normalized_record import NormalizedRecord
from models.source_snapshot import SourceSnapshot
from models.task_plan import TaskPlan
from models.task_run import TaskRun

from apps.api.main import app
from core.config import get_settings
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
    assert "最近闭环回访链接" in text
    assert "一键创建并闭环全部" in text
    assert "一键创建并闭环" in text
    assert "进入模块" in text
    assert "交付转售后回访" in text
    assert "巡检工单闭环" in text
    assert "超半年主动回访" in text


def test_console_module_pages_render(client) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    visit_page = client.get("/console/modules/visit")
    assert visit_page.status_code == 200
    assert "交付转售后回访" in visit_page.text
    assert "待执行回访任务" in visit_page.text
    assert "立即同步" in visit_page.text
    assert "重跑同步" in visit_page.text
    assert 'href="/console/modules/visit" class="active"' in visit_page.text

    inspection_page = client.get("/console/modules/inspection")
    assert inspection_page.status_code == 200
    assert "巡检工单闭环" in inspection_page.text
    assert "当月待闭环巡检任务" in inspection_page.text
    assert "当月最近闭环记录" in inspection_page.text
    assert "立即同步" in inspection_page.text
    assert "重跑同步" in inspection_page.text
    assert 'href="/console/modules/inspection" class="active"' in inspection_page.text

    proactive_page = client.get("/console/modules/proactive")
    assert proactive_page.status_code == 200
    assert "超半年主动回访" in proactive_page.text
    assert "待处理主动回访任务" in proactive_page.text
    assert "立即同步" in proactive_page.text
    assert "重跑同步" in proactive_page.text
    assert 'href="/console/modules/proactive" class="active"' in proactive_page.text


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
    assert "默认只展示待处理任务" in page.text
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


def test_console_tasks_page_hides_stale_duplicate_task_rows(client, db_session) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    tasks_response = client.get("/api/tasks?module_code=visit&status=planned")
    before_count = len(tasks_response.json()["items"])
    first_item = tasks_response.json()["items"][0]
    task_id = first_item["task_plan_id"]
    record_id = first_item["normalized_record_id"]

    original_record = db_session.get(NormalizedRecord, record_id)
    assert original_record is not None

    duplicate_record = NormalizedRecord(
        snapshot_id=original_record.snapshot_id,
        module_code=original_record.module_code,
        source_row_id=original_record.source_row_id,
        customer_name=original_record.customer_name,
        normalized_data=original_record.normalized_data,
        field_mapping=original_record.field_mapping,
        field_confidence=original_record.field_confidence,
        recognition_status=original_record.recognition_status,
        field_evidence=original_record.field_evidence,
        field_samples=original_record.field_samples,
        unresolved_fields=original_record.unresolved_fields,
    )
    db_session.add(duplicate_record)
    db_session.flush()

    duplicate_task = TaskPlan(
        module_code="visit",
        normalized_record_id=duplicate_record.id,
        task_type="visit_close",
        eligibility=True,
        skip_reason=None,
        planner_version="test-duplicate",
        plan_status="planned",
        planned_payload=original_record.normalized_data,
    )
    db_session.add(duplicate_task)
    db_session.commit()

    deduped_tasks_response = client.get("/api/tasks?module_code=visit&status=planned")
    items = deduped_tasks_response.json()["items"]
    assert len(items) == before_count
    page = client.get("/console/tasks?module_code=visit&status=planned")
    assert page.status_code == 200
    assert page.text.count(original_record.customer_name or "") <= 1


def test_console_tasks_page_defaults_to_pending_only(client) -> None:
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    page = client.get("/console/tasks")
    assert page.status_code == 200
    assert "默认只展示待处理任务" in page.text
    assert "已跳过" not in page.text


def test_console_tasks_page_renders_final_link_for_successful_run(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "false")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "false")
    get_settings.cache_clear()
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    task = db_session.query(TaskPlan).filter(TaskPlan.module_code == "visit").order_by(TaskPlan.created_at.asc()).first()
    assert task is not None
    client.post(f"/api/tasks/{task.id}/execute", json={"dry_run": False})

    page = client.get(f"/console/tasks?module_code=visit&status=all&task_id={task.id}")
    assert page.status_code == 200
    assert "回访链接" in page.text
    assert "https://pts.example.com/simulated/visit/" in page.text
    get_settings.cache_clear()


def test_console_dashboard_renders_recent_visit_links(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "false")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "false")
    get_settings.cache_clear()
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    tasks_response = client.get("/api/tasks?module_code=visit&status=planned")
    task_id = tasks_response.json()["items"][0]["task_plan_id"]
    client.post(f"/api/tasks/{task_id}/execute", json={"dry_run": False})

    page = client.get("/console")
    assert page.status_code == 200
    assert "最近闭环回访链接" in page.text
    assert "https://pts.example.com/simulated/visit/" in page.text
    get_settings.cache_clear()


def test_console_visit_links_page_renders_all_links(client, db_session, monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_REAL_EXECUTION", "false")
    monkeypatch.setenv("VISIT_REAL_EXECUTION_ENABLED", "false")
    get_settings.cache_clear()
    client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    tasks_response = client.get("/api/tasks?module_code=visit&status=planned")
    task_id = tasks_response.json()["items"][0]["task_plan_id"]
    client.post(f"/api/tasks/{task_id}/execute", json={"dry_run": False})

    page = client.get("/console/visit-links")
    assert page.status_code == 200
    assert "全部闭环回访链接" in page.text
    assert "https://pts.example.com/simulated/visit/" in page.text
    get_settings.cache_clear()


def test_console_inspection_links_page_renders_all_links(client, db_session) -> None:
    snapshot = SourceSnapshot(
        module_code="inspection",
        source_url="https://alidocs.dingtalk.com",
        source_doc_key="inspection-doc",
        source_view_key="inspection-view",
        data_source="parallelv2_binary",
        sync_status="success",
        raw_columns=[],
        raw_rows=[],
        raw_meta={},
        row_count=1,
    )
    db_session.add(snapshot)
    db_session.flush()

    record = NormalizedRecord(
        snapshot_id=snapshot.id,
        module_code="inspection",
        source_row_id="inspection-row-1",
        customer_name="昆明电力交易中心有限责任公司",
        normalized_data={
            "customer_name": "昆明电力交易中心有限责任公司",
            "inspection_month": "2026-03",
            "work_order_link": "https://pts.chaitin.net/project/order/65125a7056965446f58c2f08",
        },
        field_mapping={},
        field_confidence={},
        field_evidence={},
        field_samples={},
        unresolved_fields=[],
        recognition_status="recognized",
    )
    db_session.add(record)
    db_session.flush()

    task = TaskPlan(
        module_code="inspection",
        normalized_record_id=record.id,
        task_type="inspection_close",
        eligibility=True,
        skip_reason=None,
        planner_version="test",
        plan_status="planned",
        planned_payload={"customer_name": "昆明电力交易中心有限责任公司", "inspection_month": "2026-03"},
    )
    db_session.add(task)
    db_session.flush()

    task_run = TaskRun(
        task_plan_id=task.id,
        run_status="success",
        manual_required=False,
        result_payload={"final_link": "https://pts.chaitin.net/project/order/65125a7056965446f58c2f08/completed"},
        final_link="https://pts.chaitin.net/project/order/65125a7056965446f58c2f08/completed",
        error_message=None,
        executor_version="test",
    )
    db_session.add(task_run)
    db_session.commit()

    module_page = client.get("/console/modules/inspection?inspection_month=2026-03")
    assert module_page.status_code == 200
    assert "当月最近闭环记录" in module_page.text
    assert "昆明电力交易中心有限责任公司" in module_page.text
    assert "查看全部闭环记录" in module_page.text

    page = client.get("/console/inspection-links?inspection_month=2026-03")
    assert page.status_code == 200
    assert "全部巡检闭环记录" in page.text
    assert "昆明电力交易中心有限责任公司" in page.text
    assert "65125a7056965446f58c2f08/completed" in page.text


def test_console_inspection_links_page_excludes_simulated_success(client, db_session) -> None:
    snapshot = SourceSnapshot(
        module_code="inspection",
        source_url="https://alidocs.dingtalk.com",
        source_doc_key="inspection-doc",
        source_view_key="inspection-view",
        data_source="parallelv2_binary",
        sync_status="success",
        raw_columns=[],
        raw_rows=[],
        raw_meta={},
        row_count=1,
    )
    db_session.add(snapshot)
    db_session.flush()

    record = NormalizedRecord(
        snapshot_id=snapshot.id,
        module_code="inspection",
        source_row_id="inspection-row-simulated",
        customer_name="国网冀北电力有限公司电力科学研究院",
        normalized_data={
            "customer_name": "国网冀北电力有限公司电力科学研究院",
            "inspection_month": "2026-04",
            "work_order_link": "https://pts.chaitin.net/project/order/69143a964169b1cf477144c6",
        },
        field_mapping={},
        field_confidence={},
        field_evidence={},
        field_samples={},
        unresolved_fields=[],
        recognition_status="recognized",
    )
    db_session.add(record)
    db_session.flush()

    task = TaskPlan(
        module_code="inspection",
        normalized_record_id=record.id,
        task_type="inspection_close",
        eligibility=True,
        skip_reason=None,
        planner_version="test",
        plan_status="planned",
        planned_payload={"customer_name": "国网冀北电力有限公司电力科学研究院", "inspection_month": "2026-04"},
    )
    db_session.add(task)
    db_session.flush()

    task_run = TaskRun(
        task_plan_id=task.id,
        run_status="simulated_success",
        manual_required=False,
        result_payload={"final_link": "https://pts.chaitin.net/project/order/69143a964169b1cf477144c6"},
        final_link="https://pts.chaitin.net/project/order/69143a964169b1cf477144c6",
        error_message=None,
        executor_version="test",
    )
    db_session.add(task_run)
    db_session.commit()

    module_page = client.get("/console/modules/inspection?inspection_month=2026-04")
    assert module_page.status_code == 200
    assert "当月最近闭环记录" in module_page.text

    page = client.get("/console/inspection-links?inspection_month=2026-04")
    assert page.status_code == 200
    assert "国网冀北电力有限公司电力科学研究院" not in page.text


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
