import asyncio
from pathlib import Path

import pytest

from schemas.sync import RecognitionResult
from services.collectors.fetchers import (
    AuthenticationFailedError,
    ConfigurationMissingError,
    DingtalkPayloadFetcher,
    PayloadParseError,
    build_fetcher,
)
from services.collectors.inspection_collector import InspectionCollector
from services.collectors.proactive_collector import ProactiveCollector
from services.collectors.source_config import ModuleSourceConfig
from services.collectors.visit_collector import VisitCollector
from services.module_registry import default_module_configs
from services.recognizers.inspection_recognizer import InspectionRecognizer
from services.recognizers.proactive_recognizer import ProactiveRecognizer
from services.recognizers.visit_recognizer import VisitRecognizer


def _default_source_config(module_code: str) -> ModuleSourceConfig:
    defaults = {item["module_code"]: item for item in default_module_configs()}
    return ModuleSourceConfig.from_mapping(defaults[module_code])


def test_collector_config_validation_accepts_fixture_source() -> None:
    collector = VisitCollector(_default_source_config("visit"))
    collector.validate()
    health = collector.healthcheck()
    assert health["collector_type"] == "dws_cli"
    assert health["structured_configured"] is True


def test_transport_mode_switching_works() -> None:
    dws_cli_fetcher = build_fetcher(_default_source_config("visit"))
    fake_fetcher = build_fetcher(
        ModuleSourceConfig.from_mapping(
            {
                "module_code": "visit",
                "module_name": "visit",
                "source_url": "https://example.com",
                "source_doc_key": "doc",
                "source_view_key": "view",
                "enabled": True,
                "collector_type": "fake",
                "extra_config": {"structured_payload": {"raw_columns": [], "raw_rows": []}},
            }
        )
    )
    real_fetcher = build_fetcher(
        ModuleSourceConfig.from_mapping(
            {
                "module_code": "visit",
                "module_name": "visit",
                "source_url": "https://example.com",
                "source_doc_key": "doc",
                "source_view_key": "view",
                "enabled": True,
                "collector_type": "dingtalk",
                "extra_config": {"structured_endpoint": "https://example.com/api"},
            }
        )
    )
    assert dws_cli_fetcher.transport_mode == "dws_cli"
    assert fake_fetcher.transport_mode == "fake"
    assert real_fetcher.transport_mode == "real"


def test_collector_config_validation_rejects_missing_source_doc_key() -> None:
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "visit",
            "source_url": "https://example.com",
            "source_doc_key": "",
            "source_view_key": "view",
            "enabled": True,
            "collector_type": "fixture",
            "extra_config": {"structured_payload": {"raw_columns": [], "raw_rows": []}},
        }
    )
    collector = VisitCollector(config)
    with pytest.raises(ValueError, match="missing source_doc_key"):
        collector.validate()


def test_collector_config_validation_rejects_missing_fixture_payload_path() -> None:
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "visit",
            "source_url": "https://example.com",
            "source_doc_key": "doc",
            "source_view_key": "view",
            "enabled": True,
            "collector_type": "fixture",
            "extra_config": {"structured_payload_path": str(Path("/tmp/not-found-collector-payload.json"))},
        }
    )
    collector = VisitCollector(config)
    with pytest.raises(ValueError, match="fixture payload path missing"):
        collector.validate()


def test_real_collector_returns_partial_when_rows_are_empty() -> None:
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "visit",
            "source_url": "https://example.com",
            "source_doc_key": "doc",
            "source_view_key": "view",
            "enabled": True,
            "collector_type": "fixture",
            "extra_config": {
                "structured_payload": {
                    "raw_columns": ["客户名称"],
                    "raw_rows": [],
                    "raw_meta": {"fixture_kind": "structured_api"},
                },
                "playwright_fallback_enabled": False,
            },
        }
    )
    collector = VisitCollector(config)
    result = asyncio.run(collector.collect())
    assert result.sync_status == "partial"
    assert result.raw_rows == []
    assert result.data_source == "playwright_fallback"


def test_fake_transport_still_works() -> None:
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "visit",
            "source_url": "https://example.com",
            "source_doc_key": "doc",
            "source_view_key": "view",
            "enabled": True,
            "collector_type": "fake",
            "extra_config": {
                "structured_payload": {
                    "raw_columns": ["客户名称"],
                    "raw_rows": [{"row_id": "fake-001", "客户名称": "假数据客户"}],
                    "raw_meta": {"transport": "fake"},
                }
            },
        }
    )
    collector = VisitCollector(config)
    result = asyncio.run(collector.collect())
    assert result.sync_status == "success"
    assert result.data_source == "structured_api"
    assert result.raw_meta["transport_mode"] == "fake"


