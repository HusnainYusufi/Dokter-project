from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import StorageError


def build_fernet_key() -> bytes:
    if settings.ARTIFACT_ENCRYPTION_KEY:
        raw = settings.ARTIFACT_ENCRYPTION_KEY.strip().encode()
        if len(raw) == 44:
            return raw
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest)

    material = "|".join(
        value
        for value in (
            settings.NEXTAUTH_SECRET,
            settings.LLAMA_CLOUD_API_KEY,
            settings.PROJECT_NAME,
        )
        if value
    )
    digest = hashlib.sha256(material.encode()).digest()
    return base64.urlsafe_b64encode(digest)


class VaultEncryptionService:
    def __init__(self) -> None:
        self._fernet = Fernet(build_fernet_key())

    def encrypt_bytes(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, data: bytes) -> bytes:
        try:
            return self._fernet.decrypt(data)
        except InvalidToken as exc:
            raise StorageError("Artifact decryption failed. Check the encryption key configuration.") from exc

    def encrypt_text(self, text: str) -> str:
        return self.encrypt_bytes(text.encode("utf-8")).decode("utf-8")

    def decrypt_text(self, text: str) -> str:
        return self.decrypt_bytes(text.encode("utf-8")).decode("utf-8")
