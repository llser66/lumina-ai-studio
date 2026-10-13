import httpx
import pytest
from cryptography.fernet import Fernet

from server.asset_store import SupabaseAssetStore
from server.config import Settings
from server.db import Database
from server.main import create_app
from server.vault import CredentialVault


def test_supabase_storage_uses_private_objects_and_server_credentials():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"Key": "lumina-assets/users/u1/a1.png"})
        if request.method == "GET":
            return httpx.Response(200, content=b"image-bytes")
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    storage = SupabaseAssetStore(
        "https://project.supabase.co", "service-secret", "lumina-assets", client=client
    )

    key = storage.upload("user-1", "asset-1", b"image-bytes")
    assert key == "users/user-1/uploads/asset-1.png"
    assert storage.read(key) == b"image-bytes"
    storage.delete(key)

    assert [request.method for request in requests] == ["POST", "GET", "DELETE"]
    assert all(request.headers["authorization"] == "Bearer service-secret" for request in requests)
    assert all("service-secret" not in request.url.query.decode() for request in requests)
    assert requests[0].headers["content-type"] == "image/png"


def test_supabase_storage_rejects_keys_outside_user_namespace():
    storage = SupabaseAssetStore("https://project.supabase.co", "service-secret", "lumina-assets")

    try:
        storage.read("../other-user/private.png")
    except ValueError as exc:
        assert "object key" in str(exc).lower()
    else:
        raise AssertionError("unsafe object key was accepted")


def test_supabase_head_recognizes_missing_private_object():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json={"statusCode": "404", "error": "not_found"})
        )
    )
    storage = SupabaseAssetStore("https://project.supabase.co", "service-secret", client=client)

    assert not storage.exists("users/user-1/outputs/job-1.png")


def test_supabase_head_recognizes_empty_400_as_missing_private_object():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400)))
    storage = SupabaseAssetStore("https://project.supabase.co", "service-secret", client=client)

    assert not storage.exists("users/user-1/outputs/job-1.png")


def test_postgres_query_translation_preserves_existing_conflict_handling():
    from server.db import postgres_sql

    assert postgres_sql("SELECT * FROM users WHERE email=?") == (
        "SELECT * FROM users WHERE email=%s"
    )
    assert postgres_sql("INSERT OR IGNORE INTO assets(id,path) VALUES(?,?)") == (
        "INSERT INTO assets(id,path) VALUES(%s,%s) ON CONFLICT DO NOTHING"
    )
    assert postgres_sql("INSERT INTO rate_limits(key) VALUES(?) ON CONFLICT(key) DO UPDATE SET key=excluded.key") == (
        "INSERT INTO rate_limits(key) VALUES(%s) ON CONFLICT(key) DO UPDATE SET key=excluded.key"
    )


def test_render_fails_closed_if_database_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://lumina-test.onrender.com")
    settings = Settings(_env_file=None, data_dir=tmp_path)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app(settings, start_worker=False)


def test_vault_uses_stable_deployment_key_without_writing_ephemeral_file(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.execute(
        "INSERT INTO users(id,email,name,password_hash,created_at) VALUES(?,?,?,?,?)",
        ("user-1", "user@example.com", "User", "hash", 1),
    )
    key = Fernet.generate_key().decode()
    first = CredentialVault(db, tmp_path, encryption_key=key)
    row = first.save("user-1", "openrouter", "Test", "api_key", {"key": "secret-api-key"})

    restarted = CredentialVault(db, tmp_path, encryption_key=key)
    stored = db.one("SELECT * FROM model_connections WHERE id=?", (row["id"],))

    assert restarted.credential(stored) == {"key": "secret-api-key"}
    assert "secret-api-key" not in stored["credential"]
    assert not (tmp_path / "credentials.key").exists()


def test_vault_accepts_platform_generated_secret_and_reuses_it(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    db.execute(
        "INSERT INTO users(id,email,name,password_hash,created_at) VALUES(?,?,?,?,?)",
        ("user-1", "user@example.com", "User", "hash", 1),
    )
    first = CredentialVault(db, tmp_path, encryption_key="render-generated-random-secret-value")
    row = first.save("user-1", "openrouter", "Test", "api_key", {"key": "secret-api-key"})
    restarted = CredentialVault(
        db, tmp_path, encryption_key="render-generated-random-secret-value"
    )

    assert restarted.credential(db.one("SELECT * FROM model_connections WHERE id=?", (row["id"],))) == {
        "key": "secret-api-key"
    }
