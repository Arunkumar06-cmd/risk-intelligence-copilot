# Changelog

> Release notes per version live on the [Releases page](https://github.com/Arunkumar06-cmd/risk-intelligence-copilot/releases) (made by the release bot from `feat:`/`fix:` commit words; each ships an SBOM parts-list).
> This file is kept by hand. The bot does not write it (no insertion flag in this file).

## Unreleased

### Docs and layout
- New README: typing header, quick-link and status badges, live-demo screenshot, tooling grid, Mermaid flow, metrics table, docs index.
- Deep reference moved to `docs/ARCHITECTURE.md`; Feynman explainer added as `docs/context.html` and published at `/context.html` on Pages.
- Repo layout: deck → `docs/deck/`, screenshots → `docs/assets/` (old dummy-credential screenshots removed), `backend/__init__.py`, `CONTRIBUTING.md`, `.editorconfig`.
- Deck: team Behelit, Arun Kumar (solo).

## 1.2.0 (2026-10-02)

Released as [v1.2.0](https://github.com/Arunkumar06-cmd/risk-intelligence-copilot/releases/tag/v1.2.0) from PR #12.

### Live demo
- Free live demo on GitHub Pages: https://arunkumar06-cmd.github.io/risk-intelligence-copilot/. Runs the real Streamlit app in the browser (stlite 1.9.2) in demo mode.
- New demo mode (`backend/demo.py`, `DEMO_MODE`): fake sample data, sample clauses, template SAR, in-memory audit log; keeps the input guards, the RESTRICTED rule and the officer-only approve check. Off by default; never used inside SiS.
- New `.github/workflows/pages.yml` publishes the demo on every push to `main`.
- Tests: 9 new demo tests (no DB touched). Total 102.

### Security
- SiS (Streamlit in Snowflake) mode now runs: it is detected from the runtime, skips the secrets check, and uses `st.connection("snowflake")`. Built + unit-tested, not live-tested.
- Officer approval now depends on the real viewer: in SiS the `st.user` name must be in the new `APP_OFFICERS` table (`sql/01`), checked on the server at approve time. Before, every viewer shared one login role.
- Denied approvals are written to `AUDIT_LOGS` as `APPROVE_DENIED`, naming the real viewer from `st.user` (plus the claimed name if it differs).
- In SiS, officer status for data hiding is looked up on the server on every call (UI flag ignored); the UI role is re-checked on every rerun.
- `LIMIT`/`OFFSET` are now plain clamped ints in the SQL, not bind values (LIMIT binds in qmark mode are not proven in the docs).
- App-side data hiding for non-officers (no RESTRICTED-tier rows, customer-name column masked), because SQL `CURRENT_ROLE()` policies see only the owner role in SiS. SQL policies kept as a second layer.
- All SQL goes through `run_sql`, which keeps values bound on both `%s` and `?` connection styles.
- Rate-limit check now fails closed (blocks if it breaks). Its audit guard now counts approval tries by `APPROVED_BY` (it compared a user name with `USER_ROLE` before, so it never matched) and ignores `LOGIN_FAILED` rows, so wrong-password tries cannot lock out a real user.

### Fixed
- AI_COMPLETE guardrails: Cortex Guard (harm filter) now set the documented way, `model_parameters => {'guardrails': TRUE, 'temperature': 0}`. Removed the silent retry that called the model with **no** guardrails.
- Intent router: whole-word match (`\b` = word edge). "necessary" no longer routes to SAR; "txt" no longer routes to tx review. Plurals like "reports", "rules", "payments" still match.
- Model allow-list checks use `raise ValueError` instead of `assert` (`python -O` removes asserts).

### Changed
- Main model `mistral-large3` (public preview) → `claude-sonnet-5` (GA, needs cross-region inference). Draft/fallback stays `llama3.1-8b`.
- Audit version label is now the real model name. Removed made-up tags `v3-2026-256K` and `llama3.1-8b-2026-draft`.
- `SNOWFLAKE.CORTEX.AI_EMBED` / `AI_COMPLETE` → unqualified `AI_EMBED` / `AI_COMPLETE` (as in Snowflake docs). Removed unproven "EOL end 2026" comments.
- `cryptography` 50.0.1 → 50.0.2 (wheels built with OpenSSL 4.0.3, a security patch release).
- Snowflake connector calls pass `ocsp_fail_open=True` (keeps OCSP, the certificate-revoked check, ON if the connector is bumped to 4.8.0+).
- Streamlit: `use_container_width=True` → `width="stretch"` (old arg is deprecated); removed dead `st.experimental_user` branch (removed in Streamlit 1.54.0).
- SiS deploy (`sql/02`): container-runtime app (`CREATE OR REPLACE`, compute pool `RISK_APP_POOL`, PyPI mirror inside the CREATE) created by a separate owner role (fill-and-run grants: CREATE STREAMLIT, READ on stage, Cortex, READ SESSION, tables), then `ADD LIVE VERSION FROM LAST`; warehouse time cap and viewer grants. Not live-tested.
- Inspector no longer shows an always-empty "Query ID" before approval; `datetime.utcnow()` (deprecated) replaced; unused `json` import removed; a weak OR-test in `test_guards.py` split into two real asserts.
- Search runs once per question: `build_draft(..., policies=None)` reuses the clauses `run_pipeline` already found, so Law tab, prompt and audit `POLICY_IDS` match.
- `pytest` moved from `requirements.txt` to new `requirements-dev.txt` (with `ruff`), so test tools are not installed by the app/SiS. CI installs both files; pip-audit checks both.
- Release bot config: v10 key `version_toml`; removed old keys. `pyproject.toml` version synced to the real last tag `1.1.0`.
- pre-commit: pre-commit-hooks v6.0.0, ruff-pre-commit v0.16.9 (hook id `ruff-check`). Same ruff rules added to `pyproject.toml`.
- CI: runs `ruff check .` and all tests; read-only permissions; removed duplicate `pip install pytest`. Security: CodeQL also scans workflow files (`actions`); `pip-audit` pinned to 2.10.1.
- Dependabot: also updates pre-commit hook versions (monthly).

### Added
- Optional Cortex Search Service for law clauses (`sql/04_cortex_search.sql`, flag `USE_CORTEX_SEARCH`, default off, falls back to cosine search). New pin `snowflake-core==1.13.2`.
- Tests: 84 new (35 agent/SQL + 41 SiS/officer/masking/limits + 8 app tests incl. SiS AppTest screens). Total 93 before the demo tests.
- `backend/runtime.py`: local vs SiS detection, `st.user` viewer name, SiS connection.
- Docs: security model (SiS multi-user, local single-user demo). Cortex Search mode shows no match score.
- Deck: facts updated (claude-sonnet-5 + llama3.1-8b fallback, whole-word router, officer allow-list, SiS session, trial AI rule, real test count, run line). Layout fixed: text boxes made for a 13.33 in slide now fit the 10 in slide, white text on the white body area made dark, slide 1 team line moved onto the slide, duplicate THANK YOU text removed (the picture has it). Text on white now uses darker tones that pass 4.5:1 contrast (grey 4B5563, cyan 0369A1). Empty layout placeholders removed. Slide-3 caption reduced to repo-backed facts; slide 5/6 overclaims toned down. Architecture picture: stale lines redrawn in the same font/colours, and the arrows now show 4 → 5 → 6 → 7 (officer check before the proof trail).
