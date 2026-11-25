"""R01: numeric claims must bind entity, unit and algorithm version."""
from __future__ import annotations

import hashlib

import pytest

from src.agents.evidence import (ExecutionLedger, answer_verification_status, deterministic_answer,
                                 fact_cards, validate_answer)
from src.agents.tools.domain import DomainTools, ToolAccess
from src.platform.input_batches import BatchPrincipal, InputBatchRepository

EVID = "tool:" + "a" * 64
LEDGER_SNAPSHOT = "snap"
LEDGER_PRINCIPAL = "tester"
LEDGER_RUN = "r1"


def _ledger_snapshot():
    payload = {
        "schema_version": "0.1.0", "name": "binding", "data_mode": "synthetic",
        "source_status": "constructed", "as_of": "2026-11-03T08:00:00+08:00",
        "source_refs": ["synthetic:binding"],
        "assessments": [{
            "pipe_id": "syn-a", "as_of": "2026-11-03T08:00:00+08:00",
            "model_family": "legacy_demo", "model_version": "0.1.1",
            "legacy_result": {"Rs": 2, "Hs": 80, "completeness_percent": 100.0, "grade": "基本健康"},
            "evidence_refs": ["controlled_recompute:pipe_health_0.1.1"],
        }],
    }
    return {"snapshot_id": LEDGER_SNAPSHOT, "payload": payload, "as_of": payload["as_of"],
            "data_mode": "synthetic", "source_status": "constructed", "sha256": "fixture"}


def real_execution(tmp_path, *, run_id=LEDGER_RUN):
    """真实执行一次 query_assessment，返回 (事实卡片, 账本)。"""
    access = ToolAccess(principal=BatchPrincipal(actor_id=LEDGER_PRINCIPAL, scope="internal"),
                        snapshot_id=LEDGER_SNAPSHOT, allowed_tools=frozenset({"query_assessment"}))
    ledger = ExecutionLedger()
    tools = DomainTools(access, lambda _: _ledger_snapshot(), InputBatchRepository(tmp_path),
                        ledger=ledger, run_id=run_id)
    output = tools.execute("query_assessment", {"pipe_id": "syn-a"})
    return fact_cards([output], []), ledger, output


def facts():
    return [{
        "tool_name": "query_assessment", "tool_version": "domain-tools-1", "status": "succeeded",
        "evidence_id": EVID,
        "result": {"status": "succeeded", "assessment": {
            "pipe_id": "A", "model_version": "0.1.1",
            "legacy_result": {"Hs": 80, "Rs": 2, "grade": "基本健康"},
        }},
    }]


def citations():
    return [{"evidence_id": "doc:D1:P1", "sensitivity": "public", "document_version": "v1",
             "content_hash": "abc123", "content": "资料正文", "title": "资料", "location": "P1"}]


def test_numeric_claim_must_match_entity_value_unit_and_version():
    assert validate_answer(f"管段A健康分80分，模型0.1.1。[{EVID}]", facts(), [])[0]
    assert not validate_answer(f"管段A健康分99分。[{EVID}]", facts(), [])[0]
    assert not validate_answer(f"管段B健康分80分。[{EVID}]", facts(), [])[0]
    assert not validate_answer(f"管段A健康分80 MPa。[{EVID}]", facts(), [])[0]
    assert not validate_answer(f"管段A健康分80分，模型0.2.0。[{EVID}]", facts(), [])[0]


def test_unsupported_probability_and_rul_cannot_be_answered():
    assert not validate_answer("失效概率为80%。[doc:D1:P1]", [], citations())[0]
    assert not validate_answer("剩余寿命约10年。[doc:D1:P1]", [], citations())[0]
    assert not validate_answer("健康度H为80。[doc:D1:P1]", [], citations())[0]
    assert validate_answer("本系统不提供失效概率，也不能预测剩余寿命。[doc:D1:P1]", [], citations())[0]


def test_renderer_states_algorithm_version_and_units():
    text = deterministic_answer("评分", facts(), [], [])
    assert "Hs=80分" in text and "Rs=2（无量纲）" in text
    assert "legacy_demo/0.1.1" in text


