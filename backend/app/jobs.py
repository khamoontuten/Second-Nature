"""Background jobs: in-process thread pool with DB-persisted status, progress and cooperative cancellation.

Suitable for a single-node workstation deployment. Work functions receive ``progress(fraction, message)``
and ``check_cancel()`` callbacks; ``check_cancel`` raises :class:`JobCancelled` when cancellation was
requested, which long loops (EAHE, optimisation, Monte Carlo) call regularly. A multi-worker deployment
would replace this module with a Redis-backed queue behind the same interface.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.errors import JobCancelled, SecondNatureError
from app.database.models import Job
from app.database.session import session_scope

log = logging.getLogger("second_nature.jobs")


class JobManager:
    def __init__(self, workers: int | None = None):
        self.pool = ThreadPoolExecutor(max_workers=workers or get_settings().job_workers, thread_name_prefix="sn-job")
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        self._last_write: dict[str, float] = {}

    def submit(self, kind: str, fn: Callable, *args, **kwargs) -> str:
        with session_scope() as s:
            job = Job(kind=kind, status="queued", message="queued")
            s.add(job)
            s.flush()
            job_id = job.id
        self.pool.submit(self._run, job_id, fn, args, kwargs)
        return job_id

    def cancel(self, job_id: str) -> bool:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None or job.status in ("succeeded", "failed", "cancelled"):
                return False
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.message = "cancelled before start"
        with self._lock:
            self._cancel.add(job_id)
        return True

    def _update(self, job_id: str, *, force: bool = False, **fields):
        now = time.monotonic()
        if not force and now - self._last_write.get(job_id, 0.0) < 0.5:
            return
        self._last_write[job_id] = now
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is not None:
                for k, v in fields.items():
                    setattr(job, k, v)
                job.updated_at = datetime.now(UTC)

    def _run(self, job_id: str, fn, args, kwargs):
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job is None or job.status == "cancelled":
                return
            job.status = "running"
            job.message = "started"

        def progress(frac: float, msg: str = ""):
            self._update(job_id, progress=float(max(0.0, min(frac, 1.0))), message=msg[:500])

        def check_cancel():
            with self._lock:
                if job_id in self._cancel:
                    raise JobCancelled("job cancelled by user")

        try:
            ref = fn(*args, progress=progress, check_cancel=check_cancel, job_id=job_id, **kwargs)
            self._update(job_id, force=True, status="succeeded", progress=1.0, message="done", result_ref=ref)
        except JobCancelled:
            self._update(job_id, force=True, status="cancelled", message="cancelled")
        except SecondNatureError as e:
            log.warning(f"job {job_id} failed: {e.message}")
            self._update(job_id, force=True, status="failed", message=e.message[:500], error=e.message)
        except Exception as e:  # noqa: BLE001 - recorded for the user, full trace in logs
            log.error(f"job {job_id} crashed: {e}\n{traceback.format_exc()}")
            self._update(job_id, force=True, status="failed", message=f"{type(e).__name__}: {e}"[:500],
                         error=traceback.format_exc()[-4000:])
        finally:
            with self._lock:
                self._cancel.discard(job_id)

    def wait(self, job_id: str, timeout_s: float = 600.0) -> Job:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            with session_scope() as s:
                job = s.get(Job, job_id)
                if job and job.status in ("succeeded", "failed", "cancelled"):
                    s.expunge(job)
                    return job
            time.sleep(0.2)
        raise TimeoutError(f"job {job_id} did not finish within {timeout_s} s")


_manager: JobManager | None = None


def get_jobs() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
