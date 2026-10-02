"""Streamlit app with auth gate, HITL, rate limit, safe view. SiS-ready. Simple English."""
import datetime
import hmac
import logging
import time
import re
import uuid
import streamlit as st
from backend import config
from backend import demo
from backend import hybrid_agent as agent
from backend import runtime

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

# Static CSS only. No user data inside. Keeps XSS fix (web attack) intact:
# user-derived text is rendered via st.text / st.markdown after clean_markdown
# (tag strip), never inside unsafe HTML. Badge labels below pass through
# clean_markdown first, so <...> can never reach the browser as markup.
_CSS = """
<style>
    .block-container { padding-top: 1.5rem !important; padding-bottom: 2rem !important; max-width: 98% !important; }
    div[data-testid="stVerticalBlockBorderWrapper"] { border: 1px solid rgba(255,255,255,0.08) !important; border-radius: 10px !important; background-color: #0E131F !important; box-shadow: 0 4px 20px rgba(0,0,0,0.25) !important; }
    div[data-testid="stMetricValue"] { font-size: 1.5rem !important; font-weight: 700 !important; letter-spacing: -0.5px; }
    .stCodeBlock { border-radius: 8px !important; }
    .pill { display: inline-block; padding: 3px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; border: 1px solid; }
    .pill-high { background: rgba(239,68,68,0.15); color: #FCA5A5; border-color: rgba(239,68,68,0.35); }
    .pill-med { background: rgba(245,158,11,0.15); color: #FCD34D; border-color: rgba(245,158,11,0.35); }
    .pill-low { background: rgba(59,130,246,0.15); color: #93C5FD; border-color: rgba(59,130,246,0.35); }
    .pill-idle { background: rgba(148,163,184,0.12); color: #CBD5E1; border-color: rgba(148,163,184,0.30); }
    .pill-ok { background: rgba(34,197,94,0.15); color: #86EFAC; border-color: rgba(34,197,94,0.35); }
</style>
"""

# Quick-pill prompts. Static strings only (no user data).
QUICK_PILLS = [
    "Draft SAR for high-velocity wires to high-risk countries",
    "Show policy clauses for suspicious transaction reporting",
]


def _badge(label: str, level: str) -> None:
    """Static HTML shell + sanitized label. Tag strip stops markup inject."""
    safe = clean_markdown(label)[:80]
    cls = {"high": "pill-high", "med": "pill-med", "low": "pill-low", "ok": "pill-ok"}.get(level, "pill-idle")
    st.markdown(f"<span class='pill {cls}'>{safe}</span>", unsafe_allow_html=True)


def _case_signal() -> tuple:
    """Live case facts from session draft. (level, evidence n, policy n)."""
    d = st.session_state.get("pending_draft") or {}
    ev = d.get("evidence_refs", []) or []
    pol = d.get("policy_ids", []) or []
    if not d:
        return ("idle", 0, 0)
    if len(ev) >= 5:
        return ("high", len(ev), len(pol))
    if len(ev) >= 1:
        return ("med", len(ev), len(pol))
    return ("low", 0, len(pol))


def _evidence_limit() -> int:
    """Read tx limit live from config. No hard 5. Simple English."""
    try:
        return int(getattr(config, "EVIDENCE_TX_LIMIT", 5))
    except Exception:
        return 5


def get_sis_user() -> tuple:
    """Detect Streamlit in Snowflake + viewer name. Returns (name, is_sis). Simple English."""
    # SiS is detected from the runtime (stored procedure or SPCS container), not from
    # st.user alone, so a local login provider cannot fake SiS mode. See backend/runtime.py.
    # In SiS, st.user.user_name = the viewer's Snowflake user name (docs: personalization).
    if not runtime.is_sis_runtime():
        return "", False
    return runtime.sis_viewer_name(), True


