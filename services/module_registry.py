from pathlib import Path

from core.exceptions import UnsupportedModuleError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "services" / "collectors" / "fixtures"


MODULE_DEFINITIONS = {
    "visit": {
        "module_name": "交付转售后回访闭环",
        "source_url": "https://alidocs.dingtalk.com",
        "source_doc_key": "4j6OJ5jPAGa8eq3p",
        "source_view_key": "AKOehLK",
        "collector_type": "dws_cli",
        "extra_config": {
            "dws_cli_base_id": "o14dA3GK8g5LavPaT7dDQqoxV9ekBD76",
            "dws_cli_table_id": "Igz9TVd",
            "dws_cli_row_filter": {
                "回访状态": {"is": "已回访"},
                "回访链接": {"is_empty": True},
                "备注": {"is_not_empty": True},
            },
            "parallelv2_enabled": True,
            "parallelv2_direct_access_token_enabled": True,
            "parallelv2_dynamic_version_enabled": True,
            "parallelv2_access_token_endpoint": "/core/api/accessToken",
            "parallelv2_document_data_endpoint": "/api/document/data",
            "parallelv2_document_data_method": "POST",
            "parallelv2_document_data_json_body": {
                "pageMode": 2,
            },
            "parallelv2_endpoint": "/nt/api/sheets/Igz9TVd/records/binary/parallelV2",
            "parallelv2_query_params": {
                "version": 4308,
                "sheetType": "",
                "limit": 2001,
            },
            "parallelv2_sheet_id": "Igz9TVd",
            "parallelv2_view_id": "AKOehLK",
            "parallelv2_doc_key": "4j6OJ5jPAGa8eq3p",
            "parallelv2_dentry_key": "dYjLwGnPZcmxBbeB",
            "parallelv2_structure_payload_path": str(PROJECT_ROOT / "tests" / "fixtures" / "dingtalk" / "visit" / "document_data_live.json"),
            "parallelv2_token_header": "A-Token",
            "playwright_fallback_enabled": False,
            "execute_cron": "",
            "execute_dry_run": False,
        },
    },
    "proactive": {
        "module_name": "超半年主动回访闭环",
        "source_url": "https://alidocs.dingtalk.com",
        "source_doc_key": "J9LnW6jQKp6yelvD",
        "source_view_key": "f20Z2ZJ",
        "collector_type": "dws_cli",
        "extra_config": {
            "dws_cli_base_id": "KGZLxjv9VG37XNDXS45epDXYV6EDybno",
            "dws_cli_table_id": "Z991EZV",
            "dws_cli_row_filter": {
                "客户建联状态": {"is_in": ["已建联", "售后断联", "不用回访"]},
                "回访链接": {"is_empty": True},
            },
            "parallelv2_enabled": True,
            "parallelv2_direct_access_token_enabled": True,
            "parallelv2_dynamic_version_enabled": True,
            "parallelv2_access_token_endpoint": "/core/api/accessToken",
            "parallelv2_document_data_endpoint": "/api/document/data",
            "parallelv2_document_data_method": "POST",
            "parallelv2_document_data_json_body": {
                "pageMode": 2,
            },
            "parallelv2_endpoint": "/nt/api/sheets/Z991EZV/records/binary/parallelV2",
            "parallelv2_query_params": {
                "version": 5985,
                "sheetType": "",
                "limit": 2001,
            },
            "parallelv2_sheet_id": "Z991EZV",
            "parallelv2_view_id": "f20Z2ZJ",
            "parallelv2_doc_key": "J9LnW6jQKp6yelvD",
            "parallelv2_dentry_key": "pkEZPjVb9HGZX53X",
            "parallelv2_token_header": "A-Token",
            "playwright_fallback_enabled": False,
            "execute_cron": "",
            "execute_dry_run": False,
        },
    },
    "review": {
        "module_name": "交付转售后审核闭环",
        "source_url": "https://pts.chaitin.net",
        "source_doc_key": "pts_review",
        "source_view_key": "",
        "collector_type": "pts_api",
        "extra_config": {
            "execute_cron": "",
            "execute_dry_run": False,
        },
    },
}


def get_module_definition(module_code: str) -> dict:
    if module_code not in MODULE_DEFINITIONS:
        raise UnsupportedModuleError(f"unsupported module_code: {module_code}")
    return MODULE_DEFINITIONS[module_code]


def default_module_configs() -> list[dict]:
    return [
        {
            "module_code": module_code,
            "module_name": meta["module_name"],
            "source_url": meta["source_url"],
            "source_doc_key": meta["source_doc_key"],
            "source_view_key": meta["source_view_key"],
            "enabled": True,
            "collector_type": meta["collector_type"],
            "sync_cron": None,
            "extra_config": meta.get("extra_config", {}),
        }
        for module_code, meta in MODULE_DEFINITIONS.items()
    ]
