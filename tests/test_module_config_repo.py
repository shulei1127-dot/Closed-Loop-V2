from sqlalchemy import select

from models.module_config import ModuleConfig
from repositories.module_config_repo import ModuleConfigRepository
from services.module_registry import default_module_configs


def test_upsert_defaults_promotes_stale_proactive_fixture_config(db_session) -> None:
    db_session.add(
        ModuleConfig(
            module_code="proactive",
            module_name="超半年主动回访闭环",
            source_url="https://dingtalk.example.com/docs/proactive-real",
            source_doc_key="doc_proactive_real",
            source_view_key="view_proactive_default",
            enabled=True,
            collector_type="fixture",
            sync_cron=None,
            extra_config={
                "structured_payload_path": "services/collectors/fixtures/proactive/structured.json",
                "state_payload_path": "services/collectors/fixtures/proactive/state.json",
                "playwright_fallback_enabled": True,
            },
        )
    )
    db_session.commit()

    ModuleConfigRepository(db_session).upsert_defaults(default_module_configs())
    db_session.commit()

    config = db_session.scalar(select(ModuleConfig).where(ModuleConfig.module_code == "proactive"))
    assert config is not None
    assert config.collector_type == "dingtalk"
    assert config.source_url == "https://alidocs.dingtalk.com"
    assert config.source_doc_key == "J9LnW6jQKp6yelvD"
    assert config.source_view_key == "f20Z2ZJ"
    assert config.extra_config["parallelv2_sheet_id"] == "Z991EZV"
    assert config.extra_config["parallelv2_view_id"] == "f20Z2ZJ"
    assert "structured_payload_path" not in config.extra_config
    assert "state_payload_path" not in config.extra_config
