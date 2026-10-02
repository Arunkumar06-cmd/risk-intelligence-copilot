"""Pytest for agent SQL shape, model guards, router, OCSP, Cortex Search fallback.
No real DB: fake cursor / fake connection only. Simple English."""
import importlib
import subprocess
import sys
import types
from pathlib import Path

import pytest

from backend import config
from backend import hybrid_agent as agent


class FakeCursor:
    """Records every SQL + params. Fails for models in fail_models."""

    def __init__(self, fail_models=(), rows=None):
        self.calls = []
        self.fail_models = set(fail_models)
        self.rows = rows or []
        self._one = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if params and params[0] in self.fail_models:
            raise RuntimeError("model not reachable")
        self._one = ("draft text",)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, *args, **kwargs):
        return self.cur


@pytest.fixture
def fake_pool(monkeypatch):
    """Swap the pool for a fake conn. Returns a setter for the cursor."""
    holder = {}

    def use(cur):
        holder["conn"] = FakeConn(cur)
        return cur

    monkeypatch.setattr(agent, "get_pooled_connection", lambda: holder["conn"])
    monkeypatch.setattr(agent, "release_connection", lambda conn: None)
    return use


def _ai_calls(cur):
    return [(s, p) for (s, p) in cur.calls if "AI_COMPLETE" in s]


# ---- 1. Guardrails ON in AI_COMPLETE ----

def test_ai_complete_sql_has_guardrails_in_model_parameters():
    cur = FakeCursor()
    out = agent._run_ai_complete(cur, "claude-sonnet-5", "hello")
    assert out == "draft text"
    sql, params = cur.calls[0]
    assert sql.startswith("SELECT AI_COMPLETE(")
    assert "model_parameters => {'guardrails': TRUE, 'temperature': 0}" in sql
    # Old undocumented named arg and old namespace are gone.
    assert "guardrails=>" not in sql.replace(" ", "")
    assert "SNOWFLAKE.CORTEX" not in sql
    # Model + prompt are bound values, not pasted text.
    assert params == ("claude-sonnet-5", "hello")


def test_ai_complete_no_unguarded_retry():
    # On error: raise. Never a second call without guardrails.
    cur = FakeCursor(fail_models={"claude-sonnet-5"})
    with pytest.raises(RuntimeError):
        agent._run_ai_complete(cur, "claude-sonnet-5", "hello")
    assert len(cur.calls) == 1


def test_call_complete_primary_ok(fake_pool):
    cur = fake_pool(FakeCursor())
    text, model, version = agent._call_complete("hello")
    assert (text, model, version) == ("draft text", "claude-sonnet-5", "claude-sonnet-5")
    assert [p[0] for (_, p) in _ai_calls(cur)] == ["claude-sonnet-5"]


def test_call_complete_falls_back_to_draft_with_guardrails(fake_pool):
    # Primary fails (e.g. cross-region off) -> draft model, still guarded.
    cur = fake_pool(FakeCursor(fail_models={"claude-sonnet-5"}))
    text, model, version = agent._call_complete("hello")
    assert (model, version) == ("llama3.1-8b", "llama3.1-8b")
    calls = _ai_calls(cur)
    assert [p[0] for (_, p) in calls] == ["claude-sonnet-5", "llama3.1-8b"]
    for sql, _ in calls:
        assert "'guardrails': TRUE" in sql


def test_call_complete_both_fail_raises(fake_pool):
    cur = fake_pool(FakeCursor(fail_models={"claude-sonnet-5", "llama3.1-8b"}))
    with pytest.raises(RuntimeError):
        agent._call_complete("hello")
    # Two guarded tries only. No third unguarded call.
    calls = _ai_calls(cur)
    assert len(calls) == 2
    assert all("'guardrails': TRUE" in s for s, _ in calls)


# ---- 2. Model config ----

def test_model_config_is_honest():
    assert config.PRIMARY_MODEL == "claude-sonnet-5"
    assert config.DRAFT_MODEL == "llama3.1-8b"
    assert config.ALLOWED_LLM_MODELS == ["claude-sonnet-5", "llama3.1-8b"]
    # Version label = model name. No made-up tag.
    assert config.PRIMARY_MODEL_VERSION == config.PRIMARY_MODEL
    assert config.FALLBACK_MODEL_VERSION == config.DRAFT_MODEL
    for old in config.DEPRECATED_LLM_MODELS:
        assert old not in config.ALLOWED_LLM_MODELS


# ---- 7. Allow-list guards raise ValueError (not assert) ----

@pytest.mark.parametrize("bad", ["mistral-large2", "mistral-large3", "", "x') ; DROP TABLE t; --"])
def test_bad_llm_model_raises_value_error(bad):
    with pytest.raises(ValueError):
        agent._safe_llm_model(bad)


def test_good_llm_models_pass():
    for m in ("claude-sonnet-5", "llama3.1-8b"):
        assert agent._safe_llm_model(m) == m