def test_real_transport_missing_env_raises_error(transport_server, monkeypatch) -> None:
    monkeypatch.delenv("TEST_DINGTALK_TOKEN", raising=False)
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "visit",
            "source_url": transport_server["base_url"],
            "source_doc_key": "doc",
            "source_view_key": "view",
            "enabled": True,
            "collector_type": "dingtalk",
            "extra_config": {
                "structured_endpoint": "/structured",
                "structured_response_path": "data.payload",
                "structured_columns_path": "columns",
                "structured_rows_path": "rows",
                "structured_meta_path": "meta",
                "token_env": "TEST_DINGTALK_TOKEN",
                "token_header": "X-Auth-Token",
                "playwright_fallback_enabled": False,
            },
        }
    )
    fetcher = DingtalkPayloadFetcher()
    with pytest.raises(ConfigurationMissingError, match="missing required token env"):
        asyncio.run(fetcher.fetch_structured(config))


def test_real_transport_response_error_raises(transport_server, monkeypatch) -> None:
    monkeypatch.setenv("TEST_DINGTALK_TOKEN", "transport-token")
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "visit",
            "source_url": transport_server["base_url"],
            "source_doc_key": "doc",
            "source_view_key": "view",
            "enabled": True,
            "collector_type": "dingtalk",
            "extra_config": {
                "structured_endpoint": "/structured-invalid-json",
                "token_env": "TEST_DINGTALK_TOKEN",
                "token_header": "X-Auth-Token",
            },
        }
    )
    fetcher = DingtalkPayloadFetcher()
    with pytest.raises(PayloadParseError):
        asyncio.run(fetcher.fetch_structured(config))


def test_real_transport_auth_failure_raises(transport_server, monkeypatch) -> None:
    monkeypatch.setenv("TEST_DINGTALK_TOKEN", "wrong-token")
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "visit",
            "source_url": transport_server["base_url"],
            "source_doc_key": "doc",
            "source_view_key": "view",
            "enabled": True,
            "collector_type": "dingtalk",
            "extra_config": {
                "structured_endpoint": "/structured",
                "token_env": "TEST_DINGTALK_TOKEN",
                "token_header": "X-Auth-Token",
            },
        }
    )
    fetcher = DingtalkPayloadFetcher()
    with pytest.raises(AuthenticationFailedError):
        asyncio.run(fetcher.fetch_structured(config))


def test_real_transport_browser_auth_injects_cookie_and_xsrf_header(transport_server, monkeypatch) -> None:
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "visit",
            "source_url": transport_server["base_url"],
            "source_doc_key": "doc",
            "source_view_key": "view",
            "enabled": True,
            "collector_type": "dingtalk",
            "extra_config": {
                "structured_endpoint": "/structured",
                "structured_response_path": "data.payload",
                "structured_columns_path": "columns",
                "structured_rows_path": "rows",
                "structured_meta_path": "meta",
                "token_env": "TEST_DINGTALK_TOKEN",
                "token_header": "X-Auth-Token",
                "playwright_fallback_enabled": False,
            },
        }
    )
    monkeypatch.setenv("TEST_DINGTALK_TOKEN", "transport-token")
    fetcher = DingtalkPayloadFetcher()
    payload = asyncio.run(fetcher.fetch_structured(config))
    assert payload is not None
    request_headers = transport_server["request_log"][-1]["headers"]
    assert request_headers["X-Auth-Token"] == "transport-token"


def test_parallelv2_visit_collector_returns_structured_rows(dingtalk_parallelv2_server) -> None:
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "交付转售后回访闭环",
            "source_url": dingtalk_parallelv2_server["base_url"],
            "source_doc_key": "4j6OJ5jPAGa8eq3p",
            "source_view_key": "AKOehLK",
            "enabled": True,
            "collector_type": "dingtalk",
            "extra_config": {
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
        }
    )

    collector = VisitCollector(config)
    result = asyncio.run(collector.collect())
    recognition = VisitRecognizer().recognize(result.raw_columns, result.raw_rows)

    assert result.sync_status == "success"
    assert result.data_source == "parallelv2_binary"
    assert len(result.raw_rows) == 2
    assert result.raw_meta["sheet_id"] == "Igz9TVd"
    assert result.raw_meta["view_id"] == "AKOehLK"
    assert result.raw_meta["record_count"] == 2
    assert result.raw_meta["data_source"] == "parallelv2_binary"
    assert result.raw_meta["parallelv2_version"] == 2314
    assert result.raw_meta["decoder"]["decoded_row_count"] == 2
    assert recognition.normalized_records[0]["customer_name"] == "上海测试客户"
    assert recognition.normalized_records[0]["normalized_data"]["visit_status"] == "已回访"
    assert any("version=2314" in item["path"] for item in dingtalk_parallelv2_server["request_log"] if item["path"].startswith("/nt/api/sheets/Igz9TVd/records/binary/parallelV2"))


