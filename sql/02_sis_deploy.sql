-- SiS deploy: Streamlit in Snowflake. Simple English.
-- Docs: https://docs.snowflake.com/en/developer-guide/streamlit/getting-started/overview
USE DATABASE RISK_INTELLIGENCE_DB;
USE SCHEMA COMPLIANCE;

-- Stage for app files. Upload streamlit_app.py and backend/ here.
CREATE STAGE IF NOT EXISTS RISK_APP_STAGE;
COMMENT ON STAGE RISK_APP_STAGE IS 'Stage that holds streamlit_app.py and backend files for SiS deploy.';
-- PUT file:///local/path/streamlit_app.py @RISK_APP_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

-- Main app. Runs inside Snowflake, uses CURRENT_USER, no APP_PASSWORD.
-- Query warehouse for SiS runtime.
CREATE STREAMLIT IF NOT EXISTS RISK_COPILOT_APP
  FROM @RISK_APP_STAGE
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = 'COMPUTE_WH'
  COMMENT = 'Risk copilot SiS app. Uses st.user login.';
COMMENT ON STREAMLIT RISK_COPILOT_APP IS 'Risk copilot SiS app. Runs in Snowflake with CURRENT_USER auth. See stage RISK_APP_STAGE.';

-- Prod note: for container runtime (Native App with Python 3.11), use:
-- SELECT SYSTEM$ST_CONTAINER_RUNTIME_PY3_11('CHECK');
-- SiS containers need ACCOUNTADMIN to grant CREATE COMPUTE POOL.
-- SHOW STREAMLITS LIKE 'RISK_COPILOT_APP';
