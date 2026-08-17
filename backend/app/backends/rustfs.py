"""RustFS backend — implements deepagents ``BackendProtocol`` over boto3/S3.

Mounted as the ``/files/`` route of a CompositeBackend; the composite strips
the route prefix, so this backend receives paths like ``"/notes.md"`` and
maps them to object keys ``users/{user_id}/files/notes.md``.

Object storage has no partial writes: ``edit`` is a whole-object
GET → replace → PUT. The per-user concurrency limit (one agent at a time)
makes lost-update races irrelevant in practice.
"""

from __future__ import annotations

import fnmatch
import logging
import posixpath
import re

from botocore.client import BaseClient
from botocore.exceptions import ClientError

from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FILE_NOT_FOUND,
    FileData,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    INVALID_PATH,
    IS_DIRECTORY,
    LsResult,
    ReadResult,
    WriteResult,
)

logger = logging.getLogger(__name__)

MAX_EDIT_BYTES = 2 * 1024 * 1024  # refuse whole-object rewrites beyond this
MAX_GREP_BYTES_PER_FILE = 256 * 1024  # cap bytes scanned per file


class RustFSBackend(BackendProtocol):
    """Per-request backend bound to one user's object prefix.

    By default the root is ``users/{user_id}/files`` (the agent-facing
    persistent file area).  Pass ``root`` to mount a different prefix —
    used for the ``/skills/`` route where skills are stored as
    ``SKILL.md`` files.
    """

    def __init__(
        self, s3: BaseClient, bucket: str, user_id: str, *, root: str | None = None
    ) -> None:
        self._s3 = s3
        self._bucket = bucket
        self._root = root or f"users/{user_id}/files"

    # ------------------------------------------------------------------
    # Path mapping
    # ------------------------------------------------------------------

    def _to_key(self, vpath: str) -> str:
        """Map a backend path (starts with "/") to an S3 key inside the root.

        Rejects traversal: after normalization the path must stay under
        ``self._root`` — this is the tenant-isolation boundary.
        """
        rel = posixpath.normpath(vpath.lstrip("/"))
        if rel in (".", ""):
            raise ValueError("path resolves to root")
        if rel == ".." or rel.startswith("../"):
            raise ValueError(f"path escapes root: {vpath!r}")
        return f"{self._root}/{rel}"

    def _to_prefix(self, path: str) -> str:
        """Like :meth:`_to_key`, but the root ("/") maps to ``self._root``.

        Used by listing/search operations where the root is a valid target.
        """
        rel = posixpath.normpath(path.lstrip("/"))
        if rel == ".." or rel.startswith("../"):
            raise ValueError(f"path escapes root: {path!r}")
        if rel in (".", ""):
            return self._root
        return f"{self._root}/{rel}"

    def _to_vpath(self, key: str) -> str:
        return "/" + key[len(self._root) + 1 :]

    @staticmethod
    def _is_s3_error(exc: Exception, code: str) -> bool:
        return isinstance(exc, ClientError) and exc.response.get("Error", {}).get(
            "Code", ""
        ) in (code, "NoSuchKey", "NotFound")

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        try:
            prefix = self._to_prefix(path).rstrip("/") + "/"
        except ValueError as exc:
            return LsResult(error=str(exc))
        try:
            resp = self._s3.list_objects_v2(
                Bucket=self._bucket, Prefix=prefix, Delimiter="/"
            )
        except Exception as exc:
            return LsResult(error=str(exc))

        entries: list[FileInfo] = []
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue  # directory marker objects
            entries.append(
                FileInfo(path=self._to_vpath(key), is_dir=False, size=obj.get("Size"))
            )
        for common in resp.get("CommonPrefixes", []):
            name = common["Prefix"].rstrip("/")
            entries.append(FileInfo(path=self._to_vpath(name + "/").rstrip("/"), is_dir=True))
        return LsResult(entries=entries)

    # ------------------------------------------------------------------
    # Read / write / edit / delete
    # ------------------------------------------------------------------

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            key = self._to_key(file_path)
        except ValueError as exc:
            return ReadResult(error=INVALID_PATH)

        try:
            raw = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if self._is_s3_error(exc, "NoSuchKey"):
                logger.debug("rustfs read: key=%s file_not_found", key)
                return ReadResult(error=FILE_NOT_FOUND)
            logger.warning("rustfs read failed: key=%s error=%s", key, exc)
            return ReadResult(error=str(exc))

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ReadResult(error="not_a_text_file")

        if text == "":
            return ReadResult(
                file_data=FileData(content="System reminder: File exists but has empty contents", encoding="utf-8")
            )

        # Line-based pagination, mirroring the sandbox read semantics.
        offset = max(0, offset)
        limit = max(0, limit)
        lines = text.splitlines()
        total = len(lines)
        if limit == 0:
            return ReadResult(
                file_data=FileData(content="", encoding="utf-8"),
                total_lines=total,
                no_lines_requested=True,
            )
        start = min(offset, total)
        end = min(offset + limit, total)
        window = "\n".join(lines[start:end]) + ("\n" if text.endswith("\n") and end >= total else "")
        return ReadResult(
            file_data=FileData(content=window, encoding="utf-8"),
            total_lines=total,
            start_line=start + 1,
            end_line=end,
            next_offset=end if end < total else None,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            key = self._to_key(file_path)
        except ValueError as exc:
            return WriteResult(error=INVALID_PATH)
        try:
            self._s3.put_object(
                Bucket=self._bucket, Key=key, Body=content.encode("utf-8")
            )
        except Exception as exc:
            logger.warning("rustfs write failed: key=%s error=%s", key, exc)
            return WriteResult(error=str(exc))
        logger.debug("rustfs write: key=%s bytes=%d", key, len(content))
        return WriteResult(path=file_path)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Whole-object rewrite: GET → replace → PUT (no partial writes on S3)."""
        try:
            key = self._to_key(file_path)
        except ValueError:
            return EditResult(error=INVALID_PATH)
        try:
            raw = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if self._is_s3_error(exc, "NoSuchKey"):
                logger.debug("rustfs edit: key=%s file_not_found", key)
                return EditResult(error=FILE_NOT_FOUND)
            logger.warning("rustfs edit failed: key=%s error=%s", key, exc)
            return EditResult(error=str(exc))
        if len(raw) > MAX_EDIT_BYTES:
            return EditResult(
                error=f"file exceeds {MAX_EDIT_BYTES} bytes — edit refused on object storage"
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return EditResult(error="not_a_text_file")

        occurrences = text.count(old_string)
        if occurrences == 0:
            return EditResult(error=f"old_string not found: {old_string[:80]!r}")
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(
            old_string, new_string, 1
        )
        try:
            self._s3.put_object(
                Bucket=self._bucket, Key=key, Body=new_text.encode("utf-8")
            )
        except Exception as exc:
            return EditResult(error=str(exc))
        return EditResult(
            path=file_path,
            occurrences=occurrences if replace_all else 1,
        )

    def delete(self, file_path: str) -> DeleteResult:
        try:
            key = self._to_key(file_path)
        except ValueError:
            return DeleteResult(error=INVALID_PATH)
        try:
            self._s3.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            return DeleteResult(error=str(exc))
        return DeleteResult(path=file_path)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _list_keys_under(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kwargs)
            keys.extend(
                o["Key"]
                for o in resp.get("Contents", [])
                if not o["Key"].endswith("/")
            )
            if not resp.get("IsTruncated"):
                return keys
            token = resp.get("NextContinuationToken")

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        base = path or "/"
        try:
            prefix = self._to_prefix(base).rstrip("/") + "/"
        except ValueError:
            return GlobResult(error=INVALID_PATH)
        try:
            keys = self._list_keys_under(prefix)
        except Exception as exc:
            return GlobResult(error=str(exc))
        matches = [
            FileInfo(path=self._to_vpath(k), is_dir=False)
            for k in keys
            if fnmatch.fnmatch(self._to_vpath(k), pattern)
        ]
        matches.sort(key=lambda f: f["path"])
        return GlobResult(matches=matches)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        base = path or "/"
        try:
            prefix = self._to_prefix(base).rstrip("/") + "/"
        except ValueError:
            return GrepResult(error=INVALID_PATH)
        try:
            keys = self._list_keys_under(prefix)
        except Exception as exc:
            return GrepResult(error=str(exc))

        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return GrepResult(error=f"invalid pattern: {exc}")

        matches: list[GrepMatch] = []
        truncated = False
        for key in keys:
            if glob and not fnmatch.fnmatch(key, glob):
                continue
            try:
                obj = self._s3.get_object(Bucket=self._bucket, Key=key)
                size = obj.get("ContentLength", 0) or 0
                if size > MAX_GREP_BYTES_PER_FILE:
                    truncated = True
                    continue
                text = obj["Body"].read().decode("utf-8", errors="replace")
            except Exception:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    matches.append(
                        GrepMatch(
                            path=self._to_vpath(key), line=line_number, text=line.rstrip()
                        )
                    )
                    if max_count is not None and len(matches) >= max_count:
                        return GrepResult(matches=matches, truncated=truncated)
        return GrepResult(matches=matches, truncated=truncated)
