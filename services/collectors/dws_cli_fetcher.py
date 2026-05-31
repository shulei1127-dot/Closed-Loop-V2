from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from typing import Any

from core.config import Settings, get_settings
from services.collectors.fetchers import ConfigurationMissingError
from services.collectors.source_config import ModuleSourceConfig


# ── hardcoded field-id → 中文列名 fallback ──────────────────────────

# 交付转售后 (base_id=o14dA3GK8g5LavPaT7dDQqoxV9ekBD76, table_id=Igz9TVd)
_VISIT_FIELD_MAP: dict[str, str] = {
    "fxg8rhmv7xum7ybd4ejfs": "审核日期",
    "rbiax8fi5eklvmdlc4v5d": "客户名称",
    "o7dk5r68igm7syh8funhl": "区域",
    "ulembeeuza3ctgftx69n1": "PTS交付链接",
    "ab6g79dhla7ta1n4vtryx": "交付分配人3",
    "doox5joqqw0mae62xhmtq": "服务内容",
    "rgcjj2wcarim8lg9apphh": "售后有效服务期",
    "oz9yut6kbwr4gagps0us7": "项目类型",
    "vth94xg28fxpt6ribmpjd": "交付类型",
    "yc4gxs0mc3uxrujoduzq6": "合同类型",
    "35voqsv7xy8pk5pknr47m": "回访链接",
    "p9wt4e0aqxjbdkpopp04n": "回访类型",
    "8yhq31vqdn2vop87ryiei": "pts选择的满意度",
    "3np07ifl4yfr6jsxghcum": "审核是否通过",
    "vd8h8nk8m4tr42nmroa7q": "回访状态",
    "csur9mqp3nl98gh2ud3lb": "对接人",
    "1u4lo9afeg790wdhte6pd": "交付满意度",
    "21frjccgwrmyr0avmrkzw": "客户体感",
    "38rtbpzxb2yj3efgq0nea": "巡检时间",
    "jxdh8qmijds6w92j5szwb": "备注",
    "8b9ypudkbopzmyor1u8ab": "待办",
    "2k0lb1jrm7zxhuzaek10s": "crm下单与交付校对",
    "8q5udpxjhanq5eyn0k98a": "校对备注",
    "ovr2k6cloj9p6p04pjjmr": "是否逾期",
    "do4wo842yyzwf9ln7a1vu": "逾期原因",
    "xzHfzQm": "回访人",
    "gpf6EE7": "开始回访时间",
    "O5gfavm": "逾期时间",
    "nRyrdvN": "电话联系异常情况",
    "19r1LyK": "交付分配人",
    "F3tJ4pm": "电话联系",
    "qWDHbYc": "交付负责人",
}

# 超半年主动回访 (base_id=KGZLxjv9VG37XNDXS45epDXYV6EDybno, table_id=Z991EZV)
_PROACTIVE_FIELD_MAP: dict[str, str] = {
    "xba1d4qiszjxg6yrolj0e": "售后管家",
    "t3b1hpftuig69a01q3g86": "是否逾期",
    "da6wcmrwg5q7g300a4xiu": "启动时间",
    "hgt2r3d790vwxwhljzbv4": "客户名称",
    "ot19izh5cwhc06klq7xqr": "客户销售",
    "g4bnhpy927jrtux59da4x": "产品ID链接",
    "pkdsnmfdx9nz7d077k26f": "项目名称",
    "nyfbs7m3c68p56k3mkco8": "服务满意度",
    "vxfqjxrfpcm57vc1rlbu4": "回访链接",
    "6cysizih3m78w4r5al8g5": "建联状态-工程师",
    "00h1symig24giik0lyydo": "逾期原因",
    "uvs0taztjgklk9m628ryl": "产品是否存在异常",
    "prt9ugh2lugbsq3jz1hpz": "战队",
    "uc3ccolfpugmh86hlrz04": "产品使用满意度",
    "7fhnd4uazyxmup2hfbms1": "项目ID",
    "3nj525v2lm77gku4utpld": "是否平台联系人",
    "4tml9cmh11v7z0wgiwjww": "联系人角色",
    "9yagfcgwxnbni6ipigazd": "客户联系人",
    "2gibcuzj2eymhx5ie4z6r": "客户联系方式",
    "i8rdip4oab2azj3p82hhl": "备注（异常详情+其他备注）",
    "0lf7zfghidea7nt3m8pcj": "服务状态",
    "Oxaj2U7": "逾期日期",
    "r5kobxo": "是否进微信群",
    "uHSUwm5": "是否提供健康检查",
    "aXlp8gx": "回访人",
    "ebcf3bS": "触达客户时间",
    "Aw9oj4g": "产品是否正常使用",
    "qqNxMt2": "交付分配人",
    "SmqoeiI": "特殊情况备注",
    "sSz5qYQ": "交付负责人",
    "09QjTgw": "客户建联状态",
    "s7w1Iaf": "微信/企微群名",
    "FuIYAUV": "交付负责人2",
    "fvUKFVU": "电话联系",
    "DQ47Lcv": "电话联系异常情况",
    "bjvzQ6U": "销售",
}

