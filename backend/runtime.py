"""Where does the app run? Local (own login) or SiS (Streamlit in Snowflake). Simple English.

Proof used for each check (docs + installed source, checked 2026-10-01):
- Warehouse runtime: streamlit.connections.util.running_in_sis() (Streamlit 1.64.0 source)
  returns snowpark is_in_stored_procedure(). Warehouse-runtime SiS apps run as a stored procedure.
- Container runtime: the SiS docs read env var SNOWFLAKE_HOST and the token file
  /snowflake/session/token inside the container. We need both.
  Docs: https://docs.snowflake.com/en/developer-guide/streamlit/app-development/secrets-and-configuration
- Viewer name: st.user.user_name = the viewer's Snowflake user name in SiS (both runtimes).
  Docs: https://docs.snowflake.com/en/developer-guide/streamlit/app-development/personalization
- Connection: st.connection("snowflake") works in both runtimes (get_active_session is not
  thread-safe in container runtime). We use its .raw_connection (a Python-connector connection),
  so the same DB-API cursor code runs local and in SiS.
  Docs: https://docs.snowflake.com/en/developer-guide/streamlit/migrations-and-upgrades/runtime-migration
"""
import logging
import os

logger = logging.getLogger(__name__)

SIS_TOKEN_FILE = "/snowflake/session/token"


def _running_in_sis_warehouse() -> bool:
    """True inside a warehouse-runtime SiS app (stored procedure). Simple English."""
    try:
        from streamlit.connections.util import running_in_sis
        return bool(running_in_sis())
    except Exception:
        return False


def _running_in_sis_container() -> bool:
    """True inside a container-runtime SiS app (SPCS host + token file). Simple English."""
    return bool(os.getenv("SNOWFLAKE_HOST")) and os.path.exists(SIS_TOKEN_FILE)


def is_sis_runtime() -> bool:
    """True if this process runs inside Streamlit in Snowflake. Simple English."""
    return _running_in_sis_warehouse() or _running_in_sis_container()


def sis_viewer_name() -> str:
    """Viewer's Snowflake user name from st.user. Empty string if not found. Simple English."""
    # Read fresh on each call (server side). Never from a dropdown or session cache.
    try:
        import streamlit as st
        u = getattr(st, "user", None)
        if u is None:
            return ""
        name = u.get("user_name", "")  # Mapping.get: missing key -> default
        return str(name or "").strip()
    except Exception:
        return ""


def sis_connection():
    """Python-connector connection from st.connection("snowflake"). Simple English."""
    # Streamlit owns and caches this connection. Do not close it.
    import streamlit as st
    return st.connection("snowflake").raw_connection
