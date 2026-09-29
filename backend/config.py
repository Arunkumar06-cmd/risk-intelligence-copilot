"""Load secrets. No hardcode. Simple English."""
import logging
import os

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
# In SiS (Streamlit in Snowflake) auth uses st.user, so APP_PASSWORD is local-only.

EMBED_MODEL = "snowflake-arctic-embed-m-v1.5"
# Only these models allowed. Stops SQL inject via model name.
ALLOWED_EMBED_MODELS = ["snowflake-arctic-embed-m-v1.5"]
# LLM swap 2026: primary mistral-large3 (256K context), cheap draft llama3.1-8b.
# Old mistral-large2 is legacy (EOL Oct 14 2026). Not allowed for new calls.
# Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-regional-availability
PRIMARY_MODEL = "mistral-large3"
DRAFT_MODEL = "llama3.1-8b"
FALLBACK_MODEL = "llama3.1-8b"
PRIMARY_MODEL_VERSION = "v3-2026-256K"
FALLBACK_MODEL_VERSION = "llama3.1-8b-2026-draft"
# Only live models. Old mistral-large2 kept as deprecated const for old rows.
DEPRECATED_LLM_MODELS = ["mistral-large2"]
ALLOWED_LLM_MODELS = ["mistral-large3", "llama3.1-8b"]
# Single source for top K. Other files import from here. Do not copy.
TOP_K = 3
# Max tx rows pulled for evidence. Single source, no hard 5 in app.
EVIDENCE_TX_LIMIT = 5
# Rate cap per hour per user. Matches RATE_LIMIT table.
RATE_LIMIT_PER_HOUR = 50
# Local demo can approve without officer role if TRUE. Prod must be FALSE.
ALLOW_LOCAL_APPROVE = _get_secret("ALLOW_LOCAL_APPROVE", "FALSE").upper() == "TRUE"


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
    # require_app_password True for local mode: raise if weak.
    # SiS mode passes False because SiS uses st.user, not APP_PASSWORD.
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
