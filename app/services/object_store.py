from __future__ import annotations

import socket
from urllib.parse import urlsplit, urlunsplit
from pathlib import Path

from botocore.exceptions import EndpointConnectionError
from botocore.exceptions import ClientError
import boto3

from app.core.config import settings
from app.core.exceptions import StorageError
from app.services.encryption import VaultEncryptionService


def _host_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def _normalize_endpoint_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url)
    hostname = parsed.hostname
    if not hostname or hostname != "minio" or _host_resolves(hostname):
        return raw_url

    netloc = parsed.netloc.replace("minio", "127.0.0.1", 1)
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


class EncryptedObjectStore:
    def __init__(self, encryption: VaultEncryptionService | None = None) -> None:
        self.bucket_name = settings.S3_BUCKET_NAME
        self.encryption = encryption or VaultEncryptionService()
        self._use_local_fallback = False
        self._local_root = settings.LEGACY_JOB_STORAGE_DIR.parent / "object-store-fallback"
        endpoint_url = _normalize_endpoint_url(settings.S3_ENDPOINT_URL)
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name=settings.S3_REGION,
            use_ssl=settings.S3_USE_SSL,
        )

    def _local_path(self, object_key: str) -> Path:
        return self._local_root / object_key.replace("/", "\\")

    def ensure_bucket(self) -> None:
        if self._use_local_fallback:
            self._local_root.mkdir(parents=True, exist_ok=True)
            return

        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            return
        except EndpointConnectionError:
            if not settings.ALLOW_LOCAL_FALLBACK:
                raise StorageError("Unable to reach the configured object-storage bucket.") from None
            self._use_local_fallback = True
            self._local_root.mkdir(parents=True, exist_ok=True)
            return
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code not in {"404", "NoSuchBucket"}:
                raise StorageError("Unable to reach the configured object-storage bucket.") from exc

        try:
            self.client.create_bucket(Bucket=self.bucket_name)
        except ClientError as exc:
            raise StorageError("Unable to create the configured object-storage bucket.") from exc

    def put_bytes(self, object_key: str, payload: bytes, *, content_type: str) -> int:
        encrypted = self.encryption.encrypt_bytes(payload)
        if self._use_local_fallback:
            path = self._local_path(object_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encrypted)
            return len(payload)

        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=encrypted,
                ContentType="application/octet-stream",
                Metadata={"original-content-type": content_type},
            )
        except ClientError as exc:
            raise StorageError(f"Failed to store encrypted object '{object_key}'.") from exc
        return len(payload)

    def get_bytes(self, object_key: str) -> bytes:
        if self._use_local_fallback:
            path = self._local_path(object_key)
            if not path.exists():
                raise StorageError(f"Failed to read encrypted object '{object_key}'.")
            return self.encryption.decrypt_bytes(path.read_bytes())

        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=object_key)
        except ClientError as exc:
            raise StorageError(f"Failed to read encrypted object '{object_key}'.") from exc

        try:
            body = response["Body"].read()
            return self.encryption.decrypt_bytes(body)
        except Exception as exc:  # pragma: no cover - defensive around SDK stream handling
            raise StorageError(f"Failed to decrypt object '{object_key}'.") from exc

    def delete_object(self, object_key: str) -> None:
        if self._use_local_fallback:
            path = self._local_path(object_key)
            if path.exists():
                path.unlink()
            return

        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=object_key)
        except ClientError as exc:
            raise StorageError(f"Failed to delete object '{object_key}'.") from exc

    def object_exists(self, object_key: str) -> bool:
        if self._use_local_fallback:
            return self._local_path(object_key).exists()

        try:
            self.client.head_object(Bucket=self.bucket_name, Key=object_key)
            return True
        except ClientError:
            return False
