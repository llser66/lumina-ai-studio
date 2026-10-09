BEGIN IMMEDIATE;
CREATE TABLE users (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  csrf_hash TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE INDEX session_expiry ON sessions(expires_at);
CREATE TABLE jobs (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
  prompt TEXT NOT NULL, enhanced_prompt TEXT, negative_prompt TEXT NOT NULL DEFAULT '',
  style TEXT NOT NULL, size TEXT NOT NULL, quality TEXT NOT NULL,
  enhance INTEGER NOT NULL, reference_id TEXT, model TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', favorite INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL,
  owner TEXT, lease_until REAL, provider_started_at REAL,
  provider_request_id TEXT, asset_id TEXT, error_code TEXT, error_message TEXT,
  deleted_at REAL, UNIQUE(user_id, idempotency_key)
);
CREATE INDEX jobs_user_time ON jobs(user_id, created_at DESC);
CREATE INDEX jobs_queue ON jobs(status, created_at);
CREATE TABLE assets (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  job_id TEXT UNIQUE REFERENCES jobs(id), path TEXT NOT NULL,
  width INTEGER NOT NULL, height INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
  stage TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX events_job ON events(job_id, id);
CREATE TABLE rate_limits (
  key TEXT PRIMARY KEY, window_start REAL NOT NULL, count INTEGER NOT NULL
);
PRAGMA user_version = 1;
COMMIT;
