# Security policy

## Report a hole
Use **Report a vulnerability** (private) on the repo Security tab.
Never open a public Issue with secrets, keys, or exploit steps.

## Supported
| Version | Gets fixes |
|---|---|
| latest `main` | ✅ |
| old tags | ❌ (upgrade) |

## Auto guards (run on every push)
- **CodeQL** (deep flaw scan, incl. SQL inject shapes) on Python code + GitHub workflow files
- **Gitleaks** (key-leak scan over full history)
- **pip-audit** 2.10.1 (bad-library block on `requirements.txt` + `requirements-dev.txt`)
- **Dependabot** (auto update bot: weekly pip, monthly Actions, monthly pre-commit hooks)
- Push protection: enable at Settings → Code security → Secret scanning.

## Access model
- **SiS (Streamlit in Snowflake):** viewer = `st.user.user_name`. Only users in the `APP_OFFICERS` table can approve; this is checked on the server at approve time, and blocked tries are written to `AUDIT_LOGS` as `APPROVE_DENIED`. Non-officers never get RESTRICTED-tier rows, and a customer-name column is masked for them.
- **Local mode:** single-user demo (app password + role dropdown + one Snowflake login). The gate is that login's COMPLIANCE_OFFICER role. `ALLOW_LOCAL_APPROVE` is demo-only and must stay FALSE.
- **Not live-tested on Snowflake** yet (unit tests with a fake database only). See `context.md` section 6.
