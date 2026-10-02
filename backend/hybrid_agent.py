"""Hybrid agent: intent router + vector search + AI_COMPLETE. Simple English."""
import hashlib
import logging
import re
import uuid
try:
    import snowflake.connector
except ImportError:
    # Browser demo (stlite / Pyodide) has no Snowflake connector. Demo mode never needs it.
    snowflake = None
from backend import config
from backend import demo
from backend import runtime

# Log module events. No warnings.warn, use logging.
# Docs: https://docs.snowflake.com/en/sql-reference/functions/ai_embed
logger = logging.getLogger(__name__)

# No module TOP_K copy here. Use config.TOP_K live via _top_k().


def demo_on() -> bool:
    """True in demo mode (fake data, no Snowflake). Never inside SiS. Simple English."""
    # Read live, so tests and the env var both work. SiS always uses real Snowflake.
    return bool(getattr(config, "DEMO_MODE", False)) and not runtime.is_sis_runtime()
MAX_INPUT_LEN = 2000
MAX_EVIDENCE_CHARS = 4000
MAX_CLAUSE_CHARS = 400
MAX_PROMPT_WORDS = 350
MAX_OFFSET = 10000

BLOCKED_WORDS = ["DROP", "DELETE", "TRUNCATE", "ALTER", "GRANT", "INSERT", "UPDATE", "UNION", "INFORMATION_SCHEMA", "XP_", "EXEC"]
# Allow common chars but still block ; -- /* etc.
ALLOW_RE = re.compile(r"^[A-Za-z0-9 \.,\?!#\$@:/\(\)_\-]+$")

# Light pool cache by role. Max 5 conns per role.
# Full pooling needs snowflake.connector.pooling.SessionPool.
# Docs: https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect
_POOL: dict = {}
_POOL_MAX = 5


def _top_k() -> int:
    """Read TOP_K live from config. Single source. Simple English."""
    try:
        return int(getattr(config, "TOP_K", 3))
    except Exception:
        return 3


def _connect_timeout() -> int:
    """Read wait cap live from config. Simple English."""
    try:
        return int(getattr(config, "SNOWFLAKE_CONNECT_TIMEOUT", 30))
    except Exception:
        return 30


def _blocked_found(text_up: str) -> str:
    """Check whole words only. Simple English."""
    # Use word bound so EXEC does not block EXECUTIVE.
    for w in BLOCKED_WORDS:
        if w in ("--", ";--", "/*"):
            if w in text_up:
                return w
            continue
        # Whole word match with \b.
        if re.search(r"\b" + re.escape(w) + r"\b", text_up):
            return w
    return ""


def norm_row(row) -> dict:
    try:
        d = dict(row)
    except Exception:
        return {}
    out = {}
    for k, v in d.items():
        try:
            nk = str(k).lower()
        except Exception:
            continue
        out[nk] = v
    return out


def norm_rows(rows) -> list:
    return [norm_row(r) for r in (rows or [])]


