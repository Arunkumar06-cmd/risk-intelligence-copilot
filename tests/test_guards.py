"""Pytest for guard rails. Simple English."""
import sys
import types
# Stub snowflake (DB link) so tests run without real DB lib.
if "snowflake.connector" not in sys.modules:
    try:
        import snowflake.connector  # type: ignore
    except Exception:
        fake_conn = types.ModuleType("snowflake.connector")
        fake_conn.DictCursor = object  # type: ignore
        fake_conn.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("No DB in test"))  # type: ignore
        fake_sf = types.ModuleType("snowflake")
        fake_sf.connector = fake_conn  # type: ignore
        sys.modules["snowflake"] = fake_sf
        sys.modules["snowflake.connector"] = fake_conn
# Stub streamlit (UI frame) for config import.
if "streamlit" not in sys.modules:
    try:
        import streamlit  # type: ignore
    except Exception:
        fake_st = types.ModuleType("streamlit")
        fake_st.secrets = {}  # type: ignore
        sys.modules["streamlit"] = fake_st
import pytest
from backend.hybrid_agent import guardrail_check, classify_intent


def test_guardrail_ok_strip():
    # Spaces at ends are cut.
    assert guardrail_check("  hello SAR  ") == "hello SAR"


def test_guardrail_empty():
    # Empty raises.
    with pytest.raises(ValueError):
        guardrail_check("   ")


def test_guardrail_too_long():
    # Over 2000 chars raises.
    with pytest.raises(ValueError):
        guardrail_check("a" * 2001)


def test_guardrail_blocked_word():
    # DROP is blocked.
    with pytest.raises(ValueError):
        guardrail_check("please DROP table")


def test_guardrail_bad_chars():
    # Semicolon is bad.
    with pytest.raises(ValueError):
        guardrail_check("hello; bad")


def test_classify_sar():
    assert classify_intent("need SAR draft") == "sar_draft"


def test_classify_policy():
    # SAR words win over policy words; policy words alone give policy_lookup.
    assert classify_intent("what policy rule for SAR") == "sar_draft"
    assert classify_intent("what policy rule") == "policy_lookup"


def test_classify_tx():
    assert classify_intent("show payment transfer") == "tx_review"


def test_classify_strip():
    # Leading spaces still classify.
    assert classify_intent("   show transaction  ") == "tx_review"
