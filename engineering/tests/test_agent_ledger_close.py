"""LEDGER-CLOSE-01：执行账本两项缺陷的固定回归。

LEDGER-01：未执行任何工具，直接调用账本签发入口即可生成 verified 事实。
LEDGER-02：篡改的字典审计副本被静默忽略，验证仍然成功。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.agents import evidence as evidence_module
from src.agents.evidence import ExecutionLedger, fact_cards, verified_facts
from src.agents.tools.domain import DomainTools, ToolAccess
from src.platform.input_batches import BatchPrincipal, InputBatchRepository

ExecutionReceipt = getattr(evidence_module, "ExecutionReceipt", None)
EVID = "tool:" + "f" * 64
RUN = "run-independent"
SNAPSHOT = "syn-ledger"
PRINCIPAL = "tester"


def forged_fact() -> dict:
    """与 Codex 探针一致的伪造成功字典：结构完整、SHA 合法，但没有执行记录。"""
    return {
        "tool_name": "query_assessment", "tool_version": "domain-tools-1",
        "status": "succeeded", "evidence_id": EVID,
        "result": {"status": "succeeded", "assessment": {
            "pipe_id": "syn-independent", "model_version": "0.1.1",
            "legacy_result": {"Hs": 80.0, "Rs": 2}}},
    }


def snapshot(hs: float = 92.5, rs: float = 1.3, pipe_id: str = "syn-ledger-001") -> dict:
    payload = {
        "schema_version": "0.1.0", "name": "ledger", "data_mode": "synthetic",
        "source_status": "constructed", "as_of": "2026-11-03T08:00:00+08:00",
        "source_refs": ["synthetic:ledger"],
        "assessments": [{
            "pipe_id": pipe_id, "as_of": "2026-11-03T08:00:00+08:00",
            "model_family": "legacy_demo", "model_version": "0.1.1",
            "legacy_result": {"Rs": rs, "Hs": hs, "completeness_percent": 100.0, "grade": "健康"},
            "indicator_scores": {"材质风险": 4.0},
            "evidence_refs": ["controlled_recompute:pipe_health_0.1.1"],
        }],
    }
    return {"snapshot_id": SNAPSHOT, "payload": payload, "as_of": payload["as_of"],
            "data_mode": "synthetic", "source_status": "constructed", "sha256": "fixture"}


def execute_once(tmp_path, *, run_id: str = "run-1", snapshot_id: str = SNAPSHOT,
                 pipe_id: str = "syn-ledger-001", hs: float = 92.5, rs: float = 1.3):
    """受控执行一次 query_assessment，返回 (工具输出, 账本)。"""
    access = ToolAccess(principal=BatchPrincipal(actor_id="tester", scope="internal"),
                        snapshot_id=snapshot_id, allowed_tools=frozenset({"query_assessment"}))
    ledger = ExecutionLedger()
    try:
        tools = DomainTools(access, lambda _: snapshot(hs, rs, pipe_id),
                            InputBatchRepository(tmp_path), ledger=ledger, run_id=run_id)
    except TypeError:
        # 修复前：执行器不接受账本，只能手工登记；用于展示修复前行为。
        tools = DomainTools(access, lambda _: snapshot(hs, rs, pipe_id), InputBatchRepository(tmp_path))
        output = tools.execute("query_assessment", {"pipe_id": pipe_id})
        if hasattr(ledger, "issue"):
            ledger.issue(run_id=run_id, snapshot_id=snapshot_id, tool_result=output)
        return output, ledger
    return tools.execute("query_assessment", {"pipe_id": pipe_id}), ledger


def verify(facts, *, run_id, snapshot_id=SNAPSHOT, ledger=None, **extra):
    """调用认证；修复前签名差异时退回旧参数，以展示真实修复前行为。"""
    try:
        return verified_facts(facts, run_id=run_id, snapshot_id=snapshot_id,
                              principal_id=PRINCIPAL, ledger=ledger, **extra)
    except TypeError:
        return verified_facts(facts, run_id=run_id, snapshot_id=snapshot_id,
                              ledger=ledger, **extra)


def test_lc01_direct_issue_cannot_certify_without_execution():
    """直接调用签发入口、工具执行 0 次，不得得到可用于认证的事实。"""
    fake = forged_fact()
    ledger = ExecutionLedger()
    receipt = None
    try:
        receipt = ledger.issue(run_id=RUN, snapshot_id=SNAPSHOT, tool_result=fake)
    except (AttributeError, PermissionError, TypeError, ValueError):
        receipt = None

    assert receipt is None, "普通调用者不应获得可信签发"
    facts = verify([fake], run_id=RUN, ledger=ledger)
    assert facts == [], "没有真实工具执行时不得产生 verified 事实"


def test_lc02_executor_records_only_real_execution(tmp_path):
    """受控执行 1 次：账本记录绑定本次执行，事实可认证。"""
    output, ledger = execute_once(tmp_path, run_id="run-real")
    assert output["status"] == "succeeded"
    records = ledger.records("run-real")
    assert len(records) == 1
    record = records[0]
    assert record.tool_name == "query_assessment"
    assert record.evidence_id == output["evidence_id"]
    assert record.snapshot_id == SNAPSHOT
    assert record.status == "succeeded"
    assert len(record.result_hash) == 64
    assert getattr(record, "arguments_hash", "") and len(record.arguments_hash) == 64

    facts = verify(fact_cards([output], []), run_id="run-real", ledger=ledger)
    assert {item.metric for item in facts} == {"Hs", "Rs"}
    assert all(item.verification_status == "verified" for item in facts)


def test_lc02_empty_ledger_never_certifies():
    ledger = ExecutionLedger()
    assert verify([forged_fact()], run_id=RUN, ledger=ledger) == []


def test_lc02_failed_tool_execution_is_not_verified(tmp_path):
    """未授予工具时执行器拒绝，不登记成功回执。"""
    access = ToolAccess(principal=BatchPrincipal(actor_id="tester", scope="internal"),
                        snapshot_id=SNAPSHOT, allowed_tools=frozenset())
    ledger = ExecutionLedger()
    try:
        tools = DomainTools(access, lambda _: snapshot(), InputBatchRepository(tmp_path),
                            ledger=ledger, run_id="run-denied")
    except TypeError:
        tools = DomainTools(access, lambda _: snapshot(), InputBatchRepository(tmp_path))
    with pytest.raises(PermissionError):
        tools.execute("query_assessment", {"pipe_id": "syn-ledger-001"})
    assert ledger.records("run-denied") == []
    assert all(item.status != "succeeded" for item in ledger.records("run-denied"))


def test_lc03_audit_copy_must_be_consistent_or_rejected(tmp_path):
    """LEDGER-02：传入篡改的字典审计副本必须被拒绝，不能因账本另有记录而通过。"""
    output, ledger = execute_once(tmp_path, run_id="run-audit")
    record = ledger.records("run-audit")[0]
    facts = fact_cards([output], [])

    tampered = {**record.as_dict(), "snapshot_id": "forged-snapshot"}
    rejected = None
    try:
        rejected = verify(facts, run_id="run-audit", ledger=ledger, receipts=[tampered])
    except TypeError:
        # 认证参数已移除：任何审计副本都不会进入认证路径。
        rejected = None
    assert not rejected, "篡改审计副本不得使事实通过认证"

    authentic = None
    try:
        authentic = verify(facts, run_id="run-audit", ledger=ledger, receipts=[record])
    except TypeError:
        authentic = verify(facts, run_id="run-audit", ledger=ledger)
    assert authentic, "未篡改的原始记录仍应通过"


def test_lc03_ledger_record_tampering_is_not_possible_from_outside(tmp_path):
    """账本记录由执行器持有；外部无法用字典重新登记。"""
    output, ledger = execute_once(tmp_path, run_id="run-immutable")
    assert not hasattr(ledger, "issue"), "公开签发入口必须移除"
    with pytest.raises((AttributeError, TypeError)):
        ledger.register(run_id="run-immutable", tool_result=forged_fact())  # type: ignore[attr-defined]
    assert len(ledger.records("run-immutable")) == 1
    assert ledger.records("run-immutable")[0].evidence_id == output["evidence_id"]


def test_lc04_receipt_remains_non_constructible():
    receipt_cls = ExecutionReceipt
    assert receipt_cls is not None
    digest = hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(ValueError):
        receipt_cls(run_id=RUN, tool_name="query_assessment", evidence_id=EVID,
                    tool_version="domain-tools-1", status="succeeded",
                    snapshot_id=SNAPSHOT, result_hash=digest, issuer="domain-tools")
