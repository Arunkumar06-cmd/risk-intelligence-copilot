"""Streamlit app with auth gate, HITL, rate limit, safe view. SiS-ready. Simple English."""
import datetime
import hmac
import json
import logging
import time
import os
import re
import uuid
import streamlit as st
from backend import config
from backend import hybrid_agent as agent

# JSON log for SIEM threat watcher. Reads this log file for alerts.
# Prod note: ship risk_copilot.log to SIEM collector.
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "msg": "%(message)s"}',
)
logger = logging.getLogger(__name__)

APP_TITLE = "Risk Intel - SAR Helper"
# Single source. No dup const. Read from agent + config.
MAX_INPUT_LEN = int(agent.MAX_INPUT_LEN)


def _evidence_limit() -> int:
    """Read tx limit live from config. No hard 5. Simple English."""
    try:
        return int(getattr(config, "EVIDENCE_TX_LIMIT", 5))
    except Exception:
        return 5


def get_sis_user() -> tuple:
    """Detect Streamlit in Snowflake user. Returns (name, is_sis). Simple English."""
    # Docs: https://docs.snowflake.com/en/developer-guide/streamlit/getting-started/overview
    # In SiS, st.user gives login name. Local run has no st.user, so use password gate.
    try:
        u = getattr(st, "user", None)
        if u is not None:
            # New API: st.user.user_name or st.user.get("user_name").
            name = ""
            try:
                name = str(getattr(u, "user_name", "") or "")
            except Exception:
                name = ""
            if not name:
                try:
                    name = str(u.get("user_name", "") or "")  # type: ignore
                except Exception:
                    name = ""
            if not name:
                try:
                    name = str(getattr(u, "name", "") or "")
                except Exception:
                    name = ""
            if name:
                return name.strip(), True
    except Exception:
        pass
    try:
        eu = getattr(st, "experimental_user", None)
        if eu is not None:
            try:
                n2 = str(eu.get("user_name", "") or "")  # type: ignore
                if n2:
                    return n2.strip(), True
            except Exception:
                pass
    except Exception:
        pass
    # Extra hint: SiS sets Snowflake env vars. Not proof, but helps log.
    if os.getenv("SNOWFLAKE_ACCOUNT"):
        pass
    return "", False


def get_snowflake_role() -> str:
    """Ask Snowflake for real role. Officer only if Snowflake says so. Simple English."""
    # Docs: https://docs.snowflake.com/en/sql-reference/functions/is_role_in_session
    # Never trust dropdown in SiS. Check CURRENT_ROLE + IS_ROLE_IN_SESSION.
    # Cache role in session_state. Save once, reuse on rerun. No extra query.
    cached = st.session_state.get("snowflake_role", "")
    if cached in ("officer", "analyst"):
        return cached
    role = "analyst"
    conn = None
    try:
        conn = agent.get_pooled_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_ROLE(), IS_ROLE_IN_SESSION('COMPLIANCE_OFFICER')")
            row = cur.fetchone()
            cur.close()
            if row and len(row) >= 2 and bool(row[1]):
                role = "officer"
        finally:
            try:
                agent.release_connection(conn)
            except Exception:
                pass
    except Exception:
        pass
    st.session_state["snowflake_role"] = role
    return role


