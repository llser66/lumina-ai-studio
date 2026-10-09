import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from server.provider import ProviderFailure


class CredentialVault:
    """Account-bound authenticated encryption. Public responses never decrypt keys."""

    def __init__(self, db, data_dir: Path, encryption_key=None):
        self.db = db
        if encryption_key:
            key = (
                encryption_key.get_secret_value()
                if hasattr(encryption_key, "get_secret_value")
                else str(encryption_key)
            )
            try:
                self.cipher = Fernet(key.encode())
            except (TypeError, ValueError):
                derived = base64.urlsafe_b64encode(
                    hashlib.sha256(b"lumina-credential-key-v1\0" + key.encode()).digest()
                )
                try:
                    self.cipher = Fernet(derived)
                except (TypeError, ValueError) as key_error:
                    raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is invalid") from key_error
            return
        key_path = data_dir / "credentials.key"
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            if db.one("SELECT COUNT(*) AS count FROM model_connections WHERE credential<>''")[
                "count"
            ]:
                os.close(descriptor)
                key_path.unlink()
                raise RuntimeError("凭证加密密钥丢失，请从备份恢复 credentials.key")
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(Fernet.generate_key())
                handle.flush()
                os.fsync(handle.fileno())
        self.cipher = Fernet(key_path.read_bytes())

    def save(
        self,
        user_id,
        provider,
        name,
        method,
        credential,
        *,
        base_url="",
        model_id="",
        model_name="",
        supports_image=False,
    ):
        connection_id = str(uuid.uuid4())
        now = time.time()
        encrypted = self.seal(connection_id, user_id, provider, credential)
        hint = (
            "••••" + str(credential.get("key", ""))[-4:]
            if method == "api_key"
            else "订阅 / 账号授权"
        )
        self.db.execute(
            "INSERT INTO model_connections(id,user_id,provider,name,auth_method,credential,hint,created_at,updated_at,base_url,model_id,model_name,supports_image) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                connection_id,
                user_id,
                provider,
                name,
                method,
                encrypted,
                hint,
                now,
                now,
                base_url,
                model_id,
                model_name,
                int(supports_image),
            ),
        )
        return self.owned(connection_id, user_id)

    def seal(self, connection_id, user_id, provider, credential):
        payload = {
            "id": connection_id,
            "owner": user_id,
            "provider": provider,
            "credential": credential,
        }
        return self.cipher.encrypt(json.dumps(payload).encode()).decode()

    def owned(self, connection_id, user_id):
        return self.db.one(
            "SELECT * FROM model_connections WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (connection_id, user_id),
        )

    def credential(self, row):
        try:
            payload = json.loads(self.cipher.decrypt(row["credential"].encode()))
            if (
                payload["id"] != row["id"]
                or payload["owner"] != row["user_id"]
                or payload["provider"] != row["provider"]
            ):
                raise ValueError("Credential binding mismatch")
            return payload["credential"]
        except (InvalidToken, ValueError, KeyError, TypeError) as exc:
            raise ProviderFailure("credentials", "连接凭证无法读取，请重新连接该账号。") from exc

    def refresh(self, connection_id, user_id, version, credential):
        row = self.owned(connection_id, user_id)
        if row and row["version"] == version:
            encrypted = self.seal(connection_id, user_id, row["provider"], credential)
            self.db.execute(
                "UPDATE model_connections SET credential=?,updated_at=? WHERE id=? AND user_id=? AND version=? AND revoked_at IS NULL",
                (encrypted, time.time(), connection_id, user_id, version),
            )

    @staticmethod
    def public(row):
        return {
            name: row[name]
            for name in (
                "id",
                "provider",
                "name",
                "auth_method",
                "hint",
                "version",
                "created_at",
                "updated_at",
                "checked_at",
                "status",
            )
        }
