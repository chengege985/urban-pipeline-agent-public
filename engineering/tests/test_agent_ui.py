"""Agent clarification form uses mocked HTTP; no live model or database."""
import httpx
from streamlit.testing.v1 import AppTest


def test_clarification_form_submits_entity_and_structured_context(monkeypatch):
    calls = []
    pending = {"run_id": "run", "status": "awaiting_input", "phase": "clarification_required",
               "review_status": None, "review_revision": 0, "result": {},
               "interrupt": {"interrupt_id": "int", "type": "clarification", "missing": ["补管段"]}}
    def request(method, url, **kwargs):
        if url.endswith("/health"):
            body = {"enabled": True}
        elif url.endswith("/threads/thread"):
            body = {"thread_id": "thread", "runs": [pending]}
        elif url.endswith("/resume"):
            calls.append(kwargs["json"])
            body = {"status": "queued"}
        else:
            body = {"tools": []}
        return httpx.Response(200, json=body, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx, "request", request)
    app = AppTest.from_string(
        "import streamlit as st\nfrom ui.app import agent_panel\n"
        "st.session_state['agent_thread']='thread'\n"
        "agent_panel({'snapshot_id':'snap'}, {})\n", default_timeout=30).run()
    assert not app.exception
    assert app.selectbox[0].options == ["clarify", "cancel"]
    next(x for x in app.text_input if x.label.startswith("补充管段ID")).set_value("pipe-001")
    next(x for x in app.text_area if x.label.startswith("补充情景")).set_value('{"batch_refs": []}')
    next(x for x in app.button if x.label == "恢复Agent").click().run()
    assert not app.exception
    assert calls[-1]["action"] == "clarify"
    assert calls[-1]["values"] == {"pipe_id": "pipe-001", "batch_refs": []}
