-- Rate limit table. Strict global limit for prod. Simple English.
-- Docs: https://docs.snowflake.com/en/sql-reference/sql/merge
-- Docs: https://docs.snowflake.com/en/sql-reference/sql/create-task
-- Docs: https://docs.snowflake.com/en/user-guide/search-optimization-service
USE DATABASE RISK_INTELLIGENCE_DB;
USE SCHEMA COMPLIANCE;

-- One row per user per hour window. App checks and bumps count.
-- Block if HIT_COUNT > 50 per hour.
CREATE TABLE IF NOT EXISTS RATE_LIMIT (
  USER_NAME STRING,
  WINDOW_START TIMESTAMP_NTZ,
  HIT_COUNT NUMBER DEFAULT 1,
  PRIMARY KEY (USER_NAME, WINDOW_START)
);

-- Note for table purpose.
COMMENT ON TABLE RATE_LIMIT IS 'Global rate cap: one row per user per hour. Block if HIT_COUNT over 50. Cleaned by TASK.';

-- Fast finder on user name. Snowflake has no B-tree INDEX, use search optimization.
ALTER TABLE IF EXISTS RATE_LIMIT ADD SEARCH OPTIMIZATION ON EQUALITY(USER_NAME);

-- Real MERGE: combine (upsert) hit count per user per hour.
-- Python runs this MERGE, then reads HIT_COUNT. Block if > 50.
-- MERGE INTO RATE_LIMIT t
-- USING (SELECT ? AS U, DATE_TRUNC('hour', CURRENT_TIMESTAMP()) AS W) s
-- ON t.USER_NAME = s.U AND t.WINDOW_START = s.W
-- WHEN MATCHED THEN UPDATE SET HIT_COUNT = t.HIT_COUNT + 1
-- WHEN NOT MATCHED THEN INSERT (USER_NAME, WINDOW_START, HIT_COUNT) VALUES (s.U, s.W, 1);

-- Timed del job: delete old windows older than 7 days.
CREATE TASK IF NOT EXISTS CLEANUP_RATE_LIMIT
  WAREHOUSE = COMPUTE_WH
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  COMMENT = 'Timed del job: clears RATE_LIMIT rows older than 7 days.'
AS DELETE FROM RATE_LIMIT WHERE WINDOW_START < DATEADD(day, -7, CURRENT_TIMESTAMP());

-- Start the timed job. Task is paused by default, must resume.
ALTER TASK IF EXISTS CLEANUP_RATE_LIMIT RESUME;
