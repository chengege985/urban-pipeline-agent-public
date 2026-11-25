"""Validate business context before granting tools; never accept client authorization fields."""
from src.platform.agent_schemas import AgentChatRequest, AgentClarificationValues
from src.platform.input_batches import BatchPrincipal
from src.platform.schemas import SnapshotPayload
from src.platform.errors import InputIssues

CONTEXT_FIELDS = ("snapshot_id", "pipe_id", "batch_refs", "scenario", "decision")


def validate_context(state, snapshot_lookup, batches, values=None):
    merged = {field: state.get(field) for field in CONTEXT_FIELDS}
    merged["batch_refs"] = merged["batch_refs"] or []
    if values is not None:
        parsed = AgentClarificationValues.model_validate(values)
        merged.update(parsed.model_dump(mode="json", exclude_unset=True))
    request = AgentChatRequest.model_validate({"question": state["question"], **merged})
    principal = BatchPrincipal.model_validate(state["principal"])
    snapshot = snapshot_lookup(request.snapshot_id) if request.snapshot_id else None
    if snapshot is not None:
        if snapshot["snapshot_id"] != request.snapshot_id:
            raise PermissionError("快照与授权上下文不一致")
        data = SnapshotPayload.model_validate(snapshot["payload"])
        if principal.scope == "competition" and data.data_mode != "synthetic":
            raise PermissionError("比赛域只能使用合成快照")
        if request.scenario and request.scenario.as_of != data.as_of:
            raise ValueError("情景时点与冻结快照不一致")
        if request.pipe_id and request.pipe_id not in {a.pipe_id for a in data.assessments}:
            raise InputIssues(["管段ID不属于当前快照中的评分对象"])
    elif request.pipe_id:
        raise InputIssues(["查询管段前必须选择冻结快照"])
    for ref in request.batch_refs:
        batches.load(ref, principal)
    return {field: request.model_dump(mode="json")[field] for field in CONTEXT_FIELDS}, snapshot