def rag_citations():
    content = "文档 v1：限值为八十。"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return [{"evidence_id": "doc:D1:P1", "citation_id": f"doc:D1:v1:P1:{digest[:12]}",
             "sensitivity": "public", "document_version": "v1",
             "content_hash": digest, "content": content,
             "sent_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
             "integrity_status": "verified", "verification_status": "verified",
             "excerpts": [{"text": content, "start": 0, "end": len(content),
                           "hash": digest}],
             "title": "限值", "location": "P1"}]


@pytest.mark.parametrize("answer", [
    "依据 v2，限值为九十九。[doc:D1:P1]",
    "漏损概率为八成。[doc:D1:P1]",
    "还能使用十年。[doc:D1:P1]",
    "实际处于九十到一百分区间。[doc:D1:P1]",
    "并不是80分。[doc:D1:P1]",
    "限值为九十九。[doc:D1:P1]",
])
def test_rag_numeric_and_version_claims_require_structured_facts(answer):
    ok, problems = validate_answer(answer, [], rag_citations())
    assert not ok and problems


def test_rag_plain_explanation_without_numbers_is_allowed():
    ok, problems = validate_answer("依据文档，管龄规则只适用于已登记管段。[doc:D1:P1]", [], rag_citations())
    assert ok, problems
    citation_id = rag_citations()[0]["citation_id"]
    ok, problems = validate_answer(f"依据同一版本资料。[{citation_id}]", [], rag_citations())
    assert ok, problems


def test_rag_contradictory_prose_is_marked_unverified():
    assert answer_verification_status(
        "限值高于设计寿命。[doc:D1:P1]", [], rag_citations()) == "unverified_prose"
    assert answer_verification_status(
        "文档 v1：限值为八十。[doc:D1:P1]", [], rag_citations()) == "verified_excerpt"


def test_negated_source_never_verifies_positive_claim():
    content = "并非服务已经恢复。"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    citation = {"evidence_id": "doc:D1:P1", "citation_id": f"doc:D1:v1:P1:{digest[:12]}",
                "sensitivity": "public", "document_version": "v1",
                "content_hash": digest, "integrity_status": "verified", "content": content,
                "sent_hash": digest, "sent_length": len(content),
                "excerpts": [{"text": content, "start": 0, "end": len(content), "hash": digest}],
                "title": "状态", "location": "P1"}
    assert answer_verification_status("服务已经恢复。[doc:D1:P1]", [], [citation]) == "unverified_prose"
    assert answer_verification_status(content + "[doc:D1:P1]", [], [citation]) == "verified_excerpt"


def test_invalid_content_hash_cannot_be_verified():
    citation = {"evidence_id": "doc:D1:P1", "sensitivity": "public", "document_version": "v1",
                "content_hash": "0" * 64, "integrity_status": "invalid_content_hash",
                "content": "服务已经恢复。", "excerpts": [], "title": "状态", "location": "P1"}
    assert answer_verification_status("服务已经恢复。", [], [citation]) == "invalid_evidence"
    ok, problems = validate_answer("依据资料。[doc:D1:P1]", [], [citation])
    assert not ok and any("哈希" in item for item in problems)


def test_verified_fact_rejects_unknown_version_and_forged_status(tmp_path):
    from src.agents.evidence import VerifiedFact, verified_facts

    with pytest.raises(ValueError):
        VerifiedFact("fact:tool:a:unknown:A:Hs", "A", "Hs", 80.0, "分", "unknown",
                     "tool:a", "tool", "verified")
    with pytest.raises(ValueError):
        VerifiedFact("forged", "A", "Hs", 80.0, "分", "0.1.1",
                     "tool:a", "tool", "verified")

    cards, ledger, output = real_execution(tmp_path)
    parsed = verified_facts(cards, run_id=LEDGER_RUN, snapshot_id=LEDGER_SNAPSHOT,
                            principal_id=LEDGER_PRINCIPAL, ledger=ledger)
    assert parsed and parsed[0].fact_id.startswith(f"fact:{output['evidence_id']}:0.1.1:syn-a:Hs")
    assert parsed[0].verification_status == "verified"
    # 没有当前 run 的执行账本时，即使结构完整不得产生 verified 事实。
    assert verified_facts(facts()) == []
    assert verified_facts(facts(), run_id=LEDGER_RUN, snapshot_id=LEDGER_SNAPSHOT,
                          principal_id=LEDGER_PRINCIPAL) == []


