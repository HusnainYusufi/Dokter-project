from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.fernet import Fernet, InvalidToken


CONTENT_TYPE_EXTENSIONS = {
    "application/json": ".json",
    "application/msword": ".doc",
    "application/octet-stream": "",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}


def build_fernet_key(secret: str) -> bytes:
    raw = secret.strip().encode("utf-8")
    if len(raw) == 44:
        return raw
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def guess_extension(content_type: str | None) -> str:
    if not content_type:
        return ""
    if content_type in CONTENT_TYPE_EXTENSIONS:
        return CONTENT_TYPE_EXTENSIONS[content_type]
    return mimetypes.guess_extension(content_type, strict=False) or ""


def build_output_path(root: Path, object_key: str, content_type: str | None) -> Path:
    target = root / object_key.replace("/", os.sep)
    extension = guess_extension(content_type)
    if extension and not target.suffix:
        target = target.with_suffix(extension)
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and decrypt all objects from the configured S3 bucket.")
    parser.add_argument("--endpoint-url", default=os.getenv("S3_ENDPOINT_URL"), help="S3 endpoint URL")
    parser.add_argument("--region", default=os.getenv("S3_REGION", "auto"), help="S3 region name")
    parser.add_argument("--bucket", default=os.getenv("S3_BUCKET_NAME"), help="S3 bucket name")
    parser.add_argument("--access-key", default=os.getenv("S3_ACCESS_KEY_ID"), help="S3 access key")
    parser.add_argument("--secret-key", default=os.getenv("S3_SECRET_ACCESS_KEY"), help="S3 secret key")
    parser.add_argument(
        "--artifact-encryption-key",
        default=os.getenv("ARTIFACT_ENCRYPTION_KEY"),
        help="Artifact encryption key used by the app",
    )
    parser.add_argument("--prefix", default="", help="Optional object key prefix filter")
    parser.add_argument("--output", default="downloads/object-store", help="Output directory")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    required = {
        "endpoint URL": args.endpoint_url,
        "bucket": args.bucket,
        "access key": args.access_key,
        "secret key": args.secret_key,
        "artifact encryption key": args.artifact_encryption_key,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing required values: {joined}")


def iter_object_keys(client: Any, bucket: str, prefix: str) -> list[dict[str, Any]]:
    paginator = client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
    objects: list[dict[str, Any]] = []
    for page in pages:
        for item in page.get("Contents", []):
            if item.get("Key"):
                objects.append(item)
    return objects


def main() -> int:
    args = parse_args()
    validate_args(args)

    output_root = Path(args.output).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    client = boto3.client(
        "s3",
        endpoint_url=args.endpoint_url,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
        region_name=args.region,
    )
    fernet = Fernet(build_fernet_key(args.artifact_encryption_key))

    manifest: list[dict[str, Any]] = []
    objects = iter_object_keys(client, args.bucket, args.prefix)
    if not objects:
        print("No objects found.")
        return 0

    for item in objects:
        object_key = item["Key"]
        try:
            response = client.get_object(Bucket=args.bucket, Key=object_key)
            encrypted_body = response["Body"].read()
            decrypted_body = fernet.decrypt(encrypted_body)
            metadata = response.get("Metadata", {})
            content_type = metadata.get("original-content-type") or response.get("ContentType")
            target_path = build_output_path(output_root, object_key, content_type)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(decrypted_body)
            manifest.append(
                {
                    "key": object_key,
                    "content_type": content_type,
                    "size_bytes": len(decrypted_body),
                    "output_path": str(target_path),
                }
            )
            print(f"Downloaded {object_key} -> {target_path}")
        except InvalidToken:
            print(f"Skipped {object_key}: decryption failed")
        except (BotoCoreError, ClientError) as exc:
            print(f"Skipped {object_key}: {exc}")

    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
