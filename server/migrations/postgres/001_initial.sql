CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  csrf_hash TEXT NOT NULL,
  expires_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX session_expiry ON sessions(expires_at);
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  idempotency_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  prompt TEXT NOT NULL,
  enhanced_prompt TEXT,
  negative_prompt TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL,
  size TEXT NOT NULL,
  quality TEXT NOT NULL,
  enhance INTEGER NOT NULL,
  reference_id TEXT,
  model TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  favorite INTEGER NOT NULL DEFAULT 0,
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL,
  finished_at DOUBLE PRECISION,
  owner TEXT,
  lease_until DOUBLE PRECISION,
  provider_started_at DOUBLE PRECISION,
  provider_request_id TEXT,
  asset_id TEXT,
  error_code TEXT,
  error_message TEXT,
  deleted_at DOUBLE PRECISION,
  recovery_count INTEGER NOT NULL DEFAULT 0,
  image_connection_id TEXT,
  image_connection_version INTEGER,
  text_connection_id TEXT,
  text_connection_version INTEGER,
  text_model TEXT,
  UNIQUE(user_id, idempotency_key)
);
CREATE INDEX jobs_user_time ON jobs(user_id, created_at DESC);
CREATE INDEX jobs_queue ON jobs(status, created_at);
CREATE TABLE assets (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  job_id TEXT UNIQUE REFERENCES jobs(id),
  path TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  stage TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX events_job ON events(job_id, id);
CREATE TABLE rate_limits (
  key TEXT PRIMARY KEY,
  window_start DOUBLE PRECISION NOT NULL,
  count INTEGER NOT NULL
);
CREATE TABLE model_connections (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  provider TEXT NOT NULL,
  name TEXT NOT NULL,
  auth_method TEXT NOT NULL,
  credential TEXT NOT NULL,
  hint TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  created_at DOUBLE PRECISION NOT NULL,
  updated_at DOUBLE PRECISION NOT NULL,
  checked_at DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'configured',
  revoked_at DOUBLE PRECISION,
  base_url TEXT NOT NULL DEFAULT '',
  model_id TEXT NOT NULL DEFAULT '',
  model_name TEXT NOT NULL DEFAULT '',
  supports_image INTEGER NOT NULL DEFAULT 0 CHECK(supports_image IN (0,1))
);
CREATE INDEX model_connections_owner ON model_connections(user_id, revoked_at);
CREATE TABLE model_preferences (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  image_connection_id TEXT REFERENCES model_connections(id),
  image_model TEXT NOT NULL DEFAULT '',
  text_connection_id TEXT REFERENCES model_connections(id),
  text_model TEXT NOT NULL DEFAULT '',
  updated_at DOUBLE PRECISION NOT NULL
);
ALTER TABLE jobs ADD CONSTRAINT jobs_image_connection_fk
  FOREIGN KEY (image_connection_id) REFERENCES model_connections(id);
ALTER TABLE jobs ADD CONSTRAINT jobs_text_connection_fk
  FOREIGN KEY (text_connection_id) REFERENCES model_connections(id);
