# Contributing

Thanks for helping. Keep changes small, tested and easy to review.

## Set up
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pre-commit install            # run the 7 checks before each commit
```

## Before you open a pull request
```bash
python -m pytest tests/ -q    # all tests must pass (no Snowflake needed)
ruff check .                  # lint
pre-commit run --all-files    # whitespace, YAML, secrets, debug, ruff
```
- Try the demo locally with no keys: `DEMO_MODE=TRUE streamlit run streamlit_app.py`.
- Never commit keys, passwords or `.streamlit/secrets.toml`.

## Commit and PR titles
The release bot reads the first word ([Conventional Commits](https://www.conventionalcommits.org)):
- `feat: ...` new feature → minor version
- `fix: ...` bug fix → patch version
- `docs:`, `test:`, `chore:`, `ci:` → no release

## Rules that must not regress
See [`context.md`](context.md) section 5:
- input guard and bound SQL values
- guardrails on every AI call
- server-side officer gate
- audit rows for denied tries
- fail-closed rate limit
- safe export with no SQL
