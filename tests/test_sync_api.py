import uuid

from sqlalchemy import select

from core.config import get_settings
from models.module_config import ModuleConfig
from models.normalized_record import NormalizedRecord
from models.source_snapshot import SourceSnapshot
from models.task_plan import TaskPlan


def test_sync_run_writes_snapshot_records_and_task_plans(client, db_session) -> None:
    response = client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    assert response.status_code == 200

    payload = response.json()
    assert payload["ok"] is True
    assert payload["snapshot"]["module_code"] == "visit"
    assert payload["snapshot"]["sync_status"] == "success"
    assert payload["snapshot"]["data_source"] == "structured_api"
    assert payload["snapshot"]["row_count"] == 2
    assert payload["recognition"]["record_count"] == 2
    assert payload["recognition"]["full_count"] == 2
    assert payload["task_plans"]["total_count"] == 2
    assert payload["task_plans"]["planned_count"] == 1
    assert payload["task_plans"]["skipped_count"] == 1

    snapshots = list(db_session.scalars(select(SourceSnapshot)).all())
    records = list(db_session.scalars(select(NormalizedRecord)).all())
    tasks = list(db_session.scalars(select(TaskPlan)).all())
    assert len(snapshots) == 1
    assert len(records) == 2
    assert len(tasks) == 2

    snapshot_id = payload["snapshot"]["snapshot_id"]
    latest_response = client.get("/api/modules/visit/latest")
    assert latest_response.status_code == 200
    latest_payload = latest_response.json()
    assert latest_payload["item"]["module_code"] == "visit"
    assert latest_payload["item"]["snapshot_id"] == snapshot_id
    assert latest_payload["item"]["planned_tasks"] == 1
    assert latest_payload["item"]["skipped_tasks"] == 1

    snapshot_detail = client.get(f"/api/snapshots/{snapshot_id}")
    assert snapshot_detail.status_code == 200
    snapshot_detail_payload = snapshot_detail.json()
    assert snapshot_detail_payload["item"]["snapshot_id"] == snapshot_id
    assert snapshot_detail_payload["item"]["raw_meta"]["collector"] == "VisitCollector"
    assert snapshot_detail_payload["item"]["raw_meta"]["collector_type"] == "fixture"
    assert snapshot_detail_payload["item"]["raw_meta"]["selected_source"] == "structured_api"
    assert snapshot_detail_payload["item"]["raw_meta"]["collector_diagnostics"][0]["step"] == "structured"

    first_record = records[0]
    record_detail = client.get(f"/api/records/{first_record.id}")
    assert record_detail.status_code == 200
    assert record_detail.json()["item"]["record_id"] == str(first_record.id)

    first_task = tasks[0]
    task_detail = client.get(f"/api/tasks/{first_task.id}")
    assert task_detail.status_code == 200
    assert task_detail.json()["item"]["task_plan_id"] == str(first_task.id)


def test_detail_endpoints_return_404_for_missing_resources(client) -> None:
    missing_snapshot = client.get(f"/api/snapshots/{uuid.uuid4()}")
    missing_record = client.get(f"/api/records/{uuid.uuid4()}")
    missing_task = client.get(f"/api/tasks/{uuid.uuid4()}")

    assert missing_snapshot.status_code == 404
    assert missing_record.status_code == 404
    assert missing_task.status_code == 404


def test_invalid_module_code_returns_400(client) -> None:
    sync_response = client.post("/api/sync/run", json={"module_code": "unknown", "force": False})
    latest_response = client.get("/api/modules/unknown/latest")

    assert sync_response.status_code == 400
    assert latest_response.status_code == 400


def test_sync_run_writes_data_with_real_transport_mode(client, db_session, transport_server, monkeypatch) -> None:
    monkeypatch.setenv("TEST_DINGTALK_TOKEN", "transport-token")
    db_session.add(
        ModuleConfig(
            module_code="visit",
            module_name="交付转售后回访闭环",
            source_url=transport_server["base_url"],
            source_doc_key="doc_visit_real",
            source_view_key="view_visit_default",
            enabled=True,
            collector_type="dingtalk",
            sync_cron=None,
            extra_config={
                "structured_endpoint": "/structured",
                "state_endpoint": "/state",
                "structured_response_path": "data.payload",
                "structured_columns_path": "columns",
                "structured_rows_path": "rows",
                "structured_meta_path": "meta",
                "state_response_path": "payload",
                "state_columns_path": "columns",
                "state_rows_path": "rows",
                "state_meta_path": "meta",
                "token_env": "TEST_DINGTALK_TOKEN",
                "token_header": "X-Auth-Token",
                "playwright_fallback_enabled": False,
            },
        )
    )
    db_session.commit()

    response = client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    assert response.status_code == 200
    payload = response.json()

    assert payload["snapshot"]["data_source"] == "structured_api"
    assert payload["snapshot"]["row_count"] == 1
    assert payload["recognition"]["record_count"] == 1
    assert payload["task_plans"]["planned_count"] == 1

    snapshot = db_session.scalars(select(SourceSnapshot)).first()
    assert snapshot is not None
    assert snapshot.raw_meta["transport_mode"] == "real"
    assert snapshot.raw_meta["collector_diagnostics"][0]["transport_mode"] == "real"
    assert snapshot.raw_meta["collector_diagnostics"][0]["error_type"] is None

    record = db_session.scalars(select(NormalizedRecord)).first()
    task = db_session.scalars(select(TaskPlan)).first()
    assert record is not None
    assert task is not None
    assert record.customer_name == "真实传输客户A"
    assert record.normalized_data["visit_owner"] == "舒磊"
    assert record.normalized_data["pts_link"] == "https://pts.example.com/visit-transport-001"
    assert record.normalized_data["debug_visit_owner_raw"].startswith("[{")
    assert task.plan_status == "planned"


