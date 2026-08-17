"""File endpoints: list, upload, download files from the user's ``/files/`` area.

The agent writes deliverables (reports, data files, images, etc.) to ``/files/``
via ``write_file``. These endpoints let the user list, upload, and download them.
"""

from __future__ import annotations

import mimetypes
import posixpath
import uuid
from datetime import datetime

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.db.models import User
from app.services.storage import user_files_prefix

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB per file


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


@router.post("/upload")
def upload_file(
    request: Request,
    user: User = Depends(get_current_user),
    file: UploadFile = File(...),
) -> dict:
    """Upload a file to the user's ``/files/`` area.

    Returns the virtual path (e.g. ``/files/1234-image.png``) and a download URL.
    Images are placed in a dated subfolder with a random prefix to avoid name
    collisions.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # Read into memory; we accept up to MAX_UPLOAD_BYTES per file.
    data = file.file.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大，最大 {MAX_UPLOAD_BYTES // 1024 // 1024}MB",
        )

    orig = posixpath.basename(file.filename)
    _, ext = posixpath.splitext(orig)
    ext = ext.lower()

    # Sanitize: date + short uuid prefix to avoid collisions.
    date_dir = datetime.now().strftime("%Y%m%d")
    safe_name = f"{uuid.uuid4().hex[:8]}-{orig}"
    vpath = f"/files/{date_dir}/{safe_name}"

    clean = posixpath.normpath(vpath.lstrip("/"))
    if clean == ".." or clean.startswith("../"):
        raise HTTPException(status_code=400, detail="invalid path")

    bucket = _get_bucket()
    prefix = user_files_prefix(str(user.id))
    key = f"{prefix}/{clean}"

    content_type = file.content_type or mimetypes.guess_type(orig)[0]
    extra = {"ContentType": content_type} if content_type else {}
    try:
        _get_s3(request).put_object(Bucket=bucket, Key=key, Body=data, **extra)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}")

    download_url = f"{str(request.base_url).rstrip('/')}{get_settings().api_v1_prefix}/files/{clean}"
    is_image = ext in ALLOWED_IMAGE_EXT
    return {
        "path": vpath,
        "name": safe_name,
        "size": len(data),
        "content_type": content_type,
        "download_url": download_url,
        "is_image": is_image,
    }


@router.get("/{file_path:path}")
def download_file(
    file_path: str,
    request: Request,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Download a single file from the user's ``/files/`` area."""
    s3 = _get_s3(request)
    bucket = _get_bucket()

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
