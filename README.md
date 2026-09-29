# Risk, Fraud and Regulatory Intelligence Copilot (Banking/NBFC)

AI helper (copilot) for bank risk check. Turns plain words (user ask) into safe SQL (DB query) + law cites (policy proof) + SAR draft (suspicious report).

## Live Demo
- Streamlit Cloud (free host): add your link after deploy -> `https://YOUR-APP.streamlit.app`
- Snowflake SiS (in-DB app): see `sql/02_sis_deploy.sql`

## What it does
1. Intent router (ask sorter): SAR draft / policy lookup / tx review / general
2. Vector search (meaning match): `AI_EMBED` + `VECTOR_COSINE_SIMILARITY` on `REGULATORY_POLICIES`
3. Report maker: `AI_COMPLETE` (mistral-large3 primary, llama3.1-8b cheap draft) with guardrails (safety)
4. Human check (HITL): officer must approve before save to `AUDIT_LOGS`
5. Proof trail: stores evidence IDs + policy IDs + model + hashes (proof print) + QUERY_ID

## Quick Run (local)
```bash
cd risk_copilot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit secrets.toml with your Snowflake trial keys
streamlit run streamlit_app.py
```

## Snowflake Setup
```sql
-- run in order in Snowsight (web UI):
-- sql/01_setup.sql  (grids + locks + masks)
-- sql/03_rate_limit.sql (spam stop grid)
-- sql/02_sis_deploy.sql (only for SiS deploy)
```

## Tests
```bash
python3 -m pytest tests/test_guards.py -q
# 9 passed
```

## Stack (2026, free tier)
- Snowflake Trial ($400 free) + Cortex AI_EMBED + AI_COMPLETE
- snowflake-arctic-embed-m-v1.5 + mistral-large3 + llama3.1-8b
- Streamlit 1.37.1 + Python connector 3.13.0
- No nemotron. Only free parts.

## Repo Map
- `streamlit_app.py` : UI (login + ask + approve + export)
- `backend/hybrid_agent.py` : router + search + report + log
- `backend/config.py` : secrets (keys) + models
- `sql/` : setup + SiS + rate limit
- `tests/` : guard checks
