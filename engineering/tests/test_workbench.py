"""用Streamlit自身的AppTest验证四页渲染和API交互，不依赖浏览器或真实服务。"""
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
from streamlit.testing.v1 import AppTest

from src.platform.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def test_four_pages_and_decision_submission(tmp_path, monkeypatch):
    # The workbench reads the provider fixture, so use an explicit server-side
    # test principal instead of the removed implicit anonymous demo identity.
    monkeypatch.setenv("WATER_API_TOKEN", "workbench-test-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic,fixture_replay")
    monkeypatch.setenv("WATER_LLM_MODE", "mock")
    app = create_app(tmp_path, start_worker=False, bootstrap=True)
    with TestClient(app) as client:
        def local_request(method, url, **kwargs):
            for option in ("timeout", "follow_redirects", "trust_env"):
                kwargs.pop(option, None)
            return client.request(method, url, **kwargs)
        monkeypatch.setattr(httpx, "request", local_request)
        ui = AppTest.from_file(str(ROOT / "ui/app.py"), default_timeout=30).run()
        assert not ui.exception
        for page in ("分析工作台", "方案与智能助手"):
            ui.sidebar.radio[0].set_value(page).run()
            assert not ui.exception
        next(b for b in ui.button if b.label == "直接比较候选方案").click().run()
        assert not ui.exception
        assert app.state.runtime.run_one()
        ui.run()
        assert not ui.exception
        assert app.state.store.tasks()[0]["status"] == "succeeded"
        ui.sidebar.radio[0].set_value("反馈与历史").run()
        assert not ui.exception
        sample = next(s for s in app.state.store.snapshots() if s["data_mode"] == "fixture_replay")
        ui.sidebar.selectbox[0].set_value(sample["snapshot_id"]).run()
        ui.sidebar.radio[0].set_value("分析工作台").run()
        assert not ui.exception
        assert any(m.value == "500" for m in ui.metric)
