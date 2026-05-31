from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import patch

import pytest

from services.collectors.dws_cli_fetcher import (
    DwsCliPayloadFetcher,
    _HARDCODED_FIELD_MAPS,
    _normalize_dws_cli_cell_value,
)
from services.collectors.fetchers import ConfigurationMissingError
from services.collectors.source_config import ModuleSourceConfig
from core.config import Settings


# ── helpers ──────────────────────────────────────────────────────────


def _make_config(
    *,
    base_id: str = "o14dA3GK8g5LavPaT7dDQqoxV9ekBD76",
    table_id: str = "Igz9TVd",
) -> ModuleSourceConfig:
    return ModuleSourceConfig(
        module_code="visit",
        module_name="交付转售后回访闭环",
        source_url="https://alidocs.dingtalk.com",
        source_doc_key="4j6OJ5jPAGa8eq3p",
        source_view_key="AKOehLK",
        enabled=True,
        collector_type="dws_cli",
        extra_config={
            "dws_cli_base_id": base_id,
            "dws_cli_table_id": table_id,
        },
    )


def _completed_process(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["dws"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ── _normalize_dws_cli_cell_value tests ──────────────────────────────


class TestNormalizeCellValue:
    def test_none_returns_empty_string(self):
        assert _normalize_dws_cli_cell_value(None) == ""

    def test_string_returns_as_is(self):
        assert _normalize_dws_cli_cell_value("hello") == "hello"

    def test_empty_string(self):
        assert _normalize_dws_cli_cell_value("") == ""

    def test_int_returns_string(self):
        assert _normalize_dws_cli_cell_value(42) == "42"

    def test_float_returns_string(self):
        assert _normalize_dws_cli_cell_value(3.14) == "3.14"

    def test_bool_returns_lowercase(self):
        assert _normalize_dws_cli_cell_value(True) == "true"
        assert _normalize_dws_cli_cell_value(False) == "false"

    def test_url_type_returns_link(self):
        val = {"link": "https://example.com", "text": "点击这里"}
        assert _normalize_dws_cli_cell_value(val) == "https://example.com"

    def test_url_type_link_missing_returns_text(self):
        val = {"link": None, "text": "点击这里"}
        assert _normalize_dws_cli_cell_value(val) == "点击这里"

    def test_single_select_returns_name(self):
        val = {"id": "sel1", "name": "选项A"}
        assert _normalize_dws_cli_cell_value(val) == "选项A"

    def test_multi_select_joins_names(self):
        val = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
        assert _normalize_dws_cli_cell_value(val) == "A、B"

    def test_user_list_joins_names(self):
        val = [{"id": "u1", "name": "张三"}, {"id": "u2", "name": "李四"}]
        assert _normalize_dws_cli_cell_value(val) == "张三、李四"

    def test_generic_dict_returns_json(self):
        val = {"foo": "bar", "count": 1}
        result = _normalize_dws_cli_cell_value(val)
        assert json.loads(result) == val

    def test_list_of_strings(self):
        assert _normalize_dws_cli_cell_value(["a", "b", "c"]) == "a、b、c"

    def test_list_of_dicts_without_name(self):
        val = [{"text": "X"}, {"text": "Y"}]
        assert _normalize_dws_cli_cell_value(val) == "X、Y"


# ── DwsCliPayloadFetcher tests ───────────────────────────────────────


class TestDwsCliPayloadFetcher:
    def test_transport_mode(self):
        fetcher = DwsCliPayloadFetcher()
        assert fetcher.transport_mode == "dws_cli"

    def test_fetch_structured_missing_cli(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config()
        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value=None):
            with pytest.raises(ConfigurationMissingError, match="DWS CLI not found"):
                asyncio.run(fetcher.fetch_structured(config))

    def test_fetch_structured_missing_base_id(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config(base_id="")
        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value="/usr/local/bin/dws"):
            with pytest.raises(ConfigurationMissingError, match="dws_cli requires dws_cli_base_id"):
                asyncio.run(fetcher.fetch_structured(config))

    def test_fetch_structured_missing_table_id(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config(table_id="")
        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value="/usr/local/bin/dws"):
            with pytest.raises(ConfigurationMissingError, match="dws_cli requires dws_cli_base_id and dws_cli_table_id"):
                asyncio.run(fetcher.fetch_structured(config))

    def test_fetch_structured_happy_path(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config()

        field_response = json.dumps([
            {"fieldId": "fxg8rhmv7xum7ybd4ejfs", "fieldName": "审核日期"},
            {"fieldId": "rbiax8fi5eklvmdlc4v5d", "fieldName": "客户名称"},
            {"fieldId": "ulembeeuza3ctgftx69n1", "fieldName": "PTS交付链接"},
        ])
        record_response = json.dumps({
            "data": [
                {
                    "recordId": "rec001",
                    "cells": {
                        "fxg8rhmv7xum7ybd4ejfs": "2024-01-15",
                        "rbiax8fi5eklvmdlc4v5d": "测试客户",
                        "ulembeeuza3ctgftx69n1": {"link": "https://pts.example.com", "text": "PTS链接"},
                    },
                },
            ],
            "hasMore": False,
        })

        responses = [field_response, record_response]

        def mock_run(cmd, timeout):
            return _completed_process(stdout=responses.pop(0))

        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value="/usr/local/bin/dws"):
            with patch("services.collectors.dws_cli_fetcher._run_subprocess", side_effect=mock_run):
                result = asyncio.run(fetcher.fetch_structured(config))

        assert result is not None
        assert result["data_source"] == "dws_cli"
        assert result["raw_columns"] == ["审核日期", "客户名称", "PTS交付链接"]
        assert len(result["raw_rows"]) == 1
        row = result["raw_rows"][0]
        assert row["row_id"] == "rec001"
        assert row["审核日期"] == "2024-01-15"
        assert row["客户名称"] == "测试客户"
        assert row["PTS交付链接"] == "https://pts.example.com"
        assert result["raw_meta"]["transport_mode"] == "dws_cli"
        assert result["raw_meta"]["record_count"] == 1

    def test_fetch_structured_pagination(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config()

        field_response = json.dumps([
            {"fieldId": "f1", "fieldName": "姓名"},
        ])
        page1 = json.dumps({
            "data": [{"recordId": "r1", "cells": {"f1": "Alice"}}],
            "hasMore": True,
            "nextCursor": "cursor123",
        })
        page2 = json.dumps({
            "data": [{"recordId": "r2", "cells": {"f1": "Bob"}}],
            "hasMore": False,
        })

        responses = [field_response, page1, page2]

        def mock_run(cmd, timeout):
            return _completed_process(stdout=responses.pop(0))

        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value="/usr/local/bin/dws"):
            with patch("services.collectors.dws_cli_fetcher._run_subprocess", side_effect=mock_run):
                result = asyncio.run(fetcher.fetch_structured(config))

        assert result is not None
        assert len(result["raw_rows"]) == 2
        assert result["raw_rows"][0]["姓名"] == "Alice"
        assert result["raw_rows"][1]["姓名"] == "Bob"

    def test_fetch_structured_field_map_fallback(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config()  # table_id=Igz9TVd has hardcoded fallback

        record_response = json.dumps({
            "data": [
                {
                    "recordId": "rec001",
                    "cells": {
                        "fxg8rhmv7xum7ybd4ejfs": "2024-01-15",
                        "rbiax8fi5eklvmdlc4v5d": "测试客户",
                    },
                },
            ],
            "hasMore": False,
        })

        def mock_run(cmd, timeout):
            if "field" in cmd:
                # Simulate CLI failure → should fall back to hardcoded map
                return _completed_process(returncode=1, stderr="auth error")
            if "record" in cmd:
                return _completed_process(stdout=record_response)
            return _completed_process(returncode=1)

        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value="/usr/local/bin/dws"):
            with patch("services.collectors.dws_cli_fetcher._run_subprocess", side_effect=mock_run):
                result = asyncio.run(fetcher.fetch_structured(config))

        assert result is not None
        assert "审核日期" in result["raw_columns"]
        assert "客户名称" in result["raw_columns"]
        assert len(result["raw_rows"]) == 1

    def test_fetch_structured_field_map_fallback_no_hardcoded(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config(table_id="UNKNOWN_TABLE")

        def mock_run(cmd, timeout):
            return _completed_process(returncode=1, stderr="auth error")

        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value="/usr/local/bin/dws"):
            with patch("services.collectors.dws_cli_fetcher._run_subprocess", side_effect=mock_run):
                with pytest.raises(ConfigurationMissingError, match="DWS CLI field get failed"):
                    asyncio.run(fetcher.fetch_structured(config))

    def test_fetch_structured_record_query_failure(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config()

        field_response = json.dumps([
            {"fieldId": "f1", "fieldName": "姓名"},
        ])

        def mock_run(cmd, timeout):
            if "field" in cmd:
                return _completed_process(stdout=field_response)
            if "record" in cmd:
                return _completed_process(returncode=1, stderr="query failed")
            return _completed_process(returncode=1)

        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value="/usr/local/bin/dws"):
            with patch("services.collectors.dws_cli_fetcher._run_subprocess", side_effect=mock_run):
                with pytest.raises(ConfigurationMissingError, match="DWS CLI record query failed"):
                    asyncio.run(fetcher.fetch_structured(config))

    def test_fetch_state_returns_none(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config()
        result = asyncio.run(fetcher.fetch_state(config))
        assert result is None

    def test_hardcoded_field_maps_are_present(self):
        assert "Igz9TVd" in _HARDCODED_FIELD_MAPS
        assert "Z991EZV" in _HARDCODED_FIELD_MAPS
        visit_map = _HARDCODED_FIELD_MAPS["Igz9TVd"]
        proactive_map = _HARDCODED_FIELD_MAPS["Z991EZV"]
        assert "客户名称" in visit_map.values()
        assert "售后管家" in proactive_map.values()

    def test_fetch_structured_proactive_module(self):
        fetcher = DwsCliPayloadFetcher()
        config = _make_config(
            base_id="KGZLxjv9VG37XNDXS45epDXYV6EDybno",
            table_id="Z991EZV",
        )

        # Force field map CLI failure to use hardcoded fallback
        record_response = json.dumps({
            "data": [
                {
                    "recordId": "rec_proactive_001",
                    "cells": {
                        "hgt2r3d790vwxwhljzbv4": "主动客户",
                        "xba1d4qiszjxg6yrolj0e": "张三",
                    },
                },
            ],
            "hasMore": False,
        })

        def mock_run(cmd, timeout):
            if "field" in cmd:
                return _completed_process(returncode=1, stderr="test failure")
            if "record" in cmd:
                return _completed_process(stdout=record_response)
            return _completed_process(returncode=1)

        with patch("services.collectors.dws_cli_fetcher._resolve_dws_cli_path", return_value="/usr/local/bin/dws"):
            with patch("services.collectors.dws_cli_fetcher._run_subprocess", side_effect=mock_run):
                result = asyncio.run(fetcher.fetch_structured(config))

        assert result is not None
        assert "客户名称" in result["raw_columns"]
        assert "售后管家" in result["raw_columns"]
        row = result["raw_rows"][0]
        assert row["客户名称"] == "主动客户"
        assert row["售后管家"] == "张三"