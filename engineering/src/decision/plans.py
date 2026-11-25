"""Backward-compatible decision entry; public functions live in contracts.py."""
from src.decision.contracts import rank_candidate_plans
from src.platform.schemas import DecisionRequest


def compare_plans(snapshot: dict, request: DecisionRequest) -> dict:
    return rank_candidate_plans(snapshot, request)
