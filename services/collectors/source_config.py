from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
from typing import Any

from models.module_config import ModuleConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class ModuleSourceConfig:
    module_code: str
    module_name: str
    source_url: str
    source_doc_key: str
    source_view_key: str | None
    enabled: bool
    collector_type: str
    extra_config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_model(cls, model: ModuleConfig) -> "ModuleSourceConfig":
        return cls(
            module_code=model.module_code,
            module_name=model.module_name,
            source_url=model.source_url,
            source_doc_key=model.source_doc_key,
            source_view_key=model.source_view_key,
            enabled=model.enabled,
            collector_type=model.collector_type,
            extra_config=dict(model.extra_config or {}),
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ModuleSourceConfig":
        return cls(
            module_code=data["module_code"],
            module_name=data.get("module_name", data["module_code"]),
            source_url=data["source_url"],
            source_doc_key=data["source_doc_key"],
            source_view_key=data.get("source_view_key"),
            enabled=data.get("enabled", True),
            collector_type=data.get("collector_type", "fixture"),
            extra_config=dict(data.get("extra_config", {})),
        )

    def resolve_path(self, key: str) -> Path | None:
        value = self.extra_config.get(key)
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def supports_fallback(self) -> bool:
        return bool(self.extra_config.get("playwright_fallback_enabled", True))

    def has_inline_payload(self, key: str) -> bool:
        value = self.extra_config.get(key)
        return isinstance(value, dict)

    def get_extra(self, key: str, default: Any = None) -> Any:
        return self.extra_config.get(key, default)

    def get_env_value(self, key: str, default: str | None = None) -> str | None:
        env_name = self.extra_config.get(key)
        if not isinstance(env_name, str) or not env_name:
            return default
        return os.getenv(env_name, default)

    def get_env_name(self, key: str) -> str | None:
        env_name = self.extra_config.get(key)
        if not isinstance(env_name, str) or not env_name:
            return None
        return env_name
