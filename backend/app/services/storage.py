"""RustFS object storage access via boto3 (S3-compatible).

Connection parameters follow the verified configuration in
``test/rustfstest.py`` (path-style addressing, s3v4 signature).
"""

from __future__ import annotations

import logging

import boto3
from botocore.client import BaseClient, Config
from botocore.exceptions import ClientError

from app.core.config import Settings

logger = logging.getLogger(__name__)


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
