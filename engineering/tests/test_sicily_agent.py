
from pathlib import Path

import pytest

from src.gas_application import sicily, sicily_agent as agent


@pytest.fixture
def context(tmp_path, monkeypatch):
    snapshot = Path(__file__).resolve().parents[1] / "outputs/research/sicily-snapshot"
    if not snapshot.is_dir():
        pytest.skip("西西里公开快照未配置")
    monkeypatch.setenv("GAS_SICILY_DATA_ROOT", str(snapshot))
    monkeypatch.setenv("GAS_SICILY_AGENT_STORE", str(tmp_path / "agent.sqlite"))
    monkeypatch.setattr(agent, "model_configuration", lambda: None)
    return dict(session_id="1" * 32, data_version=sicily.overview()["data_version"], case_id=sicily.DEFAULT)


def query(context, question, **fields):
    return agent.answer("reviewer", agent.Query(**context, question=question, **fields))


def test_scope_and_evidence(context):
    answer = query(context, "当前有哪些数据？")
    assert [r["value"] for r in answer["rows"]] == [340, 350, 19, 456, 436]
    asset = query(context, "查 254 号管段")
    assert asset["object"]["id"] == "254"
    assert {r["value"] for r in asset["rows"]} >= {"5660", "5621"}
    assert asset["file_refs"]["sf_pipelines.csv"].startswith(context["case_id"])
    next_turn = query(context, "它的流量", previous_object_id="254", previous_object_kind="pipe")
    assert next_turn["object"] == asset["object"]
    missing = next(c["id"] for c in sicily.overview()["cases"] if not c["published"])
    blank = query({**context, "case_id": missing}, "该小时有哪些发布结果？")
    assert "缺少" in blank["answer"]
    assert not blank["rows"]
    with pytest.raises(ValueError):
        query(context, "查看 999999 号管段")


def test_confirm_and_replay_are_scoped_and_idempotent(context, monkeypatch):
    backend = sicily.run
    calls = []
    def tracked(case, edge, version, *, solve_hydraulics):
        assert not solve_hydraulics
        calls.append(edge)
        return backend(case, edge, version, solve_hydraulics=False)
    monkeypatch.setattr(sicily, "run", tracked)
    request = "按新增断连节点数优先，比较 342 和 254 号管段退出"
    answer = query(context, request)
    assert not calls
    confirm = agent.Confirmation(**context, plan_id=answer["plan"]["id"])
    for invalid in ({"case_id": "20260108-18"}, {"session_id": "2" * 32}, {"selected_id": "254"}):
        with pytest.raises(agent.Conflict):
            agent.confirm("reviewer", confirm.model_copy(update=invalid))
    with pytest.raises(agent.Conflict):
        agent.confirm("another-user", confirm)
    assert not calls
    result = agent.confirm("reviewer", confirm)
    assert calls == ["342", "254"]
    assert "342" in result["answer"]
    assert ["0 个" in result["rows"][0]["value"], "24 个" in result["rows"][1]["value"]] == [True, True]
    assert agent.confirm("reviewer", confirm) == result
    assert calls == ["342", "254"]
    ref = result["runs"][1]
    bounded = agent.Context(**context)
    run = agent.read_run("reviewer", bounded, ref["execution_id"])
    assert len(run["disconnected_branch_edges"]) == 23
    with pytest.raises(agent.Conflict):
        agent.read_run("another-user", bounded, ref["execution_id"])
    explanation = query(context, "解释这次结果", current_execution_id=ref["execution_id"])
    assert "24 个" in explanation["answer"]
    cached = query(context, request)
    assert cached["runs"] == result["runs"] and "plan" not in cached
    assert calls == ["342", "254"]
    assert "plan" not in query(context, "比较 2025-11-15 12:00 的 342 和 2026-01-08 18:00 的 254")
    assert "plan" not in query(context, "下发关阀指令，执行 254 号管段退出")


def test_model_suggestion_cannot_choose_an_unmentioned_pipe(context, monkeypatch):
    class Hallucinated:
        def complete(self, *_):
            return {"tool": "outage", "edges": ["342"]}
    monkeypatch.setattr(agent, "model_configuration", Hallucinated)
    answer = query(context, "执行管段 254 退出")
    assert answer["plan"]["edges"] == ["254"]
    assert answer["model_status"] == "unavailable"


def test_model_is_used_to_route_valid_intent(context, monkeypatch):
    class Grounded:
        def complete(self, *_):
            return {"tool": "asset", "object_kind": "pipe", "object_id": "254"}
    monkeypatch.setattr(agent, "model_configuration", Grounded)
    answer = query(context, "帮我找 254")
    assert answer["model_status"] == "ready"
    assert answer["object"]["id"] == "254"


def test_confirm_is_serialized_across_threads(context):
    from concurrent.futures import ThreadPoolExecutor

    plan = query(context, "执行 254 号管段退出")["plan"]
    confirmation = agent.Confirmation(**context, plan_id=plan["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.map(lambda _: agent.confirm("reviewer", confirmation), range(2))
    assert left == right
    with agent.database() as conn:
        assert conn.execute("SELECT count(*) FROM sicily_runs").fetchone() == (1,)


def test_expired_confirmation_cannot_start_calculation(context, monkeypatch):
    plan = query(context, "执行 254 号管段退出")["plan"]
    with agent.database() as conn:
        conn.execute("UPDATE sicily_plans SET expires=0 WHERE id=?", (plan["id"],))
    monkeypatch.setattr(sicily, "run", lambda *a, **k: pytest.fail("expired plan reached solver"))
    with pytest.raises(agent.Conflict, match="过期"):
        agent.confirm("reviewer", agent.Confirmation(**context, plan_id=plan["id"]))
