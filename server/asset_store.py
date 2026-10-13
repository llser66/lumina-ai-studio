import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx


class LocalAssetStore:
    """Development/test asset storage. Production deployments use private object storage."""

    is_remote = False

    def __init__(self, root: Path):
        self.root = Path(root)

    def output_key(self, user_id, job_id):
        return str(self.root / "outputs" / f"{job_id}.png")

    def upload(self, user_id, asset_id, content):
        key = str(self.root / "uploads" / f"{asset_id}.png")
        self.put(key, content)
        return key

    def put(self, key, content):
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".pending")
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def read(self, key):
        return Path(key).read_bytes()

    def exists(self, key):
        return Path(key).is_file()

    def delete(self, key):
        Path(key).unlink(missing_ok=True)

    def materialize(self, key, temp_dir):
        return Path(key), False

    @staticmethod
    def cleanup_materialized(path, should_delete):
        if should_delete:
            path.unlink(missing_ok=True)


class SupabaseAssetStore:
    """Server-only access to a private Supabase Storage bucket."""

    is_remote = True
    _key_pattern = re.compile(r"^users/[A-Za-z0-9_-]+/(?:uploads|outputs)/[A-Za-z0-9_-]+\.png$")

    def __init__(self, project_url, service_role_key, bucket="lumina-assets", *, client=None):
        if not project_url.startswith("https://"):
            raise ValueError("Supabase Storage must use HTTPS")
        if not service_role_key or not bucket or "/" in bucket:
            raise ValueError("Supabase Storage configuration is incomplete")
        self.project_url = project_url.rstrip("/")
        self.bucket = bucket
        self._key = service_role_key
        self.client = client or httpx.Client(timeout=httpx.Timeout(30, connect=10))
        self._owns_client = client is None

    def _url(self, key):
        if not self._key_pattern.fullmatch(key):
            raise ValueError("Invalid asset object key")
        return (
            self.project_url
            + "/storage/v1/object/"
            + quote(self.bucket, safe="")
            + "/"
            + quote(key, safe="/-._")
        )

    def _headers(self, **extra):
        return {
            "Authorization": "Bearer " + self._key,
            "apikey": self._key,
            **extra,
        }

    def output_key(self, user_id, job_id):
        key = f"users/{user_id}/outputs/{job_id}.png"
        self._url(key)
        return key

    def upload(self, user_id, asset_id, content):
        key = f"users/{user_id}/uploads/{asset_id}.png"
        self.put(key, content)
        return key

    def put(self, key, content):
        response = self.client.post(
            self._url(key),
            content=content,
            headers=self._headers(**{"Content-Type": "image/png", "x-upsert": "true"}),
        )
        if response.is_error:
            raise RuntimeError(f"Private image storage rejected the upload (HTTP {response.status_code})")

    def read(self, key):
        response = self.client.get(self._url(key), headers=self._headers())
        if response.status_code == 404:
            raise FileNotFoundError(key)
        if response.is_error:
            raise RuntimeError(f"Private image storage download failed (HTTP {response.status_code})")
        if len(response.content) > 25 * 1024 * 1024:
            raise RuntimeError("Stored image exceeds the configured size limit")
        return response.content

    def exists(self, key):
        response = self.client.head(self._url(key), headers=self._headers())
        if response.status_code == 404:
            return False
        # Supabase Storage may answer HEAD for a missing object with HTTP 400
        # and an empty body, so there is no 404 marker to inspect.  The key has
        # already passed our strict namespace validation; treat both forms as
        # a normal cache miss and let uploads surface real bucket errors.
        if response.status_code == 400:
            return False
        if response.is_error:
            raise RuntimeError(f"Private image storage check failed (HTTP {response.status_code})")
        return True

    def delete(self, key):
        response = self.client.delete(self._url(key), headers=self._headers())
        if response.status_code != 404 and response.is_error:
            raise RuntimeError(f"Private image storage delete failed (HTTP {response.status_code})")

    def materialize(self, key, temp_dir):
        descriptor, filename = tempfile.mkstemp(
            prefix="lumina-reference-", suffix=".png", dir=temp_dir
        )
        os.close(descriptor)
        path = Path(filename)
        try:
            path.write_bytes(self.read(key))
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path, True

    @staticmethod
    def cleanup_materialized(path, should_delete):
        if should_delete:
            path.unlink(missing_ok=True)

    def close(self):
        if self._owns_client:
            self.client.close()


def create_asset_store(settings):
    if settings.database_url.get_secret_value().strip():
        key = settings.supabase_service_role_key.get_secret_value().strip()
        if not key or not settings.supabase_url:
            raise RuntimeError("Supabase URL and service-role key are required with DATABASE_URL")
        return SupabaseAssetStore(
            settings.supabase_url,
            key,
            settings.supabase_storage_bucket,
        )
    return LocalAssetStore(settings.data_dir)
