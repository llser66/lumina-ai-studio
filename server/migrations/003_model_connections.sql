BEGIN IMMEDIATE;
CREATE TABLE model_connections (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  provider TEXT NOT NULL, name TEXT NOT NULL, auth_method TEXT NOT NULL,
  credential TEXT NOT NULL, hint TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, checked_at REAL,
  status TEXT NOT NULL DEFAULT 'configured', revoked_at REAL
);
CREATE INDEX model_connections_owner ON model_connections(user_id, revoked_at);
CREATE TABLE model_preferences (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  image_connection_id TEXT REFERENCES model_connections(id),
  image_model TEXT NOT NULL DEFAULT '',
  text_connection_id TEXT REFERENCES model_connections(id),
  text_model TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL
);
ALTER TABLE jobs ADD COLUMN image_connection_id TEXT REFERENCES model_connections(id);
ALTER TABLE jobs ADD COLUMN image_connection_version INTEGER;
ALTER TABLE jobs ADD COLUMN text_connection_id TEXT REFERENCES model_connections(id);
ALTER TABLE jobs ADD COLUMN text_connection_version INTEGER;
ALTER TABLE jobs ADD COLUMN text_model TEXT;
PRAGMA user_version = 3;
COMMIT;
