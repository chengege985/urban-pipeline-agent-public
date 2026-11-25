"""Versioned, operator-owned drills on the audited OWN-K001 schematic."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing, contextmanager
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query, Request, Response
from pydantic import Field, ValidationError, model_validator

from src.platform.api_models import COMMON_ERRORS

from .audit_storage import private_sqlite_path
from .data_m2 import (
    DEMO_ID, Digest, GasM2Binding, GasM2Source, StrictModel, details, load_catalog, require_internal_m2_access,
)
from .model_provider import MODEL_ID, LocalGasModel, ModelUnavailable
from .network import VERSION, PipeAlreadyExcluded, _digest, analyze_source_withdrawal, compare_pipe_exclusions, validate_network
from .service import GasFixtureService, GasFixtureUnavailable


ALGORITHM = f"{VERSION}/single-edge-fault-1"
SPLIT_ALGORITHM = f"{VERSION}/dual-source-split-1"
GRAPH_VERSION = "gas-drill-graph/1"
TRUST_VERSION = "gas-drill-trust/1"
LIMITATION = "合成演练仅反映图连通性。实际供气、停气、压力、流量、泄漏结果均为 unknown。"
AUDIT_BOUND = "审计覆盖本演练的私有运行时账本及所选修订。来源与计算摘要可复算，账本由运行时权限保护，签名和跨系统审计不可用。"
SCENARIO_PATTERN = r"^syn-gas-drill-[a-f0-9]{32}$"
ScenarioId = Annotated[str, ApiPath(pattern=SCENARIO_PATTERN)]
ScenarioStatus = Literal["draft", "analyzed", "rolled_back"]
FaultKind = Literal["synthetic_edge_exclusion", "synthetic_dual_source_split"]
SPLIT_PIPES = ("syn-gas-pipe-K-026", "syn-gas-pipe-K-041")


class GasScenarioCreate(StrictModel):
    binding: GasM2Binding
    pipe_id: str = Field(pattern=r"^syn-gas-pipe-[A-Za-z0-9-]+$", max_length=128)
    fault_kind: FaultKind

    def pipe_ids(self) -> tuple[str, ...]:
        if self.fault_kind == "synthetic_dual_source_split":
            if self.pipe_id != SPLIT_PIPES[0]:
                raise HTTPException(409, "gas_scenario_preset_conflict")
            return SPLIT_PIPES
        return (self.pipe_id,)


class GasScenarioRevision(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)


class GasScenarioQuestion(GasScenarioRevision):
    analysis_sha256: Digest
    question: str = Field(min_length=1, max_length=600, pattern=r"\S")


class GasScenarioSource(StrictModel):
    source_uri: str | None
    files: list[GasM2Source]
    network_content_sha256: Digest


class GasScenarioImpact(StrictModel):
    fault_edge_id: str
    baseline_reachable_node_count: int
    scenario_reachable_node_count: int
    newly_unreachable_node_ids: list[str]
    affected_edge_ids: list[str]
    affected_pipe_ids: list[str]


class GasDrillComponent(StrictModel):
    node_ids: list[str]
    source_ids: list[str]


class GasDrillGraphState(StrictModel):
    reachable_node_ids: list[str]
    components: list[GasDrillComponent]


class GasDrillGraph(StrictModel):
    rule_version: Literal["gas-drill-graph/1"] = GRAPH_VERSION
    source_nodes: dict[str, str]
    baseline: GasDrillGraphState
    scenario: GasDrillGraphState
    excluded_edge_ids: list[str]
    alternative_path_edge_ids: list[str]
    sha256: Digest


class GasScenarioAnalysis(StrictModel):
    algorithm: str
    impact: GasScenarioImpact
    analysis_sha256: Digest
    model_scope: Literal["graph_connectivity_only"] = "graph_connectivity_only"
    actual_supply: Literal["unknown"] = "unknown"
    actual_outage: Literal["unknown"] = "unknown"
    pressure: Literal["unknown"] = "unknown"
    flow: Literal["unknown"] = "unknown"
    leakage: Literal["unknown"] = "unknown"
    graph: GasDrillGraph | None = None


class GasScenarioAudit(StrictModel):
    actor_id: str
    occurred_at: datetime
    revision: int
    action: Literal["created", "analyzed", "rolled_back"]
    previous_status: ScenarioStatus | None
    current_status: ScenarioStatus
    analysis_sha256: Digest | None


class GasSyntheticScenario(StrictModel):
    scenario_id: str
    data_kind: Literal["internal_synthetic"] = "internal_synthetic"
    coordinate_system: Literal["schematic"] = "schematic"
    input: GasScenarioCreate
    source: GasScenarioSource
    analysis: GasScenarioAnalysis | None
    transition: GasScenarioAudit


class GasScenarioReport(StrictModel):
    report_version: Literal["gas-scenario-report/1"] = "gas-scenario-report/1"
    scenario: GasSyntheticScenario
    audit: list[GasScenarioAudit]
    evidence_refs: list[str]
    limitation: str = LIMITATION
    trust: GasDrillTrust
    audit_boundary: str = AUDIT_BOUND


class GasDrillTrust(StrictModel):
    rule_version: Literal["gas-drill-trust/1"] = TRUST_VERSION
    source_files_verified: int
    graph_status: Literal["valid", "valid_with_warnings"]
    graph_warnings: list[str]
    observation_count: int
    observation_quality: dict[str, int]
    observation_channels: dict[str, int]
    identity_scope: Literal["internal_synthetic_owner"] = "internal_synthetic_owner"
    revision_policy: Literal["exact_read_compare_and_latest_write"] = "exact_read_compare_and_latest_write"


class GasDrillPreset(StrictModel):
    name: str
    label: str
    pipe_ids: list[str]
    fault_kind: FaultKind
    algorithm: str


PRESETS = [
    GasDrillPreset(name="k001", label="K-001 支路断连", pipe_ids=["syn-gas-pipe-K-001"],
                   fault_kind="synthetic_edge_exclusion", algorithm=ALGORITHM),
    GasDrillPreset(name="alternate", label="K-003 替代路径", pipe_ids=["syn-gas-pipe-K-003"],
                   fault_kind="synthetic_edge_exclusion", algorithm=ALGORITHM),
    GasDrillPreset(name="dual-source", label="双气源分区", pipe_ids=list(SPLIT_PIPES),
                   fault_kind="synthetic_dual_source_split", algorithm=SPLIT_ALGORITHM),
]


class GasDrillReference(StrictModel):
    scenario_id: str = Field(pattern=SCENARIO_PATTERN)
    revision: int = Field(ge=1, strict=True)
    analysis_sha256: Digest


class GasDrillChoice(StrictModel):
    reference: GasDrillReference
    pipe_id: str
    fault_kind: FaultKind
    latest: bool


class GasDrillWorkspace(StrictModel):
    binding: GasM2Binding
    presets: list[GasDrillPreset]
    versions: list[GasDrillChoice]
    trust: GasDrillTrust


class GasDrillCompareRequest(StrictModel):
    binding: GasM2Binding
    before: GasDrillReference | None
    after: GasDrillReference | None

    @model_validator(mode="after")
    def require_scenario(self):
        if self.before is None and self.after is None:
            raise ValueError("at least one analyzed revision is required")
        return self


class GasDrillSide(StrictModel):
    report: GasScenarioReport | None
    graph: GasDrillGraphState


class GasDrillComparison(StrictModel):
    rule_version: Literal["gas-drill-comparison/1"] = "gas-drill-comparison/1"
    selection: GasDrillCompareRequest
    before: GasDrillSide
    after: GasDrillSide
    newly_unreachable_node_ids: list[str]
    newly_reachable_node_ids: list[str]
    comparison_sha256: Digest
    evidence_refs: list[str]
    audit_boundary: str = AUDIT_BOUND
    limitation: str = LIMITATION


class GasScenarioAnswer(StrictModel):
    request_id: str
    binding: GasM2Binding
    evidence_refs: list[str]
    facts: list[str]
    answer: str
    explanation_source: Literal["backend_facts", "qwen_evidence_selection"] = "backend_facts"
    model_id: str | None = None
    model_calls: int = 0
    model_usage: dict[str, int] = Field(default_factory=dict)
    model_error: str | None = None


class GasScenarioExplanation(GasScenarioAnswer):
    tool_name: Literal["synthetic_scenario.read_analysis"] = "synthetic_scenario.read_analysis"
    scenario_id: str
    revision: int
    analysis_sha256: Digest


class GasDrillToolRequest(StrictModel):
    tool: Literal["lookup", "simulate", "compare", "report"]
    selection: GasDrillCompareRequest
    expected_sha256: Digest
    question: str = Field(min_length=1, max_length=600, pattern=r"\S")


class GasDrillToolResult(GasScenarioAnswer):
    tool_name: Literal["synthetic_scenario.lookup", "synthetic_scenario.simulate_topology", "synthetic_scenario.compare_versions",
                       "synthetic_scenario.report_evidence"]
    selection: GasDrillCompareRequest
    verified_sha256: Digest


class GasSourceStressRequest(StrictModel):
    binding: GasM2Binding
    scenario: GasDrillReference | None
    source_id: str = Field(pattern=r"^syn-gas-source-[A-Za-z0-9-]+$", max_length=128)


class GasSourceStressResult(StrictModel):
    selection: GasSourceStressRequest
    rule_version: Literal["gas-drill-source-withdrawal/1"] = "gas-drill-source-withdrawal/1"
    source: GasScenarioSource
    enabled_source_nodes: dict[str, str]
    remaining_source_ids: list[str]
    excluded_edge_ids: list[str]
    before: GasDrillGraphState
    after: GasDrillGraphState
    newly_unreachable_node_ids: list[str]
    single_source_dependencies: dict[str, list[str]]
    alternate_source_id: str | None
    alternate_source_path_edge_ids: list[str]
    scenario_alternative_path_edge_ids: list[str]
    audit: list[GasScenarioAudit]
    stress_sha256: Digest
    evidence_refs: list[str]
    limitation: str = LIMITATION
    audit_boundary: str = "只读图计算引用所选演练修订的私有审计。气源退出计算本身不写入演练账本。"


class GasSourceStressQuestion(StrictModel):
    selection: GasSourceStressRequest
    expected_sha256: Digest
    question: str = Field(min_length=1, max_length=600, pattern=r"\S")


class GasSourceStressAnswer(GasScenarioAnswer):
    tool_name: Literal["synthetic_scenario.source_stress"] = "synthetic_scenario.source_stress"
    selection: GasSourceStressRequest
    verified_sha256: Digest


@dataclass(frozen=True)
class AuditedDrillInputs:
    binding: GasM2Binding
    source: GasScenarioSource
    network: dict
    trust: GasDrillTrust


class GasScenarioStore:
    """Append complete immutable versions in a private runtime ledger."""

    def __init__(self, db_path: Path):
        self.path = private_sqlite_path(db_path)
        with self.session() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS gas_scenario_versions ("
                "actor_id TEXT NOT NULL, scenario_id TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL,"
                "PRIMARY KEY (actor_id, scenario_id, revision))"
            )

    @contextmanager
    def session(self, *, writing: bool = False):
        try:
            with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
                if writing:
                    db.execute("BEGIN IMMEDIATE")
                yield db
        except (sqlite3.Error, ValidationError):
            raise HTTPException(503, "gas_scenario_audit_unavailable") from None

    def versions(self, db, actor: str, scenario_id: str, *, latest_only: bool = True) -> list[GasSyntheticScenario]:
        query = ("SELECT revision, snapshot FROM gas_scenario_versions WHERE actor_id=? AND scenario_id=? ORDER BY revision DESC"
                 + (" LIMIT 1" if latest_only else ""))
        versions = []
        for revision, raw in db.execute(query, (actor, scenario_id)):
            versions.append(self.decode(raw, actor, scenario_id, revision))
        if not versions:
            raise HTTPException(404, "gas_scenario_not_found")
        return versions

    @staticmethod
    def decode(raw: str, actor: str, scenario_id: str, revision: int) -> GasSyntheticScenario:
        row = GasSyntheticScenario.model_validate_json(raw)
        if (row.transition.actor_id, row.scenario_id, row.transition.revision) != (actor, scenario_id, revision):
            raise HTTPException(503, "gas_scenario_audit_inconsistent")
        return row

    def choices(self, db, actor: str, binding: GasM2Binding) -> list[GasDrillChoice]:
        seen = set()
        choices = []
        rows = db.execute(
            "SELECT scenario_id, revision, snapshot FROM gas_scenario_versions WHERE actor_id=? ORDER BY rowid DESC", (actor,),
        )
        for scenario_id, revision, raw in rows:
            row = self.decode(raw, actor, scenario_id, revision)
            latest = scenario_id not in seen
            seen.add(scenario_id)
            if row.input.binding == binding and row.transition.current_status == "analyzed" and row.analysis is not None:
                choices.append(GasDrillChoice(
                    reference=GasDrillReference(scenario_id=scenario_id, revision=revision,
                                                analysis_sha256=row.analysis.analysis_sha256),
                    pipe_id=row.input.pipe_id, fault_kind=row.input.fault_kind, latest=latest,
                ))
        return choices

    def append(self, db, scenario: GasSyntheticScenario):
        db.execute("INSERT INTO gas_scenario_versions VALUES (?, ?, ?, ?)", (
            scenario.transition.actor_id, scenario.scenario_id, scenario.transition.revision, scenario.model_dump_json(),
        ))


def audited_inputs(binding: GasM2Binding, service: GasFixtureService) -> AuditedDrillInputs:
    entry = details(load_catalog(), DEMO_ID)
    if binding.version is None or binding != entry.binding:
        raise HTTPException(409, "gas_m2_version_conflict")
    if not (entry.dataset.synthetic_flag and entry.dataset.data_kind == "internal_synthetic"
            and entry.dataset.coordinate_system == "schematic" and entry.dataset.usage_scope == "synthetic_demo"):
        raise HTTPException(409, "gas_scenario_synthetic_fixture_required")
    network, _, snapshot, _ = service.m2_fixture({source.name: source.sha256 for source in entry.dataset.sources})
    try:
        graph_review = validate_network(network)
    except ValueError:
        raise GasFixtureUnavailable("audited schematic failed graph validation") from None
    points = snapshot.rows.monitoring
    expected_points = entry.dataset.record_counts.get("observations")
    time_slots = {(row.asset_id, row.sensor_id, row.metric, row.observed_at) for row in points}
    if len(time_slots) != len(points) or (expected_points is not None and len(points) != expected_points):
        raise GasFixtureUnavailable("audited synthetic observations failed quality validation")
    source = GasScenarioSource(
        source_uri=entry.dataset.source_uri,
        files=sorted(entry.dataset.sources, key=lambda item: item.name),
        network_content_sha256=_digest(network),
    )
    trust = GasDrillTrust(
        source_files_verified=len(source.files), graph_status=graph_review["status"], graph_warnings=graph_review["warnings"],
        observation_count=len(points), observation_quality=dict(Counter(row.quality for row in points)),
        observation_channels=dict(Counter(f"{row.asset_id} / {row.sensor_id} / {row.metric} / {row.unit}" for row in points)),
    )
    return AuditedDrillInputs(binding, source, network, trust)


def analyze(scenario: GasSyntheticScenario, network: dict) -> GasScenarioAnalysis:
    try:
        summary, topology = compare_pipe_exclusions(network, scenario.input.pipe_ids())
    except PipeAlreadyExcluded:
        raise HTTPException(409, "gas_scenario_pipe_already_excluded") from None
    except (KeyError, ValueError):
        raise GasFixtureUnavailable("audited schematic is incompatible with the graph algorithm") from None
    algorithm = ALGORITHM if scenario.input.fault_kind == "synthetic_edge_exclusion" else SPLIT_ALGORITHM
    graph = GasDrillGraph(**topology, sha256=_digest({
        "rule_version": GRAPH_VERSION, "network_content_sha256": scenario.source.network_content_sha256, **topology,
    }))
    canonical = {"algorithm": algorithm, "input": scenario.input.model_dump(mode="json"),
                 "source": scenario.source.model_dump(mode="json"), "impact": summary}
    if algorithm == SPLIT_ALGORITHM:
        canonical["graph_sha256"] = graph.sha256
    return GasScenarioAnalysis(algorithm=algorithm, impact=GasScenarioImpact(**summary), analysis_sha256=_digest(canonical), graph=graph)


def verify_snapshot(scenario: GasSyntheticScenario, audited: AuditedDrillInputs) -> GasSyntheticScenario:
    if scenario.input.binding != audited.binding or scenario.source != audited.source:
        raise HTTPException(409, "gas_m2_version_conflict")
    audit = scenario.transition
    stored = scenario.analysis
    if audit.current_status == "draft" and stored is None and audit.analysis_sha256 is None:
        return scenario
    if stored is None or audit.analysis_sha256 != stored.analysis_sha256 or audit.current_status == "draft":
        raise HTTPException(503, "gas_scenario_audit_inconsistent")
    computed = analyze(scenario, audited.network)
    # Replay accepted single-edge revisions with the original core hash.
    expected = computed if stored.graph is not None else computed.model_copy(update={"graph": None})
    if stored != expected:
        raise HTTPException(409, "gas_scenario_evidence_conflict")
    return scenario.model_copy(update={"analysis": computed})


def report_at(versions: list[GasSyntheticScenario], revision: int, audited: AuditedDrillInputs) -> GasScenarioReport:
    selected = [item for item in reversed(versions) if item.transition.revision <= revision]
    if not selected or selected[-1].transition.revision != revision:
        raise HTTPException(409, "gas_scenario_revision_conflict")
    prior = None
    audit = []
    for index, item in enumerate(selected, 1):
        step = item.transition
        valid = (
            (index == 1 and step.action == "created" and step.current_status == "draft" and item.analysis is None)
            or (index > 1 and step.action == "analyzed" and step.current_status == "analyzed" and item.analysis is not None)
            or (prior is not None and prior.current_status == "analyzed" and step.action == "rolled_back"
                and step.current_status == "rolled_back" and step.analysis_sha256 == prior.analysis_sha256)
        )
        if (not valid or step.revision != index or step.previous_status != (prior.current_status if prior else None)
                or item.input != selected[0].input or item.source != selected[0].source
                or step.analysis_sha256 != (item.analysis.analysis_sha256 if item.analysis else None)):
            raise HTTPException(503, "gas_scenario_audit_inconsistent")
        prior = step
        audit.append(step)
    scenario = verify_snapshot(selected[-1], audited)
    return GasScenarioReport(scenario=scenario, audit=audit, trust=audited.trust, evidence_refs=scenario_evidence(scenario))


def scenario_evidence(scenario: GasSyntheticScenario) -> list[str]:
    binding = scenario.input.binding
    refs = [
        f"gas-m2-registry:sha256:{binding.registry_sha256}:{binding.dataset_id}",
        *(f"gas-m2-record:{binding.dataset_id}:{pipe_id}" for pipe_id in scenario.input.pipe_ids()),
        f"gas-schematic:sha256:{scenario.source.network_content_sha256}",
        *(f"gas-m2-source:sha256:{item.sha256}:{item.name}" for item in scenario.source.files),
    ]
    if scenario.analysis:
        refs.append(f"gas-scenario-analysis:sha256:{scenario.analysis.analysis_sha256}")
        if scenario.analysis.graph is not None:
            refs.append(f"gas-scenario-graph:sha256:{scenario.analysis.graph.sha256}")
    return refs


def scenario_facts(scenario: GasSyntheticScenario, analysis: GasScenarioAnalysis) -> list[str]:
    impact = analysis.impact
    binding = scenario.input.binding
    graph = analysis.graph
    edges = graph.excluded_edge_ids if graph else [impact.fault_edge_id]
    facts = [
        f"合成演练 {scenario.scenario_id} r{scenario.transition.revision}，数据集 {binding.dataset_id}，版本 {binding.version}。",
        f"使用算法 {analysis.algorithm}，演练排除图边 {'、'.join(edges)}。",
        f"启用气源图可达节点由 {impact.baseline_reachable_node_count} 变为 {impact.scenario_reachable_node_count}，"
        f"新增图不可达节点 {len(impact.newly_unreachable_node_ids)} 个，涉及管段 {len(impact.affected_pipe_ids)} 条。",
        f"涉及管段 {'、'.join(impact.affected_pipe_ids)}。",
        f"计算证据 SHA-256 {analysis.analysis_sha256}。",
    ]
    if graph:
        groups = "、".join(f"{len(row.node_ids)} 节点（{'、'.join(row.source_ids) or '无启用气源'}）" for row in graph.scenario.components)
        facts.append(f"演练后的图连通分区 {groups}。")
        if graph.alternative_path_edge_ids:
            facts.append(f"排除图边后的替代路径 {'、'.join(graph.alternative_path_edge_ids)}。")
    return [*facts, LIMITATION]


def explain(scenario: GasSyntheticScenario, analysis: GasScenarioAnalysis, question: str) -> GasScenarioExplanation:
    facts = scenario_facts(scenario, analysis)
    result = GasScenarioExplanation(
        request_id="gas-drill-" + uuid4().hex, scenario_id=scenario.scenario_id, revision=scenario.transition.revision,
        binding=scenario.input.binding, analysis_sha256=analysis.analysis_sha256, evidence_refs=scenario_evidence(scenario),
        facts=facts, answer="\n".join(facts),
    )
    select_evidence(result, question)
    return result


def select_evidence(result: GasScenarioAnswer, question: str) -> None:
    facts = result.facts
    provider = None
    try:
        provider = LocalGasModel.from_environment()
        if provider is not None:
            selected = provider.explain(facts.copy(), question=question)
            lines = selected.splitlines() if isinstance(selected, str) else []
            if not lines or any(line not in facts for line in lines):
                raise ModelUnavailable("model_evidence_whitelist_violation")
            result.answer = "\n".join(dict.fromkeys((facts[0], *lines, facts[-1])))
            result.explanation_source = "qwen_evidence_selection"
            result.model_id = MODEL_ID
    except ModelUnavailable as exc:
        result.model_error = str(exc)
    if provider is not None:
        result.model_calls, result.model_usage = provider.calls, provider.usage


def trace_answer(result: GasScenarioAnswer, response: Response, *, kind: str = "gas_scenario_read_tool") -> None:
    response.headers["X-Request-ID"] = result.request_id
    logging.getLogger("gas.agent").info(json.dumps({
        "kind": kind, **result.model_dump(mode="json", exclude={"facts", "answer"}),
    }, ensure_ascii=False))


def compare(selection: GasDrillCompareRequest, before: GasScenarioReport | None, after: GasScenarioReport | None) -> GasDrillComparison:
    current = after or before
    assert current and current.scenario.analysis and current.scenario.analysis.graph
    base = current.scenario.analysis.graph.baseline
    if before:
        assert before.scenario.analysis and before.scenario.analysis.graph
    if after:
        assert after.scenario.analysis and after.scenario.analysis.graph
    first = before.scenario.analysis.graph.scenario if before else base
    second = after.scenario.analysis.graph.scenario if after else base
    starts = set(first.reachable_node_ids)
    finishes = set(second.reachable_node_ids)
    refs = list(dict.fromkeys(ref for item in (before, after) if item for ref in item.evidence_refs))
    comparison_sha = _digest({
        "rule_version": "gas-drill-comparison/1", "selection": selection.model_dump(mode="json"),
        "before": first.model_dump(mode="json"), "after": second.model_dump(mode="json"),
        "evidence_refs": refs,
    })
    return GasDrillComparison(
        selection=selection, before=GasDrillSide(report=before, graph=first), after=GasDrillSide(report=after, graph=second),
        newly_unreachable_node_ids=sorted(starts - finishes), newly_reachable_node_ids=sorted(finishes - starts),
        comparison_sha256=comparison_sha, evidence_refs=[*refs, f"gas-scenario-comparison:sha256:{comparison_sha}"],
    )


def comparison_facts(result: GasDrillComparison) -> list[str]:
    def identity(side: GasDrillSide):
        if side.report is None:
            return "同源基线"
        scenario = side.report.scenario
        return f"{scenario.input.pipe_id}（{scenario.scenario_id} r{scenario.transition.revision}）"

    return [
        f"从 {identity(result.before)} 对比到 {identity(result.after)}，对比摘要 {result.comparison_sha256}。",
        f"气源图可达节点由 {len(result.before.graph.reachable_node_ids)} 变为 {len(result.after.graph.reachable_node_ids)}，"
        f"图连通分区由 {len(result.before.graph.components)} 变为 {len(result.after.graph.components)}。",
        f"新增图不可达节点 {'、'.join(result.newly_unreachable_node_ids) or '无'}，"
        f"新增图可达节点 {'、'.join(result.newly_reachable_node_ids) or '无'}。",
        LIMITATION,
    ]


def report_by_reference(ledger: GasScenarioStore, db, actor: str, ref: GasDrillReference, audited: AuditedDrillInputs) -> GasScenarioReport:
    selected = report_at(ledger.versions(db, actor, ref.scenario_id, latest_only=False), ref.revision, audited)
    row = selected.scenario
    if (row.transition.current_status != "analyzed" or row.analysis is None
            or row.analysis.analysis_sha256 != ref.analysis_sha256):
        raise HTTPException(409, "gas_scenario_analysis_conflict")
    return selected


def source_stress(payload: GasSourceStressRequest, audited: AuditedDrillInputs, report: GasScenarioReport | None) -> GasSourceStressResult:
    analysis = report.scenario.analysis if report else None
    graph = analysis.graph if analysis else None
    edges = graph.excluded_edge_ids if graph else []
    try:
        result = analyze_source_withdrawal(audited.network, payload.source_id, edges)
    except KeyError:
        raise HTTPException(404, "gas_scenario_enabled_source_not_found") from None
    except ValueError:
        raise GasFixtureUnavailable("audited schematic failed the source withdrawal graph preconditions") from None
    refs = report.evidence_refs if report else [
        f"gas-m2-registry:sha256:{audited.binding.registry_sha256}:{DEMO_ID}",
        *(f"gas-m2-source:sha256:{row.sha256}:{row.name}" for row in audited.source.files),
    ]
    reply = GasSourceStressResult(
        selection=payload, source=audited.source, excluded_edge_ids=edges, **result,
        scenario_alternative_path_edge_ids=graph.alternative_path_edge_ids if graph else [],
        audit=report.audit if report else [], stress_sha256="0" * 64, evidence_refs=refs,
    )
    digest = _digest(reply.model_dump(mode="json", exclude={"stress_sha256"}))
    return reply.model_copy(update={"stress_sha256": digest, "evidence_refs": [*refs, f"gas-source-stress:sha256:{digest}"]})


def gas_scenario_router(service: GasFixtureService) -> APIRouter:
    router = APIRouter(prefix="/synthetic-scenarios", tags=["gas-synthetic-scenarios"])
    settings = {"openapi_extra": {"security": [{"WaterToken": []}]}, "responses": COMMON_ERRORS}
    db_path = os.getenv("GAS_SCENARIO_AUDIT_DB", "").strip()
    store = GasScenarioStore(Path(db_path)) if db_path else None

    def access(request: Request, response: Response) -> tuple[str, GasScenarioStore]:
        principal = require_internal_m2_access(request, response)
        if store is None:
            raise HTTPException(503, "gas_scenario_audit_unavailable")
        return principal.actor_id, store

    def selection_result(payload: GasDrillCompareRequest, actor: str, ledger: GasScenarioStore) -> GasDrillComparison:
        audited = audited_inputs(payload.binding, service)
        with ledger.session() as db:
            before = report_by_reference(ledger, db, actor, payload.before, audited) if payload.before else None
            after = report_by_reference(ledger, db, actor, payload.after, audited) if payload.after else None
            return compare(payload, before, after)

    def stress_result(payload: GasSourceStressRequest, request: Request, response: Response) -> GasSourceStressResult:
        actor, ledger = access(request, response)
        audited = audited_inputs(payload.binding, service)
        with ledger.session() as db:
            report = report_by_reference(ledger, db, actor, payload.scenario, audited) if payload.scenario else None
        return source_stress(payload, audited, report)

    @router.post("/source-stress", response_model=GasSourceStressResult, **settings)
    def run_source_stress(payload: GasSourceStressRequest, request: Request, response: Response):
        return stress_result(payload, request, response)

    @router.post("/source-stress/explain", response_model=GasSourceStressAnswer, **settings)
    def explain_source_stress(payload: GasSourceStressQuestion, request: Request, response: Response):
        result = stress_result(payload.selection, request, response)
        if result.stress_sha256 != payload.expected_sha256:
            raise HTTPException(409, "gas_scenario_tool_evidence_conflict")
        ref = payload.selection.scenario
        context = f"{ref.scenario_id} r{ref.revision}" if ref else "同源合成基线"
        alternate_path = "、".join(result.alternate_source_path_edge_ids)
        if not alternate_path:
            alternate_path = "同一节点" if result.alternate_source_id else "无可达路径"
        facts = [
            f"{context} 中模拟退出气源 {payload.selection.source_id}，证据摘要 {result.stress_sha256}。",
            f"图可达节点由 {len(result.before.reachable_node_ids)} 变为 {len(result.after.reachable_node_ids)}，"
            f"图分区 {len(result.before.components)} → {len(result.after.components)}。",
            f"新增图不可达节点 {'、'.join(result.newly_unreachable_node_ids) or '无'}。",
            f"剩余启用气源 {'、'.join(result.remaining_source_ids) or '无'}。",
            f"其他气源到退出节点的图路径 {alternate_path}。",
            LIMITATION,
        ]
        answer = GasSourceStressAnswer(
            request_id="gas-drill-" + uuid4().hex, binding=payload.selection.binding, selection=payload.selection,
            verified_sha256=result.stress_sha256, evidence_refs=result.evidence_refs, facts=facts, answer="\n".join(facts),
        )
        select_evidence(answer, payload.question)
        access(request, response)
        audited_inputs(payload.selection.binding, service)
        trace_answer(answer, response)
        return answer

    @router.get("/workspace", response_model=GasDrillWorkspace, **settings)
    def workspace(request: Request, response: Response, registry_sha256: Digest, version: str = Query(max_length=128)):
        actor, ledger = access(request, response)
        binding = GasM2Binding(dataset_id=DEMO_ID, version=version, registry_sha256=registry_sha256)
        audited = audited_inputs(binding, service)
        with ledger.session() as db:
            return GasDrillWorkspace(binding=binding, presets=PRESETS, versions=ledger.choices(db, actor, binding), trust=audited.trust)

    @router.post("/comparison", response_model=GasDrillComparison, **settings)
    def comparison(payload: GasDrillCompareRequest, request: Request, response: Response):
        actor, ledger = access(request, response)
        return selection_result(payload, actor, ledger)

    @router.post("/tool-query", response_model=GasDrillToolResult, **settings)
    def tool_query(payload: GasDrillToolRequest, request: Request, response: Response):
        actor, ledger = access(request, response)
        if payload.tool != "compare" and (payload.selection.before is not None or payload.selection.after is None):
            raise HTTPException(422, "gas_scenario_tool_scope")
        result = selection_result(payload.selection, actor, ledger)
        refs = result.evidence_refs
        if payload.tool == "compare":
            digest, tool, facts = result.comparison_sha256, "synthetic_scenario.compare_versions", comparison_facts(result)
        else:
            assert result.after.report and result.after.report.scenario.analysis
            snapshot = result.after.report.scenario
            analysis = snapshot.analysis
            digest = analysis.analysis_sha256
            if payload.tool == "lookup":
                tool = "synthetic_scenario.lookup"
                facts = [scenario_facts(snapshot, analysis)[0],
                         f"场景排除的合成管段 {'、'.join(snapshot.input.pipe_ids())}，修订状态 {snapshot.transition.current_status}。",
                         f"原件校验 {result.after.report.trust.source_files_verified} 份，图校验 {result.after.report.trust.graph_status}。",
                         LIMITATION]
            elif payload.tool == "report":
                tool = "synthetic_scenario.report_evidence"
                report = result.after.report
                facts = [
                    scenario_facts(snapshot, analysis)[0],
                    f"报告对应 r{snapshot.transition.revision}，算法 {analysis.algorithm}，分析摘要 {analysis.analysis_sha256}。",
                    f"报告记录 {len(snapshot.source.files)} 份原件摘要和 {len(report.audit)} 条该修订以内的私有演练审计。",
                    f"本修订动作 {snapshot.transition.action}，状态 {snapshot.transition.current_status}，"
                    f"时间 {snapshot.transition.occurred_at.isoformat()}。",
                    report.audit_boundary,
                    LIMITATION,
                ]
            else:
                tool, facts = "synthetic_scenario.simulate_topology", scenario_facts(snapshot, analysis)
            refs = result.after.report.evidence_refs
        if digest != payload.expected_sha256:
            raise HTTPException(409, "gas_scenario_tool_evidence_conflict")
        answer = GasDrillToolResult(
            request_id="gas-drill-" + uuid4().hex, tool_name=tool, selection=payload.selection, verified_sha256=digest,
            binding=payload.selection.binding, evidence_refs=refs, facts=facts, answer="\n".join(facts),
        )
        select_evidence(answer, payload.question)
        access(request, response)
        audited_inputs(payload.selection.binding, service)
        trace_answer(answer, response)
        return answer

    @router.post("", response_model=GasSyntheticScenario, status_code=201, **settings)
    def create(payload: GasScenarioCreate, request: Request, response: Response):
        actor, ledger = access(request, response)
        audited = audited_inputs(payload.binding, service)
        if not set(payload.pipe_ids()) <= {row["pipe_id"] for row in audited.network["pipes"]}:
            raise HTTPException(404, "gas_scenario_pipe_not_found")
        scenario = GasSyntheticScenario(
            scenario_id="syn-gas-drill-" + uuid4().hex, input=payload, source=audited.source, analysis=None,
            transition=GasScenarioAudit(actor_id=actor, occurred_at=datetime.now(UTC), revision=1, action="created",
                                        previous_status=None, current_status="draft", analysis_sha256=None),
        )
        with ledger.session(writing=True) as db:
            ledger.append(db, scenario)
        return scenario

    @router.get("/{scenario_id}", response_model=GasSyntheticScenario, **settings)
    def get(scenario_id: ScenarioId, request: Request, response: Response):
        actor, ledger = access(request, response)
        with ledger.session() as db:
            versions = ledger.versions(db, actor, scenario_id, latest_only=False)
        latest = versions[0]
        return report_at(versions, latest.transition.revision, audited_inputs(latest.input.binding, service)).scenario

    def transition(scenario_id, payload, request, response, *, rollback: bool):
        actor, ledger = access(request, response)
        with ledger.session(writing=True) as db:
            versions = ledger.versions(db, actor, scenario_id, latest_only=False)
            latest = versions[0]
            if latest.transition.revision != payload.expected_revision:
                raise HTTPException(409, "gas_scenario_revision_conflict")
            audited = audited_inputs(latest.input.binding, service)
            scenario = report_at(versions, payload.expected_revision, audited).scenario
            previous = scenario.transition.current_status
            if rollback and previous != "analyzed":
                raise HTTPException(409, "gas_scenario_no_active_analysis")
            result = scenario.analysis if rollback else analyze(scenario, audited.network)
            audit = GasScenarioAudit(
                actor_id=actor, occurred_at=datetime.now(UTC), revision=scenario.transition.revision + 1,
                action="rolled_back" if rollback else "analyzed", previous_status=previous,
                current_status="rolled_back" if rollback else "analyzed",
                analysis_sha256=result.analysis_sha256 if result is not None else None,
            )
            updated = scenario.model_copy(update={"analysis": result, "transition": audit})
            ledger.append(db, updated)
            return updated

    @router.post("/{scenario_id}/analyze", response_model=GasSyntheticScenario, **settings)
    def run(scenario_id: ScenarioId, payload: GasScenarioRevision, request: Request, response: Response):
        return transition(scenario_id, payload, request, response, rollback=False)

    @router.post("/{scenario_id}/rollback", response_model=GasSyntheticScenario, **settings)
    def rollback(scenario_id: ScenarioId, payload: GasScenarioRevision, request: Request, response: Response):
        return transition(scenario_id, payload, request, response, rollback=True)

    @router.post("/{scenario_id}/explain", response_model=GasScenarioExplanation, **settings)
    def explain_analysis(scenario_id: ScenarioId, payload: GasScenarioQuestion, request: Request, response: Response):
        scenario = get(scenario_id, request, response)
        analysis = scenario.analysis
        if (scenario.transition.revision != payload.expected_revision or scenario.transition.current_status != "analyzed"
                or analysis is None or analysis.analysis_sha256 != payload.analysis_sha256):
            raise HTTPException(409, "gas_scenario_analysis_conflict")
        result = explain(scenario, analysis, payload.question)
        if get(scenario_id, request, response).transition.revision != payload.expected_revision:
            raise HTTPException(409, "gas_scenario_revision_conflict")
        trace_answer(result, response)
        return result

    @router.get("/{scenario_id}/report", response_model=GasScenarioReport, **settings)
    def report(scenario_id: ScenarioId, request: Request, response: Response, revision: int = Query(ge=1)):
        actor, ledger = access(request, response)
        with ledger.session() as db:
            versions = ledger.versions(db, actor, scenario_id, latest_only=False)
        return report_at(versions, revision, audited_inputs(versions[0].input.binding, service))

    return router
