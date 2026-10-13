import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

TERMINAL = {"succeeded", "failed", "cancelled", "uncertain"}
ACTIVE = {"validating", "enhancing", "moderating", "generating", "saving"}
POSTGRES_LOCK_ID = 6_503_105_007_401_002


def postgres_sql(sql):
    """Translate the small SQLite SQL subset used by the app to PostgreSQL syntax."""
    translated = sql.strip()
    ignored_insert = re.match(r"^INSERT\s+OR\s+IGNORE\s+", translated, flags=re.IGNORECASE)
    if ignored_insert:
        translated = "INSERT " + translated[ignored_insert.end() :]
        if not re.search(r"\sON\s+CONFLICT\b", translated, flags=re.IGNORECASE):
            translated += " ON CONFLICT DO NOTHING"
    return translated.replace("?", "%s")


class _PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, args=()):
        return self.connection.execute(postgres_sql(sql), args)


class Conflict(Exception):
    pass


class QuotaExceeded(Exception):
    pass


class GlobalQuotaExceeded(Exception):
    pass


class Database:
    """A transactional single-node queue. Quota reservation and enqueue are atomic."""

    def __init__(self, path: Path | str):
        raw_path = str(path)
        self.is_postgres = raw_path.startswith(("postgres://", "postgresql://"))
        self.path = raw_path if self.is_postgres else Path(path)
        self.integrity_errors = (sqlite3.IntegrityError,)
        self.database_errors = (sqlite3.Error,)
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("PostgreSQL mode requires psycopg 3") from exc
            self._psycopg = psycopg
            self._dict_row = dict_row
            self.integrity_errors = (psycopg.IntegrityError,)
            self.database_errors = (psycopg.Error,)
            self._initialize_postgres()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > 4:
                raise RuntimeError("Unsupported database schema version")
            for target, filename in [
                (1, "001_initial.sql"),
                (2, "002_recovery_limit.sql"),
                (3, "003_model_connections.sql"),
                (4, "004_custom_connections.sql"),
            ]:
                if version < target:
                    migration = Path(__file__).parent / "migrations" / filename
                    connection.executescript(migration.read_text(encoding="utf-8"))

    def _initialize_postgres(self):
        migration = Path(__file__).parent / "migrations" / "postgres" / "001_initial.sql"
        with self.connect(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS lumina_schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at DOUBLE PRECISION NOT NULL)"
            )
            version = connection.execute(
                "SELECT version FROM lumina_schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if version and version["version"] >= 1:
                return
            for statement in migration.read_text(encoding="utf-8").split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "INSERT INTO lumina_schema_migrations(version,applied_at) VALUES(1,?)",
                (time.time(),),
            )

    @contextmanager
    def connect(self, *, write=False):
        if self.is_postgres:
            connection = self._psycopg.connect(
                self.path, autocommit=True, row_factory=self._dict_row
            )
            try:
                if write:
                    connection.execute("BEGIN")
                    connection.execute("SELECT pg_advisory_xact_lock(%s)", (POSTGRES_LOCK_ID,))
                yield _PostgresConnection(connection)
                if write:
                    connection.execute("COMMIT")
            except BaseException:
                if write:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
            return
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def one(self, sql, args=()):
        with self.connect() as connection:
            row = connection.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.connect(write=True) as connection:
            return connection.execute(sql, args).rowcount

    @staticmethod
    def day_start():
        # Budget windows are explicit UTC calendar days across deployments.
        return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def quota(self, user_id, limit):
        row = self.one(
            "SELECT COUNT(*) AS used FROM jobs WHERE user_id=? AND created_at>=? "
            "AND status NOT IN ('failed','cancelled')",
            (user_id, self.day_start()),
        )
        return {
            "limit": limit,
            "used": row["used"],
            "remaining": max(0, limit - row["used"]),
            "resets_at": self.day_start() + 86400,
        }

    def create_job(
        self, user_id, spec, idempotency_key, model, limit, global_limit=200, bindings=None
    ):
        payload = spec.model_dump(mode="json")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        now = time.time()
        with self.connect(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM jobs WHERE user_id=? AND idempotency_key=?",
                (user_id, idempotency_key),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise Conflict("同一个请求编号不能用于不同的创作参数")
                return dict(existing), False
            used = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE user_id=? AND created_at>=? AND status NOT IN ('failed','cancelled')",
                (user_id, self.day_start()),
            ).fetchone()["count"]
            if used >= limit:
                raise QuotaExceeded()
            global_used = connection.execute(
                "SELECT COUNT(*) AS count FROM jobs WHERE created_at>=? AND status NOT IN ('failed','cancelled')",
                (self.day_start(),),
            ).fetchone()["count"]
            if global_used >= global_limit:
                raise GlobalQuotaExceeded()
            # Validate ownership in the same transaction as enqueue.
            if (
                payload["reference_id"]
                and not connection.execute(
                    "SELECT id FROM assets WHERE id=? AND user_id=?",
                    (payload["reference_id"], user_id),
                ).fetchone()
            ):
                raise Conflict("参考图片不存在或不属于当前账号")
            job_id = str(uuid.uuid4())
            if bindings:
                for role in ("image", "text"):
                    identifier = bindings.get(role + "_connection_id")
                    if (
                        identifier
                        and not connection.execute(
                            "SELECT id FROM model_connections WHERE id=? AND user_id=? AND version=? AND revoked_at IS NULL",
                            (identifier, user_id, bindings[role + "_connection_version"]),
                        ).fetchone()
                    ):
                        raise Conflict("模型连接已变更，请重新选择后再提交")
            connection.execute(
                "INSERT INTO jobs(id,user_id,idempotency_key,fingerprint,prompt,negative_prompt,style,size,quality,enhance,reference_id,model,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    user_id,
                    idempotency_key,
                    fingerprint,
                    payload["prompt"],
                    payload["negative_prompt"],
                    payload["style"],
                    payload["size"],
                    payload["quality"],
                    int(payload["enhance"]),
                    payload["reference_id"],
                    model,
                    now,
                    now,
                ),
            )
            if bindings:
                columns = (
                    "image_connection_id",
                    "image_connection_version",
                    "text_connection_id",
                    "text_connection_version",
                    "text_model",
                )
                connection.execute(
                    "UPDATE jobs SET " + ",".join(name + "=?" for name in columns) + " WHERE id=?",
                    (*[bindings.get(name) for name in columns], job_id),
                )
            connection.execute(
                "INSERT INTO events(job_id,stage,message,created_at) VALUES(?,?,?,?)",
                (job_id, "queued", "创作已加入队列", now),
            )
            return dict(
                connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            ), True

    def claim(self, owner):
        with self.connect(write=True) as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not job:
                return None
            connection.execute(
                "UPDATE jobs SET status='validating',owner=?,lease_until=?,updated_at=? WHERE id=?",
                (owner, time.time() + 60, time.time(), job["id"]),
            )
            return dict(job)

    def heartbeat(self, job_id, owner):
        self.execute(
            "UPDATE jobs SET lease_until=? WHERE id=? AND owner=? AND status NOT IN ('succeeded','failed','cancelled','uncertain')",
            (time.time() + 60, job_id, owner),
        )

    def stage(self, job_id, stage, message, **fields):
        allowed = {
            "enhanced_prompt",
            "provider_started_at",
            "provider_request_id",
            "asset_id",
            "error_code",
            "error_message",
            "finished_at",
        }
        if not set(fields).issubset(allowed):
            raise ValueError("Invalid job field")
        now = time.time()
        assignments = ["status=?", "updated_at=?"] + [f"{name}=?" for name in fields]
        with self.connect(write=True) as connection:
            connection.execute(
                f"UPDATE jobs SET {','.join(assignments)} WHERE id=?",
                (stage, now, *fields.values(), job_id),
            )
            connection.execute(
                "INSERT INTO events(job_id,stage,message,created_at) VALUES(?,?,?,?)",
                (job_id, stage, message, now),
            )

    def fail(self, job_id, code, message, *, uncertain=False):
        self.stage(
            job_id,
            "uncertain" if uncertain else "failed",
            message,
            error_code=code,
            error_message=message,
            finished_at=time.time(),
        )

    def recover(self, job_id):
        row = self.one("SELECT recovery_count,provider_started_at FROM jobs WHERE id=?", (job_id,))
        if row["recovery_count"] >= 2:
            self.fail(
                job_id,
                "recovery_exhausted",
                "归档恢复未完成，需要管理员核对已保存的图片。",
                uncertain=bool(row["provider_started_at"]),
            )
            return
        self.execute("UPDATE jobs SET recovery_count=recovery_count+1 WHERE id=?", (job_id,))
        self.stage(job_id, "queued", "工作进程恢复，将继续已保存的步骤")

    def cancel(self, job_id, user_id):
        with self.connect(write=True) as connection:
            changed = connection.execute(
                "UPDATE jobs SET status='cancelled',updated_at=?,finished_at=? WHERE id=? AND user_id=? AND status='queued'",
                (time.time(), time.time(), job_id, user_id),
            ).rowcount
            if changed:
                connection.execute(
                    "INSERT INTO events(job_id,stage,message,created_at) VALUES(?,?,?,?)",
                    (job_id, "cancelled", "已取消，创作额度已释放", time.time()),
                )
            return bool(changed)

    def complete(self, job_id, path, width, height):
        # Safe to repeat after a crash between the business write and graph checkpoint.
        with self.connect(write=True) as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job["status"] == "succeeded":
                return
            now = time.time()
            connection.execute(
                "INSERT OR IGNORE INTO assets(id,user_id,job_id,path,width,height,created_at) VALUES(?,?,?,?,?,?,?)",
                (job_id, job["user_id"], job_id, str(path), width, height, now),
            )
            connection.execute(
                "UPDATE jobs SET status='succeeded',asset_id=?,finished_at=?,updated_at=?,lease_until=NULL,error_code=NULL,error_message=NULL WHERE id=?",
                (job_id, now, now, job_id),
            )
            connection.execute(
                "INSERT INTO events(job_id,stage,message,created_at) VALUES(?,?,?,?)",
                (job_id, "succeeded", "作品已保存，可以下载原图", now),
            )

    def recover_expired(self, outputs):
        for job in self.all(
            "SELECT * FROM jobs WHERE status NOT IN ('queued','succeeded','failed','cancelled','uncertain') AND lease_until<?",
            (time.time(),),
        ):
            # Before the billable provider call starts, there cannot be a
            # durable provider result to recover.  Avoid probing remote object
            # storage for these jobs: a transient storage outage must not keep
            # a stale validation/moderation job blocking the whole queue.
            if not job["provider_started_at"]:
                self.recover(job["id"])
                continue
            try:
                if hasattr(outputs, "output_key"):
                    output_exists = outputs.exists(outputs.output_key(job["user_id"], job["id"]))
                else:
                    output_exists = (outputs / f"{job['id']}.png").exists()
            except Exception:
                # We cannot prove whether a provider result was persisted when
                # object storage is unavailable.  Mark it uncertain instead of
                # retrying and risking a duplicate billable generation.
                self.fail(
                    job["id"],
                    "provider_unknown",
                    "连接中断，供应商结果待确认。为避免重复计费，此任务不会自动重试。",
                    uncertain=True,
                )
                continue
            if output_exists:
                self.recover(job["id"])
            else:
                self.fail(
                    job["id"],
                    "provider_unknown",
                    "连接中断，供应商结果待确认。为避免重复计费，此任务不会自动重试。",
                    uncertain=True,
                )

    def rate_allowed(self, key, limit, seconds):
        now = time.time()
        with self.connect(write=True) as connection:
            row = connection.execute("SELECT * FROM rate_limits WHERE key=?", (key,)).fetchone()
            if not row or now - row["window_start"] >= seconds:
                connection.execute(
                    "INSERT INTO rate_limits(key,window_start,count) VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET window_start=excluded.window_start,count=1",
                    (key, now),
                )
                return True
            if row["count"] >= limit:
                return False
            connection.execute("UPDATE rate_limits SET count=count+1 WHERE key=?", (key,))
            return True
