"""交付转售后审核 — 钉钉数据表写入"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from services.audit.schemas import AuditResult

logger = logging.getLogger(__name__)

_UNICODE_INVISIBLE = re.compile(r"[​-‏ -‮⁠-⁯﻿]")


def _sanitize(text: str) -> str:
    return _UNICODE_INVISIBLE.sub("", text)


# ── 钉钉数据表配置 ────────────────────────────────────────

REGION_OPTION_MAP: dict[str, str] = {
    "华东战区": "dtU8OVeJM0",
    "华北东北战区": "6pW9KJf5fw",
    "华南战区": "dUlPuelRVS",
    "西南西北战区": "zWWhy9BF3r",
    "华中战区": "RH96m2aE0m",
    "通信头部战队": "x7Mpm5uGbE",
    "政企行业": "bpcFUBUMKP",
    "金融头部战队": "rwPSYDRLPU",
    "商业策略组": "GRr0sYDlCX",
    "战略伙伴战队": "hsjOHtG0hH",
    "政府头部战队": "o2fBZY4P4o",
    "能源央企头部战队": "XASjOCnSot",
}

DELIVERY_TYPE_OPTION_MAP: dict[str, str] = {
    "生态交付": "5sxBF2nYLP",
    "自交付": "S0SAcwL5tf",
    "原厂交付": "GxnMUksX0o",
    "无交付": "CvB65Wm2KN",
    "不回访": "PuEnRtgXRv",
}

PROJECT_TYPE_OPTION_MAP: dict[str, str] = {
    "新购": "dStkuKUtxl",
    "租用": "xgfRq8AVbr",
    "续保": "MwcXc3NeUc",
    "新购➕续保": "rP1U101AoS",
    "新购➕租用": "JbUVJkSjaf",
}

# 交付分配人姓名 → 区域（钉钉选项 ID）
ASSIGNER_REGION_MAP: dict[str, str] = {
    # 华东战区
    "任嘉伟": "dtU8OVeJM0", "鲍金鑫": "dtU8OVeJM0", "高云松": "dtU8OVeJM0", "郭林成": "dtU8OVeJM0",
    "陈欧翔": "dtU8OVeJM0", "夏睿婷": "dtU8OVeJM0", "殷培源": "dtU8OVeJM0", "宋健": "dtU8OVeJM0",
    "陈祎雯": "dtU8OVeJM0", "王瑞": "dtU8OVeJM0", "陈平远": "dtU8OVeJM0", "卢占文": "dtU8OVeJM0",
    "黄瑞": "dtU8OVeJM0", "廖雨田": "dtU8OVeJM0", "田英超": "dtU8OVeJM0", "杜文韬": "dtU8OVeJM0",
    "黄彬": "dtU8OVeJM0", "李东方": "dtU8OVeJM0",
    # 华北东北战区
    "田疆": "6pW9KJf5fw", "王弸彪": "6pW9KJf5fw", "黄诗琦": "6pW9KJf5fw", "刘胜": "6pW9KJf5fw",
    "张镇朝": "6pW9KJf5fw", "王鑫裕": "6pW9KJf5fw", "李京京": "6pW9KJf5fw", "刘超": "6pW9KJf5fw",
    "王均广": "6pW9KJf5fw", "黄建朋": "6pW9KJf5fw",
    # 华南战区
    "唐政": "dUlPuelRVS", "李真真": "dUlPuelRVS", "万姚江": "dUlPuelRVS", "叶利钢": "dUlPuelRVS",
    "闫文军": "dUlPuelRVS", "彭明豪": "dUlPuelRVS", "兰廷灶": "dUlPuelRVS", "杜冠峥": "dUlPuelRVS",
    "郑思成": "dUlPuelRVS", "郑义全": "dUlPuelRVS", "黄泽孟": "dUlPuelRVS", "陈文超": "dUlPuelRVS",
    "邓智峰": "dUlPuelRVS", "吴冬兵": "dUlPuelRVS", "邓万杰": "dUlPuelRVS", "梁圣麟": "dUlPuelRVS",
    "雷昊": "dUlPuelRVS",
    # 西南西北战区
    "饶君睿": "zWWhy9BF3r", "罗果": "zWWhy9BF3r", "黄科": "zWWhy9BF3r", "张强": "zWWhy9BF3r",
    "欧阳凯": "zWWhy9BF3r", "柯李木": "zWWhy9BF3r", "李升明": "zWWhy9BF3r", "刘樊武": "zWWhy9BF3r",
    "栗永顺": "zWWhy9BF3r", "张明江": "zWWhy9BF3r",
    # 华中战区
    "万里秦": "RH96m2aE0m", "李宁愿": "RH96m2aE0m", "杨伦": "RH96m2aE0m", "刘腾": "RH96m2aE0m",
    "朱锟": "RH96m2aE0m",
    # 金融头部战队
    "贾腾辉": "rwPSYDRLPU", "黄宏昊": "rwPSYDRLPU", "张智国": "rwPSYDRLPU", "孟祥宇": "rwPSYDRLPU",
    "侯伟": "rwPSYDRLPU", "罗娇": "rwPSYDRLPU", "仇鑫杰": "rwPSYDRLPU", "刘春洋": "rwPSYDRLPU",
    "张嘉欣": "rwPSYDRLPU", "祝方正": "rwPSYDRLPU", "邢凯迪": "rwPSYDRLPU", "郭文祥": "rwPSYDRLPU",
    # 战略伙伴战队
    "王泳": "hsjOHtG0hH", "孙昊": "hsjOHtG0hH", "任晨赫": "hsjOHtG0hH", "余佳霖": "hsjOHtG0hH",
    "张恒峰": "hsjOHtG0hH", "杨帅": "hsjOHtG0hH", "张国东": "hsjOHtG0hH", "杨祖安": "hsjOHtG0hH",
    "孟凡策": "hsjOHtG0hH", "龚榆宸": "hsjOHtG0hH", "贾诗晨": "hsjOHtG0hH",
    # 通信头部战队
    "王刚": "x7Mpm5uGbE", "田志强": "x7Mpm5uGbE", "田宇辰": "x7Mpm5uGbE", "王帅": "x7Mpm5uGbE",
    "王欣": "x7Mpm5uGbE", "张家祥": "x7Mpm5uGbE",
    # 政企行业
    "沈修阳": "bpcFUBUMKP", "郑祖江": "bpcFUBUMKP", "谢小倩": "bpcFUBUMKP", "王德鑫": "bpcFUBUMKP",
    "喻洁": "bpcFUBUMKP", "周长良": "bpcFUBUMKP", "庞临风": "bpcFUBUMKP",
}

PTS_DELIVERY_TYPE_LABEL: dict[str, str] = {
    "chaitin": "原厂交付",
    "partner": "生态交付",
    "self": "自交付",
}

# Region ID → region name (reverse lookup)
_REGION_ID_TO_NAME = {v: k for k, v in REGION_OPTION_MAP.items()}

# User ID cache
_user_id_cache: dict[str, str] = {}


def compute_region(assigner_name: str | None) -> str | None:
    if not assigner_name:
        return None
    region_id = ASSIGNER_REGION_MAP.get(assigner_name)
    if region_id:
        return _REGION_ID_TO_NAME.get(region_id)
    return None


def compute_delivery_type(
    delivery_items: list, product_details: list, partner_delivery_type: str | None,
) -> str | None:
    from services.audit.schemas import ProductType
    if product_details and all(p.type == ProductType.saas for p in product_details):
        return "不回访"
    if delivery_items and any("续保" in (item.product_category or "").split("-")[-1] for item in delivery_items):
        return "无交付"
    if partner_delivery_type:
        return PTS_DELIVERY_TYPE_LABEL.get(partner_delivery_type)
    return None


def compute_project_type(delivery_items: list) -> str | None:
    if not delivery_items:
        return None
    has_renewal = any("续保" in (item.product_category or "").split("-")[-1] for item in delivery_items)
    has_rental = any("租用" in (item.product_category or "") or "订阅" in (item.product_category or "") for item in delivery_items)
    if has_renewal and not has_rental:
        all_renewal = all("续保" in (item.product_category or "").split("-")[-1] for item in delivery_items)
        return "续保" if all_renewal else "新购➕续保"
    if has_renewal and has_rental:
        return "新购➕租用"
    if not has_renewal and has_rental:
        return "租用"
    return "新购"


async def _resolve_dingtalk_user_id(name: str, dws_cli_path: str = "dws") -> str | None:
    if not name:
        return None
    if name in _user_id_cache:
        return _user_id_cache[name]
    try:
        proc = await asyncio.create_subprocess_exec(
            dws_cli_path, "contact", "user", "search", "--query", name, "-f", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        resp = json.loads(stdout)
        if resp.get("success") and resp.get("result"):
            user_id = resp["result"][0].get("userId")
            if user_id:
                _user_id_cache[name] = user_id
                return user_id
    except Exception:
        pass
    return None


async def _dws_create_record(
    cells: dict[str, Any],
    base_id: str,
    table_id: str,
    dws_cli_path: str = "dws",
) -> dict:
    records = json.dumps([{"cells": cells}])
    proc = await asyncio.create_subprocess_exec(
        dws_cli_path, "aitable", "record", "create",
        "--base-id", base_id, "--table-id", table_id,
        "--records", records, "-y", "-f", "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    return json.loads(stdout)


async def _dws_update_record(
    record_id: str,
    cells: dict[str, Any],
    base_id: str,
    table_id: str,
    dws_cli_path: str = "dws",
) -> dict:
    records = json.dumps([{"recordId": record_id, "cells": cells}])
    proc = await asyncio.create_subprocess_exec(
        dws_cli_path, "aitable", "record", "update",
        "--base-id", base_id, "--table-id", table_id,
        "--records", records, "-y", "-f", "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    return json.loads(stdout)


async def write_audit_to_dingtalk(
    result: AuditResult,
    *,
    base_id: str = "",
    table_id: str = "",
    corp_id: str = "",
    dws_cli_path: str = "dws",
) -> dict[str, Any]:
    pts_url = f"https://pts.chaitin.net/project/{result.project_id}#base"
    pass_label = "通过" if result.conclusion == "通过" else "拒绝"

    # Build notes
    notes: list[str] = []
    for r in result.rules:
        if r.result in ("不通过", "无法判定") and r.rule_id != 9:
            notes.append(r.message)
    if result.value_added_service_reminder:
        notes.append(result.value_added_service_reminder)

    cells: dict[str, Any] = {
        "审核是否通过": pass_label,
        "审核日期": _now_iso_cn(),
        "PTS交付链接": {"link": pts_url, "text": pts_url},
        "客户名称": result.customer_name or "",
    }
    if notes:
        cells["校对备注"] = _sanitize("\n".join(notes))
    if result.service_content:
        cells["doox5joqqw0mae62xhmtq"] = _sanitize(result.service_content)
    if result.after_sales_service_period_summary:
        cells["rgcjj2wcarim8lg9apphh"] = _sanitize(result.after_sales_service_period_summary)
    if result.region:
        region_id = REGION_OPTION_MAP.get(result.region)
        if region_id:
            cells["o7dk5r68igm7syh8funhl"] = {"id": region_id}
    if result.delivery_type:
        type_id = DELIVERY_TYPE_OPTION_MAP.get(result.delivery_type)
        if type_id:
            cells["vth94xg28fxpt6ribmpjd"] = [{"id": type_id}]
    if result.project_type:
        type_id = PROJECT_TYPE_OPTION_MAP.get(result.project_type)
        if type_id:
            cells["oz9yut6kbwr4gagps0us7"] = {"id": type_id}

    try:
        resp = await _dws_create_record(cells, base_id, table_id, dws_cli_path)
        if resp.get("status") != "success":
            logger.warning("Dingtalk create failed: %s", resp)
            return {"success": False}

        record_id = (resp.get("data") or {}).get("newRecordIds", [None])[0]

        # Update user fields separately
        if record_id and (result.assigner_name or result.person_in_charge_name):
            user_cells: dict[str, Any] = {}
            if result.assigner_name:
                uid = await _resolve_dingtalk_user_id(result.assigner_name, dws_cli_path)
                if uid:
                    user_cells["19r1LyK"] = [{"corpId": corp_id, "userId": uid}]
            if result.person_in_charge_name:
                uid = await _resolve_dingtalk_user_id(result.person_in_charge_name, dws_cli_path)
                if uid:
                    user_cells["qWDHbYc"] = [{"corpId": corp_id, "userId": uid}]
            if user_cells:
                try:
                    await _dws_update_record(record_id, user_cells, base_id, table_id, dws_cli_path)
                except Exception:
                    logger.warning("Dingtalk user field update failed (record created, user fields empty)")

        return {"success": True, "recordId": record_id}
    except Exception as e:
        logger.error("Dingtalk write failed: %s", e)
        return {"success": False}


def _now_iso_cn() -> str:
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.isoformat()
