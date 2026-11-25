"""可替换的专业能力边界。

正式评分、专业预测、内部CUV与治理平台未交付时，显式返回不可用。
接入时实现对应协议并先验证业务契约，不通过空结果或随机数据冒充专业模型。
"""
from typing import Protocol

from src.decision.plans import compare_plans
from src.platform.schemas import DecisionRequest


class DecisionProvider(Protocol):
    name: str

    def compare(self, snapshot: dict, request: DecisionRequest) -> dict: ...


class DemoDecisionProvider:
    name = "demo_policy_given_candidates"

    def compare(self, snapshot, request):
        return compare_plans(snapshot, request)


class ProfessionalProvider(Protocol):
    """上游结果可由文件或服务提供；请求与结果需携带时点、版本和证据。"""
    name: str

    def request(self, payload: dict) -> dict: ...


class UnavailableProvider:
    def __init__(self, name: str):
        self.name = name

    def request(self, payload: dict) -> dict:
        return {"status": "insufficient_data", "capability": self.name,
                "missing_items": ["未取得该能力的正式交付、接口和适用验证说明"],
                "result": None, "dispatched": False}
