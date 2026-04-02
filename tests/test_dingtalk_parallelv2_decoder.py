import json
from pathlib import Path

from services.collectors.dingtalk_parallelv2_decoder import (
    decode_parallelv2_base64,
    parse_document_data_structure,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "dingtalk" / "visit"


def _load_document_payload() -> dict:
    return json.loads((FIXTURE_ROOT / "document_data.json").read_text())


def _load_parallelv2_base64() -> str:
    return (FIXTURE_ROOT / "parallelv2_base64.txt").read_text().strip()


def test_document_data_structure_parse_extracts_view_columns_and_field_mapping() -> None:
    structure = parse_document_data_structure(_load_document_payload(), sheet_id="Igz9TVd", view_id="AKOehLK")

    assert structure.sheet_id == "Igz9TVd"
    assert structure.view_id == "AKOehLK"
    assert structure.raw_columns == [
        "客户名称",
        "PTS链接",
        "交付单号",
        "回访人",
        "回访状态",
        "回访链接",
        "回访类型",
        "回访联系人",
        "满意度",
        "反馈备注",
    ]
    assert structure.field_name_by_id["rbiax8fi5eklvmdlc4v5d"] == "客户名称"
    assert structure.field_enum_label_by_id["vd8h8nk8m4tr42nmroa7q"]["khlT6gz2Ab"] == "已回访"


def test_parallelv2_base64_decode_returns_structured_rows() -> None:
    structure = parse_document_data_structure(_load_document_payload(), sheet_id="Igz9TVd", view_id="AKOehLK")

    decoded = decode_parallelv2_base64(_load_parallelv2_base64(), structure=structure)

    assert decoded["raw_record_count"] == 2
    assert decoded["field_keys"] == [
        "PTS链接",
        "交付单号",
        "反馈备注",
        "回访人",
        "回访状态",
        "回访类型",
        "回访联系人",
        "回访链接",
        "客户名称",
        "满意度",
    ]
    assert decoded["rows"][0]["row_id"] == "visit-row-001"
    assert decoded["rows"][0]["客户名称"] == "上海测试客户"
    assert decoded["rows"][0]["PTS链接"] == "https://pts.example.com/visit-row-001"
    assert decoded["rows"][0]["回访状态"] == "已回访"
    assert decoded["rows"][1]["回访状态"] == "跟进中"
    assert decoded["rows"][1]["满意度"] == "一般"
    assert decoded["diagnostics"]["decoded_row_count"] == 2
    assert decoded["diagnostics"]["unknown_value_types"] == []


def test_parallelv2_decode_aligns_rows_with_document_field_names() -> None:
    structure = parse_document_data_structure(_load_document_payload(), sheet_id="Igz9TVd", view_id="AKOehLK")
    decoded = decode_parallelv2_base64(_load_parallelv2_base64(), structure=structure)

    first_row = decoded["rows"][0]
    assert set(first_row.keys()) == {
        "row_id",
        "客户名称",
        "PTS链接",
        "交付单号",
        "回访人",
        "回访状态",
        "回访链接",
        "回访类型",
        "回访联系人",
        "满意度",
        "反馈备注",
    }
