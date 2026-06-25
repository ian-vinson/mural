# platform/storage.py
#
# Mural Content Platform — Cloudflare R2 / S3-compatible storage
# Copyright (C) 2024  Mural Contributors
# MIT License — see platform/LICENSE

"""Cloudflare R2 file storage integration.

Uses boto3 with an S3-compatible endpoint.  All public URLs are served
via Cloudflare's CDN (zero egress fees).

Environment variables (set in production):
    R2_ACCOUNT_ID       — Cloudflare account ID
    R2_ACCESS_KEY_ID    — R2 access key
    R2_SECRET_ACCESS_KEY— R2 secret key
    R2_BUCKET_NAME      — target bucket name
    R2_PUBLIC_URL       — public CDN base URL (e.g. https://cdn.mural.app)
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)


def _get_client():
    """Build and return a boto3 S3 client pointed at Cloudflare R2."""
    account_id = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


_BUCKET = os.environ.get("R2_BUCKET_NAME", "mural-wallpapers")
_PUBLIC_BASE = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")


class R2Storage:
    """Thin wrapper around boto3 for Cloudflare R2 object storage.

    All methods are synchronous; wrap them in ``asyncio.to_thread`` in
    async FastAPI route handlers.
    """

    def __init__(self) -> None:
        self._client = _get_client()
        self._bucket = _BUCKET

    def upload_file(
        self,
        data: bytes | io.IOBase,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload *data* to R2 and return the storage key.

        Args:
            data: File bytes or file-like object.
            key: R2 object key (path within the bucket).
            content_type: MIME type for the uploaded object.

        Returns:
            The object key on success.

        Raises:
            Exception: On upload failure.
        """
        if isinstance(data, bytes):
            data = io.BytesIO(data)
        self._client.upload_fileobj(
            data, self._bucket, key,
            ExtraArgs={"ContentType": content_type},
        )
        logger.info("Uploaded %s (%s)", key, content_type)
        return key

    def delete_file(self, key: str) -> bool:
        """Delete an object from R2.

        Args:
            key: R2 object key.

        Returns:
            ``True`` on success.
        """
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            logger.info("Deleted %s", key)
            return True
        except Exception as exc:
            logger.error("delete_file(%r) failed: %s", key, exc)
            return False

    def public_url(self, key: str) -> str:
        """Return the public CDN URL for *key*.

        Args:
            key: R2 object key.

        Returns:
            Public URL string.  Falls back to a pre-signed URL if no
            public CDN base URL is configured.
        """
        if _PUBLIC_BASE:
            return f"{_PUBLIC_BASE}/{key}"
        return self.presigned_url(key)

    def presigned_url(self, key: str, expires: int = 3600) -> str:
        """Generate a pre-signed GET URL for *key*.

        Args:
            key: R2 object key.
            expires: Expiry in seconds (default 1 hour).

        Returns:
            Pre-signed URL string.
        """
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires,
        )

    @staticmethod
    def make_key(prefix: str, filename: str) -> str:
        """Generate a unique storage key.

        Args:
            prefix: Key prefix, e.g. ``"wallpapers"`` or ``"thumbnails"``.
            filename: Original filename (used for extension only).

        Returns:
            A unique key string like ``"wallpapers/abc123.mp4"``.
        """
        ext = Path(filename).suffix.lower()
        return f"{prefix}/{uuid.uuid4().hex}{ext}"


# Module-level singleton (constructed lazily to avoid crashing on import
# when environment variables are not set — e.g. during testing).
_storage: R2Storage | None = None


def get_storage() -> R2Storage:
    """Return the module-level :class:`R2Storage` singleton."""
    global _storage
    if _storage is None:
        _storage = R2Storage()
    return _storage