def log_denied_login(user_name: str) -> None:
    """Write LOGIN_FAILED row to AUDIT_LOGS. Simple English."""
    # Proof for SIEM: keeps denied login attempts.
    name = str(user_name or "unknown").strip()[:200]
    log_id = str(uuid.uuid4())
    conn = None
    try:
        conn = agent.get_pooled_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO AUDIT_LOGS
                (LOG_UUID, USER_ROLE, QUERY_TEXT, INTENT, EVIDENCE_REFS, POLICY_IDS,
                 MODEL_NAME, MODEL_VERSION, PROMPT_HASH, RESULT_HASH,
                 REPORT_TEXT, STATUS, APPROVED_BY, APPROVED_AT)
                SELECT %s, %s, 'LOGIN_FAILED', 'login', PARSE_JSON('[]'), PARSE_JSON('[]'),
                 '', '', '', '', '', 'LOGIN_FAILED', %s, CURRENT_TIMESTAMP()
                """,
                (log_id, name, name),
            )
            cur.close()
            conn.commit()
        finally:
            agent.release_connection(conn)
    except Exception as e:
        logger.warning("Denied login audit failed for %s: %r", name, e)
    logger.warning("Denied login for %s", name)


def check_login() -> bool:
    if st.session_state.get("auth_ok", False):
        return True
    sis_name, is_sis = get_sis_user()
    if is_sis:
        # SiS path: trust CURRENT_USER from Snowflake, no local password.
        # Do NOT trust dropdown. Ask Snowflake for real role.
        st.title(APP_TITLE)
        st.caption(f"Running in Snowflake as {sis_name}. Using Snowflake login, no app password.")
        st.info("Role is read from Snowflake session, not dropdown.")
        if st.button("Continue as Snowflake user"):
            role = get_snowflake_role()
            st.session_state["auth_ok"] = True
            st.session_state["user_role"] = role
            st.session_state["user_name"] = sis_name.strip()
            st.session_state["auth_mode"] = "sis"
            st.rerun()
        return False
    # Local path: need APP_PASSWORD gate. Dropdown is demo only.
    st.title(APP_TITLE)
    st.write("Please log in first.")
    st.warning("Local demo only. Role dropdown is not secure. SiS uses Snowflake role.")
    # Swap to SSO later: replace this form with OAuth login button.
    role = st.selectbox("Role", ["analyst", "officer"])
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        ok, msg = config.is_app_password_ok()
        if not ok:
            st.error(msg)
            return False
        real = str(config.APP_PASSWORD or "")
        # Use compare_digest to stop timing attack.
        if pwd and hmac.compare_digest(str(pwd), real):
            st.session_state["auth_ok"] = True
            st.session_state["user_role"] = role
            st.session_state["user_name"] = f"{role}-user"
            st.session_state["auth_mode"] = "local"
            # Clear cached Snowflake role on new login.
            st.session_state.pop("snowflake_role", None)
            logger.info("Login ok for %s", role)
            st.rerun()
        else:
            # Audit log for denied login. Writes LOGIN_FAILED row.
            log_denied_login(f"{role}-user")
            st.error("Wrong password.")
            return False
    return False


def check_global_rate(user_name: str) -> bool:
    """Real global limit via RATE_LIMIT table per hour. Simple English."""
    # MERGE bumps HIT_COUNT per user per hour window. Block if > 50.
    # Docs: https://docs.snowflake.com/en/sql-reference/sql/merge
    # Cleanup: DELETE windows older than 7 days via TASK. See sql/03_rate_limit.sql.
    # Single pooled conn. MERGE + SELECT in one txn deal. One commit.
    requester = str(user_name or "unknown").strip() or "unknown"
    per_hour = int(getattr(config, "RATE_LIMIT_PER_HOUR", 50))
    conn = None
    try:
        conn = agent.get_pooled_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                MERGE INTO RATE_LIMIT t
                USING (SELECT %s AS U, DATE_TRUNC('hour', CURRENT_TIMESTAMP()) AS W) s
                ON t.USER_NAME = s.U AND t.WINDOW_START = s.W
                WHEN MATCHED THEN UPDATE SET HIT_COUNT = t.HIT_COUNT + 1
                WHEN NOT MATCHED THEN INSERT (USER_NAME, WINDOW_START, HIT_COUNT) VALUES (s.U, s.W, 1)
                """,
                (requester,),
            )
            cur.execute(
                "SELECT HIT_COUNT FROM RATE_LIMIT WHERE USER_NAME = %s AND WINDOW_START = DATE_TRUNC('hour', CURRENT_TIMESTAMP())",
                (requester,),
            )
            row = cur.fetchone()
            n = int(row[0]) if row and row[0] is not None else 0
            if n > per_hour:
                conn.commit()
                cur.close()
                st.warning(f"Global limit hit. Max {per_hour} per hour per user.")
                return False
            # Extra guard: count drafts by requester. Reuse same cur, not cur2.
            cur.execute(
                "SELECT COUNT(*) FROM AUDIT_LOGS WHERE USER_ROLE = %s AND CREATED_AT > DATEADD(hour, -1, CURRENT_TIMESTAMP())",
                (requester,),
            )
            row2 = cur.fetchone()
            cur.close()
            conn.commit()
            n2 = int(row2[0]) if row2 and row2[0] is not None else 0
            if n2 > per_hour:
                st.warning("Global audit guard hit. Too many drafts per hour.")
                return False
        finally:
            try:
                agent.release_connection(conn)
            except Exception:
                pass
    except Exception:
        pass
    return True


def check_rate() -> bool:
    # Local per-session limit: 10 per minute.
    # Note: this is demo limit only, not global. Prod needs Redis/Snowflake.
    now = time.time()
    hits = st.session_state.get("req_times", [])
    hits = [t for t in hits if now - t < 60]
    # Cap list to last 20. Stops memory leak on long session.
    hits = hits[-20:]
    if len(hits) >= 10:
        st.warning("Too many requests. Wait 1 minute. Max 10 per minute. (Demo limit only, not global.)")
        return False
    # Real global guard for saves, keyed by requester user_name.
    user_name = st.session_state.get("user_name", "unknown")
    if not check_global_rate(user_name):
        return False
    hits.append(now)
    st.session_state["req_times"] = hits[-20:]
    return True


def clean_markdown(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"<[^>]*>", "", t)
    # Prod note: bleach clean is optional for download only. Keep strip here for speed.
    return t.strip()