_HARDCODED_FIELD_MAPS: dict[str, dict[str, str]] = {
    "Igz9TVd": _VISIT_FIELD_MAP,
    "Z991EZV": _PROACTIVE_FIELD_MAP,
}


# ── helpers ──────────────────────────────────────────────────────────


def _resolve_dws_cli_path(settings: Settings) -> str | None:
    """Return DWS CLI executable path, or None if not found."""
    explicit = settings.dws_cli_path
    if explicit:
        return explicit if os.path.isfile(explicit) and os.access(explicit, os.X_OK) else None
    return shutil.which("dws")


def _normalize_dws_cli_cell_value(value: Any) -> str:
    """Normalize a DWS CLI cell value to a flat string, matching parallelv2 decoder output."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        # URL type: {"link": "...", "text": "..."} → prefer link (actual URL) over text (display label)
        # parallelv2 decoder returns url for link fields; we return link here for consistency.
        if "link" in value and "text" in value:
            return str(value.get("link") or value.get("text") or "")
        # Single select: {"id": "...", "name": "..."} → use name
        if "id" in value and "name" in value:
            return str(value["name"])
        # Generic dict fallback
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        # Multi-select or user list: [{"name": "A"}, {"name": "B"}] → comma-join
        # Use "、" separator to match parallelv2 decoder multi-select format
        names = []
        for item in value:
            if isinstance(item, dict):
                # Prefer name/text for display, then userId (Dingtalk user field), then JSON fallback
                name_val = item.get("name") or item.get("text") or item.get("realName") or item.get("displayName")
                if name_val and isinstance(name_val, str) and name_val.strip():
                    names.append(name_val.strip())
                elif item.get("userId") and isinstance(item["userId"], str):
                    names.append(item["userId"].strip())
                else:
                    names.append(json.dumps(item, ensure_ascii=False))
            else:
                names.append(str(item))
        return "、".join(names)
    return str(value)


def _run_subprocess(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run subprocess synchronously, for asyncio.to_thread."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ── DwsCliPayloadFetcher ────────────────────────────────────────────


class DwsCliPayloadFetcher:
    transport_mode = "dws_cli"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def fetch_structured(self, config: ModuleSourceConfig) -> dict[str, Any] | None:
        dws_cli = _resolve_dws_cli_path(self.settings)
        if not dws_cli:
            raise ConfigurationMissingError("DWS CLI not found; install dws or set DWS_CLI_PATH")

        base_id = config.get_extra("dws_cli_base_id")
        table_id = config.get_extra("dws_cli_table_id")
        if not base_id or not table_id:
            raise ConfigurationMissingError("dws_cli requires dws_cli_base_id and dws_cli_table_id in extra_config")

        # 1. Resolve field-id → fieldName mapping
        field_map = await self._fetch_field_map(dws_cli, base_id, table_id)

        # 2. Build DWS CLI --filters argument if configured
        dws_filters = config.get_extra("dws_cli_filters")

        # 3. Paginate all records
        all_records = await self._fetch_all_records(dws_cli, base_id, table_id, filters=dws_filters)

        # 4. Build raw_columns / raw_rows compatible with downstream recognizers
        # Build reverse map: fieldName → fieldId for row filter matching
        name_to_id = {v: k for k, v in field_map.items()}
        raw_columns = [field_map[fid] for fid in field_map]
        raw_rows = []
        total_fetched = len(all_records)
        for record in all_records:
            row: dict[str, Any] = {"row_id": record.get("recordId", "")}
            for fid, cname in field_map.items():
                cell_value = record.get("cells", {}).get(fid)
                row[cname] = _normalize_dws_cli_cell_value(cell_value)
            raw_rows.append(row)

        # 5. Apply client-side row filter if configured
        row_filter = config.get_extra("dws_cli_row_filter")
        if row_filter and isinstance(row_filter, dict):
            raw_rows = self._apply_row_filter(raw_rows, row_filter)

        return {
            "data_source": "dws_cli",
            "raw_columns": raw_columns,
            "raw_rows": raw_rows,
            "raw_meta": {
                "transport_mode": self.transport_mode,
                "base_id": base_id,
                "table_id": table_id,
                "total_fetched": total_fetched,
                "filtered_count": len(raw_rows),
                "filter_applied": bool(row_filter),
                "record_count": len(raw_rows),
                "field_count": len(raw_columns),
            },
        }

    async def fetch_state(self, config: ModuleSourceConfig) -> dict[str, Any] | None:
        # DWS CLI does not need a separate state step
        return None

    @staticmethod
    def _apply_row_filter(
        rows: list[dict[str, Any]],
        row_filter: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Filter rows client-side based on dws_cli_row_filter config.

        row_filter format (all conditions AND-ed):
        {
            "回访状态": {"is": "已回访"},
            "回访链接": {"is_empty": true},
            "备注": {"is_not_empty": true},
        }

        Supported operators per field:
        - {"is": "value"}           — field value equals string (case-insensitive)
        - {"is_not": "value"}       — field value does not equal string
        - {"is_empty": true}        — field value is empty/None
        - {"is_not_empty": true}    — field value is non-empty
        - {"is_in": ["a", "b"]}     — field value is one of the listed values
        - {"contains": "text"}      — field value contains substring
        """
        filtered = []
        for row in rows:
            match = True
            for field_name, condition in row_filter.items():
                if not isinstance(condition, dict):
                    continue
                value = str(row.get(field_name, "") or "").strip()

                if "is" in condition:
                    if value.lower() != str(condition["is"]).strip().lower():
                        match = False
                        break
                if "is_not" in condition:
                    if value.lower() == str(condition["is_not"]).strip().lower():
                        match = False
                        break
                if condition.get("is_empty"):
                    if value:
                        match = False
                        break
                if condition.get("is_not_empty"):
                    if not value:
                        match = False
                        break
                if "is_in" in condition and isinstance(condition["is_in"], list):
                    if value.lower() not in [str(v).strip().lower() for v in condition["is_in"]]:
                        match = False
                        break
                if "contains" in condition:
                    if str(condition["contains"]).lower() not in value.lower():
                        match = False
                        break
            if match:
                filtered.append(row)
        return filtered

    async def _fetch_field_map(self, dws_cli: str, base_id: str, table_id: str) -> dict[str, str]:
        """Call `dws aitable field get` to build {fieldId: fieldName}.

        Falls back to hardcoded map when available.
        """
        cmd = [
            dws_cli,
            "aitable", "field", "get",
            "--base-id", base_id,
            "--table-id", table_id,
        ]
        try:
            result = await asyncio.to_thread(
                _run_subprocess,
                cmd,
                self.settings.dws_cli_timeout_seconds,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            # Fallback to hardcoded map on CLI failure
            fallback = _HARDCODED_FIELD_MAPS.get(table_id)
            if fallback:
                return dict(fallback)
            raise ConfigurationMissingError(f"DWS CLI field get failed and no hardcoded fallback for table {table_id}: {exc}") from exc

        if result.returncode != 0:
            fallback = _HARDCODED_FIELD_MAPS.get(table_id)
            if fallback:
                return dict(fallback)
            raise ConfigurationMissingError(f"DWS CLI field get failed (rc={result.returncode}): {(result.stderr or '').strip()[:200]}")

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            fallback = _HARDCODED_FIELD_MAPS.get(table_id)
            if fallback:
                return dict(fallback)
            raise ConfigurationMissingError(f"DWS CLI field get returned invalid JSON: {exc}") from exc

        fields = payload if isinstance(payload, list) else None
        if fields is None:
            # DWS CLI returns {"data": {"fields": [...]}}
            data = payload.get("data", payload)
            if isinstance(data, dict):
                fields = data.get("fields", data.get("items", []))
            elif isinstance(data, list):
                fields = data
            else:
                fields = []
        if not isinstance(fields, list):
            fallback = _HARDCODED_FIELD_MAPS.get(table_id)
            if fallback:
                return dict(fallback)
            raise ConfigurationMissingError(f"DWS CLI field get unexpected response shape: {type(fields)}")

        field_map: dict[str, str] = {}
        for field in fields:
            if isinstance(field, dict):
                fid = field.get("fieldId") or field.get("id")
                fname = field.get("fieldName") or field.get("name")
                if fid and fname:
                    field_map[str(fid)] = str(fname)

        if not field_map:
            fallback = _HARDCODED_FIELD_MAPS.get(table_id)
            if fallback:
                return dict(fallback)

        return field_map

    async def _fetch_all_records(
        self,
        dws_cli: str,
        base_id: str,
        table_id: str,
        *,
        filters: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Paginate `dws aitable record query` until all records are fetched."""
        all_records: list[dict[str, Any]] = []
        cursor: str | None = None
        page_size = self.settings.dws_cli_page_size

        while True:
            cmd = [
                dws_cli,
                "aitable", "record", "query",
                "--base-id", base_id,
                "--table-id", table_id,
                "--limit", str(page_size),
            ]
            if cursor:
                cmd.extend(["--cursor", cursor])
            if filters and isinstance(filters, dict):
                cmd.extend(["--filters", json.dumps(filters, ensure_ascii=False)])

            result = await asyncio.to_thread(
                _run_subprocess,
                cmd,
                self.settings.dws_cli_timeout_seconds,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                raise ConfigurationMissingError(f"DWS CLI record query failed (rc={result.returncode}): {stderr[:200]}")

            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise ConfigurationMissingError(f"DWS CLI record query returned invalid JSON: {exc}") from exc

            if not isinstance(payload, dict):
                raise ConfigurationMissingError(f"DWS CLI record query unexpected response shape: {type(payload)}")

            # DWS CLI returns {"data": {"records": [...], "nextCursor": "...", "hasMore": bool}}
            data = payload.get("data", payload)
            if isinstance(data, dict):
                records = data.get("records", [])
            elif isinstance(data, list):
                records = data
            else:
                records = []

            if isinstance(records, list):
                all_records.extend(records)

            # Check if there are more pages — DWS CLI returns nextCursor even when hasMore is None
            next_cursor = data.get("nextCursor") or data.get("cursor") if isinstance(data, dict) else None
            has_more_flag = data.get("hasMore") if isinstance(data, dict) else None
            # Continue if nextCursor exists (DWS CLI always provides it when more data available)
            # OR if hasMore is explicitly True
            has_more = bool(next_cursor) if has_more_flag is None else bool(has_more_flag)
            if not has_more or not next_cursor:
                break
            cursor = next_cursor

        return all_records
