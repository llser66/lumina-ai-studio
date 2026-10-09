BEGIN IMMEDIATE;
ALTER TABLE model_connections ADD COLUMN base_url TEXT NOT NULL DEFAULT '';
ALTER TABLE model_connections ADD COLUMN model_id TEXT NOT NULL DEFAULT '';
ALTER TABLE model_connections ADD COLUMN model_name TEXT NOT NULL DEFAULT '';
ALTER TABLE model_connections ADD COLUMN supports_image INTEGER NOT NULL DEFAULT 0 CHECK(supports_image IN (0,1));
PRAGMA user_version = 4;
COMMIT;
