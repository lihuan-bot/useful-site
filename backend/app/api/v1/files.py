"""File endpoints: list and download files from the user's ``/files/`` area in RustFS.

The agent writes deliverables (reports, data files, etc.) to ``/files/``
via ``write_file``. These endpoints let the user list and download them.
"""

from __future__ import annotations

import mimetypes
import posixpath

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.db.models import User
from app.services.storage import user_files_prefix

router = APIRouter(prefix="/files", tags=["files"])


def _get_s3(request: Request) -> BaseClient:
    return request.app.state.s3


def _get_bucket() -> str:
    return get_settings().rustfs_bucket


@router.get("")
def list_files(
    request: Request,
    user: User = Depends(get_current_user),
) -> dict:
    """List all files under the user's ``/files/`` area."""
    s3 = _get_s3(request)
    bucket = _get_bucket()
    prefix = f"{user_files_prefix(str(user.id))}/"

    files: list[dict] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            # Strip the prefix to get the user-visible path.
            name = key[len(prefix):]
            files.append({
                "name": name,
                "size": obj.get("Size", 0),
                "last_modified": obj.get("LastModified").isoformat() if obj.get("LastModified") else None,
            })
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

    return {"items": files, "total": len(files)}


@router.get("/{file_path:path}")
def download_file(
    file_path: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Download a single file from the user's ``/files/`` area."""
    s3 = _get_s3(request)
    bucket = _get_bucket()

    # Prevent path traversal: normalize and ensure it doesn't escape.
    clean = posixpath.normpath(file_path.lstrip("/"))
    if clean == ".." or clean.startswith("../"):
        raise HTTPException(status_code=400, detail="invalid path")
    key = f"{user_files_prefix(str(user.id))}/{clean}"

    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
            raise HTTPException(status_code=404, detail="文件不存在")
        raise

    filename = posixpath.basename(clean)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return StreamingResponse(
        resp["Body"],
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
