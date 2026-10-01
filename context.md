# context.md — Risk, Fraud & Regulatory Intelligence Copilot

> Handoff doc (handover note). Everything a new agent or dev needs to pick up this project. Generated 2026-09-30.

## 1. What this is
AI copilot (helper) for Banking/NBFC compliance teams. An analyst asks in plain English → the app returns **verified SQL evidence** (DB proof) + **cited regulation clauses** (law quotes) + a **draft SAR** (suspicious-activity report). A compliance officer must approve before anything is saved. Every step is audit-logged (written to a proof trail).

- **Repo:** https://github.com/Arunkumar06-cmd/risk-intelligence-copilot (public, MIT)
- **Release:** v1.1.0+ (auto-cut by release bot; each release ships an SBOM parts-list)
- **Deck:** `Risk_Intelligence_Copilot_Submission.pptx` (CoCo CLI Hackathon, GCC Edition)
- **Stack:** Snowflake Trial + Cortex AI (`AI_EMBED`, `AI_COMPLETE`) + Streamlit UI + Python connector

## 2. How it works (3 steps + human gate)
1. **Intent router** (`classify_intent` in `backend/hybrid_agent.py`) — sorts the question (SAR draft / policy lookup / tx review / general). Allow-list SQL templates (pre-approved queries) + bound params (safe inputs) + blocked-word guard. No raw user text ever reaches SQL.
2. **Evidence + Law** — Snowflake SQL pulls transaction signals; `AI_EMBED` vector search (meaning match) returns TOP-3 regulation clauses with cosine scores (match numbers).
3. **Report** — `AI_COMPLETE` (model `mistral-large3`, cheap drafts via `llama3.1-8b`, guardrails ON) writes a short SAR draft.
4. **Officer sign-off (HITL = human in the loop)** — analyst cannot save. Officer approves → `APPROVED` row in `AUDIT_LOGS` with evidence IDs, policy IDs, model name/version, prompt+result hashes (proof prints), `QUERY_ID`.

## 3. File map
```
risk_copilot/
├── streamlit_app.py          # UI: login, KPI strip, chat + inspector, transactions
├── backend/
│   ├── hybrid_agent.py       # router + vector search + report + audit insert
│   └── config.py             # secrets, model names, limits (single source of truth)
├── sql/
│   ├── 01_setup.sql          # DB, tables, row locks, name masking, audit cols
│   ├── 02_sis_deploy.sql     # Streamlit-in-Snowflake deploy (runs app inside DB)
│   └── 03_rate_limit.sql     # per-user/hour spam-stop table + cleanup task
├── tests/test_guards.py      # 9 guard-rail tests (pytest)
├── .streamlit/config.toml    # dark theme; secrets.toml.example = key template
├── docs/app_login.png, app_dashboard.png  # real screenshots from live runs
├── .github/workflows/        # ci.yml (tests) + security.yml (CodeQL, secret scan, pip audit) + release.yml (auto version + SBOM)
├── pyproject.toml            # version + release-bot rules + ruff (linter) rules
└── README.md / SECURITY.md / CHANGELOG.md / LICENSE (MIT)
```

## 4. Run it
```bash
pip install -r requirements.txt
# Snowflake: run sql/01_setup.sql then sql/03_rate_limit.sql in Snowsight (web UI)
cp .streamlit/secrets.toml.example .streamlit/secrets.toml  # fill trial keys
streamlit run streamlit_app.py
pytest tests/ -q   # 9 passed
```
Key secrets (keys): `SNOWFLAKE_ACCOUNT/USER/PASSWORD`, `APP_PASSWORD` (local demo login, min 12 chars), optional `SNOWFLAKE_PRIVATE_KEY_PATH` (key-file login, prod path).

## 5. Trust / security decisions (do not regress)
- **XSS (web-attack) fix:** user-derived text renders via `st.text`/`st.markdown` after `clean_markdown` (tag strip). CSS/badges are static strings only; badge labels pass through the stripper first.
- **SQL inject (bad-input) fix:** allow-list templates + bound params + char allow-list + blocked whole words + LIKE-escape.
- **Auth (login):** local password gate (hmac compare, timing-safe) + role select; SiS mode uses Snowflake `CURRENT_USER` + officer check, never the dropdown.
- **Audit race fix:** `LOG_UUID` (random unique ID made in Python), never `MAX(LOG_ID)`.
- **Rate limits:** 10/min per session + 50/hour global in `RATE_LIMIT` table (MERGE = combined upsert).
- **Export:** Markdown contains findings + IDs + hashes only — never raw SQL.
- **Gates:** CI + Security + Release workflows green on main; pre-commit (7 hooks) green locally; Dependabot (auto update bot) PRs merged after green runs.

## 6. Known limits / next steps
- Single analyst+officer demo flow; multi-team roles, SSO (company login), and Redis global limits are noted in-code as prod TODOs.
- Screenshots in `docs/` were taken with dummy Snowflake creds (login + empty dashboard states).
- PPTX placeholders to fill before submitting: Team Name, Problem Statement ID, Team Leader, Team Size.
- No `nemotron` (big AI model) anywhere; agents run on Muse Spark. "Space bunny mode" request is unresolved — user picked "Model switch", no model ID given yet.
