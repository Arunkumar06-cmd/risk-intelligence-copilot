"""Hybrid agent: intent router + vector search + AI_COMPLETE. Simple English."""
import hashlib
import logging
import re
import uuid
import snowflake.connector
from backend import config

# Log module events. No warnings.warn, use logging.
# Docs: https://docs.snowflake.com/en/sql-reference/functions/ai_embed
logger = logging.getLogger(__name__)

# No module TOP_K copy here. Use config.TOP_K live via _top_k().
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
    )


def get_connection():
    """Old name kept for compat. Uses pooled conn. Simple English."""
    # Do not use directly. Use get_pooled_connection + release_connection.
    return get_pooled_connection()


def get_pooled_connection():
    """Reuse cached conn by role, max 5. Simple English."""
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


def classify_intent(text: str) -> str:
    clean = guardrail_check(text)
    low = clean.strip().lower()
    if "sar" in low or "suspicious" in low or "report" in low:
        return "sar_draft"
    if "policy" in low or "rule" in low or "clause" in low or "regulation" in low:
        return "policy_lookup"
    if "transaction" in low or "payment" in low or "transfer" in low or "tx" in low:
        return "tx_review"
    return "general"


def sha256_hex(s: str) -> str:
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()


def _safe_embed_model() -> str:
    """Pick embed model from config only if in allow list."""
    m = str(getattr(config, "EMBED_MODEL", "snowflake-arctic-embed-m-v1.5"))
    allowed = getattr(config, "ALLOWED_EMBED_MODELS", ["snowflake-arctic-embed-m-v1.5"])
    # Hard stop. No fallback to bad name.
    assert m in allowed, f"Bad embed model: {m}"
    return m


def _safe_llm_model(name: str) -> str:
    """Pick LLM only if in allow list. Stops inject. Simple English."""
    # Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-regional-availability
    m = str(name or "")
    allowed = getattr(config, "ALLOWED_LLM_MODELS", ["mistral-large3", "llama3.1-8b"])
    # Hard stop. No silent swap to other model.
    assert m in allowed, f"Bad LLM model: {m}"
    return m


def vector_search(question: str, k: int = 0) -> list:
    clean = guardrail_check(question).strip()
    # New func AI_EMBED, old EMBED_TEXT_768 is legacy EOL end 2026.
    # Syntax: AI_EMBED('snowflake-arctic-embed-m-v1.5', text) returns VECTOR.
    # Docs: https://docs.snowflake.com/en/sql-reference/functions/ai_embed
    # Keep VECTOR_COSINE_SIMILARITY, do NOT swap to L2.
    model = _safe_embed_model()
    # AI_EMBED needs fixed text (literal) for model name, so bind var not allowed.
    # Safe because _safe_embed_model asserts model is in allow list first.
    assert model in getattr(config, "ALLOWED_EMBED_MODELS", ["snowflake-arctic-embed-m-v1.5"]), "Bad model"
    sql = f"""
    SELECT POLICY_ID, TITLE, CLAUSE_TEXT,
      VECTOR_COSINE_SIMILARITY(
        CLAUSE_VECTOR,
        SNOWFLAKE.CORTEX.AI_EMBED('{model}', %s)
      ) AS SIM
    FROM REGULATORY_POLICIES
    ORDER BY SIM DESC
    LIMIT %s
    """
    conn = get_pooled_connection()
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        # Query time cap 30s. Stops slow vector scan.
        # Docs: https://docs.snowflake.com/en/sql-reference/parameters
        cur.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 30")
        # Cap k at live config TOP_K single source.
        top = _top_k()
        kk = int(k) if k else top
        if kk > top:
            kk = top
        if kk < 1:
            kk = 1
        cur.execute(sql, (clean, kk))
        rows = cur.fetchall()
        cur.close()
    finally:
        release_connection(conn)
    fixed = norm_rows(rows)
    for r in fixed:
        if "clause_text" in r and r["clause_text"]:
            s = str(r["clause_text"])
            if len(s) > MAX_CLAUSE_CHARS:
                r["clause_text"] = s[:MAX_CLAUSE_CHARS] + "...[cut]"
    return fixed


