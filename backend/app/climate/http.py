"""HTTP access for external providers: timeouts, retries with exponential backoff, rate-limit handling,
and an on-disk response cache so that previously retrieved data remain available offline.

Secrets (API keys, tokens) are passed in headers and never become part of cache keys or log lines.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.errors import ProviderError
from app.core.logging import redact

log = logging.getLogger("second_nature.http")

RETRYABLE = {429, 500, 502, 503, 504}


class CachedClient:
    def __init__(self, provider: str, *, cache_dir: Path | None = None, transport: httpx.BaseTransport | None = None,
                 timeout_s: float | None = None, max_retries: int | None = None, backoff_base_s: float | None = None,
                 sleep=time.sleep):
        s = get_settings()
        self.provider = provider
        self.cache_dir = (cache_dir or s.cache_dir) / "http" / provider
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout_s or s.http_timeout_s
        self.max_retries = s.http_max_retries if max_retries is None else max_retries
        self.backoff = s.http_backoff_base_s if backoff_base_s is None else backoff_base_s
        self.sleep = sleep
        ua = s.http_user_agent + (f" mailto:{s.contact_email}" if s.contact_email else "")
        self.client = httpx.Client(timeout=self.timeout, transport=transport, headers={"User-Agent": ua}, follow_redirects=True)

    def _key(self, url: str, params: dict | None) -> str:
        blob = json.dumps({"u": url, "p": params or {}}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None, use_cache: bool = True) -> tuple[dict, dict]:
        """Return (json, meta). meta records cache use, attempts and the endpoint (without secrets)."""
        key = self._key(url, params)
        path = self.cache_dir / f"{key}.json"
        if use_cache and path.exists():
            payload = json.loads(path.read_text())
            return payload["body"], {"cached": True, "retrieved_at": payload["retrieved_at"], "endpoint": url, "attempts": 0}
        attempt = 0
        last_err = None
        while attempt <= self.max_retries:
            attempt += 1
            try:
                r = self.client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = f"{type(e).__name__}: {e}"
                log.warning(redact(f"{self.provider} transport error (attempt {attempt}): {last_err}"))
                self._wait(attempt, None)
                continue
            if r.status_code == 200:
                try:
                    body = r.json()
                except ValueError as e:
                    raise ProviderError(self.provider, f"invalid JSON response: {e}") from e
                retrieved = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                path.write_text(json.dumps({"retrieved_at": retrieved, "url": url, "params": params, "body": body}))
                return body, {"cached": False, "retrieved_at": retrieved, "endpoint": url, "attempts": attempt}
            if r.status_code in RETRYABLE and attempt <= self.max_retries:
                last_err = f"HTTP {r.status_code}"
                log.warning(f"{self.provider} HTTP {r.status_code} (attempt {attempt}); backing off")
                self._wait(attempt, r.headers.get("Retry-After"))
                continue
            detail = r.text[:300]
            raise ProviderError(self.provider, f"HTTP {r.status_code}: {redact(detail)}", retryable=r.status_code in RETRYABLE)
        raise ProviderError(self.provider, f"failed after {attempt} attempts ({last_err})", retryable=True)

    def _wait(self, attempt: int, retry_after: str | None):
        delay = self.backoff * (2 ** (attempt - 1))
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        self.sleep(min(delay, 60.0))
