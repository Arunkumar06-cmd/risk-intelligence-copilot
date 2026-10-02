-- SiS deploy: Streamlit in Snowflake. Simple English.
--
-- Status: BUILT + UNIT-TESTED, NOT LIVE-TESTED. No Snowflake account was used to run this file
-- or the app inside Snowflake. Check each step in Snowsight.
-- How the app runs in SiS (backend/runtime.py):
-- - Detects SiS from the runtime (stored procedure = warehouse runtime, or SPCS host + token
--   file = container runtime). Then it skips the secrets check and uses
--   st.connection("snowflake") (Snowflake's own session). No keys are stored in the app.
-- - Viewer = st.user.user_name. Approval needs that user in APP_OFFICERS (sql/01).
-- - Non-officers get no RESTRICTED-tier rows (app-side filter).
-- Trial note: compute pool (container host) access on a trial account is not confirmed.
-- Docs: https://docs.snowflake.com/en/developer-guide/streamlit/app-development/secrets-and-configuration
-- Docs: https://docs.snowflake.com/en/developer-guide/streamlit/app-development/runtime-environments
-- Docs: https://docs.snowflake.com/en/sql-reference/sql/create-streamlit
-- Docs: https://docs.snowflake.com/en/developer-guide/streamlit/app-development/dependency-management
-- Docs: https://docs.snowflake.com/en/release-notes/bcr-bundles/2026_06/bcr-2342
USE DATABASE RISK_INTELLIGENCE_DB;
USE SCHEMA COMPLIANCE;

-- Stage for app files. Upload streamlit_app.py, backend/ and requirements.txt here.
-- requirements.txt holds runtime pins only (pytest/ruff are in requirements-dev.txt, not uploaded).
CREATE STAGE IF NOT EXISTS RISK_APP_STAGE;
COMMENT ON STAGE RISK_APP_STAGE IS 'Stage that holds streamlit_app.py, backend files and requirements.txt for SiS deploy.';
-- PUT file:///local/path/streamlit_app.py @RISK_APP_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
-- PUT file:///local/path/backend/*.py @RISK_APP_STAGE/backend AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
-- PUT file:///local/path/requirements.txt @RISK_APP_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

-- Why container runtime (app runs in a Snowpark container, not in a warehouse):
-- - requirements.txt pins streamlit==1.64.0. Container runtime takes any Streamlit 1.50+
--   from PyPI. Warehouse runtime only has a limited Streamlit list from the Snowflake Conda channel.
-- - Bundle 2026_06 (BCR-2342, status "Pending") makes container runtime the default when
--   RUNTIME_NAME is left out. We set it on purpose so the result is the same either way.

-- Step 1 (admin, once). FILL AND RUN: replace <APP_OWNER_ROLE> with the role that will own the app.
-- Use a separate role (not the admin role that owns APP_OFFICERS), so the app cannot edit the list.
-- 1a. Install pip packages from Snowflake's PyPI mirror:
-- GRANT DATABASE ROLE SNOWFLAKE.PYPI_REPOSITORY_USER TO ROLE <APP_OWNER_ROLE>;
-- 1b. Call Cortex AI functions (AI_EMBED, AI_COMPLETE):
-- GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE <APP_OWNER_ROLE>;
-- 1c. Context functions + row access policies inside SiS need READ SESSION (SiS row-access doc):
-- GRANT READ SESSION ON ACCOUNT TO ROLE <APP_OWNER_ROLE>;
-- 1d. Data access for the app:
-- GRANT USAGE ON DATABASE RISK_INTELLIGENCE_DB TO ROLE <APP_OWNER_ROLE>;
-- GRANT USAGE ON SCHEMA RISK_INTELLIGENCE_DB.COMPLIANCE TO ROLE <APP_OWNER_ROLE>;
-- GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE <APP_OWNER_ROLE>;
-- GRANT SELECT ON TABLE ACCOUNTS TO ROLE <APP_OWNER_ROLE>;
-- GRANT SELECT ON TABLE TRANSACTIONS TO ROLE <APP_OWNER_ROLE>;
-- GRANT SELECT ON TABLE REGULATORY_POLICIES TO ROLE <APP_OWNER_ROLE>;
-- GRANT SELECT ON TABLE APP_OFFICERS TO ROLE <APP_OWNER_ROLE>;
-- GRANT SELECT, INSERT, UPDATE ON TABLE AUDIT_LOGS TO ROLE <APP_OWNER_ROLE>;
-- GRANT SELECT, INSERT, UPDATE ON TABLE RATE_LIMIT TO ROLE <APP_OWNER_ROLE>;
-- 1e. Row policies in sql/01 check the session role. In SiS that is the owner role for every
--     viewer. Choose one (the app hides RESTRICTED rows per viewer either way):
--     - GRANT ROLE COMPLIANCE_OFFICER TO ROLE <APP_OWNER_ROLE>;  -- officers can see RESTRICTED rows
--     - GRANT ROLE ANALYST TO ROLE <APP_OWNER_ROLE>;             -- nobody sees RESTRICTED rows in SiS
--     (A custom owner role with neither: the sql/01 policies return FALSE, so the app sees no rows.)
-- 1f. Let the owner role create the app from the stage (CREATE STREAMLIT doc, access control):
-- GRANT CREATE STREAMLIT ON SCHEMA RISK_INTELLIGENCE_DB.COMPLIANCE TO ROLE <APP_OWNER_ROLE>;
-- GRANT READ ON STAGE RISK_INTELLIGENCE_DB.COMPLIANCE.RISK_APP_STAGE TO ROLE <APP_OWNER_ROLE>;

-- Step 2 (admin, once). FILL AND RUN: a compute pool (container host machines) named RISK_APP_POOL.
-- CREATE COMPUTE POOL IF NOT EXISTS RISK_APP_POOL MIN_NODES = 1 MAX_NODES = 1 INSTANCE_FAMILY = CPU_X64_XS;
-- GRANT USAGE ON COMPUTE POOL RISK_APP_POOL TO ROLE <APP_OWNER_ROLE>;

-- Step 3 (admin, once). Query time cap. In SiS the app skips ALTER SESSION (warehouse-runtime
-- apps run as owner's-rights stored procedures, which cannot set session parameters),
-- so set the cap on the warehouse instead:
-- ALTER WAREHOUSE COMPUTE_WH SET STATEMENT_TIMEOUT_IN_SECONDS = 120;

-- Step 4 runs as the app owner role, so the app is NOT owned by the admin role.
-- FILL AND RUN (before Step 4): switch to the owner role.
-- USE ROLE <APP_OWNER_ROLE>;

-- Step 4: main app. OR REPLACE = safe re-run (an old app is replaced, not silently kept).
-- QUERY_WAREHOUSE runs the SQL; the compute pool runs the Python code.
-- ARTIFACT_REPOSITORIES attaches Snowflake's PyPI mirror so requirements.txt can install.
-- This clause inside CREATE STREAMLIT is shown in the dependency-management doc example
-- (the CREATE STREAMLIT reference syntax block does not list it yet).
CREATE OR REPLACE STREAMLIT RISK_COPILOT_APP
  FROM @RISK_APP_STAGE
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = COMPUTE_WH
  RUNTIME_NAME = 'SYSTEM$ST_CONTAINER_RUNTIME_PY3_11'
  COMPUTE_POOL = RISK_APP_POOL
  ARTIFACT_REPOSITORIES = (snowflake.snowpark.pypi_shared_repository)
  COMMENT = 'Risk copilot SiS app (container runtime). Built + unit-tested, not live-tested.';

-- Step 4b: make the app live. CREATE STREAMLIT alone does not; the doc says run this
-- (or open the app in Snowsight with the owner role).
-- Docs: https://docs.snowflake.com/en/sql-reference/sql/create-streamlit (usage notes)
ALTER STREAMLIT RISK_COPILOT_APP ADD LIVE VERSION FROM LAST;

-- Step 5 (app owner role or admin). Who can open the app. FILL AND RUN: replace <VIEWER_ROLE> (SiS privileges doc).
-- GRANT USAGE ON DATABASE RISK_INTELLIGENCE_DB TO ROLE <VIEWER_ROLE>;
-- GRANT USAGE ON SCHEMA RISK_INTELLIGENCE_DB.COMPLIANCE TO ROLE <VIEWER_ROLE>;
-- GRANT USAGE ON STREAMLIT RISK_INTELLIGENCE_DB.COMPLIANCE.RISK_COPILOT_APP TO ROLE <VIEWER_ROLE>;
-- SHOW STREAMLITS LIKE 'RISK_COPILOT_APP';
