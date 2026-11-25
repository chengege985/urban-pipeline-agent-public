import pytest
from server.llm_assistant import _endpoint

@pytest.mark.parametrize("value", [
    "file:///tmp/model", "http://user:pass@localhost/v1",
    "http://127.0.0.1/v1?x=1", "http://127.0.0.1/evil",
    "http://169.254.169.254/v1", "https://example.invalid/v1",
])
def test_model_endpoint_rejects_untrusted_targets(monkeypatch, value):
    monkeypatch.delenv("COMPETITION_LLM_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ValueError):
        _endpoint(value)

def test_model_endpoint_requires_explicit_remote_allowlist(monkeypatch):
    monkeypatch.setenv("COMPETITION_LLM_ALLOWED_HOSTS", "model.internal")
    assert _endpoint("https://model.internal/v1").endswith("/chat/completions")

def test_local_model_endpoint_is_bounded(monkeypatch):
    monkeypatch.delenv("COMPETITION_LLM_ALLOWED_HOSTS", raising=False)
    assert _endpoint("http://127.0.0.1:30000/v1").endswith("/chat/completions")