def _load_private_key():
    """Load DER private key bytes for key-pair JWT. None only if path not set. Simple English."""
    # Docs: https://docs.snowflake.com/en/user-guide/key-pair-auth
    path = str(getattr(config, "SNOWFLAKE_PRIVATE_KEY_PATH", "") or "").strip()
    # No path means password mode. Return None, no error.
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            pem_data = f.read()
        from cryptography.hazmat.primitives import serialization
        pw = getattr(config, "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "") or None
        pw_bytes = str(pw).encode("utf-8") if pw else None
        pkey = serialization.load_pem_private_key(pem_data, password=pw_bytes)
        der = pkey.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return der
    except Exception as e:
        # Path was set but load failed. Raise loud, do not hide.
        logger.warning("Private key load failed for %s: %r", path, e)
        raise RuntimeError(f"Bad private key at {path}: {e}. Check file and passphrase.") from e


def _new_connection():
    """Make one fresh connection. Simple English."""
    # Wait cap stops hang. Passed as login + network timeout.
    # Docs: https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-connect
    # ocsp_fail_open=True keeps OCSP (check that server cert is not revoked) ON.
    # Same as the 4.7.5 default. Connector 4.8.0 turns OCSP OFF unless this is set,
    # so being explicit stops a silent change on a future bump.
    # Docs: https://docs.snowflake.com/en/user-guide/ocsp
    timeout = _connect_timeout()
    pkey = _load_private_key()
    if pkey is not None:
        return snowflake.connector.connect(
            account=config.SNOWFLAKE_ACCOUNT,
            user=config.SNOWFLAKE_USER,
            private_key=pkey,
            warehouse=config.SNOWFLAKE_WAREHOUSE,
            database=config.SNOWFLAKE_DATABASE,
            schema=config.SNOWFLAKE_SCHEMA,
            role=config.SNOWFLAKE_ROLE,
            login_timeout=timeout,
            network_timeout=timeout,
            ocsp_fail_open=True,
        )
    logger.warning("Using password login. Set SNOWFLAKE_PRIVATE_KEY_PATH for prod key-pair auth.")
    return snowflake.connector.connect(
        account=config.SNOWFLAKE_ACCOUNT,
        user=config.SNOWFLAKE_USER,
        password=config.SNOWFLAKE_PASSWORD,
        warehouse=config.SNOWFLAKE_WAREHOUSE,
        database=config.SNOWFLAKE_DATABASE,
        schema=config.SNOWFLAKE_SCHEMA,
        role=config.SNOWFLAKE_ROLE,
        login_timeout=timeout,
        network_timeout=timeout,
        ocsp_fail_open=True,
    )


def get_connection():
    """Old name kept for compat. Uses pooled conn. Simple English."""
    # Do not use directly. Use get_pooled_connection + release_connection.
    return get_pooled_connection()


def get_pooled_connection():
    """Reuse cached conn by role, max 5. SiS: Snowflake's own session. Simple English."""
    # SiS (Streamlit in Snowflake): no secrets. Use st.connection("snowflake"),
    # owned + cached by Streamlit. Local: our own connector login (below), unchanged.
    if runtime.is_sis_runtime():
        return runtime.sis_connection()
    # Simple cache. Drop closed conns. Caller must not share across threads.
    role = str(getattr(config, "SNOWFLAKE_ROLE", "ANALYST"))
    bucket = _POOL.setdefault(role, [])
    while bucket:
        c = bucket.pop()
        try:
            if c.is_closed():
                continue
            return c
        except Exception:
            continue
    return _new_connection()


def release_connection(conn) -> None:
    """Return conn to cache or close if full. Simple English."""
    # SiS conn belongs to st.connection. Never pool or close it here.
    if runtime.is_sis_runtime():
        return
    try:
        role = str(getattr(config, "SNOWFLAKE_ROLE", "ANALYST"))
        bucket = _POOL.setdefault(role, [])
        if len(bucket) < _POOL_MAX and not conn.is_closed():
            bucket.append(conn)
            return
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def close_all() -> None:
    """Close all cached conns. Call on shutdown. Simple English."""
    for _role, bucket in list(_POOL.items()):
        while bucket:
            try:
                bucket.pop().close()
            except Exception:
                pass


def _uses_pyformat(cur) -> bool:
    """True if this cursor's connection pastes %s values client-side. Simple English."""
    # Connector default is pyformat (%s). st.connection("snowflake") sets qmark (?) style
    # (Streamlit 1.64.0 source), so SiS conns may bind on the server with ?.
    conn = getattr(cur, "connection", None)
    if conn is None:
        return True  # Test fakes / unknown: keep %s.
    flag = getattr(conn, "is_pyformat", None)
    if isinstance(flag, bool):
        return flag
    style = getattr(conn, "_paramstyle", None)
    return style in (None, "pyformat", "format")


def run_sql(cur, sql: str, params=None):
    """Run one statement with bound values on any conn style. Simple English."""
    # All app SQL is written with %s. For qmark/numeric conns, swap %s -> ?.
    # Values always go as bound params, never pasted by us. No other % signs in app SQL.
    if params is not None and not _uses_pyformat(cur):
        sql = sql.replace("%s", "?")
    if params is None:
        return cur.execute(sql)
    return cur.execute(sql, params)


def _set_timeout(cur, seconds: int) -> None:
    """Cap query time. Local only. Simple English."""
    # SiS warehouse runtime runs as an owner's-rights stored procedure, which cannot set
    # session parameters. So in SiS we skip it; set STATEMENT_TIMEOUT_IN_SECONDS on the
    # warehouse instead (see sql/02).
    # Docs: https://docs.snowflake.com/en/developer-guide/stored-procedure/stored-procedures-rights
    if runtime.is_sis_runtime():
        return
    run_sql(cur, f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(seconds)}")


def guardrail_check(text: str) -> str:
    if text is None:
        raise ValueError("Empty input.")
    t = str(text).strip()
    if len(t) == 0:
        raise ValueError("Empty input.")
    if len(t) > MAX_INPUT_LEN:
        raise ValueError(f"Too long. Max {MAX_INPUT_LEN} chars.")
    if not ALLOW_RE.match(t):
        raise ValueError("Bad chars. Use only letters, numbers, space and .,?!-#$@:/()_")
    up = t.upper()
    hit = _blocked_found(up)
    if hit:
        raise ValueError(f"Blocked word: {hit}")
    return t


def sanitize_like(text: str) -> str:
    t = str(text or "").strip()
    t = t.replace("\\", "\\\\")
    t = t.replace("%", "\\%")
    t = t.replace("_", "\\_")
    return t


# Intent words. Whole words only (\b = word edge), so "sar" in "necessary" does not count.
# Plural / -ing forms listed so "reports", "rules", "payments" still match like before.
_SAR_RE = re.compile(r"\b(?:sars?|suspicious|report(?:s|ed|ing)?)\b")
_POLICY_RE = re.compile(r"\b(?:polic(?:y|ies)|rules?|clauses?|regulations?)\b")
_TX_RE = re.compile(r"\b(?:transactions?|payments?|transfer(?:s|red|ring)?|tx)\b")


def classify_intent(text: str) -> str:
    clean = guardrail_check(text)
    low = clean.strip().lower()
    if _SAR_RE.search(low):
        return "sar_draft"
    if _POLICY_RE.search(low):
        return "policy_lookup"
    if _TX_RE.search(low):
        return "tx_review"
    return "general"


def sha256_hex(s: str) -> str:
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()


def _safe_embed_model() -> str:
    """Pick embed model from config only if in allow list."""
    m = str(getattr(config, "EMBED_MODEL", "snowflake-arctic-embed-m-v1.5"))
    allowed = getattr(config, "ALLOWED_EMBED_MODELS", ["snowflake-arctic-embed-m-v1.5"])
    # Hard stop. No fallback to bad name.
    # Real if + raise, not assert: python -O strips assert lines.
    if m not in allowed:
        raise ValueError(f"Bad embed model: {m}")
    return m


def _safe_llm_model(name: str) -> str:
    """Pick LLM only if in allow list. Stops inject. Simple English."""
    # Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-regional-availability
    m = str(name or "")
    allowed = getattr(config, "ALLOWED_LLM_MODELS", ["claude-sonnet-5", "llama3.1-8b"])
    # Hard stop. No silent swap to other model. Real raise, survives python -O.
    if m not in allowed:
        raise ValueError(f"Bad LLM model: {m}")
    return m


def _cortex_search(conn, clean: str, kk: int) -> list:
    """Ask Cortex Search Service for top law clauses. Raises on any error. Simple English."""
    # Opt-in only (config.USE_CORTEX_SEARCH). Service made by sql/04_cortex_search.sql.
    # Query text goes as JSON over REST API, not inside SQL text, so no SQL inject path.
    # Not SEARCH_PREVIEW: docs say it is for testing (slower) and takes constant args only.
    # Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/query-cortex-search-service
    from snowflake.core import Root  # Lazy import: only needed when flag is ON.
    svc = (
        Root(conn)
        .databases[config.SNOWFLAKE_DATABASE]
        .schemas[config.SNOWFLAKE_SCHEMA]
        .cortex_search_services[str(getattr(config, "CORTEX_SEARCH_SERVICE", "POLICY_SEARCH"))]
    )
    resp = svc.search(query=clean, columns=["POLICY_ID", "TITLE", "CLAUSE_TEXT"], limit=kk)
    return norm_rows(list(resp.results or [])[:kk])


def vector_search(question: str, k: int = 0) -> list:
    clean = guardrail_check(question).strip()
    # AI_EMBED replaces legacy SNOWFLAKE.CORTEX.EMBED_TEXT_768 (migration doc).
    # Unqualified name AI_EMBED, as in the docs.
    # Syntax: AI_EMBED('snowflake-arctic-embed-m-v1.5', text) returns VECTOR.
    # Docs: https://docs.snowflake.com/en/sql-reference/functions/ai_embed
    # Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-migrate-legacy-functions
    # Keep VECTOR_COSINE_SIMILARITY, do NOT swap to L2.
    model = _safe_embed_model()
    # AI_EMBED needs fixed text (literal) for model name, so bind var not allowed.
    # Safe because _safe_embed_model raises if model is not in allow list.
    if model not in getattr(config, "ALLOWED_EMBED_MODELS", ["snowflake-arctic-embed-m-v1.5"]):
        raise ValueError("Bad model")
    sql = f"""
    SELECT POLICY_ID, TITLE, CLAUSE_TEXT,
      VECTOR_COSINE_SIMILARITY(
        CLAUSE_VECTOR,
        AI_EMBED('{model}', %s)
      ) AS SIM
    FROM REGULATORY_POLICIES
    ORDER BY SIM DESC
    LIMIT {{limit}}
    """
    # Cap k at live config TOP_K single source. int() + clamp, so only a small int
    # can reach the SQL. LIMIT is written as that int (not a bind value): docs do not
    # prove LIMIT binds in qmark (server-side) mode.
    top = _top_k()
    try:
        kk = int(k) if k else top
    except (TypeError, ValueError):
        kk = top
    if kk > top:
        kk = top
    if kk < 1:
        kk = 1
    sql = sql.replace("{limit}", str(int(kk)))
    if demo_on():
        # Demo: rank the sample clauses by shared words (backend/demo.py). No DB.
        return _cut_clauses(norm_rows(demo.search(clean, kk)))
    conn = get_pooled_connection()
    try:
        rows = []
        if bool(getattr(config, "USE_CORTEX_SEARCH", False)):
            try:
                rows = _cortex_search(conn, clean, kk)
            except Exception as e:
                # Any error: log and use old cosine search. App keeps working.
                logger.warning("Cortex Search failed (%s), using cosine search", type(e).__name__)
                rows = []
        if not rows:
            cur = conn.cursor(snowflake.connector.DictCursor)
            # Query time cap 30s. Stops slow vector scan.
            # Docs: https://docs.snowflake.com/en/sql-reference/parameters
            _set_timeout(cur, 30)
            run_sql(cur, sql, (clean,))
            rows = cur.fetchall()
            cur.close()
    finally:
        release_connection(conn)
    return _cut_clauses(norm_rows(rows))


def _cut_clauses(fixed: list) -> list:
    """Cut each clause to MAX_CLAUSE_CHARS. Simple English."""
    for r in fixed:
        if "clause_text" in r and r["clause_text"]:
            s = str(r["clause_text"])
            if len(s) > MAX_CLAUSE_CHARS:
                r["clause_text"] = s[:MAX_CLAUSE_CHARS] + "...[cut]"
    return fixed


# Columns hidden from non-officers in the app (second layer next to the SQL mask policy).
MASKED_COLS = ("customer_name",)
MASK_TEXT = "***masked***"

# App-side row lock for non-officers: same rule as TRANSACTIONS_ROLE_FILTER in sql/01.
# Needed because in SiS every query runs with the owner's rights, so CURRENT_ROLE()
# policies cannot tell viewers apart. Static text only, no user input inside.
_NON_OFFICER_TX_FILTER = (
    " AND EXISTS (SELECT 1 FROM ACCOUNTS A WHERE A.ACCOUNT_ID = T.ACCOUNT_ID"
    " AND (A.RISK_TIER IS NULL OR A.RISK_TIER != 'RESTRICTED'))"
)


def mask_rows(rows, is_officer: bool) -> list:
    """Hide private columns for non-officers before display/export. Simple English."""
    out = []
    for r in (rows or []):
        d = dict(r)
        if not is_officer:
            for k in list(d.keys()):
                if str(k).lower() in MASKED_COLS:
                    d[k] = MASK_TEXT
        out.append(d)
    return out


def officer_now(local_ui_hint: bool = False) -> bool:
    """Is the current viewer an officer right now? Simple English."""
    # SiS: always asked on the server (APP_OFFICERS + fresh st.user). The UI flag is
    # ignored, so removing someone from the list takes effect on their next click.
    # Local single-user demo: the login dropdown hint (DB row/mask policies still apply).
    if runtime.is_sis_runtime():
        return viewer_is_officer(runtime.sis_viewer_name())
    return bool(local_ui_hint)


def mask_rows_for_viewer(rows, local_ui_hint: bool = False) -> list:
    """mask_rows with officer status worked out server-side (see officer_now)."""
    return mask_rows(rows, officer_now(local_ui_hint))


def fetch_transactions(account_filter: str = "", limit: int = 20, offset: int = 0,
                       local_ui_hint: bool = False) -> list:
    lim = 20
    try:
        lim = int(limit)
    except Exception:
        lim = 20
    if lim > 20:
        lim = 20
    if lim < 1:
        lim = 1
    # Cap offset to stop deep scan. Max 10000.
    try:
        off = int(offset)
    except Exception:
        off = 0
    if off < 0:
        off = 0
    if off > MAX_OFFSET:
        off = MAX_OFFSET
    filt = str(account_filter or "").strip()
    if demo_on():
        # Demo: same input guard + same RESTRICTED rule, on the sample rows. No DB.
        clean = guardrail_check(filt).strip() if filt else ""
        is_officer = bool(local_ui_hint)
        return mask_rows(norm_rows(demo.transactions(clean, lim, off, is_officer)), is_officer)
    conn = get_pooled_connection()
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        _set_timeout(cur, 30)
        # Officer status: SiS = server-side list lookup now (UI flag ignored); local = hint.
        if runtime.is_sis_runtime():
            is_officer = _is_listed_officer(cur, runtime.sis_viewer_name())
        else:
            is_officer = bool(local_ui_hint)
        # Non-officers never get RESTRICTED-tier rows (fixed text, no user input).
        lock = "" if is_officer else _NON_OFFICER_TX_FILTER
        base = "SELECT T.TX_ID, T.ACCOUNT_ID, T.AMOUNT, T.CURRENCY, T.TX_DATE, T.MERCHANT FROM TRANSACTIONS T WHERE 1=1"
        # LIMIT/OFFSET: Python ints, already clamped above, written into the SQL as ints.
        # Not bind values: docs do not prove LIMIT/OFFSET binds in qmark (server-side) mode.
        page = f" ORDER BY T.TX_DATE DESC LIMIT {int(lim)} OFFSET {int(off)}"
        if filt:
            clean = guardrail_check(filt).strip()
            safe = sanitize_like(clean)
            run_sql(
                cur,
                base + " AND T.ACCOUNT_ID LIKE %s ESCAPE '\\\\'" + lock + page,
                (f"%{safe}%",),
            )
        else:
            run_sql(cur, base + lock + page)
        rows = cur.fetchall()
        cur.close()
    finally:
        release_connection(conn)
    return mask_rows(norm_rows(rows), is_officer)


def _build_prompt(question: str, tx_rows: list, policies: list) -> str:
    ev_lines = []
    for r in (tx_rows or [])[:10]:
        tid = r.get("tx_id", "")
        aid = r.get("account_id", "")
        amt = r.get("amount", "")
        m = r.get("merchant", "")
        ev_lines.append(f"- {tid} acct {aid} amt {amt} at {m}")
    ev_text = "\n".join(ev_lines)
    if len(ev_text) > MAX_EVIDENCE_CHARS:
        ev_text = ev_text[:MAX_EVIDENCE_CHARS] + "\n...[cut for token limit]"
    pol_lines = []
    for p in (policies or [])[:_top_k()]:
        pid = p.get("policy_id", "")
        title = p.get("title", "")
        clause = str(p.get("clause_text", ""))[:MAX_CLAUSE_CHARS]
        pol_lines.append(f"- {pid} {title}: {clause}")
    pol_text = "\n".join(pol_lines)
    prompt = (
        "You are a compliance helper. Write short SAR draft.\n"
        f"Question: {question}\n"
        f"Evidence:\n{ev_text}\n"
        f"Policies:\n{pol_text}\n"
        "Rules: short, facts only, list evidence IDs, list policy IDs, no extra guess."
    )
    words = prompt.split()
    if len(words) > MAX_PROMPT_WORDS:
        prompt = " ".join(words[:MAX_PROMPT_WORDS])
    return prompt


# AI_COMPLETE replaces legacy SNOWFLAKE.CORTEX.COMPLETE (migration doc).
# Cortex Guard (harm filter) goes inside model_parameters: {'guardrails': TRUE}.
# temperature 0 = most stable answer for the same prompt (good for audit).
# Model + prompt are bound values (%s), never pasted user text.
# Docs: https://docs.snowflake.com/en/sql-reference/functions/ai_complete-single-string
AI_COMPLETE_SQL = (
    "SELECT AI_COMPLETE(model => %s, prompt => %s, "
    "model_parameters => {'guardrails': TRUE, 'temperature': 0})"
)


def _run_ai_complete(cur, model: str, prompt: str) -> str:
    """Run AI_COMPLETE with guardrails ON. No unguarded retry. Simple English."""
    # No fallback to a call without guardrails. Error goes up to _call_complete,
    # which tries the draft model (also with guardrails).
    run_sql(cur, AI_COMPLETE_SQL, (model, prompt))
    row = cur.fetchone()
    return str(row[0])


def _call_complete(prompt: str) -> tuple:
    """Call primary, else cheap draft. One close only. Simple English."""
    if demo_on():
        # Demo: fixed template, no AI model is called (backend/demo.py).
        return demo.complete(prompt), demo.DEMO_MODEL, demo.DEMO_MODEL_VERSION
    # Primary claude-sonnet-5 (1M context, needs cross-region), cheap draft llama3.1-8b.
    primary = _safe_llm_model(getattr(config, "PRIMARY_MODEL", "claude-sonnet-5"))
    draft_m = _safe_llm_model(getattr(config, "DRAFT_MODEL", getattr(config, "FALLBACK_MODEL", "llama3.1-8b")))
    conn = get_pooled_connection()
    cur = None
    try:
        cur = conn.cursor()
        # Query time cap 60s for LLM. Stops slow AI_COMPLETE hang.
        # Docs: https://docs.snowflake.com/en/sql-reference/parameters
        _set_timeout(cur, 60)
        try:
            # Try main model first.
            text = _run_ai_complete(cur, primary, prompt)
            return text, primary, getattr(config, "PRIMARY_MODEL_VERSION", primary)
        except Exception as e:
            # Cheap draft model on same connection. Guardrails still ON.
            # Common cause: cross-region inference off, so claude-sonnet-5 is not reachable.
            # Log error type only. Full text may echo the prompt (tx data).
            logger.warning("Primary model %s failed (%s), using draft %s", primary, type(e).__name__, draft_m)
            text2 = _run_ai_complete(cur, draft_m, prompt)
            return text2, draft_m, getattr(config, "FALLBACK_MODEL_VERSION", draft_m)
    finally:
        # Close once. No double close, no leak. Return conn to pool.
        try:
            if cur is not None:
                cur.close()
        except Exception:
            pass
        try:
            release_connection(conn)
        except Exception:
            pass


def build_draft(question: str, tx_rows: list, user_role: str, policies=None) -> dict:
    clean = guardrail_check(question).strip()
    intent = classify_intent(clean)
    # Search once per question. Caller may pass the clauses it already showed,
    # so the officer sees the same clauses that go into the prompt + audit row.
    if policies is None:
        policies = vector_search(clean, k=_top_k())
    prompt = _build_prompt(clean, tx_rows, policies)
    report_text, model_name, model_version = _call_complete(prompt)
    # Hash tied to model plus version. Same prompt on other model gives other hash.
    prompt_hash = sha256_hex(f"{prompt}|{model_name}|{model_version}")
    result_hash = sha256_hex(report_text)
    evidence_refs = []
    for r in (tx_rows or []):
        tid = r.get("tx_id")
        if tid:
            evidence_refs.append(str(tid))
    policy_ids = []
    for p in (policies or []):
        pid = p.get("policy_id")
        if pid:
            policy_ids.append(str(pid))
    return {
        "query_text": clean,
        "intent": intent,
        "user_role": user_role,
        "evidence_refs": evidence_refs,
        "policy_ids": policy_ids,
        "model_name": model_name,
        "model_version": model_version,
        "prompt_hash": prompt_hash,
        "result_hash": result_hash,
        "prompt": prompt,
        "report_text": report_text,
        "status": "DRAFT",
    }


def _is_officer_session(cur) -> bool:
    """LOCAL mode only: does the app's connector login hold COMPLIANCE_OFFICER? Simple English."""
    # Docs: https://docs.snowflake.com/en/sql-reference/functions/is_role_in_session
    # Local mode is a single-user demo: one person, one connector login (SNOWFLAKE_ROLE).
    # Never used in SiS: there every query runs with the app owner's rights, so a role
    # check would see the owner, not the viewer. SiS uses _is_listed_officer instead.
    try:
        run_sql(cur, "SELECT IS_ROLE_IN_SESSION('COMPLIANCE_OFFICER')")
        row = cur.fetchone()
        return bool(row[0]) if row else False
    except Exception:
        return False


def _is_listed_officer(cur, user_name: str) -> bool:
    """SiS mode: is this Snowflake user in the APP_OFFICERS allow-list? Simple English."""
    # Name is a bound value. UPPER both sides: Snowflake user names are stored upper-case
    # unless quoted. Any error = not an officer (fail closed).
    name = str(user_name or "").strip()
    if not name:
        return False
    try:
        run_sql(cur, "SELECT COUNT(*) FROM APP_OFFICERS WHERE UPPER(USER_NAME) = UPPER(%s)", (name,))
        row = cur.fetchone()
        return bool(row and row[0] and int(row[0]) > 0)
    except Exception as e:
        logger.warning("Officer list check failed (%s). Treat as not officer.", type(e).__name__)
        return False


def viewer_is_officer(user_name: str) -> bool:
    """SiS: look up the viewer in APP_OFFICERS (own conn). Local: False. Simple English."""
    if not runtime.is_sis_runtime():
        return False
    conn = get_pooled_connection()
    try:
        cur = conn.cursor()
        ok = _is_listed_officer(cur, user_name)
        cur.close()
        return ok
    finally:
        release_connection(conn)


def _log_denied_approval(cur, draft: dict, user_name: str) -> None:
    """Write APPROVE_DENIED row to AUDIT_LOGS. Never hides the denial. Simple English."""
    import json
    try:
        run_sql(
            cur,
            """
            INSERT INTO AUDIT_LOGS
            (LOG_UUID, USER_ROLE, QUERY_TEXT, INTENT, EVIDENCE_REFS, POLICY_IDS,
             MODEL_NAME, MODEL_VERSION, PROMPT_HASH, RESULT_HASH,
             REPORT_TEXT, STATUS, APPROVED_BY, APPROVED_AT)
            SELECT %s, %s, %s, %s,
              PARSE_JSON(%s), PARSE_JSON(%s),
              %s, %s, %s, %s, '', 'APPROVE_DENIED', %s, CURRENT_TIMESTAMP()
            """,
            (
                str(uuid.uuid4()),
                (draft or {}).get("user_role", ""),
                (draft or {}).get("query_text", ""),
                (draft or {}).get("intent", ""),
                json.dumps((draft or {}).get("evidence_refs", [])),
                json.dumps((draft or {}).get("policy_ids", [])),
                (draft or {}).get("model_name", ""),
                (draft or {}).get("model_version", ""),
                (draft or {}).get("prompt_hash", ""),
                (draft or {}).get("result_hash", ""),
                str(user_name or "unknown").strip()[:200],
            ),
        )
    except Exception as e:
        logger.warning("Denied-approval audit failed (%s)", type(e).__name__)


def approve_and_log(draft: dict, approved_by: str) -> str:
    """Save approved SAR with UUID. No MAX race. Simple English."""
    # Note: Snowflake has no safe RETURNING for IDs in all drivers (2026 docs).
    # So we make UUID in Python, insert it, return same value. No race.
    # QUERY_ID needs 2 steps: INSERT first, then UPDATE with cur.sfqid.
    # sfqid is known only after INSERT, so second UPDATE fills proof col.
    if not approved_by or len(str(approved_by).strip()) == 0:
        raise ValueError("Need officer approve first.")
    if not draft or draft.get("status") != "DRAFT":
        raise ValueError("Need valid DRAFT first.")
    approver = str(approved_by).strip()
    log_id_str = str(uuid.uuid4())
    if demo_on():
        return _demo_approve(draft, approver, log_id_str)
    conn = get_pooled_connection()
    qid = ""
    try:
        cur = conn.cursor()
        # Server-side gate, checked here at approve time (never trusts the UI role).
        if runtime.is_sis_runtime():
            # SiS: the approver must be the viewer st.user reports right now,
            # and that user must be in APP_OFFICERS.
            viewer = runtime.sis_viewer_name()
            ok = bool(viewer) and viewer.upper() == approver.upper() and _is_listed_officer(cur, viewer)
            reason = "Not in APP_OFFICERS list."
        else:
            # Local single-user demo: connector login must hold COMPLIANCE_OFFICER,
            # unless the demo-only switch ALLOW_LOCAL_APPROVE is TRUE (prod must be FALSE).
            ok = bool(getattr(config, "ALLOW_LOCAL_APPROVE", False)) or _is_officer_session(cur)
            reason = "Need COMPLIANCE_OFFICER role. Snowflake says no."
        if not ok:
            if runtime.is_sis_runtime():
                # Log the real viewer (st.user), plus the claimed name if it differs.
                who = viewer or "unknown"
                if approver.upper() != who.upper():
                    who = f"{who} (claimed {approver})"
            else:
                who = approver
            _log_denied_approval(cur, draft, who)
            try:
                conn.commit()
            except Exception:
                pass
            raise PermissionError(reason)
        import json
        try:
            run_sql(
                cur,
                """
                INSERT INTO AUDIT_LOGS
                (LOG_UUID, USER_ROLE, QUERY_TEXT, INTENT, EVIDENCE_REFS, POLICY_IDS,
                 MODEL_NAME, MODEL_VERSION, PROMPT_HASH, RESULT_HASH,
                 REPORT_TEXT, STATUS, APPROVED_BY, APPROVED_AT, QUERY_ID)
                SELECT %s, %s, %s, %s,
                  PARSE_JSON(%s), PARSE_JSON(%s),
                  %s, %s, %s, %s, %s, 'APPROVED', %s, CURRENT_TIMESTAMP(), %s
                """,
                (
                    log_id_str,
                    draft.get("user_role", ""),
                    draft.get("query_text", ""),
                    draft.get("intent", ""),
                    json.dumps(draft.get("evidence_refs", [])),
                    json.dumps(draft.get("policy_ids", [])),
                    draft.get("model_name", ""),
                    draft.get("model_version", ""),
                    draft.get("prompt_hash", ""),
                    draft.get("result_hash", ""),
                    draft.get("report_text", ""),
                    approver,
                    "",
                ),
            )
            try:
                qid = str(getattr(cur, "sfqid", "") or "")
            except Exception:
                qid = ""
            # Save real query ID for proof trail. Small win, no extra table scan.
            if qid:
                try:
                    run_sql(
                        cur,
                        "UPDATE AUDIT_LOGS SET QUERY_ID = %s WHERE LOG_UUID = %s",
                        (qid, log_id_str),
                    )
                except Exception:
                    pass
        except PermissionError:
            raise
        except Exception:
            # Old table without QUERY_ID col: retry without that col.
            run_sql(
                cur,
                """
                INSERT INTO AUDIT_LOGS
                (LOG_UUID, USER_ROLE, QUERY_TEXT, INTENT, EVIDENCE_REFS, POLICY_IDS,
                 MODEL_NAME, MODEL_VERSION, PROMPT_HASH, RESULT_HASH,
                 REPORT_TEXT, STATUS, APPROVED_BY, APPROVED_AT)
                SELECT %s, %s, %s, %s,
                  PARSE_JSON(%s), PARSE_JSON(%s),
                  %s, %s, %s, %s, %s, 'APPROVED', %s, CURRENT_TIMESTAMP()
                """,
                (
                    log_id_str,
                    draft.get("user_role", ""),
                    draft.get("query_text", ""),
                    draft.get("intent", ""),
                    json.dumps(draft.get("evidence_refs", [])),
                    json.dumps(draft.get("policy_ids", [])),
                    draft.get("model_name", ""),
                    draft.get("model_version", ""),
                    draft.get("prompt_hash", ""),
                    draft.get("result_hash", ""),
                    draft.get("report_text", ""),
                    approver,
                ),
            )
            try:
                qid = str(getattr(cur, "sfqid", "") or "")
            except Exception:
                qid = ""
        cur.close()
        conn.commit()
    finally:
        release_connection(conn)
    return log_id_str


DEMO_OFFICER = "officer-user"


def _demo_approve(draft: dict, approver: str, log_id_str: str) -> str:
    """Demo approve: only the demo officer login may approve. In-memory log. Simple English."""
    # Same rule shape as the real gate: checked here, not trusted from the UI button.
    row = {
        "log_uuid": log_id_str,
        "user_role": draft.get("user_role", ""),
        "query_text": draft.get("query_text", ""),
        "evidence_refs": list(draft.get("evidence_refs", [])),
        "policy_ids": list(draft.get("policy_ids", [])),
        "model_name": draft.get("model_name", ""),
        "prompt_hash": draft.get("prompt_hash", ""),
        "result_hash": draft.get("result_hash", ""),
        "approved_by": approver[:200],
    }
    if approver != DEMO_OFFICER:
        row["status"] = "APPROVE_DENIED"
        demo.DEMO_AUDIT_LOG.append(row)
        raise PermissionError("Demo: only the officer login can approve.")
    row["status"] = "APPROVED"
    demo.DEMO_AUDIT_LOG.append(row)
    return log_id_str


def generate_and_log(question: str, tx_rows: list, user_role: str, approved_by: str) -> str:
    draft = build_draft(question, tx_rows, user_role)
    return approve_and_log(draft, approved_by)
