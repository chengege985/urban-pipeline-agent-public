"""评分适配器：复用原0.1.1，保留legacy_result而不冒充正式P/C/R/H。

导入路径固定到已验收的0.1.1结果。受控重算不接受外部文件路径，先检查
输入唯一性、时间窗、缺项与数值，再调用evaluate；不修改原程序的兼容分支。
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from src.pipe_health.config import WEIGHTS, PACKAGE_VERSION
from src.platform.errors import InputIssues
from src.platform.schemas import Assessment, LegacyResult, RecomputeRequest, SnapshotPayload

ROOT = Path(__file__).resolve().parents[2]
BASELINE_RUN = "pipe_health_20261028_150251_2c9bbd"
META = ROOT / f"experiments/pipe_health/{BASELINE_RUN}/metadata.json"
RESULT = ROOT / f"outputs/pipe_health/{BASELINE_RUN}/V3.0健康评价结果.xlsx"


def clean(value):
    """JSON中的缺失为null；保留字面nan文本，避免伪造原件内容。"""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def assessment_rows(result: pd.DataFrame, as_of, source_ref: str):
    properties = ["材质", "管径", "投运年份", "风险点数量", "风险点ID列表"]
    monitoring = ["动态压力均值", "动态压力CV", "余氯均值", "余氯最小值", "浊度均值", "浊度最大值", "瞬时流量均值", "瞬时流量CV"]
    items = []
    for row in result.to_dict("records"):
        pid = row["管线编号"]
        if not isinstance(pid, str) or not pid.strip():
            raise InputIssues([{"field": "管线编号", "reason": "ID必须为非空字符串"}])
        issues = row.get("数据问题") or ""
        items.append(Assessment(
            pipe_id=pid, as_of=as_of, model_version=PACKAGE_VERSION,
            legacy_result=LegacyResult(Rs=clean(row.get("综合风险指数Rs")), Hs=clean(row.get("健康度Hs")),
                                       completeness_percent=clean(row.get("数据可信度(%)")), grade=row["健康等级"]),
            indicator_scores={k: clean(row[k]) for k in WEIGHTS if clean(row.get(k)) is not None},
            properties={k: clean(row.get(k)) for k in properties}, monitoring={k: clean(row.get(k)) for k in monitoring},
            main_risk=str(row.get("主要风险因子", "")), data_issues=str(issues).split(";") if issues else [],
            evidence_refs=[source_ref, f"{source_ref}#pipe={pid}"],
        ))
    return items


def provided_sample() -> SnapshotPayload:
    """读取固定的已验证样例；不执行原程序，不修改原结果。"""
    import pandas as pd

    meta = json.loads(META.read_text(encoding="utf-8"))
    relative = RESULT.relative_to(ROOT).as_posix()
    expected = next(x["sha256"] for x in meta["outputs"] if x["path"] == relative)
    if hashlib.sha256(RESULT.read_bytes()).hexdigest() != expected:
        raise InputIssues([{"field": relative, "reason": "结果文件与历史哈希不一致"}])
    frame = pd.read_excel(RESULT, sheet_name="健康评价总表", keep_default_na=False, dtype={"管线编号": str})
    as_of = datetime.fromisoformat(meta["evaluation_clock"])
    return SnapshotPayload(
        name="提供方500条评分样例（来源真实性未确认）", data_mode="fixture_replay", source_status="source_unknown",
        as_of=as_of, source_refs=[relative, META.relative_to(ROOT).as_posix()],
        assessments=assessment_rows(frame, as_of, relative),
    )


def validate_recompute(request: RecomputeRequest):
    """严格检查输入，已知缺失误评分支在入口处阻断；不修补数据。"""
    import pandas as pd

    issues = []
    static_ids = []
    evaluation_year = request.as_of.astimezone(ZoneInfo("Asia/Shanghai")).year
    static_required = {"管线编号", "材质", "投运年份", "管径", "起点埋深", "终点埋深", "压力"}
    dynamic_required = {"管线编号", "时间", "动态压力", "余氯", "浊度", "瞬时流量", "风险点数量"}

    def issue(table, index, field, reason):
        issues.append({"table": table, "record_index": index, "field": field, "reason": reason})

    for table, rows, required in [("static", request.static_rows, static_required), ("dynamic", request.dynamic_rows, dynamic_required)]:
        for i, row in enumerate(rows):
            for field in sorted(required):
                v = row.get(field)
                if v is None or v == "":
                    issue(table, i, field, "必填字段缺失；未知不能按零或低风险计算")
            pid = row.get("管线编号")
            if not isinstance(pid, str) or not pid.strip():
                issue(table, i, "管线编号", "必须为字符串，保留前导零")
            if table == "static":
                static_ids.append(pid if isinstance(pid, str) else "")
                for field in ("材质", "压力"):
                    value = row.get(field)
                    if not isinstance(value, str) or not value.strip():
                        issue(table, i, field, "分类字段必须为非空文本，不能传入数值、数组或对象")
                    elif field == "压力":
                        # Reject numeric measurements in a categorical column.
                        try:
                            float(value.strip())
                        except ValueError:
                            pass
                        else:
                            issue(table, i, field, "压力字段为等级文本；数值压力须使用动态压力字段和明确单位")
                numeric = ["投运年份", "管径", "起点埋深", "终点埋深"]
            else:
                numeric = ["动态压力", "余氯", "浊度", "瞬时流量", "风险点数量"]
            for field in numeric:
                v = row.get(field)
                try:
                    x = float(v)
                    positive = field in {"管径", "起点埋深", "终点埋深", "动态压力"}
                    valid = not isinstance(v, bool) and math.isfinite(x) and (x > 0 if positive else x >= 0)
                    if field in {"投运年份", "风险点数量"}:
                        valid = valid and x.is_integer()
                    if field == "投运年份":
                        valid = valid and 1800 <= x <= evaluation_year
                    if not valid:
                        raise ValueError()
                except (TypeError, ValueError):
                    issue(table, i, field, "数值缺失、非有限、负数或超出允许范围")
    if len(static_ids) != len(set(static_ids)):
        issue("static", None, "管线编号", "重复ID禁止后行覆盖前行")
    if issues:
        raise InputIssues(issues[:100])
    dynamic = pd.DataFrame(request.dynamic_rows)
    try:
        # 每条记录必须自带时区，不让pandas默认为UTC。
        for i, text in enumerate(dynamic["时间"]):
            d = datetime.fromisoformat(str(text))
            if d.tzinfo is None or d.utcoffset() is None:
                raise ValueError(f"第{i}条时间缺时区")
        times = pd.to_datetime(dynamic["时间"], utc=True)
    except (TypeError, ValueError) as exc:
        raise InputIssues([{"field": "时间", "reason": str(exc)}]) from exc
    mask = (times >= request.window_start) & (times < request.window_end)
    if not mask.all():
        raise InputIssues([{"field": "时间", "reason": "输入含统计窗口外记录，请先明确筛选范围"}])
    if set(dynamic["管线编号"]) != set(static_ids):
        raise InputIssues([{"field": "管线编号", "reason": "静态与动态ID集合不一致"}])
    if (dynamic.groupby("管线编号").size() < 2).any():
        raise InputIssues([{"field": "动态记录", "reason": "每条管段至少两条有效记录，避免样本标准差不可计算"}])
    if (dynamic.groupby("管线编号")["风险点数量"].nunique() > 1).any():
        raise InputIssues([{"field": "风险点数量", "reason": "同一窗口内数量变化，聚合口径待确认"}])
    return pd.DataFrame(request.static_rows), dynamic


def recompute(request: RecomputeRequest) -> SnapshotPayload:
    from src.pipe_health.pipeline import evaluate

    static, dynamic = validate_recompute(request)
    result = evaluate(static, dynamic, request.as_of)
    origin = {"fixture_replay": "source_unknown", "synthetic": "constructed", "real": "declared_real"}
    ref = "controlled_recompute:pipe_health_" + PACKAGE_VERSION
    return SnapshotPayload(name=request.name, data_mode=request.data_mode, source_status=origin[request.data_mode],
                           as_of=request.as_of, source_refs=request.source_refs,
                           assessments=assessment_rows(result, request.as_of, ref))
