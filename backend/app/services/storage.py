"""RustFS object storage access via boto3 (S3-compatible).

Connection parameters follow the verified configuration in
``test/rustfstest.py`` (path-style addressing, s3v4 signature).
"""

from __future__ import annotations

import logging
import posixpath
from collections.abc import Iterator

import boto3
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Image extensions accepted by both the upload endpoint and the multimodal
# input builder.  Defined here so the files API and the agent runtime share
# a single source of truth.
ALLOWED_IMAGE_EXT: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
)


def init_s3(settings: Settings) -> BaseClient:
    """Create the boto3 S3 client and ensure the bucket exists."""
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.rustfs_endpoint,
        aws_access_key_id=settings.rustfs_access_key,
        aws_secret_access_key=settings.rustfs_secret_key,
        region_name=settings.rustfs_region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )
    ensure_bucket(s3, settings.rustfs_bucket)
    return s3


def ensure_bucket(s3: BaseClient, bucket: str) -> None:
    """Create the bucket if missing (idempotent — same pattern as test/rustfstest.py)."""
    try:
        s3.create_bucket(Bucket=bucket)
        logger.info("created bucket %s", bucket)
    except ClientError as exc:
        if "BucketAlreadyOwnedByYou" in str(exc):
            return
        raise


def user_document_key(user_id: str, document_id: str, filename: str) -> str:
    """S3 key for an uploaded document's original file."""
    return f"users/{user_id}/documents/{document_id}/{filename}"


def user_files_prefix(user_id: str) -> str:
    """S3 key prefix for the agent-facing persistent file area."""
    return f"users/{user_id}/files"


def user_skills_prefix(user_id: str) -> str:
    """S3 key prefix for the user's skill library (SKILL.md files)."""
    return f"users/{user_id}/skills"


# ---------------------------------------------------------------------------
# S3 helpers — shared by the files/skills REST endpoints to avoid duplicating
# the pagination loop, 404 detection, and path-escape check everywhere.
# ---------------------------------------------------------------------------


def list_objects(s3: BaseClient, bucket: str, prefix: str) -> Iterator[dict]:
    """Yield every object dict under ``prefix`` across all pages.

    Skips directory marker objects (keys ending with ``/``).
    """
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            if obj["Key"].endswith("/"):
                continue
            yield obj
        if not resp.get("IsTruncated"):
            return
        token = resp.get("NextContinuationToken")


def is_s3_not_found(exc: Exception) -> bool:
    """True if ``exc`` is a boto3 ClientError indicating the object is missing."""
    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code", "")
    if code in ("404", "NoSuchKey", "NotFound"):
        return True
    return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404


def object_exists(s3: BaseClient, bucket: str, key: str) -> bool:
    """True if the object exists.  Any non-404 error is re-raised."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if is_s3_not_found(exc):
            return False
        raise


def safe_relative_path(vpath: str) -> str:
    """Normalize a user-supplied path and reject traversal outside the root.

    Returns the cleaned relative path (no leading slash).  Raises
    ``ValueError`` on traversal attempts (``..`` segments).
    """
    clean = posixpath.normpath(vpath.lstrip("/"))
    if clean == ".." or clean.startswith("../"):
        raise ValueError(f"path escapes root: {vpath!r}")
    return clean