def get_viewer_role(user_name: str) -> str:
    """SiS: officer only if the viewer is in APP_OFFICERS. Simple English."""
    # Shows/hides the Approve button and the masking. approve_and_log checks again
    # on the server at approve time, so this UI value cannot grant approval by itself.
    try:
        return "officer" if agent.viewer_is_officer(user_name) else "analyst"
    except Exception:
        return "analyst"


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
            agent.run_sql(
                cur,
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
    if agent.demo_on():
        # Demo path (public link): fake data, no password, no Snowflake.
        # Pick a role to try both sides of the officer sign-off.
        st.title(APP_TITLE)
        st.info("DEMO MODE: fake sample data, template SAR (no AI call), nothing is sent to Snowflake. "
                "Pick a role to try the flow. The real app uses a password or Snowflake login.")
        role = st.selectbox("Demo role", ["analyst", "officer"])
        if st.button("Enter demo"):
            st.session_state["auth_ok"] = True
            st.session_state["user_role"] = role
            st.session_state["user_name"] = f"{role}-user"
            st.session_state["auth_mode"] = "demo"
            st.rerun()
        return False
    sis_name, is_sis = get_sis_user()
    if is_sis:
        # SiS path: name from st.user, no local password, no dropdown.
        # Role = viewer in APP_OFFICERS list or not (checked again at approve time).
        st.title(APP_TITLE)
        if not sis_name:
            # Fail closed: no viewer name means no identity, so no access.
            st.error("Cannot read your Snowflake user name (st.user). Access stopped.")
            return False
        st.caption(f"Running in Snowflake as {clean_markdown(sis_name)}. Using Snowflake login, no app password.")
        st.info("Officer role comes from the APP_OFFICERS list, not a dropdown.")
        if st.button("Continue as Snowflake user"):
            role = get_viewer_role(sis_name)
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
            logger.info("Login ok for %s", role)
            st.rerun()
        else:
            # Audit log for denied login. Writes LOGIN_FAILED row.
            log_denied_login(f"{role}-user")
            st.error("Wrong password.")
            return False
    return False


def check_global_rate(user_name: str) -> bool:
    """Real global limit via RATE_LIMIT table per hour. Fails closed. Simple English."""
    # MERGE bumps HIT_COUNT per user per hour window. Block if > 50.
    # Docs: https://docs.snowflake.com/en/sql-reference/sql/merge
    # Cleanup: DELETE windows older than 7 days via TASK. See sql/03_rate_limit.sql.
    # Single pooled conn. MERGE + SELECT in one txn deal. One commit.
    # Fail closed: if the check itself breaks (e.g. sql/03 not run), block the request.
    if agent.demo_on():
        # Demo has no RATE_LIMIT table. The per-session 10/min cap (check_rate) still applies.
        return True
    requester = str(user_name or "unknown").strip() or "unknown"
    per_hour = int(getattr(config, "RATE_LIMIT_PER_HOUR", 50))
    conn = None
    try:
        conn = agent.get_pooled_connection()
        try:
            cur = conn.cursor()
            agent.run_sql(
                cur,
                """
                MERGE INTO RATE_LIMIT t
                USING (SELECT %s AS U, DATE_TRUNC('hour', CURRENT_TIMESTAMP()) AS W) s
                ON t.USER_NAME = s.U AND t.WINDOW_START = s.W
                WHEN MATCHED THEN UPDATE SET HIT_COUNT = t.HIT_COUNT + 1
                WHEN NOT MATCHED THEN INSERT (USER_NAME, WINDOW_START, HIT_COUNT) VALUES (s.U, s.W, 1)
                """,
                (requester,),
            )
            agent.run_sql(
                cur,
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
            # Extra guard: approval tries (APPROVED + APPROVE_DENIED) by this user in the last hour.
            # LOGIN_FAILED rows are NOT counted: anyone can write those with a wrong password,
            # so counting them would let a stranger lock out a real officer.
            agent.run_sql(
                cur,
                "SELECT COUNT(*) FROM AUDIT_LOGS WHERE APPROVED_BY = %s AND STATUS IN ('APPROVED', 'APPROVE_DENIED') AND CREATED_AT > DATEADD(hour, -1, CURRENT_TIMESTAMP())",
                (requester,),
            )
            row2 = cur.fetchone()
            cur.close()
            conn.commit()
            n2 = int(row2[0]) if row2 and row2[0] is not None else 0
            if n2 > per_hour:
                st.warning("Global audit guard hit. Too many audit rows per hour.")
                return False
        finally:
            try:
                agent.release_connection(conn)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Rate-limit check failed (%s). Blocking request.", type(e).__name__)
        st.warning("Rate-limit check failed, so the request was blocked. Run sql/03_rate_limit.sql and check the connection.")
        return False
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
    made = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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


def run_pipeline(q: str, role: str) -> dict:
    """One real pipeline: intent + signals + law + draft. Raises on error."""
    intent = agent.classify_intent(q)
    # Non-officers never get RESTRICTED-tier rows; private columns come back masked.
    # In SiS the backend checks APP_OFFICERS itself and ignores this local hint.
    tx_rows = agent.fetch_transactions("", limit=_evidence_limit(), offset=0,
                                       local_ui_hint=(role == "officer"))
    policies = agent.vector_search(q, k=config.TOP_K)
    # Pass the same clauses in: one search per question, so Law tab == prompt == audit POLICY_IDS.
    draft = agent.build_draft(q, tx_rows, role, policies=policies)
    draft["intent"] = intent
    draft["policies"] = policies
    draft["tx_rows"] = tx_rows
    st.session_state["pending_draft"] = draft
    turns = st.session_state.get("turns", [])
    turns.append({"q": q, "intent": intent,
                  "ev": len(draft.get("evidence_refs", [])),
                  "pol": len(draft.get("policy_ids", []))})
    st.session_state["turns"] = turns[-20:]
    return draft


def render_sidebar(user_name: str, role: str, auth_mode: str) -> None:
    """Quiet session rail: who, what role, what engine, what budget."""
    with st.sidebar:
        st.markdown("### 🛡️ Risk Intel")
        st.caption("Snowflake Cortex AI • GCC Edition")
        _badge(f"{role.upper()} • {auth_mode}", "ok" if auth_mode == "sis" else ("med" if auth_mode == "demo" else "low"))
        st.caption(f"User: {clean_markdown(user_name)[:60]}")
        st.divider()
        st.caption("**Engine**")
        if auth_mode == "demo":
            st.caption("DEMO: sample data + template SAR. No AI model, no Snowflake.")
            st.caption(f"Real app: `{config.PRIMARY_MODEL}` (fallback `{config.DRAFT_MODEL}`), guardrails ON")
            log = list(demo.DEMO_AUDIT_LOG)
            st.caption(f"**Demo audit log (in memory):** {len(log)} rows")
            for r in log[-5:][::-1]:
                st.caption(f"{clean_markdown(r.get('status', ''))} • {clean_markdown(r.get('approved_by', ''))} • {clean_markdown(r.get('log_uuid', ''))[:8]}")
        else:
            st.caption(f"LLM: `{config.PRIMARY_MODEL}` (drafts: `{config.DRAFT_MODEL}`)")
            st.caption(f"Embed: `{config.EMBED_MODEL}` • guardrails ON")
        st.divider()
        used_min = len([t for t in st.session_state.get("req_times", []) if time.time() - t < 60])
        st.caption(f"**Budget:** {used_min}/10 per min • {config.RATE_LIMIT_PER_HOUR}/h global")
        st.caption("Per-session cap is demo only. Prod: Redis/Snowflake.")
        if st.button("Logout"):
            for k in ("auth_ok", "pending_draft", "turns", "req_times"):
                st.session_state.pop(k, None)
            st.rerun()


def render_kpi_strip() -> None:
    """Top-fold status cards. Live session values only, never faked."""
    level, ev_n, pol_n = _case_signal()
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        with st.container(border=True):
            st.caption("CASE RISK (rule: ≥5 signals = HIGH)")
            if level == "idle":
                _badge("NO CASE YET", "idle")
            else:
                _badge("HIGH VELOCITY" if level == "high" else "REVIEW", level)
    with k2:
        with st.container(border=True):
            st.metric("Evidence items", str(ev_n), ("sample" if agent.demo_on() else "live") if ev_n else "run a query")
    with k3:
        with st.container(border=True):
            st.metric("Cited clauses", str(pol_n), ("word-match TOP-3" if agent.demo_on() else "vector TOP-3") if pol_n else "—")
    with k4:
        with st.container(border=True):
            used = len([t for t in st.session_state.get("req_times", []) if time.time() - t < 60])
            st.metric("Queries this min", f"{used}/10", "session pace")


def render_inspector(role: str, user_name: str) -> None:
    """Right pane: SQL proof, law cites, SAR + officer sign-off."""
    draft = st.session_state.get("pending_draft")
    t_sql, t_law, t_sar = st.tabs(["⚡ SQL Signals", "📜 Cited Law", "📝 Draft SAR"])
    with t_sql:
        if not draft:
            st.caption("Run a query on the left. Proof lands here.")
        else:
            if agent.demo_on():
                st.caption(f"Model `{clean_markdown(draft.get('model_name', ''))}` • demo: approval goes to the in-memory audit log")
            else:
                st.caption(f"Model `{clean_markdown(draft.get('model_name', ''))}` • Snowflake QUERY_ID is saved in AUDIT_LOGS on approval")
            # Mask again at display (second layer). SiS: officer status asked on the server now.
            rows = agent.mask_rows_for_viewer(draft.get("tx_rows", []) or [], role == "officer")
            if rows:
                st.dataframe(rows, width="stretch", hide_index=True, height=280)
            else:
                st.info("No signal rows for this query.")
            st.caption(f"Evidence IDs: {clean_markdown(', '.join(draft.get('evidence_refs', [])))}")
    with t_law:
        if not draft:
            st.caption("Matched clauses land here with distance scores.")
        else:
            for p in draft.get("policies", []) or []:
                with st.container(border=True):
                    st.markdown(f"**📍 {clean_markdown(p.get('policy_id', ''))} — {clean_markdown(p.get('title', ''))}**")
                    sim = p.get("sim", p.get("SIM", ""))
                    if sim != "":
                        try:
                            kind = "word overlap" if agent.demo_on() else "cosine"
                            st.caption(f"Match: {float(sim):.3f} {kind} (near 1.0 = close)")
                        except Exception:
                            pass
                    st.info(clean_markdown(str(p.get("clause_text", "")))[:500])
    with t_sar:
        if not draft:
            st.caption("Approved-wording draft lands here after you run a query.")
        else:
            st.text_area("SAR narrative (read-only draft)", value=clean_markdown(draft.get("report_text", "")), height=220)
            st.caption(f"Hashes: prompt `{clean_markdown(draft.get('prompt_hash', ''))[:16]}…` • result `{clean_markdown(draft.get('result_hash', ''))[:16]}…`")
            st.markdown("**Officer sign-off**")
            if role != "officer":
                st.info("You are analyst. An officer logs in to approve.")
            else:
                c1, c2 = st.columns([1, 1])
                with c1:
                    if st.button("✅ Approve & Log", type="primary", width="stretch"):
                        if not check_rate():
                            st.stop()
                        try:
                            # SiS: re-read the viewer from st.user now (server side), not from
                            # session state. The backend checks APP_OFFICERS again.
                            approver = runtime.sis_viewer_name() if runtime.is_sis_runtime() else user_name
                            log_id = agent.approve_and_log(draft, approved_by=approver)
                            st.session_state.pop("pending_draft", None)
                            try:
                                st.toast(f"Approved + logged {log_id} 🔒")
                            except Exception:
                                pass
                            st.success(f"Saved APPROVED SAR {log_id} by {clean_markdown(user_name)}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Approve failed: {clean_markdown(str(e))}")
                with c2:
                    if st.button("❌ Discard draft", width="stretch"):
                        st.session_state.pop("pending_draft", None)
                        st.rerun()
            safe_md = build_safe_export(draft, user_name=user_name)
            st.download_button("⬇ Safe export (.md, no raw SQL)", data=safe_md, file_name="sar_findings.md", mime="text/markdown")


def main():
    st.set_page_config(page_title=APP_TITLE + " | Bank Compliance", page_icon="🛡️", layout="wide")
    st.markdown(_CSS, unsafe_allow_html=True)
    # Fail fast if secrets missing. Clear error, not silent.
    # SiS: Snowflake gives the session (st.connection), so no secrets and no APP_PASSWORD.
    # Local mode needs Snowflake keys + a strong APP_PASSWORD.
    is_sis_boot = runtime.is_sis_runtime()
    if not is_sis_boot and not agent.demo_on():
        try:
            config.validate_secrets(require_app_password=True)
        except Exception as e:
            st.error(str(e))
            st.stop()
    if not check_login():
        st.stop()
    role = st.session_state.get("user_role", "analyst")
    user_name = st.session_state.get("user_name", "analyst-user")
    auth_mode = st.session_state.get("auth_mode", "local")
    if auth_mode == "sis":
        # Re-check the officer list on every rerun, so a removed officer loses the
        # officer view on the next click (the backend checks again anyway).
        role = get_viewer_role(runtime.sis_viewer_name())
        st.session_state["user_role"] = role
    render_sidebar(user_name, role, auth_mode)
    if auth_mode == "demo":
        st.warning("DEMO MODE • fake sample data • SAR text comes from a fixed template, not an AI model • "
                   "audit log is in memory only. Run locally or in Snowflake for the real Cortex AI path.")
    render_kpi_strip()
    st.markdown("")
    ws, tx = st.tabs(["💬 Workspace", "📊 Transactions"])
    with ws:
        chat_col, insp_col = st.columns([1.2, 0.8], gap="medium")
        with chat_col:
            st.markdown("#### 💬 Compliance Copilot")
            st.caption("Quick queries (run the demo pipeline):" if agent.demo_on() else "Quick queries (run the real pipeline):")
            p1, p2 = st.columns(2)
            for i, pill in enumerate(QUICK_PILLS):
                with (p1 if i % 2 == 0 else p2):
                    if st.button(pill, width="stretch", key=f"pill_{i}"):
                        if not check_rate():
                            st.stop()
                        try:
                            with st.spinner("Running router + vector + Cortex…"):
                                run_pipeline(pill, role)
                            st.rerun()
                        except Exception as e:
                            logger.warning("Draft failed: %s", e)
                            st.error(f"Error: {clean_markdown(str(e))}")
            with st.container(border=True):
                turns = st.session_state.get("turns", [])
                if not turns:
                    with st.chat_message("assistant", avatar="🛡️"):
                        st.write("Ask about wires, structuring, or policy. I return SQL proof + cited law, then draft the SAR.")
                for t in turns[-6:]:
                    with st.chat_message("user"):
                        st.write(clean_markdown(t["q"])[:500])
                    with st.chat_message("assistant", avatar="🛡️"):
                        st.write(f"Intent `{clean_markdown(t['intent'])}` • {t['ev']} signals • {t['pol']} cites. Proof is on the right →")
            q = st.chat_input("Ask a compliance question…")
            if q:
                if not check_rate():
                    st.stop()
                try:
                    with st.spinner("Running router + vector + Cortex…"):
                        run_pipeline(q, role)
                    st.rerun()
                except Exception as e:
                    logger.warning("Draft failed: %s", e)
                    st.error(f"Error: {clean_markdown(str(e))}")
        with insp_col:
            st.markdown("#### 🔍 Evidence Inspector")
            render_inspector(role, user_name)
    with tx:
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
                rows = agent.fetch_transactions(filt_clean, limit=20, offset=offset,
                                                local_ui_hint=(role == "officer"))
                if not rows:
                    st.info("No rows.")
                else:
                    st.dataframe(rows, height=400, width="stretch")
                    st.text(f"Page {p} - showing {len(rows)} rows")
            except Exception as e:
                st.error(f"Load failed: {clean_markdown(str(e))}")


if __name__ == "__main__":
    main()
