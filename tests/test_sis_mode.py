"""Pytest for SiS mode (Streamlit in Snowflake), officer allow-list, masking, denied-approval
audit, qmark adapter and fail-closed rate limit. No real DB: fakes only. Simple English."""
import types

import pytest

from backend import config
from backend import hybrid_agent as agent
from backend import runtime


class QCursor:
    """Fake connector cursor. Knows its connection (for paramstyle). Scripted answers."""

    def __init__(self, conn):
        self.connection = conn
        self.calls = []
        self.sfqid = "01b2-fake-qid"
        self._one = None
        self.rows = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        for key, row in self.connection.answers.items():
            if key in sql:
                if isinstance(row, Exception):
                    raise row
                self._one = row
                return
        self._one = None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class QConn:
    """Fake connection. is_pyformat=False = qmark style, like st.connection('snowflake')."""

    def __init__(self, is_pyformat=False, answers=None):
        self.is_pyformat = is_pyformat
        self.answers = answers or {}
        self.cur = QCursor(self)
        self.closed = False
        self.commits = 0

    def cursor(self, *a, **k):
        return self.cur

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


@pytest.fixture
def sis(monkeypatch):
    """Turn on SiS mode with viewer JSMITH and a fake st.connection raw conn."""
    holder = {"conn": QConn(is_pyformat=False), "viewer": "JSMITH"}
    monkeypatch.setattr(runtime, "is_sis_runtime", lambda: True)
    monkeypatch.setattr(runtime, "sis_viewer_name", lambda: holder["viewer"])
    monkeypatch.setattr(runtime, "sis_connection", lambda: holder["conn"])
    return holder


def _draft():
    return {
        "status": "DRAFT", "user_role": "officer", "query_text": "q", "intent": "sar_draft",
        "evidence_refs": ["T1"], "policy_ids": ["P1"], "model_name": "claude-sonnet-5",
        "model_version": "claude-sonnet-5", "prompt_hash": "ph", "result_hash": "rh",
        "report_text": "txt",
    }


def _sqls(cur):
    return [s for s, _ in cur.calls]


# ---- runtime detection (proof: Streamlit source + SiS docs) ----

def test_local_is_not_sis(monkeypatch):
    monkeypatch.delenv("SNOWFLAKE_HOST", raising=False)
    monkeypatch.setattr(runtime, "_running_in_sis_warehouse", lambda: False)
    assert runtime.is_sis_runtime() is False


def test_container_needs_host_and_token(monkeypatch):
    monkeypatch.setattr(runtime, "_running_in_sis_warehouse", lambda: False)
    monkeypatch.setenv("SNOWFLAKE_HOST", "acct.snowflakecomputing.com")
    monkeypatch.setattr(runtime.os.path, "exists", lambda p: p == runtime.SIS_TOKEN_FILE)
    assert runtime.is_sis_runtime() is True
    monkeypatch.setattr(runtime.os.path, "exists", lambda p: False)
    assert runtime.is_sis_runtime() is False  # host alone is not enough


def test_warehouse_runtime_uses_streamlit_helper(monkeypatch):
    import streamlit.connections.util as su
    monkeypatch.delenv("SNOWFLAKE_HOST", raising=False)
    monkeypatch.setattr(su, "running_in_sis", lambda: True)
    assert runtime.is_sis_runtime() is True


def test_viewer_name_from_st_user(monkeypatch):
    import streamlit as st
    monkeypatch.setattr(st, "user", {"user_name": " JSMITH "}, raising=False)
    assert runtime.sis_viewer_name() == "JSMITH"
    monkeypatch.setattr(st, "user", {"email": "x@y"}, raising=False)
    assert runtime.sis_viewer_name() == ""


def test_sis_connection_is_st_connection_raw(monkeypatch):
    import streamlit as st
    raw = object()
    seen = {}

    def fake_connection(name):
        seen["name"] = name
        return types.SimpleNamespace(raw_connection=raw)

    monkeypatch.setattr(st, "connection", fake_connection)
    assert runtime.sis_connection() is raw
    assert seen["name"] == "snowflake"


# ---- connection + qmark adapter ----

def test_sis_pool_uses_st_connection_and_never_closes(sis):
    conn = agent.get_pooled_connection()
    assert conn is sis["conn"]
    agent.release_connection(conn)
    assert conn.closed is False


def test_run_sql_qmark_and_pyformat():
    q = QConn(is_pyformat=False)
    agent.run_sql(q.cur, "SELECT %s, %s", (1, 2))
    assert q.cur.calls[-1] == ("SELECT ?, ?", (1, 2))
    p = QConn(is_pyformat=True)
    agent.run_sql(p.cur, "SELECT %s", (1,))
    assert p.cur.calls[-1] == ("SELECT %s", (1,))
    agent.run_sql(q.cur, "SELECT 1")
    assert q.cur.calls[-1] == ("SELECT 1", None)


