# Architecture and deep reference

> The short tour is the [README](../README.md). For the plain-words version, read the [Feynman explainer](https://arunkumar06-cmd.github.io/risk-intelligence-copilot/context.html) ([source](context.html)).
> This page holds the full detail: pipeline, models, optional Cortex Search, the Snowflake (SiS) deploy, the security model, cost, and what is not verified yet.

## 🧠 How it works

| Step | What happens |
|---|---|
| **1. Router** | Whole-word keyword match (SAR / policy / tx review) → allow-list SQL templates (only pre-written queries) with bound params (user text is passed as a value, never pasted into SQL). Blocked words + char allow-list reject injection (bad input that tries to change the SQL). |
| **2. Evidence + Law** | Snowflake SQL pulls transaction signals. `AI_EMBED` + cosine search (meaning match score) returns TOP-3 law clauses. Optional: Cortex Search (see below). |
| **3. Report** | `AI_COMPLETE` with Cortex Guard ON (harm filter, set as `model_parameters => {'guardrails': TRUE}`) writes a short SAR draft. |
| **4. Human check** | Officer must approve. The check runs on the server at approve time: in Snowflake (SiS) the viewer's user name must be in the `APP_OFFICERS` list; locally the app login must hold COMPLIANCE_OFFICER. Only then is the APPROVED row written to `AUDIT_LOGS`. A blocked try writes an `APPROVE_DENIED` row. |

**Trust built in:** officer allow-list (`APP_OFFICERS`), app-side row lock (non-officers never get RESTRICTED-tier rows) + name masking, SQL row-access policies + name masking policy as a second layer, per-user rate limits (fail closed = block if the check breaks), prompt/result hashes (fingerprints), `QUERY_ID` per report, denied-login + denied-approval audit, safe Markdown export (no raw SQL).

## 🤖 Models (set in `backend/config.py`)

| Role | Model | Why |
|---|---|---|
| Main | `claude-sonnet-5` | GA (generally available, fit for production), 1M token context. Needs cross-region inference. |
| Draft + fallback | `llama3.1-8b` | GA, cheap. Native (runs in-region) in some regions, e.g. AWS US West 2. |
| Embeddings | `snowflake-arctic-embed-m-v1.5` | 768-number vector per clause. |

- Not used: `mistral-large3` is public preview (Snowflake: "not suitable for production"). `mistral-large2` is legacy (old, closing) since Aug 12 2026.
- Stuck with cross-region off? `llama3.3-70b` (GA, native in AWS US West 2) is a stronger in-region option. Add it to `ALLOWED_LLM_MODELS` and set it as `PRIMARY_MODEL`.
- Audit log stores the model name as the version label (Cortex puts the version in the model name). No made-up version tags.
- Source: [regional availability](https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-regional-availability), [cross-region inference](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cross-region-inference), [Aug 2026 model deprecations](https://docs.snowflake.com/en/release-notes/bcr-bundles/un-bundled/bcr-august-model-deprecations).

## 🔎 Optional: Cortex Search (better law-clause search)

- Cortex Search = hybrid search (vector meaning match + keyword match + rerank, a second-pass re-score).
- Off by default. Turn on: run `sql/04_cortex_search.sql`, then set `USE_CORTEX_SEARCH = "TRUE"` in secrets.
- The app calls it through the Python API (`snowflake-core`). On any error, or zero hits, it falls back to the cosine search.
- In Cortex Search mode the Law tab shows **no match score** (the app asks only for POLICY_ID, TITLE, CLAUSE_TEXT). Cosine mode still shows it.
- Not tested on a live account.

## ☁️ Run inside Snowflake (SiS = Streamlit in Snowflake)

**Status: built + unit-tested, not live-tested.**

- **How it starts:** `backend/runtime.py` detects SiS from the runtime: a stored procedure (warehouse runtime) or the container host + session token file (container runtime). Then the app skips the secrets check and uses `st.connection("snowflake")` (Snowflake's own session). No keys are stored in the app. ([source](https://docs.snowflake.com/en/developer-guide/streamlit/app-development/secrets-and-configuration))
- **Bound values everywhere:** the SiS connection uses `?` placeholders (qmark style), the local one uses `%s`. One small adapter (`run_sql`) swaps the placeholder per connection. Values are always bound, never pasted.
- **Who may approve:** the viewer's Snowflake user name comes from `st.user.user_name`. Approval needs that name in the `APP_OFFICERS` table (`sql/01`). It is checked again on the server at approve time, so the UI role cannot fake it. ([source](https://docs.snowflake.com/en/developer-guide/streamlit/app-development/personalization))
- **Data hiding:** SiS runs every query with the app owner's rights, so `CURRENT_ROLE()` policies cannot tell viewers apart. So the app itself hides RESTRICTED-tier rows and masks a customer-name column for non-officers. Officer status is looked up on the server on every call, so a removed officer loses access on the next click. The SQL policies stay as a second layer. ([source](https://docs.snowflake.com/en/developer-guide/streamlit/features/row-access))
- **Deploy:** `sql/02_sis_deploy.sql` makes a **container runtime** app (Python runs in a Snowflake container) with compute pool (container host) `RISK_APP_POOL` and Snowflake's PyPI mirror, so the `streamlit==1.64.0` pin can install. Admin steps (grants, compute pool, warehouse time cap) are commented "fill and run". The app is created by a separate app owner role (`USE ROLE <APP_OWNER_ROLE>`), not the admin role. Safe to re-run (`CREATE OR REPLACE`).
- **Make it live:** `CREATE STREAMLIT` alone does not start the app. `sql/02` then runs `ALTER STREAMLIT RISK_COPILOT_APP ADD LIVE VERSION FROM LAST;` (or open the app once in Snowsight with the owner role). ([source](https://docs.snowflake.com/en/sql-reference/sql/create-streamlit))
- Compute pool access on trial accounts is not confirmed.

## 🔐 Security model and limits

- **SiS (multi-user):** per-viewer identity from `st.user`; officer = listed in `APP_OFFICERS` (only the admin role that owns the table can change it).
- **Local mode is a single-user demo:** one app password, a role dropdown (shows/hides the Approve button only), and one Snowflake login (`SNOWFLAKE_ROLE`). The real gate is that login's COMPLIANCE_OFFICER role. `ALLOW_LOCAL_APPROVE` is a demo-only switch, default FALSE.
- **Not live-tested on Snowflake:** Cortex calls, SQL scripts, SiS deploy and Cortex Search are checked by docs + fake-DB tests only.
- **Later:** multi-team roles, SSO (company login) for local mode, shared (Redis) per-minute limits.

## 💰 Cost

- **Trial account:** AI features are off on self-service trials until you add a credit card. Adding a card does not upgrade the trial or end it. ([source](https://docs.snowflake.com/en/user-guide/admin-trial-account))
- **Prices** (AI credits per 1M tokens, input / output, Snowflake Service Consumption Table, effective Sep 30 2026):
  - `claude-sonnet-5`: 1.20 / 6.00
  - `llama3.1-8b`: 0.132 / 0.132
  - `AI_EMBED` with `snowflake-arctic-embed-m-v1.5`: 0.03
  - Cortex Guard (harm filter) also bills per input token it checks.
  - Cortex Search (optional): 6.3 AI credits per GB per month of indexed data.
- Demo data is tiny and prompts are capped (350 words), so a demo run uses very few credits.

## ❗ Not verified

- No live Snowflake run was done for this version: Cortex calls, SQL scripts, SiS deploy and Cortex Search are untested on a real account.
- What *is* tested: Python logic with a fake database, SQL text shape (both `%s` and `?` styles), the officer gate, and the Streamlit screens in local and SiS mode (AppTest, no network).
