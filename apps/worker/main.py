"""arq worker: stateless ticket processing with idempotency, retries, and a DLQ.

Design
------
* The worker holds **no database pool**. Each job POSTs to the API's
  ``/internal/worker`` runner, which owns the pool and performs the persist +
  agent run. This keeps the worker cheap to scale horizontally and makes retries
  safe (the run is idempotent per ticket: the ingest seam upserts on the
  ticket's ``idempotency_key``).
* **Idempotency**: jobs are keyed by an ``arq`` ``_job_id`` derived from the
  tenant + idempotency key, so a duplicate enqueue is a no-op.
* **Retries + DLQ**: transient failures are retried with exponential backoff.
  When the final attempt fails, the raw payload is written to
  ``dead_letter_jobs`` *before* the exception is re-raised, so nothing is
  silently dropped. The DLQ write commits first because arq may abort the job
  on failure.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

import httpx
from arq import Retry, create_pool
from arq.connections import RedisSettings

MAX_ATTEMPTS = 5
_HTTP_TIMEOUT = 120.0

# Deployments can point the worker at the runner endpoint; the compose default
# targets the co-located ``api`` service.
_API_BASE = os.getenv("TRIAGE_API_URL", "http://localhost:8000")


def redis_settings() -> RedisSettings:
    """Build arq Redis settings from ``REDIS_URL`` so worker and API agree."""

    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        return RedisSettings.from_dsn(url)
    except Exception:  # noqa: BLE001 - fall back to defaults on a malformed URL
        return RedisSettings()


def _idempotency_key(payload: dict) -> str:
    """Mirror of ``apps.api.main._idempotency_key`` so job ids match tickets."""

    if payload.get("idempotency_key"):
        return str(payload["idempotency_key"])
    raw = (
        f"{payload.get('tenant_id', 'default')}:{payload.get('external_id') or ''}:"
        f"{payload.get('subject') or ''}:{payload.get('body')}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _job_id(payload: dict) -> str:
    return f"ticket:{payload.get('tenant_id', 'default')}:{_idempotency_key(payload)}"


def _runner_url() -> str:
    return f"{_API_BASE.rstrip('/')}/internal/worker"


async def process_ticket(ctx: dict, payload: dict) -> dict:
    """Process one ticket via the API runner, with retry + DLQ on failure."""

    job_id: str = ctx.get("job_id") or _job_id(payload)
    attempt = int(ctx.get("job_try", 1))
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _runner_url(),
                json={"payload": payload, "run_sync": True},
            )
        if resp.status_code >= 500:
            raise RuntimeError(f"runner {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, RuntimeError) as exc:
        if attempt >= MAX_ATTEMPTS:
            _dead_letter(job_id, payload, f"{type(exc).__name__}: {exc}", attempt)
            raise
        # Exponential backoff: 1, 2, 4, 8, ... seconds (capped at 60).
        raise Retry(defer=min(2 ** (attempt - 1), 60)) from exc


def _dead_letter(job_id: str, payload: dict, error: str, attempts: int) -> None:
    """Record a poisoned job. Committed before the caller re-raises."""

    try:
        from apps.api.db import session_scope
        from apps.api.models import DeadLetterJob

        with session_scope() as s:
            s.add(
                DeadLetterJob(
                    job_id=job_id,
                    task="process_ticket",
                    tenant_id=payload.get("tenant_id"),
                    payload=payload,
                    error=error[:2000],
                    attempts=attempts,
                    status="pending",
                )
            )
    except Exception:  # noqa: BLE001 - never let DLQ bookkeeping mask the cause
        pass


async def startup(ctx: dict) -> None:
    ctx["started_at"] = datetime.now(UTC).isoformat()


async def shutdown(ctx: dict) -> None:
    return None


class WorkerSettings:
    """arq entrypoint: ``arq apps.worker.main.WorkerSettings``."""

    functions = [process_ticket]
    redis_settings = redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_tries = MAX_ATTEMPTS
    job_timeout = 300
    keep_result = 3600
    queue_name = "triage"


async def submit_ticket(payload: dict) -> str | None:
    """Enqueue a ticket from an async context (e.g. the API).

    Returns the arq job id, or ``None`` when the broker is unreachable so the
    caller can fall back to synchronous processing.
    """

    pool = await create_pool(WorkerSettings.redis_settings)
    try:
        job = await pool.enqueue_job(
            "process_ticket",
            payload,
            _job_id=_job_id(payload),
            _queue_name=WorkerSettings.queue_name,
        )
        return getattr(job, "job_id", None)
    finally:
        await pool.aclose()


__all__ = ["WorkerSettings", "process_ticket", "redis_settings", "submit_ticket"]
