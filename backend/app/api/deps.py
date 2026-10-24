"""Shared API dependencies: DB session, rate limiting, upload validation."""

from __future__ import annotations

import threading
import time

from fastapi import Request, UploadFile

from app.core.config import get_settings
from app.core.errors import SecondNatureError, ValidationFailure
from app.database.session import get_session  # noqa: F401  (re-exported for routers)


class RateLimited(SecondNatureError):
    status_code = 429
    code = "rate_limited"


class _Bucket:
    def __init__(self):
        self.lock = threading.Lock()
        self.hits: dict[str, list[float]] = {}

    def check(self, key: str, limit: int, window_s: float = 60.0):
        now = time.monotonic()
        with self.lock:
            q = [t for t in self.hits.get(key, []) if now - t < window_s]
            if len(q) >= limit:
                raise RateLimited(f"rate limit of {limit} requests per minute exceeded; retry shortly")
            q.append(now)
            self.hits[key] = q


_bucket = _Bucket()


def rate_limit(request: Request):
    """Applied to expensive endpoints (jobs, uploads, geocoding)."""
    client = request.client.host if request.client else "unknown"
    _bucket.check(f"{client}:{request.url.path}", get_settings().rate_limit_per_minute)


SIGNATURES = {".nc": [b"\x89HDF", b"CDF\x01", b"CDF\x02"], ".nc4": [b"\x89HDF"]}


async def read_upload(file: UploadFile, allowed: set[str] | None = None) -> tuple[bytes, str]:
    """Read an upload with a size limit and verify the file type by suffix and content signature."""
    s = get_settings()
    name = (file.filename or "upload").lower()
    suffix = next((suf for suf in sorted(s.allowed_upload_suffixes, key=len, reverse=True) if name.endswith(suf)), None)
    if name.endswith(".epw.gz"):
        suffix = ".epw"
    if suffix is None or (allowed and suffix not in allowed):
        raise ValidationFailure(f"file type not allowed ({file.filename}); allowed: {sorted(allowed or s.allowed_upload_suffixes)}")
    limit = s.max_upload_mb * 1024 * 1024
    chunks, size = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ValidationFailure(f"upload exceeds the {s.max_upload_mb} MB limit")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw:
        raise ValidationFailure("empty upload")
    if suffix in SIGNATURES and not any(raw.startswith(sig) for sig in SIGNATURES[suffix]):
        raise ValidationFailure("file content is not NetCDF (signature mismatch)")
    if suffix in (".csv", ".txt", ".epw"):
        body = raw
        if raw[:2] == b"\x1f\x8b":
            import gzip

            body = gzip.decompress(raw)
        try:
            body[:4096].decode("utf-8")
        except UnicodeDecodeError:
            try:
                body[:4096].decode("latin-1")
            except UnicodeDecodeError as e:
                raise ValidationFailure("text upload is not valid UTF-8/Latin-1") from e
        if suffix == ".epw" and not body.startswith(b"LOCATION"):
            raise ValidationFailure("EPW files must start with a LOCATION header")
        if b"\x00" in body[:4096]:
            raise ValidationFailure("binary content in a text upload")
    return raw, suffix
