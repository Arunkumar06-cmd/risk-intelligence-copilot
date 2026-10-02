"""Load secrets. No hardcode. Simple English."""
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _get_secret(key: str, default: str = "") -> str:
    val = os.getenv(key, "")
    if val:
        return val
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            try:
                v = st.secrets.get(key, "")
                if v:
                    return str(v)
            except Exception:
                pass
    except Exception:
        pass
    return default


SNOWFLAKE_ACCOUNT = _get_secret("SNOWFLAKE_ACCOUNT")
SNOWFLAKE_USER = _get_secret("SNOWFLAKE_USER")
SNOWFLAKE_PASSWORD = _get_secret("SNOWFLAKE_PASSWORD")
SNOWFLAKE_WAREHOUSE = _get_secret("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")
SNOWFLAKE_DATABASE = _get_secret("SNOWFLAKE_DATABASE", "RISK_INTELLIGENCE_DB")
SNOWFLAKE_SCHEMA = _get_secret("SNOWFLAKE_SCHEMA", "COMPLIANCE")
SNOWFLAKE_ROLE = _get_secret("SNOWFLAKE_ROLE", "ANALYST")

# Key-pair JWT auth. Preferred over password in prod.
# Docs: https://docs.snowflake.com/en/user-guide/key-pair-auth
# Set path to private key file (PEM). Keep password as fallback for local dev only.
SNOWFLAKE_PRIVATE_KEY_PATH = _get_secret("SNOWFLAKE_PRIVATE_KEY_PATH", "")
SNOWFLAKE_PRIVATE_KEY_PASSPHRASE = _get_secret("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "")

# Wait cap for connect in seconds. Stops hang on bad network.
try:
    SNOWFLAKE_CONNECT_TIMEOUT = int(_get_secret("SNOWFLAKE_CONNECT_TIMEOUT", "30") or "30")
except Exception:
    SNOWFLAKE_CONNECT_TIMEOUT = 30
if SNOWFLAKE_CONNECT_TIMEOUT < 5:
    SNOWFLAKE_CONNECT_TIMEOUT = 5
if SNOWFLAKE_CONNECT_TIMEOUT > 120:
    SNOWFLAKE_CONNECT_TIMEOUT = 120

# Default must be changed. Blocks empty password bypass.
APP_PASSWORD = _get_secret("APP_PASSWORD", "CHANGE_ME___SET_ME")

# Swap to SSO later: replace APP_PASSWORD check with OAuth/SAML verify.
# SSO swap point: add verify_sso_token() here and call it in streamlit_app.
# In SiS (Streamlit in Snowflake) the viewer comes from st.user and the connection from
# st.connection("snowflake"), so APP_PASSWORD and the SNOWFLAKE_* keys are local-only.

EMBED_MODEL = "snowflake-arctic-embed-m-v1.5"
# Only these models allowed. Stops SQL inject via model name.
ALLOWED_EMBED_MODELS = ["snowflake-arctic-embed-m-v1.5"]
# LLM pick, checked 2026-10-01 on the regional availability doc:
# - Primary claude-sonnet-5: GA (generally available), 1M token context.
#   Needs cross-region inference (CORTEX_ENABLED_CROSS_REGION = ANY_REGION or AWS_US).
# - Draft + fallback llama3.1-8b: GA, cheap, native in some regions (e.g. AWS US West 2).
# - mistral-large3 is public preview there ("not suitable for production"). Not used.
# - Old mistral-large2 is legacy since Aug 12 2026, EOL "no sooner than Oct 14 2026". Not allowed.
# Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-regional-availability
# Docs: https://docs.snowflake.com/en/release-notes/bcr-bundles/un-bundled/bcr-august-model-deprecations
PRIMARY_MODEL = "claude-sonnet-5"
DRAFT_MODEL = "llama3.1-8b"
FALLBACK_MODEL = "llama3.1-8b"
# Audit label. Cortex puts the version in the model name, so we log the name.
# No made-up version tag.
PRIMARY_MODEL_VERSION = PRIMARY_MODEL
FALLBACK_MODEL_VERSION = DRAFT_MODEL
# Models this app no longer calls (old audit rows may name them):
# mistral-large2 = legacy at Snowflake; mistral-large3 = preview, dropped by this app.
DEPRECATED_LLM_MODELS = ["mistral-large2", "mistral-large3"]
ALLOWED_LLM_MODELS = ["claude-sonnet-5", "llama3.1-8b"]
# Single source for top K. Other files import from here. Do not copy.
TOP_K = 3
# Max tx rows pulled for evidence. Single source, no hard 5 in app.
EVIDENCE_TX_LIMIT = 5
# Rate cap per hour per user. Matches RATE_LIMIT table.
RATE_LIMIT_PER_HOUR = 50
# Local single-user demo only: approve without the connector-login officer role if TRUE.
# Default FALSE. Ignored in SiS mode (SiS always needs APP_OFFICERS). Prod must be FALSE.
ALLOW_LOCAL_APPROVE = _get_secret("ALLOW_LOCAL_APPROVE", "FALSE").upper() == "TRUE"
# Opt-in: use Cortex Search Service (hybrid search = vector + keyword + rerank) for law clauses.
# Default FALSE = old AI_EMBED cosine search. Needs sql/04_cortex_search.sql run first.
# Any error falls back to cosine search. Docs:
# https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/query-cortex-search-service
USE_CORTEX_SEARCH = _get_secret("USE_CORTEX_SEARCH", "FALSE").upper() == "TRUE"
CORTEX_SEARCH_SERVICE = "POLICY_SEARCH"
# Demo mode: fake sample data (backend/demo.py), no Snowflake, no AI call, no secrets.
# On if DEMO_MODE=TRUE, or when the app runs in a web browser (stlite / Pyodide,
# sys.platform == "emscripten"), which is how the public GitHub Pages demo runs.
# Never used inside SiS (Streamlit in Snowflake): hybrid_agent.demo_on() checks that.
DEMO_MODE = (_get_secret("DEMO_MODE", "FALSE").upper() == "TRUE") or sys.platform == "emscripten"


def use_key_pair() -> bool:
    """True if private key path is set. Simple English."""
    return bool(SNOWFLAKE_PRIVATE_KEY_PATH)


def is_app_password_ok() -> tuple:
    """Check password is strong. Simple English."""
    # Block empty and default. Need long password.
    if not APP_PASSWORD or APP_PASSWORD in ("", "CHANGE_ME___SET_ME"):
        return False, "Server password not set. Ask admin to set APP_PASSWORD."
    if len(str(APP_PASSWORD)) < 12:
        return False, "Set strong password. Min 12 chars."
    return True, ""


def validate_secrets(require_app_password: bool = True) -> None:
    """Fail fast at startup if secrets missing. Simple English."""
    # Call once at app start. Gives clear error, not silent fail later.
    # Local mode only. SiS mode skips this call (no keys needed, see backend/runtime.py).
    # require_app_password True for local mode: raise if weak.
    missing = []
    if not SNOWFLAKE_ACCOUNT:
        missing.append("SNOWFLAKE_ACCOUNT")
    if not SNOWFLAKE_USER:
        missing.append("SNOWFLAKE_USER")
    if not SNOWFLAKE_PRIVATE_KEY_PATH and not SNOWFLAKE_PASSWORD:
        missing.append("SNOWFLAKE_PRIVATE_KEY_PATH or SNOWFLAKE_PASSWORD")
    if missing:
        raise RuntimeError("Missing secrets: " + ", ".join(missing) + ". Set env vars or st.secrets.")
    if require_app_password:
        ok, msg = is_app_password_ok()
        if not ok:
            raise RuntimeError(msg)
    if ALLOW_LOCAL_APPROVE:
        # Warn loud. Prod must keep FALSE or any analyst can approve.
        logger.warning("ALLOW_LOCAL_APPROVE is TRUE. Prod must set FALSE.")