def fetch_transactions(account_filter: str = "", limit: int = 20, offset: int = 0) -> list:
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
    conn = get_pooled_connection()
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 30")
        if filt:
            clean = guardrail_check(filt).strip()
            safe = sanitize_like(clean)
            cur.execute(
                "SELECT TX_ID, ACCOUNT_ID, AMOUNT, CURRENCY, TX_DATE, MERCHANT FROM TRANSACTIONS WHERE ACCOUNT_ID LIKE %s ESCAPE '\\\\' ORDER BY TX_DATE DESC LIMIT %s OFFSET %s",
                (f"%{safe}%", lim, off),
            )
        else:
            cur.execute(
                "SELECT TX_ID, ACCOUNT_ID, AMOUNT, CURRENCY, TX_DATE, MERCHANT FROM TRANSACTIONS ORDER BY TX_DATE DESC LIMIT %s OFFSET %s",
                (lim, off),
            )
        rows = cur.fetchall()
        cur.close()
    finally:
        release_connection(conn)
    return norm_rows(rows)


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


def _run_ai_complete(cur, model: str, prompt: str) -> str:
    """Run AI_COMPLETE with guardrails, fallback to plain call. Simple English."""
    # New func AI_COMPLETE, old COMPLETE is legacy EOL end 2026.
    # Named style adds guardrails and JSON mode. Fall back to positional if old region.
    # Docs: https://docs.snowflake.com/en/sql-reference/functions/ai_complete
    # Cortex Guard flag: guardrails=>TRUE turns on harm filter.
    try:
        cur.execute(
            "SELECT SNOWFLAKE.CORTEX.AI_COMPLETE(model=>%s, prompt=>%s, guardrails=>TRUE)",
            (model, prompt),
        )
        row = cur.fetchone()
        return str(row[0])
    except Exception:
        cur.execute("SELECT SNOWFLAKE.CORTEX.AI_COMPLETE(%s, %s)", (model, prompt))
        row = cur.fetchone()
        return str(row[0])


def _call_complete(prompt: str) -> tuple:
    """Call primary, else cheap draft. One close only. Simple English."""
    # Primary mistral-large3 (256K), cheap draft llama3.1-8b.
    primary = _safe_llm_model(getattr(config, "PRIMARY_MODEL", "mistral-large3"))
    draft_m = _safe_llm_model(getattr(config, "DRAFT_MODEL", getattr(config, "FALLBACK_MODEL", "llama3.1-8b")))
    conn = get_pooled_connection()
    cur = None
    try:
        cur = conn.cursor()
        # Query time cap 60s for LLM. Stops slow AI_COMPLETE hang.
        # Docs: https://docs.snowflake.com/en/sql-reference/parameters
        cur.execute("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 60")
        try:
            # Try main model first.
            text = _run_ai_complete(cur, primary, prompt)
            return text, primary, getattr(config, "PRIMARY_MODEL_VERSION", "v3-2026-256K")
        except Exception:
            # Cheap draft model on same connection.
            text2 = _run_ai_complete(cur, draft_m, prompt)
            return text2, draft_m, getattr(config, "FALLBACK_MODEL_VERSION", "llama3.1-8b-2026-draft")
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


def build_draft(question: str, tx_rows: list, user_role: str) -> dict:
    clean = guardrail_check(question).strip()
    intent = classify_intent(clean)
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
    """Check Snowflake role with given cur. Reuse cur, no new conn. Simple English."""
    # Docs: https://docs.snowflake.com/en/sql-reference/functions/is_role_in_session
    # Caller passes open cur. No new connection here.
    try:
        cur.execute("SELECT IS_ROLE_IN_SESSION('COMPLIANCE_OFFICER')")
        row = cur.fetchone()
        return bool(row[0]) if row else False
    except Exception:
        return False


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
    log_id_str = str(uuid.uuid4())
    conn = get_pooled_connection()
    qid = ""
    try:
        cur = conn.cursor()
        # Real check: only officer role can approve in prod/SiS.
        # Single source: config.ALLOW_LOCAL_APPROVE only. Prod must be FALSE.
        allow_local = bool(getattr(config, "ALLOW_LOCAL_APPROVE", False))
        if not allow_local:
            if not _is_officer_session(cur):
                raise PermissionError("Need COMPLIANCE_OFFICER role. Snowflake says no.")
        import json
        try:
            cur.execute(
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
                    str(approved_by).strip(),
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
                    cur.execute(
                        "UPDATE AUDIT_LOGS SET QUERY_ID = %s WHERE LOG_UUID = %s",
                        (qid, log_id_str),
                    )
                except Exception:
                    pass
        except PermissionError:
            raise
        except Exception:
            # Old table without QUERY_ID col: retry without that col.
            cur.execute(
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
                    str(approved_by).strip(),
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


def generate_and_log(question: str, tx_rows: list, user_role: str, approved_by: str) -> str:
    draft = build_draft(question, tx_rows, user_role)
    return approve_and_log(draft, approved_by)
