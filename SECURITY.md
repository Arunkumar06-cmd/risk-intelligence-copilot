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
- **CodeQL** (deep flaw scan, incl. SQL inject shapes)
- **Gitleaks** (key-leak scan over full history)
- **pip-audit** (bad-library block on `requirements.txt`)
- **Dependabot** (weekly pip, monthly Actions)
- Push protection: enable at Settings → Code security → Secret scanning.
