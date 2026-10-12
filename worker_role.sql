-- Run ONCE in the Neon SQL editor as the owner. Replace the password with a long random one.
-- Worker machines get THIS role's connection string - never the owner's.
-- It can add data but cannot read the case table, drop anything, or touch other databases.

CREATE ROLE court_worker LOGIN PASSWORD 'CHANGE_ME_LONG_RANDOM';
GRANT CONNECT ON DATABASE neondb TO court_worker;
GRANT USAGE ON SCHEMA public TO court_worker;

GRANT INSERT ON cases TO court_worker;                               -- insert-only (ON CONFLICT DO NOTHING)
GRANT SELECT, INSERT, UPDATE ON name_summary, scraped_pages TO court_worker;   -- these two are upserts
GRANT INSERT ON failed_tasks TO court_worker;
GRANT USAGE, SELECT ON SEQUENCE failed_tasks_id_seq TO court_worker;

-- Worker connection string (use the POOLED host, same as the owner's):
--   postgresql://court_worker:PASSWORD@<same -pooler host>/neondb?sslmode=require&channel_binding=require
-- If a worker logs "permission denied for table ...", tell the developer which table.
