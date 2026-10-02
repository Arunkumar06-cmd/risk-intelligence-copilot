-- OPTIONAL: Cortex Search Service for law clauses. Simple English.
-- Cortex Search = hybrid search (vector meaning match + keyword match + rerank, a second-pass re-score).
-- App uses it only when USE_CORTEX_SEARCH = TRUE (env var or st.secrets). Default is FALSE.
-- On any error the app falls back to the AI_EMBED cosine search in hybrid_agent.vector_search.
-- NOT TESTED: no Snowflake account was used to run this file.
-- Docs: https://docs.snowflake.com/en/sql-reference/sql/create-cortex-search
-- Docs: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/query-cortex-search-service
USE DATABASE RISK_INTELLIGENCE_DB;
USE SCHEMA COMPLIANCE;

-- Change tracking (row change log) lets the service refresh in small steps.
-- Snowflake tries to turn it on by itself; we set it on purpose.
ALTER TABLE REGULATORY_POLICIES SET CHANGE_TRACKING = TRUE;

-- Search on CLAUSE_TEXT. POLICY_ID + TITLE come back with each hit.
-- Same embed model as the app (snowflake-arctic-embed-m-v1.5).
-- TARGET_LAG = how stale the index may get. Policy text changes rarely, so 1 day.
CREATE CORTEX SEARCH SERVICE IF NOT EXISTS POLICY_SEARCH
  ON CLAUSE_TEXT
  ATTRIBUTES POLICY_ID
  WAREHOUSE = COMPUTE_WH
  TARGET_LAG = '1 day'
  EMBEDDING_MODEL = 'snowflake-arctic-embed-m-v1.5'
AS SELECT POLICY_ID, TITLE, CLAUSE_TEXT FROM REGULATORY_POLICIES;

-- The app role needs USAGE on the service to query it. Replace <APP_ROLE> (e.g. ANALYST).
-- GRANT USAGE ON CORTEX SEARCH SERVICE POLICY_SEARCH TO ROLE <APP_ROLE>;

-- Do NOT use SNOWFLAKE.CORTEX.SEARCH_PREVIEW in the app: docs say it is for testing and
-- validation (slower), and it only takes constant arguments. App uses the Python API instead.
-- Cost: Cortex Search bills per GB per month of indexed data (Snowflake Service Consumption Table).
-- DESCRIBE CORTEX SEARCH SERVICE POLICY_SEARCH;
