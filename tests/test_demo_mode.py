"""Demo mode (public GitHub Pages link): fake data, no Snowflake, no AI call. Simple English.
Every test swaps the DB to raise, which proves demo mode never touches Snowflake."""
from pathlib import Path

import pytest

from backend import config
from backend import demo
from backend import hybrid_agent as agent
from backend import runtime

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


def _no_db():
    raise RuntimeError("No DB in demo")


@pytest.fixture
def demo_mode(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(runtime, "is_sis_runtime", lambda: False)
    monkeypatch.setattr(agent, "get_pooled_connection", _no_db)
    monkeypatch.setattr(demo, "DEMO_AUDIT_LOG", [])


def test_demo_off_by_default_and_never_in_sis(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    assert agent.demo_on() is False
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(runtime, "is_sis_runtime", lambda: True)
    assert agent.demo_on() is False


def test_demo_search_no_db_and_capped(demo_mode):
    rows = agent.vector_search("draft SAR for wires to high-risk country", k=99)
    assert 1 <= len(rows) <= config.TOP_K
    assert rows[0]["policy_id"] == "DEMO-POL-02"
    assert all("sim" in r for r in rows)


def test_demo_search_keeps_guardrails(demo_mode):
    with pytest.raises(ValueError):
        agent.vector_search("please DROP table")


def test_demo_transactions_hide_restricted_for_analyst(demo_mode):
    analyst = agent.fetch_transactions("", limit=20, offset=0, local_ui_hint=False)
    officer = agent.fetch_transactions("", limit=20, offset=0, local_ui_hint=True)
    assert "ACC-1003" not in {r["account_id"] for r in analyst}
    assert "ACC-1003" in {r["account_id"] for r in officer}


def test_demo_transactions_filter_page_and_guard(demo_mode):
    rows = agent.fetch_transactions("ACC-1002", limit=20, offset=0, local_ui_hint=False)
    assert {r["account_id"] for r in rows} == {"ACC-1002"}
    assert len(agent.fetch_transactions("", limit="abc", offset=-5, local_ui_hint=True)) == len(demo.DEMO_TRANSACTIONS)
    assert len(agent.fetch_transactions("", limit=2, offset=0, local_ui_hint=True)) == 2
    with pytest.raises(ValueError):
        agent.fetch_transactions("x; DROP", local_ui_hint=True)


def test_demo_draft_is_template_with_real_ids(demo_mode):
    tx = agent.fetch_transactions("", limit=3, offset=0, local_ui_hint=False)
    draft = agent.build_draft("draft SAR for high-risk wires", tx, "analyst")
    assert draft["model_name"] == demo.DEMO_MODEL
    assert draft["report_text"].startswith("DEMO DRAFT (template, no AI model was called)")
    for tid in draft["evidence_refs"]:
        assert tid in draft["report_text"]
    for pid in draft["policy_ids"]:
        assert pid in draft["report_text"]


def test_demo_officer_approves_analyst_denied_and_logged(demo_mode):
    draft = agent.build_draft("draft SAR", [], "analyst")
    with pytest.raises(PermissionError):
        agent.approve_and_log(draft, approved_by="analyst-user")
    log_id = agent.approve_and_log(draft, approved_by=agent.DEMO_OFFICER)
    assert [r["status"] for r in demo.DEMO_AUDIT_LOG] == ["APPROVE_DENIED", "APPROVED"]
    assert demo.DEMO_AUDIT_LOG[1]["log_uuid"] == log_id
    assert demo.DEMO_AUDIT_LOG[0]["approved_by"] == "analyst-user"


def _demo_app(monkeypatch):
    # No secrets at all: demo must not need them.
    monkeypatch.setattr(config, "SNOWFLAKE_ACCOUNT", "")
    monkeypatch.setattr(config, "SNOWFLAKE_USER", "")
    monkeypatch.setattr(config, "SNOWFLAKE_PASSWORD", "")
    monkeypatch.setattr(config, "APP_PASSWORD", "CHANGE_ME___SET_ME")
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    return at


def test_demo_app_login_needs_no_secrets(demo_mode, monkeypatch):
    at = _demo_app(monkeypatch)
    assert not at.exception
    assert [s.label for s in at.selectbox] == ["Demo role"]
    assert [b.label for b in at.button] == ["Enter demo"]
    assert not at.text_input  # no password box in demo


def test_demo_app_officer_runs_question_end_to_end(demo_mode, monkeypatch):
    at = _demo_app(monkeypatch)
    at.selectbox[0].select("officer")
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state["auth_mode"] == "demo"
    at.chat_input[0].set_value("Draft SAR for high-velocity wires to high-risk countries").run()
    assert not at.exception
    draft = at.session_state["pending_draft"]
    assert draft["model_name"] == demo.DEMO_MODEL
    assert draft["policy_ids"] and draft["evidence_refs"]
    assert any("DEMO MODE" in w.value for w in at.warning)
