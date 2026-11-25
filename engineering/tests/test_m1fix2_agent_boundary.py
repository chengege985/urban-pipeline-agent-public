"""Platform integration regressions: synthetic only; no socket/model calls."""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from src.agents.providers.llm import FakeLLM
from src.agents.rag.retriever import HybridRetriever
from src.agents.state import AgentSettings
from src.platform.agent_runtime import AgentRuntime
from src.platform.agent_store import AgentStore
from src.platform.app import create_app
from src.platform.input_batches import BatchPrincipal
from src.platform.agent_schemas import AgentChatRequest
from src.platform.schemas import SnapshotPayload

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=['sqlite', 'postgres'] if os.getenv('WATER_AGENT_TEST_POSTGRES_DSN') else ['sqlite'])
def env(tmp_path, monkeypatch, request):
    for key in ('WATER_API_TOKEN', 'AGENT_ENABLED', 'AGENT_LOCAL_DEMO'):
        monkeypatch.delenv(key, raising=False)
    principals = {'a': {'actor_id': 'actor-a', 'modes': ['synthetic'], 'corpora': []},
                  'b': {'actor_id': 'actor-b', 'modes': ['synthetic'], 'corpora': []}}
    monkeypatch.setenv('WATER_PRINCIPALS', json.dumps(principals))
    admin = None
    dsn = f"sqlite:///{tmp_path / 'agent.db'}"
    if request.param == 'postgres':
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url
        admin = create_engine(os.environ['WATER_AGENT_TEST_POSTGRES_DSN'])
        schema = 'm1fix2_' + uuid.uuid4().hex
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        dsn = make_url(os.environ['WATER_AGENT_TEST_POSTGRES_DSN']).update_query_dict(
            {'options': f'-csearch_path={schema}'}).render_as_string(hide_password=False)
    runtime = AgentRuntime(AgentStore(dsn), tmp_path,
                           settings=AgentSettings(), llm=FakeLLM(), retriever=HybridRetriever([]),
                           checkpointer=MemorySaver())
    snap = runtime.snapshots.save_snapshot(SnapshotPayload.model_validate_json(
        (ROOT / 'data/synthetic/m1a_unified.json').read_text()), 'synthetic-fixture')
    app = create_app(tmp_path, bootstrap=False, start_worker=False,
                     agent_runtime=runtime, agent_start_worker=False)
    try:
        with TestClient(app) as client:
            yield client, runtime, snap['snapshot_id'], principals
    finally:
        if admin is not None:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def headers(token='a', key='chat'):
    return {'X-Water-Token': token, 'X-Water-Client': 'unified-react', 'Idempotency-Key': key}


def submit(env, *, pipe=True, key='chat'):
    client, _, sid, _ = env
    body = {'question': '查询评分', 'snapshot_id': sid}
    if pipe:
        body['pipe_id'] = 'syn-pipe-fault'
    res = client.post('/api/v1/agent/chat', json=body, headers=headers(key=key))
    assert res.status_code == 202, res.text
    return res.json()


def test_authenticated_agent_uses_backend_identity_and_returns_bound_result(env):
    client, runtime, sid, _ = env
    run = submit(env)
    assert runtime.process_once()
    poll = client.get(f"/api/v1/agent/runs/{run['run_id']}", headers=headers())
    assert poll.status_code == 200, poll.text
    body = poll.json()
    assert body['status'] == 'succeeded'
    assert body['result']['principal']['actor_id'] == 'actor-a'
    assert body['result']['run_context']['snapshot_id'] == sid
    assert body['result']['verified_facts']
    assert body['result']['question_sensitivity'] == 'unknown'


def test_headers_and_app_global_do_not_authenticate_private_agent(env, monkeypatch):
    client, runtime, _, _ = env
    principal = BatchPrincipal(actor_id='victim', scope='internal')
    run = runtime.store.enqueue(principal, {'question': 'private'}, 'private')
    monkeypatch.setenv('AGENT_LOCAL_DEMO', '1')
    client.app.state.agent_principal = principal
    res = client.get(f"/api/v1/agent/threads/{run['thread_id']}",
                     headers={'X-Agent-Actor': 'victim', 'X-Agent-Scope': 'internal'})
    assert res.status_code == 401