def test_run_sql_reads_old_paramstyle_attr():
    c = types.SimpleNamespace(connection=types.SimpleNamespace(_paramstyle="qmark"), calls=[])
    c.execute = lambda sql, params=None: c.calls.append((sql, params))
    agent.run_sql(c, "X = %s", ("v",))
    assert c.calls[-1] == ("X = ?", ("v",))


def test_sis_vector_search_qmark_no_alter_session(sis, monkeypatch):
    monkeypatch.setattr(config, "USE_CORTEX_SEARCH", False)
    sis["conn"].cur.rows = [{"POLICY_ID": "P1"}]
    out = agent.vector_search("what is SAR")
    sqls = _sqls(sis["conn"].cur)
    assert not any("ALTER SESSION" in s for s in sqls)
    assert any("AI_EMBED('snowflake-arctic-embed-m-v1.5', ?)" in s for s in sqls)
    assert sis["conn"].cur.calls[-1][1] == ("what is SAR",)
    assert f"LIMIT {config.TOP_K}" in sis["conn"].cur.calls[-1][0]
    assert out == [{"policy_id": "P1"}]


def test_sis_ai_complete_qmark_guarded(sis):
    sis["conn"].answers = {"AI_COMPLETE": ("draft",)}
    text, model, _ = agent._call_complete("hello")
    assert (text, model) == ("draft", "claude-sonnet-5")
    sql, params = [c for c in sis["conn"].cur.calls if "AI_COMPLETE" in c[0]][0]
    assert "model => ?, prompt => ?" in sql
    assert "'guardrails': TRUE" in sql
    assert params == ("claude-sonnet-5", "hello")
    assert not any("ALTER SESSION" in s for s in _sqls(sis["conn"].cur))


# ---- app-side row lock + masking ----

def test_fetch_tx_non_officer_gets_restricted_filter(sis):
    # Not in APP_OFFICERS -> filter, even if the UI hint says officer (ignored in SiS).
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    agent.fetch_transactions("", limit=5, local_ui_hint=True)
    sql, params = sis["conn"].cur.calls[-1]
    assert "RISK_TIER != 'RESTRICTED'" in sql
    assert "LIMIT 5 OFFSET 0" in sql
    assert params is None