def build_safe_export(draft: dict, user_name: str = "") -> str:
    report = clean_markdown(draft.get("report_text", ""))
    user = str(user_name or draft.get("user_role", "")).strip()
    made = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append("# SAR Draft - Findings")
    lines.append("")
    lines.append(f"Generated: {made}")
    lines.append(f"User: {clean_markdown(user)}")
    lines.append(f"Intent: {draft.get('intent','')}")
    lines.append(f"Model: {draft.get('model_name','')} ({draft.get('model_version','')})")
    lines.append("")
    lines.append("## Findings")
    lines.append(report)
    lines.append("")
    lines.append("## Evidence IDs")
    for eid in draft.get("evidence_refs", []):
        lines.append(f"- {clean_markdown(str(eid))}")
    lines.append("")
    lines.append("## Policy cites")
    for pid in draft.get("policy_ids", []):
        lines.append(f"- {clean_markdown(str(pid))}")
    lines.append("")
    lines.append(f"Prompt hash: {draft.get('prompt_hash','')}")
    lines.append(f"Result hash: {draft.get('result_hash','')}")
    return "\n".join(lines)


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    # Fail fast if secrets missing. Clear error, not silent.
    # SiS uses st.user so no APP_PASSWORD. Local mode needs strong password.
    try:
        _, is_sis_boot = get_sis_user()
    except Exception:
        is_sis_boot = False
    try:
        config.validate_secrets(require_app_password=not is_sis_boot)
    except Exception as e:
        st.error(str(e))
        st.stop()
    if not check_login():
        st.stop()
    role = st.session_state.get("user_role", "analyst")
    user_name = st.session_state.get("user_name", "analyst-user")
    auth_mode = st.session_state.get("auth_mode", "local")
    if auth_mode == "sis":
        st.sidebar.caption(f"Snowflake user: {user_name} (SiS login, CURRENT_USER)")
    else:
        st.sidebar.write(f"Logged in as: {user_name} ({role})")
    st.sidebar.caption("Rate limit is per-session demo only. Prod needs Redis/Snowflake global limit.")
    if st.sidebar.button("Logout"):
        st.session_state["auth_ok"] = False
        st.session_state.pop("pending_draft", None)
        st.session_state.pop("snowflake_role", None)
        st.rerun()
    st.title(APP_TITLE)
    tab1, tab2 = st.tabs(["Ask + SAR draft", "Transactions"])
    with tab1:
        st.subheader("Ask question")
        q = st.text_area("Type question (letters, numbers, space .,?!-#$@:/()_ only, max 2000)", max_chars=MAX_INPUT_LEN)
        if st.button("Make draft"):
            if not check_rate():
                st.stop()
            try:
                intent = agent.classify_intent(q)
                st.write("Intent found:")
                st.text(intent)
                tx_rows = agent.fetch_transactions("", limit=_evidence_limit(), offset=0)
                policies = agent.vector_search(q, k=config.TOP_K)
                st.write("Top policies:")
                for p in policies:
                    st.text(f"{p.get('policy_id','')} - {p.get('title','')}")
                    st.text(str(p.get("clause_text",""))[:400])
                draft = agent.build_draft(q, tx_rows, role)
                st.session_state["pending_draft"] = draft
                st.write("Draft report (needs officer approve):")
                st.text(draft["report_text"])
                st.text(f"Evidence: {', '.join(draft['evidence_refs'])}")
                st.text(f"Policies: {', '.join(draft['policy_ids'])}")
            except Exception as e:
                logger.warning("Draft failed: %s", e)
                st.error(f"Error: {clean_markdown(str(e))}")
        draft = st.session_state.get("pending_draft")
        if draft:
            st.divider()
            st.write("Human check: officer must approve before save.")
            if role != "officer":
                st.info("You are analyst. Ask an officer to log in and approve.")
            else:
                if st.button("Approve and save SAR"):
                    if not check_rate():
                        st.stop()
                    try:
                        log_id = agent.approve_and_log(draft, approved_by=user_name)
                        st.success(f"Saved APPROVED SAR log {log_id} by {user_name}")
                        st.session_state.pop("pending_draft", None)
                    except Exception as e:
                        st.error(f"Approve failed: {clean_markdown(str(e))}")
            safe_md = build_safe_export(draft, user_name=user_name)
            st.download_button("Download safe export (md)", data=safe_md, file_name="sar_findings.md", mime="text/markdown")
    with tab2:
        st.subheader("Transactions (20 per page)")
        page = st.number_input("Page number", min_value=1, max_value=500, value=1, step=1)
        filt = st.text_input("Filter by account (optional)")
        if st.button("Load page"):
            if not check_rate():
                st.stop()
            try:
                # Cap page so offset stays under MAX_OFFSET (10000).
                p = min(int(page), 500)
                offset = (p - 1) * 20
                filt_clean = str(filt or "").strip()
                rows = agent.fetch_transactions(filt_clean, limit=20, offset=offset)
                if not rows:
                    st.info("No rows.")
                else:
                    st.dataframe(rows, height=400, use_container_width=True)
                    st.text(f"Page {p} - showing {len(rows)} rows")
            except Exception as e:
                st.error(f"Load failed: {clean_markdown(str(e))}")


if __name__ == "__main__":
    main()
