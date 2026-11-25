"""验收指标回归：种子集不是最终人工集，只验证度量逻辑。"""
from pathlib import Path

import httpx
import json

from src.agents.evaluation import load_cases, rag_metrics, route_metrics
from src.agents.outbound import RequestClassification
from src.agents.rag.retriever import OpenAIEmbedding
from src.agents.router import FakeRouter

ROOT = Path(__file__).resolve().parents[1]


def embedding_classification(purpose="document_embedding"):
    return RequestClassification(
        "tester", "internal", "synthetic", "not_applicable" if purpose == "document_embedding" else "synthetic",
        (), ("synthetic",), purpose, "embedding:qwen3-vl-test", "test", "http://127.0.0.1:30000/v1")


def test_routing_seed_reaches_expected_route_metrics():
    cases = load_cases(ROOT / "data/agent/eval/routing_seed.jsonl")
    report = route_metrics(cases, FakeRouter().route)
    assert report["total"] == 12
    assert report["task_exact"] == 1.0
    assert report["intent_accuracy"] == 1.0


def test_rag_metric_recall_and_no_answer():
    class Hit:
        def __init__(self, document_id, location):
            self.document_id, self.location = document_id, location

    def search(query, scope, limit):
        if "无答案" in query:
            return [], "bm25"
        return [Hit("KD01", "L1-3")], "hybrid"

    cases = [
        {"question": "供水评分", "expected_evidence_ids": ["doc:KD01:"]},
        {"question": "无答案问题", "expected_evidence_ids": []},
    ]
    report = rag_metrics(cases, search)
    assert report["recall_at_5"] == 1.0
    assert report["no_answer_correct"] == 1
    assert report["failures"] == []


def test_routing_100_draft_is_balanced_and_routable():
    cases = load_cases(ROOT / "data/agent/eval/routing_100.jsonl")
    assert len(cases) == 100
    report = route_metrics(cases, FakeRouter().route)
    assert report["task_exact"] == 1.0
    assert report["intent_accuracy"] == 1.0
    assert report["failures"] == []


def test_rag_60_draft_has_50_answerable_and_10_no_answer():
    cases = load_cases(ROOT / "data/agent/eval/rag_60.jsonl")
    assert len(cases) == 60
    assert sum(1 for case in cases if case["expected_evidence_ids"]) == 50
    assert sum(1 for case in cases if not case["expected_evidence_ids"]) == 10


def test_openai_embedding_mrl_truncates_and_renormalizes():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 2048}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    embedding = OpenAIEmbedding("http://127.0.0.1:30000/v1", "qwen3-vl-test", 1024, client=client)
    vectors = embedding.embed(["测试"], kind="query", classification=embedding_classification("query_embedding"))
    assert len(vectors[0]) == 1024
    assert abs(sum(value * value for value in vectors[0]) ** 0.5 - 1.0) < 1e-9
    assert json.loads(seen[0].content)["input"] == ["测试"]
    client.close()


def test_openai_embedding_retries_once():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 1023 + [1.0]}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    embedding = OpenAIEmbedding("http://127.0.0.1:30000/v1", "qwen3-vl-test", 1024, client=client)
    vectors = embedding.embed(["测试"], classification=embedding_classification())
    assert len(calls) == 2
    assert len(vectors[0]) == 1024
    client.close()
