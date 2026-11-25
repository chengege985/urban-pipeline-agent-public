"""与参考结果按管线编号逐格比对——改动任何规则前后都应跑一遍，确认差异在预期之内。

参考文件默认为 M07 程序包自带的 输出/V1.0健康评价结果.xlsx（提供方运行结果）。
只比对内存结果与参考"健康评价总表"；不据此判定落盘 Excel 三张表及 TXT 已验证。

比对规则（沿用 src/baseline/run_provided_model.py 的口径）
- 用 openpyxl 直接读参考表，避免 pandas 把字面字符串 "nan" 当成缺失值。
- 数值：绝对差 ≤ tolerance（默认 1e-9）视为相同；其余类型要求类型与值都相等。
- "数据问题"和"风险点ID列表"列：空单元格与空字符串视为相同；字面 "nan" 保持原义。
- 双方管线编号必须唯一；任一方重复都不能通过，避免字典覆盖记录造成假通过。
- 不比较行序（原程序 sort_values 不是稳定排序，同分行序可能变化），按管线编号对齐。
"""
from __future__ import annotations

import math
from numbers import Real
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from .config import SHEET_MAIN

KEY = "管线编号"


def _cell(value):
    """把 DataFrame 单元格归一成可比对的 Python 值：NaN→None，numpy 标量→Python 标量。"""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _same(actual, expected, field: str, tolerance: float) -> tuple[bool, float | None]:
    """单格比对，返回 (是否相同, 数值差或 None)。"""
    if field in {"数据问题", "风险点ID列表"}:
        actual = "" if actual is None else actual
        expected = "" if expected is None else expected
    if actual is None or expected is None:
        return actual is None and expected is None, None
    if isinstance(actual, Real) and isinstance(expected, Real):
        if not math.isfinite(float(actual)) or not math.isfinite(float(expected)):
            return actual == expected, None
        delta = abs(float(actual) - float(expected))
        return delta <= tolerance, delta
    return type(actual) is type(expected) and actual == expected, None


def compare_with_reference(
    result: pd.DataFrame,
    reference_xlsx: Path,
    tolerance: float = 1e-9,
    max_examples: int = 50,
) -> dict:
    """把 evaluate() 的结果与参考工作簿的"健康评价总表"逐格比对。

    返回字典含：passed、行数、列集合是否一致、编号集合是否一致、双方编号唯一性、
    不一致格数（总数与按列分布）、最大数值差、前若干个差异样例。
    重复编号时格数仅作辅助诊断，必须结合 unique_keys；不能因 cell_mismatches=0 就认为通过。
    """
    wb = load_workbook(reference_xlsx, read_only=True, data_only=True)
    try:
        rows = list(wb[SHEET_MAIN].iter_rows(values_only=True))
    finally:
        wb.close()
    header = list(rows[0])
    reference = [dict(zip(header, r)) for r in rows[1:] if any(v is not None for v in r)]

    actual = {}
    for record in result.to_dict("records"):
        actual[_cell(record[KEY])] = {k: _cell(v) for k, v in record.items()}

    reference_ids = [r[KEY] for r in reference]
    # 字典会合并重复编号，必须用合并前的行数判断唯一性；相同 ID 集合并不充分。
    actual_unique = len(actual) == len(result)
    reference_unique = len(set(reference_ids)) == len(reference_ids)
    unique_ok = actual_unique and reference_unique
    schema_ok = set(header) == set(result.columns) and len(header) == len(result.columns)
    ids_ok = set(reference_ids) == set(actual)

    mismatches = 0
    by_field: dict[str, int] = {}
    max_delta = 0.0
    examples: list[dict] = []
    for ref in reference:
        act = actual.get(ref[KEY])
        if act is None:
            continue
        for fld in header:
            ok, delta = _same(act.get(fld), ref[fld], fld, tolerance)
            if delta is not None:
                max_delta = max(max_delta, delta)
            if not ok:
                mismatches += 1
                by_field[fld] = by_field.get(fld, 0) + 1
                if len(examples) < max_examples:
                    examples.append({"id": ref[KEY], "field": fld,
                                     "expected": ref[fld], "actual": act.get(fld)})

    passed = unique_ok and schema_ok and ids_ok and len(reference) == len(result) and mismatches == 0
    return {
        "passed": passed,
        "reference": str(reference_xlsx),
        "tolerance": tolerance,
        "actual_rows": len(result),
        "reference_rows": len(reference),
        "schema_matches": schema_ok,
        "id_sets_match": ids_ok,
        "unique_keys": unique_ok,
        "actual_unique_keys": actual_unique,
        "reference_unique_keys": reference_unique,
        "cell_mismatches": mismatches,
        "mismatches_by_field": by_field,
        "max_numeric_difference": max_delta,
        "examples": examples,
    }
