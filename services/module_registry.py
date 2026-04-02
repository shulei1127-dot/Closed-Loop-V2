from pathlib import Path

from core.exceptions import UnsupportedModuleError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "services" / "collectors" / "fixtures"


MODULE_DEFINITIONS = {
    "visit": {
        "module_name": "交付转售后回访闭环",
        "source_url": "https://dingtalk.example.com/docs/visit-real",
        "source_doc_key": "doc_visit_real",
        "source_view_key": "view_visit_default",
        "collector_type": "fixture",
        "extra_config": {
            "structured_payload_path": str(FIXTURE_ROOT / "visit" / "structured.json"),
            "state_payload_path": str(FIXTURE_ROOT / "visit" / "state.json"),
            "playwright_fallback_enabled": True,
        },
    },
    "inspection": {
        "module_name": "巡检工单闭环",
        "source_url": "https://dingtalk.example.com/docs/inspection-real",
        "source_doc_key": "doc_inspection_real",
        "source_view_key": "view_inspection_default",
        "collector_type": "fixture",
        "extra_config": {
            "structured_payload_path": str(FIXTURE_ROOT / "inspection" / "structured.json"),
            "state_payload_path": str(FIXTURE_ROOT / "inspection" / "state.json"),
            "playwright_fallback_enabled": True,
        },
    },
    "proactive": {
        "module_name": "超半年主动回访闭环",
        "source_url": "https://dingtalk.example.com/docs/proactive-real",
        "source_doc_key": "doc_proactive_real",
        "source_view_key": "view_proactive_default",
        "collector_type": "fixture",
        "extra_config": {
            "structured_payload_path": str(FIXTURE_ROOT / "proactive" / "structured.json"),
            "state_payload_path": str(FIXTURE_ROOT / "proactive" / "state.json"),
            "playwright_fallback_enabled": True,
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