def test_excerpt_verification_is_bound_to_the_cited_document():
    def citation(doc_id, content):
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return {"evidence_id": f"doc:{doc_id}:P1", "citation_id": f"doc:{doc_id}:v1:P1:{digest[:12]}",
                "sensitivity": "public", "document_version": "v1", "content_hash": digest,
                "integrity_status": "verified", "content": content,
                "sent_hash": digest, "sent_length": len(content),
                "excerpts": [{"text": content, "start": 0, "end": len(content), "hash": digest}],
                "title": doc_id, "location": "P1"}
    d1 = citation("D1", "服务尚未恢复。")
    d2 = citation("D2", "服务已经恢复。")
    assert answer_verification_status(
        "服务已经恢复。[doc:D1:P1]", [], [d1, d2]) == "unverified_prose"
    assert answer_verification_status(
        "服务已经恢复。[doc:D2:P1]", [], [d1, d2]) == "verified_excerpt"
    assert answer_verification_status(
        "服务已经恢复。[doc:D1:P1][doc:D2:P1]", [], [d1, d2]) == "unverified_prose"


def test_verified_fact_requires_trusted_tool_source_and_status(tmp_path):
    from src.agents.evidence import VerifiedFact, verified_facts

    cards, ledger, _ = real_execution(tmp_path)
    assert verified_facts(cards, run_id=LEDGER_RUN, snapshot_id=LEDGER_SNAPSHOT,
                          principal_id=LEDGER_PRINCIPAL, ledger=ledger)
    failed = [{**card, "result": {**card["result"], "status": "failed"}} for card in cards]
    assert verified_facts(failed, run_id=LEDGER_RUN, snapshot_id=LEDGER_SNAPSHOT,
                          principal_id=LEDGER_PRINCIPAL, ledger=ledger) == []
    arbitrary = [{**card, "tool_name": "unknown_tool"} for card in cards]
    assert verified_facts(arbitrary, run_id=LEDGER_RUN, snapshot_id=LEDGER_SNAPSHOT,
                          principal_id=LEDGER_PRINCIPAL, ledger=ledger) == []
    with pytest.raises(ValueError):
        VerifiedFact("fact:tool:a:0.1.1:A:Hs", "A", "Hs", 80.0, "MPa", "0.1.1",
                     "tool:a", "tool", "verified", "query_assessment",
                     _trusted_token=__import__("src.agents.evidence", fromlist=["_VERIFIED_FACT_TOKEN"])._VERIFIED_FACT_TOKEN)


def test_short_or_forged_tool_evidence_id_is_not_verified(tmp_path):
    from src.agents.evidence import verified_facts

    cards, ledger, _ = real_execution(tmp_path)
    forged = [{**card, "evidence_id": "tool:fake"} for card in cards]
    assert verified_facts(forged, run_id=LEDGER_RUN, snapshot_id=LEDGER_SNAPSHOT,
                          principal_id=LEDGER_PRINCIPAL, ledger=ledger) == []


def test_excerpt_offsets_and_hash_cannot_be_forged():
    content = "服务已经恢复。"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    base = {"evidence_id": "doc:D1:P1", "citation_id": "doc:D1:v1:P1:abc",
            "sensitivity": "public", "document_version": "v1", "content_hash": digest,
            "integrity_status": "verified", "content": content, "title": "D1", "location": "P1"}
    for excerpt in ({"text": content, "start": 1, "end": len(content), "hash": digest},
                    {"text": content, "start": 0, "end": len(content), "hash": "0" * 64}):
        citation = {**base, "excerpts": [excerpt]}
        # 引用身份（citation_id/sent_hash）本身不成立时为 invalid_evidence，
        # 两者都不允许得到 verified_excerpt。
        assert answer_verification_status(
            content + "[doc:D1:P1]", [], [citation]) != "verified_excerpt"