def test_fetch_tx_officer_no_filter(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": (1,)}
    agent.fetch_transactions("", limit=5, local_ui_hint=False)
    sql, _ = sis["conn"].cur.calls[-1]
    assert "RESTRICTED" not in sql


def test_fetch_tx_filter_text_is_bound(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    agent.fetch_transactions("ACC_1", limit=5)
    sql, params = sis["conn"].cur.calls[-1]
    assert "LIKE ? ESCAPE" in sql and "ACC_1" not in sql
    assert params == ("%ACC\\_1%",)


def test_mask_rows_hides_names_for_non_officer():
    rows = [{"CUSTOMER_NAME": "Jane Doe", "tx_id": "T1"}, {"customer_name": "Bob", "tx_id": "T2"}]
    masked = agent.mask_rows(rows, is_officer=False)
    assert [r.get("CUSTOMER_NAME", r.get("customer_name")) for r in masked] == ["***masked***", "***masked***"]
    assert [r["tx_id"] for r in masked] == ["T1", "T2"]
    assert agent.mask_rows(rows, is_officer=True)[0]["CUSTOMER_NAME"] == "Jane Doe"
    assert rows[0]["CUSTOMER_NAME"] == "Jane Doe"  # input not changed


def test_fetch_tx_masks_name_column(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    sis["conn"].cur.rows = [{"TX_ID": "T1", "CUSTOMER_NAME": "Jane"}]
    out = agent.fetch_transactions("")
    assert out == [{"tx_id": "T1", "customer_name": "***masked***"}]


# ---- officer allow-list + denied-approval audit ----

def test_sis_officer_in_list_can_approve(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": (1,)}
    log_id = agent.approve_and_log(_draft(), approved_by="JSMITH")
    cur = sis["conn"].cur
    chk_sql, chk_params = [c for c in cur.calls if "APP_OFFICERS" in c[0]][0]
    assert "UPPER(USER_NAME) = UPPER(?)" in chk_sql and chk_params == ("JSMITH",)
    ins = [c for c in cur.calls if "'APPROVED'" in c[0]][0]
    assert ins[1][0] == log_id and ins[1][11] == "JSMITH"
    upd = [c for c in cur.calls if c[0].startswith("UPDATE AUDIT_LOGS SET QUERY_ID")][0]
    assert upd == ("UPDATE AUDIT_LOGS SET QUERY_ID = ? WHERE LOG_UUID = ?", ("01b2-fake-qid", log_id))
    assert not any("IS_ROLE_IN_SESSION" in s for s in _sqls(cur))


def test_sis_non_officer_denied_and_logged(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    with pytest.raises(PermissionError):
        agent.approve_and_log(_draft(), approved_by="JSMITH")
    sqls = _sqls(sis["conn"].cur)
    assert any("'APPROVE_DENIED'" in s for s in sqls)
    assert not any("'APPROVED'" in s for s in sqls)
    assert sis["conn"].commits >= 1


def test_sis_ui_role_or_name_cannot_fake_approval(sis):
    # Draft says user_role "officer" and caller passes an officer's name,
    # but st.user says the viewer is BOB -> denied before any list lookup.
    sis["viewer"] = "BOB"
    sis["conn"].answers = {"FROM APP_OFFICERS": (1,)}
    with pytest.raises(PermissionError):
        agent.approve_and_log(_draft(), approved_by="JSMITH")
    sqls = _sqls(sis["conn"].cur)
    assert any("'APPROVE_DENIED'" in s for s in sqls)
    assert not any("'APPROVED'" in s for s in sqls)


def test_sis_list_lookup_error_fails_closed(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": RuntimeError("no table")}
    with pytest.raises(PermissionError):
        agent.approve_and_log(_draft(), approved_by="JSMITH")


def test_local_role_check_denied_and_logged(monkeypatch):
    conn = QConn(is_pyformat=True, answers={"IS_ROLE_IN_SESSION": (False,)})
    monkeypatch.setattr(runtime, "is_sis_runtime", lambda: False)
    monkeypatch.setattr(agent, "get_pooled_connection", lambda: conn)
    monkeypatch.setattr(agent, "release_connection", lambda c: None)
    monkeypatch.setattr(config, "ALLOW_LOCAL_APPROVE", False)
    with pytest.raises(PermissionError):
        agent.approve_and_log(_draft(), approved_by="officer-user")
    assert any("'APPROVE_DENIED'" in s and "%s" in s for s in _sqls(conn.cur))


def test_local_role_check_ok(monkeypatch):
    conn = QConn(is_pyformat=True, answers={"IS_ROLE_IN_SESSION": (True,)})
    monkeypatch.setattr(runtime, "is_sis_runtime", lambda: False)
    monkeypatch.setattr(agent, "get_pooled_connection", lambda: conn)
    monkeypatch.setattr(agent, "release_connection", lambda c: None)
    monkeypatch.setattr(config, "ALLOW_LOCAL_APPROVE", False)
    agent.approve_and_log(_draft(), approved_by="officer-user")
    assert any("'APPROVED'" in s for s in _sqls(conn.cur))
    assert not any("APP_OFFICERS" in s for s in _sqls(conn.cur))


def test_viewer_is_officer_local_is_false(monkeypatch):
    monkeypatch.setattr(runtime, "is_sis_runtime", lambda: False)
    assert agent.viewer_is_officer("JSMITH") is False


def test_viewer_is_officer_sis_lookup(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": (1,)}
    assert agent.viewer_is_officer("JSMITH") is True
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    assert agent.viewer_is_officer("JSMITH") is False
    assert agent.viewer_is_officer("") is False


# ---- rate limit through the adapter + fails closed ----

def test_rate_limit_merge_qmark_in_sis(sis):
    import streamlit_app
    sis["conn"].answers = {"SELECT HIT_COUNT": (1,), "FROM AUDIT_LOGS": (0,)}
    assert streamlit_app.check_global_rate("JSMITH") is True
    sql, params = sis["conn"].cur.calls[0]
    assert "MERGE INTO RATE_LIMIT" in sql and "SELECT ? AS U" in sql and params == ("JSMITH",)
    audit_sql = [s for s in _sqls(sis["conn"].cur) if "FROM AUDIT_LOGS" in s][0]
    assert "APPROVED_BY = ?" in audit_sql


def test_rate_limit_fails_closed(monkeypatch):
    import streamlit_app

    def boom():
        raise RuntimeError("no db")

    monkeypatch.setattr(agent, "get_pooled_connection", boom)
    assert streamlit_app.check_global_rate("someone") is False


def test_rate_limit_blocks_over_cap(sis):
    import streamlit_app
    sis["conn"].answers = {"SELECT HIT_COUNT": (config.RATE_LIMIT_PER_HOUR + 1,)}
    assert streamlit_app.check_global_rate("JSMITH") is False


# ---- round 4: real-viewer denial log, live officer status, rate guard, ints, real connector ----

def _denied_params(cur):
    return [p for s, p in cur.calls if "'APPROVE_DENIED'" in s]


def test_denied_log_names_real_viewer_not_claimed(sis):
    # Viewer JSMITH (st.user) claims to be BOSS -> the row must name JSMITH.
    sis["viewer"] = "JSMITH"
    sis["conn"].answers = {"FROM APP_OFFICERS": (1,)}
    with pytest.raises(PermissionError):
        agent.approve_and_log(_draft(), approved_by="BOSS")
    logged = _denied_params(sis["conn"].cur)[0][-1]
    assert logged == "JSMITH (claimed BOSS)"


def test_denied_log_plain_viewer_when_names_match(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    with pytest.raises(PermissionError):
        agent.approve_and_log(_draft(), approved_by="jsmith")
    assert _denied_params(sis["conn"].cur)[0][-1] == "JSMITH"


def test_officer_removed_mid_session_loses_restricted_rows(sis):
    sis["conn"].answers = {"FROM APP_OFFICERS": (1,)}
    agent.fetch_transactions("", local_ui_hint=True)
    assert "RESTRICTED" not in sis["conn"].cur.calls[-1][0]
    # Admin removes the user from APP_OFFICERS; same session, same UI flag.
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    agent.fetch_transactions("", local_ui_hint=True)
    assert "RISK_TIER != 'RESTRICTED'" in sis["conn"].cur.calls[-1][0]


def test_mask_for_viewer_rechecks_list_in_sis(sis):
    rows = [{"customer_name": "Jane"}]
    sis["conn"].answers = {"FROM APP_OFFICERS": (1,)}
    assert agent.mask_rows_for_viewer(rows, local_ui_hint=False)[0]["customer_name"] == "Jane"
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    assert agent.mask_rows_for_viewer(rows, local_ui_hint=True)[0]["customer_name"] == "***masked***"


def test_mask_for_viewer_local_uses_hint(monkeypatch):
    monkeypatch.setattr(runtime, "is_sis_runtime", lambda: False)
    rows = [{"customer_name": "Jane"}]
    assert agent.mask_rows_for_viewer(rows, True)[0]["customer_name"] == "Jane"
    assert agent.mask_rows_for_viewer(rows, False)[0]["customer_name"] == "***masked***"


def test_rate_guard_ignores_login_failed_rows(sis):
    import streamlit_app
    sis["conn"].answers = {"SELECT HIT_COUNT": (1,), "FROM AUDIT_LOGS": (0,)}
    assert streamlit_app.check_global_rate("officer-user") is True
    audit_sql = [s for s in _sqls(sis["conn"].cur) if "FROM AUDIT_LOGS" in s][0]
    assert "STATUS IN ('APPROVED', 'APPROVE_DENIED')" in audit_sql
    assert "LOGIN_FAILED" not in audit_sql


@pytest.mark.parametrize("limit,offset", [("5; DROP TABLE X", "0 OR 1=1"), (None, None), ("abc", -7), (10**9, 10**9)])
def test_limit_offset_only_ints_reach_sql(sis, limit, offset):
    import re
    sis["conn"].answers = {"FROM APP_OFFICERS": (0,)}
    agent.fetch_transactions("", limit=limit, offset=offset)
    sql, params = sis["conn"].cur.calls[-1]
    m = re.search(r"LIMIT (\S+) OFFSET (\S+)$", sql)
    assert m and m.group(1).isdigit() and m.group(2).isdigit()
    assert 1 <= int(m.group(1)) <= 20 and 0 <= int(m.group(2)) <= agent.MAX_OFFSET
    assert "DROP" not in sql and "OR 1=1" not in sql
    assert params is None


def test_vector_search_bad_k_cannot_reach_sql(sis, monkeypatch):
    monkeypatch.setattr(config, "USE_CORTEX_SEARCH", False)
    agent.vector_search("what is SAR", k="1; DROP TABLE X")
    sql, params = sis["conn"].cur.calls[-1]
    assert f"LIMIT {config.TOP_K}" in sql and "DROP" not in sql
    assert params == ("what is SAR",)


@pytest.mark.parametrize("style,expected", [("qmark", "SELECT ?"), ("numeric", "SELECT ?"), ("pyformat", "SELECT %s"), ("format", "SELECT %s")])
def test_run_sql_real_connector_objects(style, expected):
    # Real SnowflakeConnection / DictCursor classes (no network: __new__, no __init__).
    from snowflake.connector import DictCursor
    from snowflake.connector.connection import SnowflakeConnection
    c = SnowflakeConnection.__new__(SnowflakeConnection)
    c._paramstyle = style
    cur = DictCursor.__new__(DictCursor)
    cur._connection = c
    sent = []
    cur.execute = lambda sql, params=None: sent.append((sql, params))
    assert cur.connection is c and c.is_pyformat is (style in ("pyformat", "format"))
    agent.run_sql(cur, "SELECT %s", (1,))
    assert sent == [(expected, (1,))]
