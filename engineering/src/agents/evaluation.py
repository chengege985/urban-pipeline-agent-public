"""Agent验收指标：路由任务集与RAG证据召回。仅提供度量，不把种子集冒充100条人工集。"""
from __future__ import annotations

import json
from pathlib import Path


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not value.get("question"):
            raise ValueError(f"第{line_no}行缺少question")
        value["_line"] = line_no
        cases.append(value)
    return cases


def route_metrics(cases: list[dict], route_fn) -> dict:
    """tasks按集合比较，intent按精确值；报告逐项失败，不只给一个平均分。"""
    total = correct_tasks = correct_intent = 0
    failures = []
    for case in cases:
        expected_tasks = set(case.get("expected_tasks", []))
        expected_intent = case.get("expected_intent")
        decision = route_fn(case["question"], allowed_tasks=case.get("allowed_tasks", ["materials", "assessment", "simulation", "decision"]),
                            pipe_ids=case.get("pipe_ids", []), data_mode=case.get("data_mode", "synthetic"))
        actual_tasks = set(decision.tasks)
        total += 1
        ok_tasks = actual_tasks == expected_tasks
        ok_intent = decision.intent == expected_intent
        correct_tasks += ok_tasks
        correct_intent += ok_intent
        if not (ok_tasks and ok_intent):
            failures.append({"line": case.get("_line"), "question": case["question"],
                             "expected_tasks": sorted(expected_tasks), "actual_tasks": sorted(actual_tasks),
                             "expected_intent": expected_intent, "actual_intent": decision.intent})
    return {"total": total, "task_exact": correct_tasks / total if total else 0.0,
            "intent_accuracy": correct_intent / total if total else 0.0, "failures": failures}


def rag_metrics(cases: list[dict], search_fn) -> dict:
    """Recall@5按查询计算证据ID前缀命中；无答案查询单独统计。"""
    total = recalls = no_answer_ok = 0
    failures = []
    for case in cases:
        query = case["question"]
        expected = case.get("expected_evidence_ids", [])
        hits, mode = search_fn(query, scope=case.get("scope", "internal"), limit=5)
        if hits and hasattr(hits[0], "evidence_id"):
            actual = [h.evidence_id for h in hits]
        else:
            actual = [f"doc:{h.document_id}:{h.location}" for h in hits]
        total += 1
        if expected:
            hit = any(any(a.startswith(prefix) for a in actual) for prefix in expected)
            recalls += hit
            if not hit:
                failures.append({"line": case.get("_line"), "query": query, "expected": expected,
                                 "actual": actual, "mode": mode})
        else:
            ok = not actual
            no_answer_ok += ok
            if not ok:
                failures.append({"line": case.get("_line"), "query": query, "expected": [],
                                 "actual": actual, "mode": mode})
    answered = sum(1 for c in cases if c.get("expected_evidence_ids"))
    return {"total": total, "recall_at_5": recalls / answered if answered else 0.0,
            "no_answer_cases": total - answered, "no_answer_correct": no_answer_ok, "failures": failures}