def test_sync_run_writes_snapshot_records_and_tasks_with_parallelv2_visit_doc(client, db_session, dingtalk_parallelv2_server) -> None:
    db_session.add(
        ModuleConfig(
            module_code="visit",
            module_name="交付转售后回访闭环",
            source_url=dingtalk_parallelv2_server["base_url"],
            source_doc_key="4j6OJ5jPAGa8eq3p",
            source_view_key="AKOehLK",
            enabled=True,
            collector_type="dingtalk",
            sync_cron=None,
            extra_config={
                "parallelv2_enabled": True,
                "structured_endpoint": "/api/document/data",
                "structured_method": "POST",
                "record_count_endpoint": "/nt/api/sheets/Igz9TVd/record/count",
                "parallelv2_endpoint": "/nt/api/sheets/Igz9TVd/records/binary/parallelV2",
                "parallelv2_query_params": {
                    "version": 2230,
                    "sheetType": "",
                    "limit": 2001,
                    "lastCursor": "offline-fixture",
                },
                "parallelv2_sheet_id": "Igz9TVd",
                "parallelv2_view_id": "AKOehLK",
                "parallelv2_token_header": "A-Token",
                "playwright_fallback_enabled": False,
            },
        )
    )
    db_session.commit()

    response = client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    assert response.status_code == 200
    payload = response.json()

    assert payload["snapshot"]["data_source"] == "parallelv2_binary"
    assert payload["snapshot"]["row_count"] == 2
    assert payload["recognition"]["record_count"] == 2
    assert payload["task_plans"]["total_count"] == 2
    assert payload["task_plans"]["planned_count"] == 1

    snapshot = db_session.scalars(select(SourceSnapshot)).first()
    assert snapshot is not None
    assert snapshot.raw_meta["sheet_id"] == "Igz9TVd"
    assert snapshot.raw_meta["view_id"] == "AKOehLK"
    assert snapshot.raw_meta["record_count"] == 2
    assert snapshot.raw_meta["data_source"] == "parallelv2_binary"
    assert snapshot.raw_meta["decoder"]["decoded_row_count"] == 2

    records = list(db_session.scalars(select(NormalizedRecord)).all())
    assert len(records) == 2
    assert records[0].customer_name in {"上海测试客户", "杭州测试客户"}


def test_sync_run_backfills_visit_delivery_id_from_pts_link(client, db_session, visit_delivery_backfill_server, monkeypatch) -> None:
    monkeypatch.setenv("PTS_BASE_URL", visit_delivery_backfill_server["base_url"])
    monkeypatch.setenv("PTS_COOKIE_HEADER", "session=pts-backfill-cookie")
    get_settings.cache_clear()

    db_session.add(
        ModuleConfig(
            module_code="visit",
            module_name="交付转售后回访闭环",
            source_url=visit_delivery_backfill_server["base_url"],
            source_doc_key="doc_visit_backfill",
            source_view_key="view_visit_backfill",
            enabled=True,
            collector_type="dingtalk",
            sync_cron=None,
            extra_config={
                "structured_endpoint": "/structured",
                "structured_method": "GET",
                "structured_response_path": "data.payload",
                "structured_columns_path": "columns",
                "structured_rows_path": "rows",
                "structured_meta_path": "meta",
                "playwright_fallback_enabled": False,
            },
        )
    )
    db_session.commit()

    response = client.post("/api/sync/run", json={"module_code": "visit", "force": False})
    assert response.status_code == 200
    payload = response.json()

    assert payload["recognition"]["record_count"] == 1
    assert payload["task_plans"]["planned_count"] == 1

    record = db_session.scalars(select(NormalizedRecord)).first()
    task = db_session.scalars(select(TaskPlan)).first()
    assert record is not None
    assert task is not None
    assert record.normalized_data["delivery_id"] == "694bb8f8c1df4508b53003a2"
    assert record.normalized_data["debug_pts_project_id"] == "694bb8f8c1df4508b53003a2"
    assert record.normalized_data["debug_delivery_id_source"] == "pts_link_project_id"
    assert task.plan_status == "planned"
    get_settings.cache_clear()
