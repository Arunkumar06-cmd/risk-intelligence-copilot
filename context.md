# context.md — Risk, Fraud & Regulatory Intelligence Copilot

> Handoff doc (handover note). Everything a new agent or dev needs to pick up this project. Updated 2026-10-02 (first written 2026-09-30).

## 1. What this is
AI copilot (helper) for Banking/NBFC compliance teams. An analyst asks in plain English → the app returns **verified SQL evidence** (DB proof) + **cited regulation clauses** (law quotes) + a **draft SAR** (suspicious-activity report). A compliance officer must approve before anything is saved. Every approval, denied approval and denied login is audit-logged (written to a proof trail); drafts that are not approved are not stored.

- **Repo:** https://github.com/Arunkumar06-cmd/risk-intelligence-copilot (public, MIT)
- **Live demo:** https://arunkumar06-cmd.github.io/risk-intelligence-copilot/ (GitHub Pages, free). The app runs in the browser via stlite (Streamlit on Pyodide = Python in the browser). Demo mode only: fake sample data, template SAR, no Snowflake, no AI call. Built by `.github/workflows/pages.yml` on every push to `main`.
- **Release:** latest GitHub Release is `v1.2.0` (2026-10-02), made by the release bot from the `feat:` merge of PR #12; it also wrote `1.2.0` into `pyproject.toml` (`version_toml` works). Each release ships an SBOM parts-list. Releases: https://github.com/Arunkumar06-cmd/risk-intelligence-copilot/releases
- **Deck:** `Risk_Intelligence_Copilot_Submission.pptx` (CoCo CLI Hackathon, GCC Edition)
- **Stack:** Snowflake Trial + Cortex AI (`AI_EMBED`, `AI_COMPLETE`, optional Cortex Search) + Streamlit UI + Python connector. Runs locally (own Snowflake login) or inside Snowflake (SiS = Streamlit in Snowflake, Snowflake's own session).

## 2. How it works (3 steps + human gate)
1. **Intent router** (`classify_intent` in `backend/hybrid_agent.py`) — sorts the question (SAR draft / policy lookup / tx review / general) by **whole-word** match (`\b` = word edge), so "necessary" no longer counts as "sar". Allow-list SQL templates (pre-approved queries) + bound params (safe inputs) + blocked-word guard. No raw user text ever reaches SQL.
2. **Evidence + Law** — Snowflake SQL pulls transaction signals; `AI_EMBED` vector search (meaning match) returns TOP-3 regulation clauses with cosine scores (match numbers). Optional: Cortex Search Service (hybrid search = vector + keyword + rerank) when `USE_CORTEX_SEARCH=TRUE`; any error or zero hits → falls back to cosine search. Cortex Search mode shows **no match score** in the Law tab. Search runs **once** per question: the same clauses feed the Law tab, the prompt and the audit `POLICY_IDS`.
3. **Report** — `AI_COMPLETE` writes a short SAR draft. Main model `claude-sonnet-5`; if it fails, draft model `llama3.1-8b`. Cortex Guard (harm filter) is ON for both via `model_parameters => {'guardrails': TRUE, 'temperature': 0}`. There is **no** call without guardrails.
4. **Officer sign-off (HITL = human in the loop)** — analyst cannot save. Server-side gate at approve time (`approve_and_log`): SiS = viewer's `st.user` name must equal the approver and be in `APP_OFFICERS`; local = app login must hold COMPLIANCE_OFFICER (or demo switch `ALLOW_LOCAL_APPROVE`). Pass → `APPROVED` row in `AUDIT_LOGS` with evidence IDs, policy IDs, model name + version label, prompt+result hashes (proof prints), `QUERY_ID`. Fail → `APPROVE_DENIED` row + error.

## 3. File map
```
risk_copilot/
├── streamlit_app.py          # UI: login, KPI strip, chat + inspector, transactions
├── backend/
│   ├── hybrid_agent.py       # router + vector/Cortex search + AI_COMPLETE + run_sql adapter + officer gate + masking + audit insert
│   ├── runtime.py            # local vs SiS detection, st.user viewer name, SiS connection (st.connection)
│   ├── demo.py               # DEMO MODE: fake rows, sample clauses (not real law), template SAR, in-memory audit log
│   └── config.py             # secrets, model names, limits, USE_CORTEX_SEARCH + DEMO_MODE flags (single source of truth)
├── sql/
│   ├── 01_setup.sql          # DB, tables, APP_OFFICERS allow-list, row locks, name masking, audit cols
│   ├── 02_sis_deploy.sql     # Streamlit-in-Snowflake deploy: container runtime, owner-role grants (built, not live-tested)
│   ├── 03_rate_limit.sql     # per-user/hour spam-stop table + cleanup task
│   └── 04_cortex_search.sql  # OPTIONAL Cortex Search service on law clauses (untested)
├── tests/
│   ├── test_guards.py        # 9 input-guard + router tests
│   ├── test_agent_sql.py     # 35 tests: SQL shape, guardrails, model guards, router, OCSP, search fallback, one search per question (fake DB)
│   ├── test_app_smoke.py     # 8 tests: AppTest local + SiS screens (no DB/network) + pipeline searches once
│   ├── test_sis_mode.py      # 41 tests: SiS detection, qmark adapter (fakes + real connector classes), officer list, denied-approval audit, masking, rate limit, int-only LIMIT/OFFSET
│   └── test_demo_mode.py     # 9 tests: demo mode never touches the DB, hides RESTRICTED rows, officer-only approve, AppTest end to end
├── site/index.html           # GitHub Pages page (stlite 1.9.2 loads streamlit_app.py + backend in the browser)
├── requirements.txt          # app runtime pins only (this is what SiS installs)
├── requirements-dev.txt      # pytest + ruff (CI / dev only, never shipped)
├── .streamlit/config.toml    # dark theme; secrets.toml.example = key template
├── docs/app_login.png, app_dashboard.png  # screenshots (dummy Snowflake creds)
├── .github/workflows/        # ci.yml (compile + ruff + tests) + security.yml (CodeQL python+actions, Gitleaks, pip-audit) + release.yml (auto version + SBOM) + pages.yml (live demo to GitHub Pages)
├── .github/dependabot.yml    # auto update bot: pip weekly, Actions monthly, pre-commit monthly
├── .pre-commit-config.yaml   # 7 hooks (pre-commit-hooks v6.0.0 + ruff-check v0.16.9)
├── pyproject.toml            # version + release-bot rules (version_toml) + ruff lint rules
└── README.md / SECURITY.md / CHANGELOG.md / LICENSE (MIT)
```

## 4. Run it
```bash
pip install -r requirements.txt -r requirements-dev.txt   # dev file = pytest + ruff
# Snowflake: run sql/01_setup.sql then sql/03_rate_limit.sql in Snowsight (web UI)
# Optional: sql/04_cortex_search.sql, then USE_CORTEX_SEARCH = "TRUE"
cp .streamlit/secrets.toml.example .streamlit/secrets.toml  # fill trial keys
streamlit run streamlit_app.py
python -m pytest tests/ -q   # 102 passed (no Snowflake needed)
ruff check .                 # lint, same rules as pre-commit
```
Key secrets (keys, local mode only; SiS needs none): `SNOWFLAKE_ACCOUNT/USER/PASSWORD`, `APP_PASSWORD` (local demo login, min 12 chars), optional `SNOWFLAKE_PRIVATE_KEY_PATH` (key-file login, prod path), optional `USE_CORTEX_SEARCH`.

Snowflake must-haves:
- Role has `SNOWFLAKE.CORTEX_USER` (permission to call Cortex AI functions).
- Cross-region inference (AI call may run in another region) ON for `claude-sonnet-5`: `CORTEX_ENABLED_CROSS_REGION` = `ANY_REGION` or `AWS_US` (ACCOUNTADMIN sets it). If OFF, the app falls back to `llama3.1-8b`.
- Trial accounts: AI features are off until a credit card is added (this does not upgrade the trial).
- Officers: an admin adds Snowflake user names to `APP_OFFICERS` (see `sql/01`). SiS deploy: `sql/02` (fill-and-run grants).

## 5. Trust / security decisions (do not regress)
- **XSS (web-attack) fix:** user-derived text renders via `st.text`/`st.markdown` after `clean_markdown` (tag strip). CSS/badges are static strings only; badge labels pass through the stripper first.
- **SQL inject (bad-input) fix:** allow-list templates + bound params + char allow-list + blocked whole words + LIKE-escape. Every statement goes through `run_sql`, which keeps values bound on both connection styles (`%s` local, `?` in SiS).
- **Model allow-list:** `_safe_llm_model` / `_safe_embed_model` use `if ...: raise ValueError` (not `assert`, which `python -O` deletes). A test runs them under `python -O`.
- **Guardrails always ON:** Cortex Guard set in `model_parameters`. No silent retry without it (old code had one; removed).
- **Auth (login):** local password gate (hmac compare, timing-safe) + role dropdown (single-user demo). SiS mode: detected from the runtime (not from `st.user` alone), no app password, no dropdown, no secrets check; viewer = `st.user.user_name`, role = listed in `APP_OFFICERS` or not. No viewer name → access stopped (fail closed).
- **Officer gate:** checked on the server at approve time, never from the UI role. SiS re-reads `st.user` at click time and compares it with the approver. Lookup errors = not officer (fail closed). Denied tries → `APPROVE_DENIED` audit row naming the real viewer (plus `(claimed X)` if a different name was passed).
- **Data hiding:** in SiS, `CURRENT_ROLE()` is always the owner role (SiS row-access doc), so the app hides RESTRICTED-tier rows and masks a `CUSTOMER_NAME` column for non-officers (`fetch_transactions` + `mask_rows_for_viewer`). In SiS the officer status is looked up on the server on every call (the UI flag is ignored), so removing someone from `APP_OFFICERS` takes effect on their next click; the UI role is also re-checked on every rerun. SQL row/mask policies stay as a second layer. The app does not select customer names today; masking guards any future column.
- **Audit race fix:** `LOG_UUID` (random unique ID made in Python), never `MAX(LOG_ID)`.
- **Rate limits:** 10/min per session + 50/hour per user in `RATE_LIMIT` table (MERGE = combined upsert) + audit-row guard (approval tries only: `STATUS IN ('APPROVED','APPROVE_DENIED')`, so wrong-password `LOGIN_FAILED` rows cannot lock out a real user). Fails closed: if the check breaks, the request is blocked.
- **LIMIT / OFFSET:** cast with `int()`, clamped, written into the SQL as plain ints (not bind values, because LIMIT binds in qmark mode are not proven in the docs). All other values stay bound.
- **Export:** Markdown contains findings + IDs + hashes only — never raw SQL.
- **OCSP (check that a server certificate is not revoked):** both `connect()` calls pass `ocsp_fail_open=True`. Same as connector 4.7.5 default; connector 4.8.0 turns OCSP off unless this is set, so a future Dependabot bump keeps OCSP on.
- **Logs:** new warning lines log only the error type, not the error text (it may echo the prompt with tx data).
- **Dev tools out of prod:** pytest + ruff live in `requirements-dev.txt`; `requirements.txt` (what SiS installs) has runtime pins only. CI installs both; pip-audit checks both. Dependabot's pip scan picks up every `*requirements*.txt` in `/`.
- **Gates:** CI (compile + ruff + 102 tests), Security (CodeQL python+actions, Gitleaks, pip-audit 2.10.1), Release workflows; pre-commit (7 hooks) green locally; Dependabot PRs merged after green runs.

## 6. Known limits / next steps
- **Demo mode** (`DEMO_MODE=TRUE`, or automatic in the browser): sample data written for the demo, clauses are sample text (not real law), SAR comes from a fixed template, audit log is in memory only. Never active inside SiS (`demo_on()` checks this). The real AI path needs Snowflake.
- **Not live-tested on Snowflake.** Cortex calls, SQL scripts (`sql/01`–`04`), the SiS deploy and Cortex Search are verified only by docs + fake-DB tests. First live run: check `st.connection("snowflake")` in the container runtime, the qmark (`?`) binds, and the grants in `sql/02`.
- **Local mode is single-user.** One app password, one Snowflake login (`SNOWFLAKE_ROLE`); the dropdown is demo only. Multi-user use = SiS.
- **SiS query time cap** is set on the warehouse (`sql/02` step 3), because the app skips `ALTER SESSION` in SiS (owner's-rights stored procedures cannot set session parameters).
- **SQL row policies in SiS** see the owner role only; the app-side filter does the per-viewer work there. Pick the owner-role option in `sql/02` step 1e.
- Trial compute-pool access unknown.
- **Release bot:** with `version_toml`, each release commits the new version into `pyproject.toml` (`chore(release): vX [skip ci]`) and pushes to `main` (seen working on v1.2.0, commit f25f01b). Before this, the bot only made tags, so `pyproject.toml` said `0.1.0` while tags said `v1.1.0`. `main` has no branch protection today, so the push works. CHANGELOG.md is hand-kept (it has no PSR insertion flag, so the bot does not write it); full notes live on the GitHub Releases page.
- Later: multi-team roles, SSO (company login) for local mode, shared (Redis) per-minute limits.
- Screenshots in `docs/` were taken with dummy Snowflake creds (login + empty dashboard states).
- Deck: updated 2026-10-01 (facts, fit-to-slide layout, readable text colour, architecture picture lines). Team fields `[Your Team Name]`, `[PS-ID / Title]`, `[Name]`, `[N]` (slides 1 and 7) are left for the user to fill by hand. Checked by Quick Look renders only, not in PowerPoint/Keynote.

> Note from an old chat (not a project fact): the first version of this file mentioned "Muse Spark", "Space bunny mode" and "nemotron". None of these appear anywhere in the code or config. Ignore them for project work.