def test_bad_embed_model_raises_value_error(monkeypatch):
    monkeypatch.setattr(config, "EMBED_MODEL", "evil'); DROP TABLE x; --")
    with pytest.raises(ValueError):
        agent._safe_embed_model()


def test_guards_still_work_with_python_O():
    # python -O strips assert lines. Our guards must still raise.
    code = (
        "from backend import hybrid_agent as a\n"
        "try:\n"
        "    a._safe_llm_model('evil')\n"
        "except ValueError:\n"
        "    print('RAISED')\n"
    )
    repo = Path(__file__).resolve().parents[1]
    res = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True, timeout=120, cwd=repo)
    assert "RAISED" in res.stdout, res.stderr


# ---- 1b. AI_EMBED unqualified name, bound params ----

def test_vector_search_sql_uses_unqualified_ai_embed(fake_pool, monkeypatch):
    monkeypatch.setattr(config, "USE_CORTEX_SEARCH", False)
    rows = [{"POLICY_ID": "P1", "TITLE": "T", "CLAUSE_TEXT": "x" * 900, "SIM": 0.9}]
    cur = fake_pool(FakeCursor(rows=rows))
    out = agent.vector_search("what is SAR", k=99)
    sql, params = cur.calls[-1]
    assert "AI_EMBED('snowflake-arctic-embed-m-v1.5', %s)" in sql
    assert "SNOWFLAKE.CORTEX" not in sql
    # Question bound as a value; k capped at TOP_K and written as a plain int.
    assert params == ("what is SAR",)
    assert f"LIMIT {config.TOP_K}" in sql
    assert out[0]["policy_id"] == "P1"
    assert out[0]["clause_text"].endswith("...[cut]")


# ---- 12. Cortex Search flag + fallback ----

def test_cortex_search_flag_from_env_on_reload(monkeypatch):
    # Real module load: no env var -> FALSE; env "TRUE" -> TRUE.
    # Reload again at the end (env restored) so no state leaks into other tests.
    try:
        monkeypatch.delenv("USE_CORTEX_SEARCH", raising=False)
        importlib.reload(config)
        assert config.USE_CORTEX_SEARCH is False
        monkeypatch.setenv("USE_CORTEX_SEARCH", "TRUE")
        importlib.reload(config)
        assert config.USE_CORTEX_SEARCH is True
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_cortex_search_flag_off_never_called(fake_pool, monkeypatch):
    monkeypatch.setattr(config, "USE_CORTEX_SEARCH", False)

    def boom(*a, **k):
        raise AssertionError("must not call Cortex Search when flag is off")

    monkeypatch.setattr(agent, "_cortex_search", boom)
    cur = fake_pool(FakeCursor(rows=[{"POLICY_ID": "P1"}]))
    out = agent.vector_search("policy for wires")
    assert out == [{"policy_id": "P1"}]
    assert any("VECTOR_COSINE_SIMILARITY" in s for s, _ in cur.calls)


def test_cortex_search_error_falls_back_to_cosine(fake_pool, monkeypatch):
    monkeypatch.setattr(config, "USE_CORTEX_SEARCH", True)

    def boom(*a, **k):
        raise RuntimeError("service missing")

    monkeypatch.setattr(agent, "_cortex_search", boom)
    cur = fake_pool(FakeCursor(rows=[{"POLICY_ID": "COS1"}]))
    out = agent.vector_search("policy for wires")
    assert out == [{"policy_id": "COS1"}]
    assert any("VECTOR_COSINE_SIMILARITY" in s for s, _ in cur.calls)


def test_cortex_search_empty_falls_back_to_cosine(fake_pool, monkeypatch):
    monkeypatch.setattr(config, "USE_CORTEX_SEARCH", True)
    monkeypatch.setattr(agent, "_cortex_search", lambda conn, q, k: [])
    cur = fake_pool(FakeCursor(rows=[{"POLICY_ID": "COS1"}]))
    assert agent.vector_search("policy for wires") == [{"policy_id": "COS1"}]
    assert any("VECTOR_COSINE_SIMILARITY" in s for s, _ in cur.calls)


def test_cortex_search_hit_skips_sql(fake_pool, monkeypatch):
    monkeypatch.setattr(config, "USE_CORTEX_SEARCH", True)
    hits = [{"policy_id": "CS1", "title": "T", "clause_text": "y" * 900}]
    monkeypatch.setattr(agent, "_cortex_search", lambda conn, q, k: hits)
    cur = fake_pool(FakeCursor(rows=[{"POLICY_ID": "COS1"}]))
    out = agent.vector_search("policy for wires")
    assert out[0]["policy_id"] == "CS1"
    assert out[0]["clause_text"].endswith("...[cut]")
    assert cur.calls == []


