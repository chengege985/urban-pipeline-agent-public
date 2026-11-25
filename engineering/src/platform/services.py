"""REST、旧工作流与新Agent共用的确定性业务入口。

本层仅调用已有模块，不复制权重、阈值或公式；持久化由各调用方负责。
图分析与方案比较的模型边界不因包装为Tool而改变。
"""
from __future__ import annotations

from .schemas import DecisionRequest, RecomputeRequest, ScenarioRequest


def query_assessment(snapshot: dict, pipe_id: str) -> dict:
    """只读取快照中的评分，不能将结果表冒充完整原始输入并再次评分。"""
    row = next((r for r in snapshot["payload"]["assessments"] if r["pipe_id"] == pipe_id), None)
    if row is None:
        return {"status": "insufficient_data", "missing_items": ["该快照没有此管段评分"]}
    return {"status": "succeeded", "assessment": row, "evidence_refs": row["evidence_refs"],
            "limitations": ["仅为简化Rs/Hs评分，未提供失效概率、RUL或正式P/C/R/H。",
                            "有效权重占比不是预测准确率；原程序兼容分支的限制仍适用。"]}


def calculate_assessment(request: RecomputeRequest):
    """严格输入检查后调用0.1.1，返回内存快照，不落盘、不覆盖历史结果。"""
    from src.adapters.scoring import recompute
    return recompute(request)


def simulate_snapshot(snapshot: dict, request: ScenarioRequest) -> dict:
    """仅在快照副本上计算通路影响，不能控制设施。"""
    from src.simulation.topology import simulate
    return simulate(snapshot, request)


def compare_snapshot(snapshot: dict, request: DecisionRequest, provider=None) -> dict:
    """通过同一provider比较给定方案，保留demo_policy/正式能力的区别。"""
    if provider is None:
        from src.adapters.providers import DemoDecisionProvider
        provider = DemoDecisionProvider()
    return provider.compare(snapshot, request)
