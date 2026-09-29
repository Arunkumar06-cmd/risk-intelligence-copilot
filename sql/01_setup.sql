-- Risk Intel setup
-- Simple English comments.
-- Docs 2026-09-29:
-- https://docs.snowflake.com/en/sql-reference/functions/ai_embed
-- https://docs.snowflake.com/en/sql-reference/functions/ai_complete
-- https://docs.snowflake.com/en/sql-reference/functions/vector_cosine_similarity
-- https://docs.snowflake.com/en/user-guide/snowflake-cortex/aisql-regional-availability
-- https://docs.snowflake.com/en/user-guide/key-pair-auth
-- https://docs.snowflake.com/en/developer-guide/streamlit/getting-started/overview
-- https://docs.snowflake.com/en/sql-reference/functions/is_role_in_session
-- https://docs.snowflake.com/en/user-guide/search-optimization-service
-- Note on IDs: Snowflake INSERT does NOT give safe RETURNING in all drivers.
-- So we use UUID string for no race. Python makes uuid4, inserts it, returns it.

CREATE DATABASE IF NOT EXISTS RISK_INTELLIGENCE_DB;
CREATE SCHEMA IF NOT EXISTS RISK_INTELLIGENCE_DB.COMPLIANCE;
USE DATABASE RISK_INTELLIGENCE_DB;
USE SCHEMA COMPLIANCE;