def test_cortex_search_calls_python_api(monkeypatch):
    # Fake snowflake.core.Root: check db/schema/service names and search args.
    seen = {}

    class Svc:
        def search(self, **kw):
            seen["kw"] = kw
            return types.SimpleNamespace(results=[
                {"POLICY_ID": "A", "TITLE": "t", "CLAUSE_TEXT": "c"},
                {"POLICY_ID": "B", "TITLE": "t", "CLAUSE_TEXT": "c"},
                {"POLICY_ID": "C", "TITLE": "t", "CLAUSE_TEXT": "c"},
            ])

    class Named(dict):
        def __init__(self, key, child):
            super().__init__()
            self.key, self.child = key, child

        def __getitem__(self, name):
            seen.setdefault("path", []).append((self.key, name))
            return self.child

    class FakeRoot:
        def __init__(self, conn):
            seen["conn"] = conn
            svc_coll = Named("services", Svc())
            schema = types.SimpleNamespace(cortex_search_services=svc_coll)
            db = types.SimpleNamespace(schemas=Named("schemas", schema))
            self.databases = Named("databases", db)

    fake_core = types.ModuleType("snowflake.core")
    fake_core.Root = FakeRoot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake.core", fake_core)
    out = agent._cortex_search("CONN", "wire policy", 2)
    assert seen["conn"] == "CONN"
    assert seen["path"] == [
        ("databases", config.SNOWFLAKE_DATABASE),
        ("schemas", config.SNOWFLAKE_SCHEMA),
        ("services", "POLICY_SEARCH"),
    ]
    assert seen["kw"] == {"query": "wire policy", "columns": ["POLICY_ID", "TITLE", "CLAUSE_TEXT"], "limit": 2}
    assert [r["policy_id"] for r in out] == ["A", "B"]


# ---- One search per question (Law tab == prompt == audit) ----

def test_build_draft_uses_given_policies_no_second_search(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "vector_search", lambda q, k=0: calls.append(q) or [])
    monkeypatch.setattr(agent, "_call_complete", lambda prompt: ("txt", "claude-sonnet-5", "claude-sonnet-5"))
    given = [{"policy_id": "P9", "title": "T", "clause_text": "c"}]
    d = agent.build_draft("draft SAR for wires", [{"tx_id": "T1"}], "analyst", policies=given)
    assert calls == []
    assert d["policy_ids"] == ["P9"]
    assert "P9" in d["prompt"]


def test_build_draft_searches_once_when_no_policies(monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "vector_search", lambda q, k=0: calls.append(q) or [{"policy_id": "P1"}])
    monkeypatch.setattr(agent, "_call_complete", lambda prompt: ("txt", "claude-sonnet-5", "claude-sonnet-5"))
    d = agent.build_draft("draft SAR for wires", [], "analyst")
    assert calls == ["draft SAR for wires"]
    assert d["policy_ids"] == ["P1"]


# ---- 6. Router whole-word match ----

def test_classify_necessary_is_not_sar():
    assert agent.classify_intent("is it necessary to review this policy") == "policy_lookup"


def test_classify_necessary_alone_is_general():
    assert agent.classify_intent("is it necessary") == "general"


def test_classify_tx_word():
    assert agent.classify_intent("show tx history") == "tx_review"


def test_classify_tx_inside_word_is_not_tx():
    # "ntx" / "txt" are not the word tx.
    assert agent.classify_intent("open the txt file") == "general"


@pytest.mark.parametrize("q,want", [
    ("draft reports for wires", "sar_draft"),
    ("SAR-123 status", "sar_draft"),
    ("list policies and rules", "policy_lookup"),
    ("show regulations and clauses", "policy_lookup"),
    ("list payments and transfers", "tx_review"),
    ("Show policy clauses for suspicious transaction reporting", "sar_draft"),
])
def test_classify_plurals_still_match(q, want):
    assert agent.classify_intent(q) == want


# ---- 4. OCSP stays ON (explicit ocsp_fail_open=True) ----

def test_ocsp_kwarg_exists_in_installed_connector():
    conn_mod = pytest.importorskip("snowflake.connector.connection")
    assert "ocsp_fail_open" in conn_mod.DEFAULT_CONFIGURATION


def test_connect_password_mode_sets_ocsp(monkeypatch):
    seen = {}
    monkeypatch.setattr(config, "SNOWFLAKE_PRIVATE_KEY_PATH", "")
    monkeypatch.setattr(agent.snowflake.connector, "connect", lambda **kw: seen.update(kw) or "C")
    assert agent._new_connection() == "C"
    assert seen["ocsp_fail_open"] is True
    assert "password" in seen


def test_connect_key_pair_mode_sets_ocsp(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p = tmp_path / "k.pem"
    p.write_bytes(pem)
    seen = {}
    monkeypatch.setattr(config, "SNOWFLAKE_PRIVATE_KEY_PATH", str(p))
    monkeypatch.setattr(config, "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "")
    monkeypatch.setattr(agent.snowflake.connector, "connect", lambda **kw: seen.update(kw) or "C")
    assert agent._new_connection() == "C"
    assert seen["ocsp_fail_open"] is True
    assert isinstance(seen["private_key"], bytes)
    assert "password" not in seen