def test_agent_cross_principal_and_dataset_are_denied(env, monkeypatch):
    client, runtime, sid, principals = env
    run = submit(env)
    runtime.process_once()
    for suffix in ('', '/evidence'):
        assert client.get(f"/api/v1/agent/runs/{run['run_id']}{suffix}", headers=headers('b')).status_code == 403
    principals['a']['datasets'] = ['another-dataset']
    monkeypatch.setenv('WATER_PRINCIPALS', json.dumps(principals))
    assert client.get(f"/api/v1/agent/runs/{run['run_id']}/evidence", headers=headers()).status_code == 403


def test_worker_rechecks_revocation_before_any_graph_call(env, monkeypatch):
    client, runtime, _, principals = env
    run = submit(env)
    monkeypatch.setenv('WATER_PRINCIPALS', json.dumps({'b': principals['b']}))
    calls = []
    monkeypatch.setattr(runtime.graph, 'invoke', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(runtime.graph, 'stream', lambda *a, **k: calls.append(a))
    assert runtime.process_once()
    value = runtime.store.get_run(run['run_id'], BatchPrincipal(actor_id='actor-a', scope='internal'))
    assert value['status'] == 'failed' and not calls
    assert not value['result'].get('verified_facts')


def test_resume_revalidates_revision_caller_and_checkpoint(env):
    client, runtime, _, _ = env
    run = submit(env, pipe=False)
    runtime.process_once()
    parent = runtime.store.get_run(run['run_id'], BatchPrincipal(actor_id='actor-a', scope='internal'))
    assert parent['status'] == 'awaiting_input'
    body = {'interrupt_id': parent['interrupt']['interrupt_id'], 'action': 'clarify',
            'values': {'pipe_id': 'syn-pipe-fault'}, 'expected_revision': 0}
    url = f"/api/v1/agent/threads/{run['thread_id']}/resume"
    assert client.post(url, json=body, headers=headers('b', 'cross')).status_code == 403
    assert client.post(url, json={**body, 'expected_revision': 5}, headers=headers(key='stale')).status_code == 409
    resumed = client.post(url, json=body, headers=headers(key='resume'))
    assert resumed.status_code == 202, resumed.text
    runtime.process_once()
    child = client.get(f"/api/v1/agent/runs/{resumed.json()['run_id']}", headers=headers()).json()
    assert child['status'] == 'succeeded'
    assert child['execution_run_id'] == run['run_id']
    assert child['result']['run_id'] == run['run_id']
    assert child['result']['verified_facts']
    retry = client.post(url, json=body, headers=headers(key='resume-retry'))
    assert retry.status_code == 202 and retry.json()['run_id'] == child['run_id']
    assert runtime.process_once() is False


def test_resume_rejects_modified_checkpoint_before_execution(env):
    client, runtime, _, _ = env
    run = submit(env, pipe=False)
    runtime.process_once()
    parent = runtime.store.get_run(run['run_id'], BatchPrincipal(actor_id='actor-a', scope='internal'))
    cfg = {'configurable': {'thread_id': run['thread_id']}}
    runtime.graph.update_state(cfg, {'principal': {'actor_id': 'actor-b', 'scope': 'internal'}})
    res = client.post(f"/api/v1/agent/threads/{run['thread_id']}/resume", headers=headers(key='resume'),
        json={'interrupt_id': parent['interrupt']['interrupt_id'], 'action': 'clarify',
              'values': {'pipe_id': 'syn-pipe-fault'}, 'expected_revision': 0})
    assert res.status_code == 409, res.text


def test_private_execution_history_is_persistent_not_reissued(env):
    client, runtime, _, _ = env
    run = submit(env)
    runtime.process_once()
    result = client.get(f"/api/v1/agent/runs/{run['run_id']}/evidence", headers=headers())
    assert result.status_code == 200, result.text
    history = result.json()['execution_history']
    assert history['trust'] == 'platform_observed_execution'
    assert history['usable_as_live_receipt'] is False
    assert history['records'][0]['receipt']['principal_id'] == 'actor-a'
    second = AgentStore(str(runtime.store.engine.url))
    try:
        assert second.execution_history(run['run_id'], BatchPrincipal(actor_id='actor-a', scope='internal')) == history
    finally:
        second.close()
    # Actual fresh Python process reads the archive; it never reconstructs a
    # kernel ExecutionReceipt or an in-memory Ledger.
    script = ('import json,sys; from src.platform.agent_store import AgentStore; '
              'from src.platform.input_batches import BatchPrincipal; '
              's=AgentStore(sys.argv[1]); '
              'print(json.dumps(s.execution_history(sys.argv[2], '
              'BatchPrincipal(actor_id="actor-a",scope="internal")))); s.close()')
    readback = subprocess.run([sys.executable, '-B', '-c', script,
                               runtime.store.engine.url.render_as_string(hide_password=False), run['run_id']],
                              capture_output=True, text=True, check=True, timeout=30)
    assert json.loads(readback.stdout) == history


def test_new_run_cannot_reuse_previous_facts(env):
    client, runtime, sid, _ = env
    run = submit(env)
    runtime.process_once()
    next_run = client.post('/api/v1/agent/chat', headers=headers(key='unsupported'),
        json={'thread_id': run['thread_id'], 'snapshot_id': sid, 'question': '预测失效概率'}).json()
    runtime.process_once()
    result = client.get(f"/api/v1/agent/runs/{next_run['run_id']}", headers=headers()).json()['result']
    assert result['verified_facts'] == [] and result['tool_results'] == []


def decision_run(env):
    client, runtime, sid, _ = env
    snapshot = runtime.snapshots.get_snapshot(sid)
    scenario = {'snapshot_id': sid, 'as_of': snapshot['as_of'], 'fault_edge_id': 'syn-fault',
                'isolation_plan_id': 'syn-isolate', 'horizon_hours': 8}
    decision = {**scenario, 'plan_ids': ['syn-plan-repair', 'syn-plan-backup', 'syn-plan-combined'],
                'budget_fen': 500000, 'resource_capacities': {'repair_team': 2, 'water_truck': 2}}
    response = client.post('/api/v1/agent/chat', headers=headers(key='decision'), json={
        'question': '比较方案', 'snapshot_id': sid, 'scenario': scenario, 'decision': decision})
    assert response.status_code == 202, response.text
    run = response.json()
    runtime.process_once()
    parent = runtime.store.get_run(run['run_id'], BatchPrincipal(actor_id='actor-a', scope='internal'))
    assert parent['status'] == 'awaiting_approval', parent
    return parent


def test_approval_requires_current_review_permission_and_no_field_spoofing(env, monkeypatch):
    client, runtime, _, principals = env
    parent = decision_run(env)
    body = {'interrupt_id': parent['interrupt']['interrupt_id'], 'action': 'accept', 'expected_revision': 0}
    url = f"/api/v1/agent/threads/{parent['thread_id']}/resume"
    assert client.post(url, json=body, headers=headers('b', 'approve-b')).status_code == 403
    principals['a']['permissions'] = ['run', 'resume']
    monkeypatch.setenv('WATER_PRINCIPALS', json.dumps(principals))
    assert client.post(url, json=body, headers=headers(key='no-review')).status_code == 403
    principals['a']['permissions'].append('review')
    monkeypatch.setenv('WATER_PRINCIPALS', json.dumps(principals))
    res = client.post(url, json=body, headers=headers(key='approve'))
    assert res.status_code == 202, res.text
    runtime.process_once()
    child = client.get(f"/api/v1/agent/runs/{res.json()['run_id']}", headers=headers()).json()
    assert child['status'] == 'succeeded' and child['execution_run_id'] == parent['run_id']
    assert child['result']['approval_result']['action'] == 'accept'


def test_new_process_cannot_restore_live_receipts_from_checkpoint(env):
    client, runtime, _, _ = env
    parent = decision_run(env)
    # A new runtime means a new Ledger; sharing the checkpoint deliberately does
    # not share in-memory execution authority.
    second = AgentRuntime(runtime.store, runtime.root, settings=AgentSettings(), llm=FakeLLM(),
        retriever=HybridRetriever([]), checkpointer=runtime.graph.checkpointer)
    from src.platform.agent_schemas import AgentResumeRequest
    with pytest.raises(Exception, match='unsupported_cross_process_receipt_restore'):
        second.resume(parent['run_id'], AgentResumeRequest(
            interrupt_id=parent['interrupt']['interrupt_id'], action='accept'),
            BatchPrincipal(actor_id='actor-a', scope='internal'), 'restart-resume')
    assert runtime.store.get_run(parent['run_id'], BatchPrincipal(actor_id='actor-a', scope='internal'))['status'] == 'awaiting_approval'


def test_archive_tampering_fails_instead_of_creating_verified_facts(env):
    client, runtime, _, _ = env
    run = submit(env)
    runtime.process_once()
    from src.platform.agent_store import AgentExecutionEventRow
    from sqlalchemy import select
    with runtime.store._write() as db:
        row = db.scalar(select(AgentExecutionEventRow).where(AgentExecutionEventRow.job_id == run['run_id']))
        row.payload = {**row.payload, 'receipt': {**row.payload['receipt'], 'result_hash': 'a' * 64}}
    res = client.get(f"/api/v1/agent/runs/{run['run_id']}/evidence", headers=headers())
    assert res.status_code == 409, res.text


def test_client_cannot_supply_run_contract_or_model_mode(env):
    client, runtime, sid, _ = env
    for extra in ({'run_id': 'old'}, {'run_context': {'question_sensitivity': 'synthetic'}},
                  {'request_fingerprint': 'a' * 64}, {'principal': {'actor_id': 'admin'}}):
        res = client.post('/api/v1/agent/chat', json={'question': '查询评分', 'snapshot_id': sid, **extra},
                          headers=headers(key='forge'))
        assert res.status_code == 422
    with pytest.raises(RuntimeError, match='Fake'):
        AgentRuntime(runtime.store, runtime.root, settings=AgentSettings(llm_mode='openai'))


def test_clarification_resumes_in_fresh_process_before_any_tool_execution(tmp_path, monkeypatch):
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    monkeypatch.setenv('WATER_PRINCIPALS', json.dumps({'a': {
        'actor_id': 'actor-a', 'scope': 'internal', 'modes': ['synthetic']}}))
    checkpoint_path = tmp_path / 'checkpoint.db'
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    dsn = f"sqlite:///{tmp_path / 'agent.db'}"
    runtime = AgentRuntime(AgentStore(dsn), tmp_path, settings=AgentSettings(), llm=FakeLLM(),
        retriever=HybridRetriever([]), checkpointer=SqliteSaver(connection))
    principal = BatchPrincipal(actor_id='actor-a', scope='internal')
    sid = runtime.snapshots.save_snapshot(SnapshotPayload.model_validate_json(
        (ROOT / 'data/synthetic/m1a_unified.json').read_text()), 'synthetic')['snapshot_id']
    run = runtime.submit(principal, AgentChatRequest(question='查询评分', snapshot_id=sid), 'initial')
    runtime.process_once()
    parent = runtime.store.get_run(run['run_id'], principal)
    assert parent['status'] == 'awaiting_input'
    assert parent['result']['execution_receipts'] == []
    runtime.close()
    connection.close()
    script = '''import json,sys,sqlite3
from pathlib import Path
from langgraph.checkpoint.sqlite import SqliteSaver
from src.agents.providers.llm import FakeLLM
from src.agents.rag.retriever import HybridRetriever
from src.agents.state import AgentSettings
from src.platform.agent_runtime import AgentRuntime
from src.platform.agent_store import AgentStore
from src.platform.agent_schemas import AgentResumeRequest
from src.platform.input_batches import BatchPrincipal
conn=sqlite3.connect(sys.argv[2],check_same_thread=False)
r=AgentRuntime(AgentStore(sys.argv[3]),Path(sys.argv[1]),settings=AgentSettings(),llm=FakeLLM(),retriever=HybridRetriever([]),checkpointer=SqliteSaver(conn))
p=BatchPrincipal(actor_id='actor-a',scope='internal')
child=r.resume(sys.argv[4],AgentResumeRequest(interrupt_id=sys.argv[5],action='clarify',values={'pipe_id':'syn-pipe-fault'}),p,'fresh-process')
r.process_once()
print(json.dumps(r.store.get_run(child['run_id'],p),ensure_ascii=False))
r.close();conn.close()
'''
    response = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path), str(checkpoint_path),
                               dsn, parent['run_id'], parent['interrupt']['interrupt_id']],
                              capture_output=True, text=True, check=True, timeout=45)
    child = json.loads(response.stdout)
    assert child['status'] == 'succeeded'
    assert child['execution_run_id'] == parent['run_id']
    assert len(child['result']['verified_facts']) == 2