-- Accounts table. Holds customer risk data.
CREATE TABLE IF NOT EXISTS ACCOUNTS (
  ACCOUNT_ID STRING PRIMARY KEY,
  CUSTOMER_NAME STRING,
  RISK_TIER STRING,
  COUNTRY STRING,
  CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Transactions table. Holds money moves.
CREATE TABLE IF NOT EXISTS TRANSACTIONS (
  TX_ID STRING PRIMARY KEY,
  ACCOUNT_ID STRING REFERENCES ACCOUNTS(ACCOUNT_ID),
  AMOUNT NUMBER(18,2),
  CURRENCY STRING,
  TX_DATE DATE,
  MERCHANT STRING,
  CHANNEL STRING
);

-- Fast finder for date + account lookups. Snowflake has no B-tree INDEX.
-- Search optimization is the Snowflake way for fast point lookup.
-- Docs: https://docs.snowflake.com/en/user-guide/search-optimization-service
ALTER TABLE IF EXISTS TRANSACTIONS ADD SEARCH OPTIMIZATION ON EQUALITY(ACCOUNT_ID);
ALTER TABLE IF EXISTS TRANSACTIONS ADD SEARCH OPTIMIZATION ON EQUALITY(TX_DATE);

-- Policy table. Uses real vector type for search.
-- AI_EMBED gives 768 length vector with arctic model.
-- Old EMBED_TEXT_768 is legacy EOL end 2026, use AI_EMBED now.
CREATE TABLE IF NOT EXISTS REGULATORY_POLICIES (
  POLICY_ID STRING PRIMARY KEY,
  TITLE STRING,
  CLAUSE_TEXT STRING,
  CLAUSE_VECTOR VECTOR(FLOAT, 768)
);

-- Audit log. Proof trail. Stores full chain.
-- LOG_UUID is main safe ID (uuid string, no race).
-- LOG_ID kept for old rows (backward compat).
-- QUERY_ID stores Snowflake query ID (cur.sfqid) for proof.
CREATE TABLE IF NOT EXISTS AUDIT_LOGS (
  LOG_UUID STRING PRIMARY KEY DEFAULT UUID_STRING(),
  LOG_ID NUMBER AUTOINCREMENT UNIQUE,
  CREATED_AT TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
  USER_ROLE STRING,
  QUERY_TEXT STRING,
  INTENT STRING,
  EVIDENCE_REFS VARIANT,
  POLICY_IDS VARIANT,
  MODEL_NAME STRING,
  MODEL_VERSION STRING,
  PROMPT_HASH STRING,
  RESULT_HASH STRING,
  REPORT_TEXT STRING,
  STATUS STRING,
  APPROVED_BY STRING,
  APPROVED_AT TIMESTAMP_NTZ,
  QUERY_ID STRING
);

-- Add new proof columns if old table exists. Safe re-run.
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS LOG_UUID STRING;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS EVIDENCE_REFS VARIANT;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS POLICY_IDS VARIANT;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS MODEL_NAME STRING;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS MODEL_VERSION STRING;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS PROMPT_HASH STRING;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS RESULT_HASH STRING;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS APPROVED_BY STRING;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS APPROVED_AT TIMESTAMP_NTZ;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS STATUS STRING;
ALTER TABLE IF EXISTS AUDIT_LOGS ADD COLUMN IF NOT EXISTS QUERY_ID STRING;

-- Fill missing UUID for old rows. No race, one time backfill.
UPDATE AUDIT_LOGS SET LOG_UUID = UUID_STRING() WHERE LOG_UUID IS NULL;

-- Row lock: hide RESTRICTED rows from analyst role.
-- Only officer and admin see all rows.
-- Use IS_ROLE_IN_SESSION so SiS role lift is checked by Snowflake, not app dropdown.
-- Docs: https://docs.snowflake.com/en/sql-reference/functions/is_role_in_session
CREATE OR REPLACE ROW ACCESS POLICY COMPLIANCE.ACCOUNTS_ROLE_FILTER
AS (RISK_TIER VARCHAR) RETURNS BOOLEAN ->
  CASE
    WHEN IS_ROLE_IN_SESSION('COMPLIANCE_OFFICER') THEN TRUE
    WHEN CURRENT_ROLE() IN ('COMPLIANCE_OFFICER', 'ACCOUNTADMIN', 'SYSADMIN') THEN TRUE
    WHEN IS_ROLE_IN_SESSION('ANALYST') AND (RISK_TIER IS NULL OR RISK_TIER != 'RESTRICTED') THEN TRUE
    WHEN CURRENT_ROLE() = 'ANALYST' AND (RISK_TIER IS NULL OR RISK_TIER != 'RESTRICTED') THEN TRUE
    ELSE FALSE
  END;
COMMENT ON ROW ACCESS POLICY COMPLIANCE.ACCOUNTS_ROLE_FILTER IS 'Row lock: analysts cannot see RESTRICTED tier. Officers see all.';

ALTER TABLE IF EXISTS ACCOUNTS ADD ROW ACCESS POLICY COMPLIANCE.ACCOUNTS_ROLE_FILTER ON (RISK_TIER);

-- Row lock for TRANSACTIONS via ACCOUNTS join on RISK_TIER.
-- Tx table has no tier col, so we look up parent account tier.
-- Alt prod note: dup RISK_TIER col into TRANSACTIONS for faster filter if join is slow.
CREATE OR REPLACE ROW ACCESS POLICY COMPLIANCE.TRANSACTIONS_ROLE_FILTER
AS (AID VARCHAR) RETURNS BOOLEAN ->
  CASE
    WHEN IS_ROLE_IN_SESSION('COMPLIANCE_OFFICER') THEN TRUE
    WHEN CURRENT_ROLE() IN ('COMPLIANCE_OFFICER', 'ACCOUNTADMIN', 'SYSADMIN') THEN TRUE
    WHEN (IS_ROLE_IN_SESSION('ANALYST') OR CURRENT_ROLE() = 'ANALYST')
      AND EXISTS (
        SELECT 1 FROM COMPLIANCE.ACCOUNTS A
        WHERE A.ACCOUNT_ID = AID
          AND (A.RISK_TIER IS NULL OR A.RISK_TIER != 'RESTRICTED')
      ) THEN TRUE
    ELSE FALSE
  END;
COMMENT ON ROW ACCESS POLICY COMPLIANCE.TRANSACTIONS_ROLE_FILTER IS 'Row lock: analyst sees tx only if parent account is not RESTRICTED. Join on ACCOUNTS.';

ALTER TABLE IF EXISTS TRANSACTIONS ADD ROW ACCESS POLICY COMPLIANCE.TRANSACTIONS_ROLE_FILTER ON (ACCOUNT_ID);

-- Hide private data: mask customer name for non-officer.
CREATE OR REPLACE MASKING POLICY COMPLIANCE.MASK_CUSTOMER_NAME
AS (VAL STRING) RETURNS STRING ->
  CASE
    WHEN CURRENT_ROLE() IN ('COMPLIANCE_OFFICER', 'ACCOUNTADMIN', 'SYSADMIN') THEN VAL
    ELSE '***masked***'
  END;
COMMENT ON MASKING POLICY COMPLIANCE.MASK_CUSTOMER_NAME IS 'Hide: mask customer_name unless officer.';

ALTER TABLE IF EXISTS ACCOUNTS MODIFY COLUMN CUSTOMER_NAME SET MASKING POLICY COMPLIANCE.MASK_CUSTOMER_NAME FORCE;

-- Table notes for data catalog.
COMMENT ON TABLE ACCOUNTS IS 'Use row lock + mask to protect PII.';
COMMENT ON TABLE TRANSACTIONS IS 'Money moves. Fast finder on TX_DATE + ACCOUNT_ID. Row lock via parent ACCOUNTS RISK_TIER join.';
COMMENT ON TABLE REGULATORY_POLICIES IS 'Policy clauses with 768 vector from AI_EMBED arctic model for cosine search.';
COMMENT ON TABLE AUDIT_LOGS IS 'Proof trail: must store evidence_refs, policy_ids, model, hashes, approver, query_id. LOG_UUID is safe ID.';

-- Column notes for each new proof col in audit trail.
COMMENT ON COLUMN AUDIT_LOGS.LOG_UUID IS 'Safe UUID string made in Python. No race.';
COMMENT ON COLUMN AUDIT_LOGS.LOG_ID IS 'Old auto number. Kept for backward compat.';
COMMENT ON COLUMN AUDIT_LOGS.EVIDENCE_REFS IS 'JSON list of TX_IDs used as evidence.';
COMMENT ON COLUMN AUDIT_LOGS.POLICY_IDS IS 'JSON list of POLICY_IDs cited.';
COMMENT ON COLUMN AUDIT_LOGS.MODEL_NAME IS 'LLM used: mistral-large3 or llama3.1-8b.';
COMMENT ON COLUMN AUDIT_LOGS.MODEL_VERSION IS 'Model version tag for audit.';
COMMENT ON COLUMN AUDIT_LOGS.PROMPT_HASH IS 'SHA256 of prompt plus model plus version.';
COMMENT ON COLUMN AUDIT_LOGS.RESULT_HASH IS 'SHA256 of report text.';
COMMENT ON COLUMN AUDIT_LOGS.STATUS IS 'DRAFT, APPROVED, or LOGIN_FAILED for denied login.';
COMMENT ON COLUMN AUDIT_LOGS.APPROVED_BY IS 'Officer user name who approved.';
COMMENT ON COLUMN AUDIT_LOGS.APPROVED_AT IS 'Time of approval.';
COMMENT ON COLUMN AUDIT_LOGS.QUERY_ID IS 'Snowflake query ID from cur.sfqid for proof. 2-step save.';

-- Key-pair auth: run once per user to add public key. Then use private key login.
-- Docs: https://docs.snowflake.com/en/user-guide/key-pair-auth
-- ALTER USER my_user SET RSA_PUBLIC_KEY='MIIBIj...';

-- Cortex Guard: use guardrails=>TRUE in AI_COMPLETE to turn on harm filter.
-- Docs: https://docs.snowflake.com/en/sql-reference/functions/ai_complete

-- Example: fill vector with new func. Keep model name exact.
-- AI_EMBED(model, text) returns VECTOR.
-- UPDATE REGULATORY_POLICIES
-- SET CLAUSE_VECTOR = SNOWFLAKE.CORTEX.AI_EMBED('snowflake-arctic-embed-m-v1.5', CLAUSE_TEXT)
-- WHERE CLAUSE_VECTOR IS NULL;

-- Example: LLM call with guardrails. Primary mistral-large3 (256K), draft llama3.1-8b.
-- SELECT SNOWFLAKE.CORTEX.AI_COMPLETE(model=>'mistral-large3', prompt=>'hello', guardrails=>TRUE);
-- Keep VECTOR_COSINE_SIMILARITY for search, do NOT swap to L2.
-- SELECT VECTOR_COSINE_SIMILARITY(CLAUSE_VECTOR, SNOWFLAKE.CORTEX.AI_EMBED('snowflake-arctic-embed-m-v1.5', 'what is SAR')) FROM REGULATORY_POLICIES LIMIT 3;