def test_direct_parallelv2_visit_collector_resolves_version_from_live_document_data(dingtalk_parallelv2_server) -> None:
    config = ModuleSourceConfig.from_mapping(
        {
            "module_code": "visit",
            "module_name": "交付转售后回访闭环",
            "source_url": dingtalk_parallelv2_server["base_url"],
            "source_doc_key": "4j6OJ5jPAGa8eq3p",
            "source_view_key": "AKOehLK",
            "enabled": True,
            "collector_type": "dingtalk",
            "extra_config": {
                "parallelv2_enabled": True,
                "parallelv2_direct_access_token_enabled": True,
                "parallelv2_dynamic_version_enabled": True,
                "parallelv2_access_token_endpoint": "/core/api/accessToken",
                "parallelv2_document_data_endpoint": "/api/document/data",
                "parallelv2_document_data_method": "POST",
                "parallelv2_document_data_json_body": {"pageMode": 2},
                "parallelv2_endpoint": "/nt/api/sheets/Igz9TVd/records/binary/parallelV2",
                "parallelv2_query_params": {
                    "version": 9999,
                    "sheetType": "",
                    "limit": 2001,
                },
                "parallelv2_sheet_id": "Igz9TVd",
                "parallelv2_view_id": "AKOehLK",
                "parallelv2_doc_key": "4j6OJ5jPAGa8eq3p",
                "parallelv2_dentry_key": "dYjLwGnPZcmxBbeB",
                "parallelv2_structure_payload_path": "tests/fixtures/dingtalk/visit/document_data_live.json",
                "parallelv2_token_header": "A-Token",
                "playwright_fallback_enabled": False,
            },
        }
    )

    collector = VisitCollector(config)
    result = asyncio.run(collector.collect())

    assert result.sync_status == "success"
    assert result.raw_meta["parallelv2_version"] == 2314
    assert result.raw_meta["parallelv2_version_source"] == "live_document_data_checkpoint"
    assert result.raw_meta["document_structure"]["source"] == "live_document_data"
    assert any(item["path"].startswith("/core/api/accessToken") for item in dingtalk_parallelv2_server["request_log"])
    assert any(item["path"].startswith("/api/document/data") for item in dingtalk_parallelv2_server["request_log"])
    assert any("version=2314" in item["path"] for item in dingtalk_parallelv2_server["request_log"] if item["path"].startswith("/nt/api/sheets/Igz9TVd/records/binary/parallelV2"))


@pytest.mark.parametrize(
    ("module_code", "collector_cls", "recognizer_cls"),
    [
        ("visit", VisitCollector, VisitRecognizer),
        ("inspection", InspectionCollector, InspectionRecognizer),
        ("proactive", ProactiveCollector, ProactiveRecognizer),
    ],
)
def test_recognizer_outputs_complete_structure_for_real_fixture_rows(module_code, collector_cls, recognizer_cls) -> None:
    # Use fixture config instead of registry defaults (visit/proactive now use dws_cli which needs CLI)
    fixture_config = _fixture_source_config(module_code)
    collector = collector_cls(fixture_config)
    collect_result = asyncio.run(collector.collect())

    recognizer = recognizer_cls()
    recognition_result = recognizer.recognize(collect_result.raw_columns, collect_result.raw_rows)

    assert isinstance(recognition_result, RecognitionResult)
    assert recognition_result.normalized_records
    assert isinstance(recognition_result.field_mapping, dict)
    assert isinstance(recognition_result.field_confidence, dict)
    assert isinstance(recognition_result.field_evidence, dict)
    assert isinstance(recognition_result.field_samples, dict)
    assert isinstance(recognition_result.unresolved_fields, list)
    assert recognition_result.recognition_status in {"full", "partial", "failed"}


def _fixture_source_config(module_code: str) -> ModuleSourceConfig:
    """Build a fixture config that uses local fixture files, independent of DWS CLI or registry."""
    fixture_path = str(Path(__file__).resolve().parents[1] / "services" / "collectors" / "fixtures" / module_code / "structured.json")
    mapping = {
        "visit": {
            "source_url": "https://alidocs.dingtalk.com",
            "source_doc_key": "4j6OJ5jPAGa8eq3p",
            "source_view_key": "AKOehLK",
        },
        "proactive": {
            "source_url": "https://alidocs.dingtalk.com",
            "source_doc_key": "J9LnW6jQKp6yelvD",
            "source_view_key": "f20Z2ZJ",
        },
        "inspection": {
            "source_url": "https://dingtalk.example.com/docs/inspection-real",
            "source_doc_key": "doc_inspection_real",
            "source_view_key": "view_inspection_default",
        },
    }
    meta = mapping[module_code]
    return ModuleSourceConfig.from_mapping({
        "module_code": module_code,
        "module_name": module_code,
        "source_url": meta["source_url"],
        "source_doc_key": meta["source_doc_key"],
        "source_view_key": meta["source_view_key"],
        "enabled": True,
        "collector_type": "fixture",
        "extra_config": {
            "structured_payload_path": fixture_path,
            "playwright_fallback_enabled": True,
        },
    })
