"""Shared helpers for the MCP servers: auth, DB access, serialization."""

from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import select

from apps.api.db import session_scope


class AuthError(Exception):
    pass


def check_token(provided: str | None, env_var: str) -> None:
    """Validate a per-server token. Dev default is allowed only locally.

    In production the expected token must be set and match; reads and writes
    can be split further with scoped tokens if needed.
    """

    expected = os.environ.get(env_var)
    if not expected:
        if os.environ.get("ENVIRONMENT", "local").lower() in {"production", "prod"}:
            raise AuthError(f"{env_var} must be set in production")
        return
    if provided != expected:
        raise AuthError("invalid token")


def jsonable(obj: Any) -> Any:
    """Recursively coerce ORM/date values into JSON-safe structures."""

    return json.loads(json.dumps(obj, default=str))


def fetch_all(model, **filters) -> list:
    with session_scope() as s:
        stmt = select(model)
        for k, v in filters.items():
            stmt = stmt.where(getattr(model, k) == v)
        return list(s.execute(stmt).scalars().all())


def get_by_id(model, pk: str):
    with session_scope() as s:
        return s.get(model, pk)
