"""Auth/authz enforcement (REQUIREMENTS.md C7-C10, task C3.5): every route except
GET /health and GET /audit/export/public-key requires a valid API key (C7) and the
role C9's table assigns to that endpoint. Business-logic behavior of each endpoint
is covered elsewhere (test_http.py, service-layer tests) — this file is scoped to
the auth layer itself: does the right key/role combination get through, and does
every wrong combination get denied.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.core.config import Principal, settings
from tests.conftest import auth_headers

ALL_ROLES = ("writer", "reader", "compliance", "scheduler")

# (method, path, request body if any, role the endpoint requires per C9)
PROTECTED_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, str]] = [
    (
        "POST",
        "/audit/events",
        {
            "eventType": "USER_LOGIN",
            "actorId": "user-1",
            "resourceType": "SESSION",
            "resourceId": "sess-1",
            "payload": {},
            "timestamp": "2026-01-01T00:00:00Z",
        },
        "writer",
    ),
    ("GET", "/audit/events", None, "reader"),
    ("GET", "/audit/verify", None, "reader"),
    ("POST", "/audit/events/1/redact", {"field": "x"}, "compliance"),
    ("POST", "/audit/retention/sweep", None, "scheduler"),
    ("GET", "/audit/export?resourceId=acct-1", None, "compliance"),
]


async def _send(
    client: AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
) -> Any:
    kwargs: dict[str, Any] = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    return await client.request(method, path, **kwargs)


@pytest.mark.parametrize("method,path,body,role", PROTECTED_ENDPOINTS)
async def test_missing_api_key_returns_401(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None, role: str
) -> None:
    response = await _send(client, method, path, body, headers={})
    assert response.status_code == 401


@pytest.mark.parametrize("method,path,body,role", PROTECTED_ENDPOINTS)
async def test_invalid_api_key_returns_401(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None, role: str
) -> None:
    response = await _send(client, method, path, body, headers={"X-API-Key": "not-a-real-key"})
    assert response.status_code == 401


@pytest.mark.parametrize("method,path,body,role", PROTECTED_ENDPOINTS)
async def test_wrong_role_returns_403(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None, role: str
) -> None:
    # A valid key authenticates fine; every role other than the one this endpoint
    # requires should still be denied by the role check.
    other_role = next(r for r in ALL_ROLES if r != role)
    response = await _send(client, method, path, body, headers=auth_headers(other_role))
    assert response.status_code == 403


@pytest.mark.parametrize("method,path,body,role", PROTECTED_ENDPOINTS)
async def test_correct_role_passes_the_auth_gate(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None, role: str
) -> None:
    # Scoped to the auth layer specifically, not full business-logic success (some
    # of these — e.g. redact against a record that doesn't exist yet — legitimately
    # 404 downstream; the full happy path per endpoint is test_http.py's job). What
    # this proves: the right role is never blocked by 401/403.
    response = await _send(client, method, path, body, headers=auth_headers(role))
    assert response.status_code not in (401, 403)


async def test_health_requires_no_auth(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200


async def test_public_key_requires_no_auth(client: AsyncClient) -> None:
    response = await client.get("/audit/export/public-key")
    assert response.status_code == 200


async def test_compliance_role_can_redact_a_record_the_writer_role_created(
    client: AsyncClient,
) -> None:
    """Full happy path through the auth layer for redact specifically, since C10
    changed what this endpoint accepts: actorId is no longer a request field, it's
    derived from the authenticated `compliance` principal.
    """
    write = await client.post(
        "/audit/events",
        headers=auth_headers("writer"),
        json={
            "eventType": "RECORD_UPDATED",
            "actorId": "user-1",
            "resourceType": "ACCOUNT",
            "resourceId": "acct-1",
            "payload": {"accountNumber": "123-45-6789"},
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    seq = write.json()["sequenceNumber"]

    response = await client.post(
        f"/audit/events/{seq}/redact",
        headers=auth_headers("compliance"),
        json={"field": "accountNumber"},
    )
    assert response.status_code == 200
    assert response.json()["payload"]["accountNumber"]["__redacted__"] is True

    # The FIELD_REDACTED system event's actorId is the authenticated principal
    # (compliance-officer-1 per config.py's dev key map), not caller-supplied.
    events = await client.get(
        "/audit/events", headers=auth_headers("reader"), params={"eventType": "FIELD_REDACTED"}
    )
    [redaction_event] = events.json()["records"]
    assert redaction_event["actorId"] == "compliance-officer-1"


async def test_redact_rejects_a_spoofed_actor_id_instead_of_silently_ignoring_it(
    client: AsyncClient,
) -> None:
    """C10: actorId used to be a trusted body field; now it's derived from the
    authenticated principal. A caller still sending it (e.g. a stale client, or
    an attempted spoof) gets a loud 422 (RedactRequest's extra="forbid"), not a
    silent no-op that could mask a caller's misunderstanding of who's on record
    as having performed the redaction.
    """
    response = await client.post(
        "/audit/events/1/redact",
        headers=auth_headers("compliance"),
        json={"field": "x", "actorId": "someone-else"},
    )
    assert response.status_code == 422


async def test_scheduler_role_retention_sweep_actor_id_is_the_authenticated_principal(
    client: AsyncClient, maintenance_session: AsyncSession
) -> None:
    """Same C10 guarantee as redact's, checked for retention sweep: the
    RECORD_ARCHIVED system event's actorId is the authenticated `scheduler`
    principal (cron-scheduler per config.py's dev key map), not anything a
    caller could set — there's no request body for this endpoint at all now.
    """
    await client.post(
        "/audit/events",
        headers=auth_headers("writer"),
        json={
            "eventType": "USER_LOGIN",
            "actorId": "user-1",
            "resourceType": "SESSION",
            "resourceId": "sess-1",
            "payload": {},
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    await maintenance_session.execute(
        text("UPDATE audit_events SET recorded_at = now() - interval '2 years'")
    )
    await maintenance_session.commit()

    sweep = await client.post("/audit/retention/sweep", headers=auth_headers("scheduler"))
    assert sweep.status_code == 200
    assert sweep.json()["archivedSequenceNumbers"] == [1]

    events = await client.get(
        "/audit/events", headers=auth_headers("reader"), params={"eventType": "RECORD_ARCHIVED"}
    )
    [archive_event] = events.json()["records"]
    assert archive_event["actorId"] == "cron-scheduler"


async def test_verify_is_not_resource_scoped_even_for_a_scoped_principal(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C12: GET /audit/verify deliberately isn't resource-scoped — it discloses
    no account-specific content, only sequenceNumber/violationType, so a scoped
    `reader` principal still gets the full-chain result, not a restricted view.
    """
    monkeypatch.setitem(
        settings.api_keys,
        "test-scoped-reader-key",
        Principal(
            principal_id="scoped-reader",
            roles=frozenset({"reader"}),
            resource_scope=frozenset({"acct-a"}),
        ),
    )

    response = await client.get(
        "/audit/verify", headers={"X-API-Key": "test-scoped-reader-key"}
    )
    assert response.status_code == 200
    assert response.json()["intact"] is True
