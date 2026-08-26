from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from audit_log_service.core.config import Principal, settings

API_KEY_HEADER = "X-API-Key"


def _authenticate(request: Request) -> Principal:
    """C7/C8: every route except GET /health and GET /audit/export/public-key runs
    behind this. A missing header and an unrecognized key both come back as the
    same 401 — deliberately not distinguished, so a caller probing keys can't tell
    "wrong key" from "no key" apart.
    """
    api_key = request.headers.get(API_KEY_HEADER)
    principal = settings.api_keys.get(api_key) if api_key is not None else None
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid API key")
    return principal


def require_roles(*roles: str) -> Callable[..., Principal]:
    """C8/C9: FastAPI dependency factory requiring the authenticated principal to
    hold at least one of `roles`. Authentication (401) always runs first, so a
    caller learns "you're not authenticated" before "you're authenticated but not
    authorized" — never the reverse.
    """
    required = frozenset(roles)

    def _require_roles(principal: Annotated[Principal, Depends(_authenticate)]) -> Principal:
        if not principal.roles & required:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Principal lacks the required role")
        return principal

    return _require_roles


def check_resource_access(principal: Principal, resource_id: str | None) -> None:
    """C12: denies an explicitly-named resourceId outside a scoped principal's
    allow-list, with 404 (not 403) — a scoped caller shouldn't be able to tell
    "that account doesn't exist" from "that account isn't yours" apart. An
    unscoped principal (resource_scope is None) or a request naming no
    resourceId at all passes through untouched; the latter is still constrained
    by intersecting resource_scope into the query itself (services/query.py,
    services/export.py), not by this check alone.
    """
    if principal.resource_scope is None:
        return
    if resource_id is not None and resource_id not in principal.resource_scope:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
