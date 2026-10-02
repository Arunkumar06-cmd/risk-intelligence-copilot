"""Streamlit AppTest smoke test. No network, no DB: DB calls are swapped to raise.
Checks the login screen and the dashboard render with no exception. Simple English."""
from pathlib import Path

import pytest

from backend import config
from backend import hybrid_agent as agent

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
TEST_PW = "smoke-test-password-123"


def _no_db():
    raise RuntimeError("No DB in test")


@pytest.fixture
def app(monkeypatch):
    # Dummy secrets so validate_secrets passes. Never used to connect.
    monkeypatch.setattr(config, "SNOWFLAKE_ACCOUNT", "dummy")
    monkeypatch.setattr(config, "SNOWFLAKE_USER", "dummy")
    monkeypatch.setattr(config, "SNOWFLAKE_PASSWORD", "dummy")
    monkeypatch.setattr(config, "SNOWFLAKE_PRIVATE_KEY_PATH", "")
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PW)
    # Any DB call fails fast. Proves the screens below need no DB.
    monkeypatch.setattr(agent, "get_pooled_connection", _no_db)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def test_login_screen_renders(app):
    assert not app.exception
    assert [t.value for t in app.title] == ["Risk Intel - SAR Helper"]
    assert [b.label for b in app.button] == ["Login"]
    assert [s.label for s in app.selectbox] == ["Role"]
    assert [t.label for t in app.text_input] == ["Password"]


def test_wrong_password_shows_error_no_crash(app):
    app.text_input[0].input("wrong-password-xyz")
    app.button[0].click().run()
    assert not app.exception
    assert "Wrong password." in [e.value for e in app.error]
    auth = app.session_state["auth_ok"] if "auth_ok" in app.session_state else False
    assert auth is False


def test_good_login_renders_dashboard(app):
    app.selectbox[0].select("officer")
    app.text_input[0].input(TEST_PW)
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["auth_ok"] is True
    labels = [t.label for t in app.tabs]
    assert "💬 Workspace" in labels and "📊 Transactions" in labels
    assert [m.label for m in app.metric] == ["Evidence items", "Cited clauses", "Queries this min"]


def test_run_pipeline_searches_once_per_question(monkeypatch):
    # Law tab, prompt and audit POLICY_IDS must come from ONE search.
    import streamlit_app
    monkeypatch.setattr(agent, "get_pooled_connection", _no_db)
    calls = []
    monkeypatch.setattr(agent, "fetch_transactions", lambda *a, **k: [{"tx_id": "T1"}])
    monkeypatch.setattr(agent, "vector_search", lambda q, k=0: calls.append(q) or [{"policy_id": "P1", "clause_text": "c"}])
    monkeypatch.setattr(agent, "_call_complete", lambda prompt: ("txt", "claude-sonnet-5", "claude-sonnet-5"))
    monkeypatch.setattr(streamlit_app.st, "session_state", {})
    d = streamlit_app.run_pipeline("draft SAR for wires", "analyst")
    assert calls == ["draft SAR for wires"]
    assert d["policies"] == [{"policy_id": "P1", "clause_text": "c"}]
    assert d["policy_ids"] == ["P1"]


# ---- SiS mode (Streamlit in Snowflake): no secrets, st.user name, APP_OFFICERS role ----

def _sis_app(monkeypatch, viewer: str, officer: bool):
    from backend import runtime
    # No Snowflake secrets at all: SiS mode must not need them.
    monkeypatch.setattr(config, "SNOWFLAKE_ACCOUNT", "")
    monkeypatch.setattr(config, "SNOWFLAKE_USER", "")
    monkeypatch.setattr(config, "SNOWFLAKE_PASSWORD", "")
    monkeypatch.setattr(config, "APP_PASSWORD", "CHANGE_ME___SET_ME")
    monkeypatch.setattr(runtime, "is_sis_runtime", lambda: True)
    monkeypatch.setattr(runtime, "sis_viewer_name", lambda: viewer)
    monkeypatch.setattr(agent, "viewer_is_officer", lambda name: officer)
    monkeypatch.setattr(agent, "get_pooled_connection", _no_db)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def test_sis_mode_skips_secrets_and_has_no_dropdown(monkeypatch):
    at = _sis_app(monkeypatch, "JSMITH", officer=False)
    assert not at.exception
    assert not at.error  # no "Missing secrets" error
    assert len(at.selectbox) == 0 and len(at.text_input) == 0  # no role dropdown, no password
    assert [b.label for b in at.button] == ["Continue as Snowflake user"]


def test_sis_non_officer_gets_analyst_role(monkeypatch):
    at = _sis_app(monkeypatch, "JSMITH", officer=False)
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state["user_role"] == "analyst"
    assert at.session_state["user_name"] == "JSMITH"
    assert at.session_state["auth_mode"] == "sis"


def test_sis_listed_officer_gets_officer_role(monkeypatch):
    at = _sis_app(monkeypatch, "JSMITH", officer=True)
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state["user_role"] == "officer"


def test_sis_without_viewer_name_is_stopped(monkeypatch):
    at = _sis_app(monkeypatch, "", officer=True)
    assert not at.exception
    assert any("Cannot read your Snowflake user name" in e.value for e in at.error)
    assert len(at.button) == 0
