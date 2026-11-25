"""S01确定性图计算。

以快照的运行关系为依据，用MultiDiGraph保留平行管边和双向弧。
先计算正常状态，再应用故障/给定隔离方案/措施变更，取新增不可达集合。
所有改动只作用于内存图，绝不改写资产台账或实际阀门。
"""
from collections import deque

from src.platform.schemas import ScenarioRequest, SnapshotPayload

VERSION = "0.1.0"
LIMITATIONS = [
    "结果仅表示供水通路，不表示压力、流量或实际供水容量。",
    "指定故障是情景假设，不包含失效概率或物理级联结论。",
    "方案操作仅在快照副本试算，未控制任何实际设施。",
]


def envelope(snapshot: dict, status: str, **fields):
    return {"status": status, "snapshot_id": snapshot["snapshot_id"],
            "as_of": snapshot["payload"]["as_of"], "data_mode": snapshot["payload"]["data_mode"],
            "source_status": snapshot["payload"]["source_status"],
            "validation_status": "not_business_validated", "engine_type": "topology",
            "algorithm_version": VERSION, "evidence_refs": ["snapshot:" + snapshot["snapshot_id"]],
            "limitations": list(LIMITATIONS), **fields}


def check_inputs(snapshot: dict, request: ScenarioRequest) -> list[str]:
    """结构已由Pydantic校验；这里检查运行状态、边界和当前情景适用条件。"""
    data = SnapshotPayload.model_validate(snapshot["payload"])
    issues = []
    if request.snapshot_id != snapshot["snapshot_id"] or request.as_of != data.as_of:
        issues.append("请求对象或时点与冻结快照不一致")
    if not data.nodes or not data.edges:
        issues.append("缺少运行网络，不能从评分样例推断网络关系")
    if not any(n.source and n.active is True for n in data.nodes):
        issues.append("没有已确认启用的水源或外部供水节点")
    for n in data.nodes:
        if n.active is None or (n.boundary and not n.boundary_confirmed):
            issues.append(f"节点{n.node_id}运行状态或外部边界未确认")
    edges = {e.edge_id: e for e in data.edges}
    for e in data.edges:
        if e.direction == "unknown" or e.state == "unknown":
            issues.append(f"边{e.edge_id}方向或开闭未知")
    fault = edges.get(request.fault_edge_id)
    if fault is None:
        issues.append("指定故障边不存在于当前快照")
    elif fault.state != "open":
        issues.append("指定故障边不在正常开启状态，不能直接当作新增中断")
    plan = next((p for p in data.isolation_plans if p.plan_id == request.isolation_plan_id), None)
    if plan is None or request.fault_edge_id not in plan.fault_edge_ids:
        issues.append("缺少适用于该故障的给定隔离方案")
    elif not plan.evidence_ref.strip():
        issues.append("隔离方案缺少来源依据")
    else:
        for vid in plan.valve_ids:
            valve = edges[vid]
            if valve.operable is not True:
                issues.append(f"隔离阀{vid}不可操作或可操作性未知")
    return issues


def build_graph(data: SnapshotPayload, closed: set[str], restored: set[str], overrides: dict[str, str]):
    """每条双向管边建立两条带相同edge_id的弧；平行管边不能被一起误删。"""
    import networkx as nx

    graph = nx.MultiDiGraph()
    active = {n.node_id for n in data.nodes if n.active}
    graph.add_nodes_from(sorted(active))
    for e in sorted(data.edges, key=lambda x: x.edge_id):
        state = overrides.get(e.edge_id, e.state)
        if e.edge_id in closed and e.edge_id not in restored:
            continue
        if state != "open" or not {e.from_node, e.to_node} <= active:
            continue
        if e.direction in {"both", "forward"}:
            graph.add_edge(e.from_node, e.to_node, key=e.edge_id)
        if e.direction in {"both", "reverse"}:
            graph.add_edge(e.to_node, e.from_node, key=e.edge_id)
    return graph


def paths_from_sources(graph, sources):
    """按固定ID顺序BFS，返回可重现的节点路径和对应管边路径。"""
    paths = {s: {"nodes": [s], "edges": []} for s in sorted(sources) if s in graph}
    queue = deque(paths)
    while queue:
        node = queue.popleft()
        for nxt in sorted(graph.successors(node)):
            if nxt not in paths:
                eid = sorted(graph[node][nxt])[0]
                paths[nxt] = {"nodes": paths[node]["nodes"] + [nxt], "edges": paths[node]["edges"] + [eid]}
                queue.append(nxt)
    return paths


def simulate(snapshot: dict, request: ScenarioRequest, *, restored=None, overrides=None) -> dict:
    issues = check_inputs(snapshot, request)
    if issues:
        return envelope(snapshot, "insufficient_data", missing_items=issues)
    data = SnapshotPayload.model_validate(snapshot["payload"])
    plan = next(p for p in data.isolation_plans if p.plan_id == request.isolation_plan_id)
    sources = {n.node_id for n in data.nodes if n.source and n.active}
    baseline_graph = build_graph(data, set(), set(), {})
    baseline = paths_from_sources(baseline_graph, sources)
    # 关断操作和故障分别表示；抢修只能恢复显式指定的故障边，开阀必须另给操作。
    states = {v: "closed" for v in plan.valve_ids}
    states.update(overrides or {})
    changed_graph = build_graph(data, {request.fault_edge_id}, set(restored or []), states)
    changed = paths_from_sources(changed_graph, sources)
    newly = set(baseline) - set(changed)
    services, critical, counts = [], [], {"household": 0, "person": 0, "facility": 0}
    unknown_counts = []
    for service in data.services:
        rule = any if service.supply_rule == "any" else all
        before = rule(n in baseline for n in service.node_ids)
        after = rule(n in changed for n in service.node_ids)
        if before and not after:
            services.append(service.service_id)
            if service.critical:
                critical.append(service.service_id)
            if service.count_value is None:
                unknown_counts.append(service.service_id)
            else:
                counts[service.count_unit] += service.count_value
    # 不完整映射的总数不可用，已知小计单列；没有服务表不能解释成0人受影响。
    metrics = {k: (None if not data.services or any(
        s.service_id in unknown_counts and s.count_unit == k for s in data.services) else v)
        for k, v in counts.items()}
    status = "succeeded" if data.services and not unknown_counts else "partial"
    return envelope(
        snapshot, status, scenario=request.model_dump(mode="json"),
        source_node_ids=sorted(sources), normal_unreachable_node_ids=sorted(set(n.node_id for n in data.nodes) - set(baseline)),
        newly_unreachable_node_ids=sorted(newly), affected_service_ids=sorted(services),
        affected_critical_facility_ids=sorted(critical), potentially_disconnected_counts=metrics,
        known_count_subtotals=counts, unknown_count_service_ids=unknown_counts,
        isolated_valve_ids=plan.valve_ids, restored_edge_ids=sorted(restored or []),
        valve_overrides=states, reachable_node_ids=sorted(changed),
        normal_path_examples={k: baseline[k] for k in sorted(newly)[:100]},
        alternative_path_examples={k: changed[k] for k in sorted(changed)[:20]},
        missing_items=[] if status == "succeeded" else ["服务映射或计数不完整；只返回已知影响和节点结果"],
    )
