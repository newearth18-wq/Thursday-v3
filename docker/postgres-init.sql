-- Extensions Thursday needs. Created at first boot so a fresh volume is ready for the
-- first migration rather than failing on it.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- trigram search over memory content and filenames
