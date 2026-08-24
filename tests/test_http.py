"""HTTP-layer tests: routing, request validation, dependency injection, and
response serialization through the real FastAPI app — the layer every other test
module bypasses by calling service functions directly. Deep behavioral correctness
(every violation type, redaction/retention mechanics, signature verification) is
already covered at the service layer; these tests focus on what's unique to HTTP:
status codes, response shape, and that the wiring between routers and services is
actually correct end to end.
"""

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def test_create_event_returns_camelcase_response(client: AsyncClient) -> None:
    response = await client.post(
        "/audit/events",
        json={
            "eventType": "USER_LOGIN",
            "actorId": "user-1",
            "resourceType": "SESSION",
            "resourceId": "sess-1",
            "payload": {"ip": "127.0.0.1"},
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["sequenceNumber"] == 1
    assert body["contentHash"] and len(body["contentHash"]) == 64
    assert body["prevHash"] == "0" * 64
    assert body["archived"] is False


async def test_create_event_invalid_event_type_returns_422_and_no_sequence_consumed(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/audit/events",
        json={
            "eventType": "user_login",  # lowercase — violates the pattern (1a)
            "actorId": "user-1",
            "resourceType": "SESSION",
            "resourceId": "sess-1",
            "payload": {},
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 422

    # Confirm the rejected write never touched the chain (1d).
    ok = await client.post(
        "/audit/events",
        json={
            "eventType": "USER_LOGIN",
            "actorId": "user-1",
            "resourceType": "SESSION",
            "resourceId": "sess-1",
            "payload": {},
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )
    assert ok.json()["sequenceNumber"] == 1


async def test_query_events_returns_pagination_envelope(client: AsyncClient) -> None:
    for i in range(3):
        await client.post(
            "/audit/events",
            json={
                "eventType": "USER_LOGIN",
                "actorId": f"user-{i}",
                "resourceType": "SESSION",
                "resourceId": f"sess-{i}",
                "payload": {},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )

    response = await client.get("/audit/events", params={"limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert [r["sequenceNumber"] for r in body["records"]] == [3, 2]
    assert body["nextCursor"] == 2


async def test_verify_reports_intact_over_http(client: AsyncClient) -> None:
    response = await client.get("/audit/verify")
    assert response.status_code == 200
    assert response.json() == {
        "intact": True,
        "sequenceNumber": None,
        "violationType": None,
        "detail": None,
    }


async def test_redact_via_http_returns_marker(client: AsyncClient) -> None:
    await client.post(
        "/audit/events",
        json={
            "eventType": "RECORD_UPDATED",
            "actorId": "user-1",
            "resourceType": "ACCOUNT",
            "resourceId": "acct-1",
            "payload": {"accountNumber": "123-45-6789"},
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )

    response = await client.post(
        "/audit/events/1/redact", json={"field": "accountNumber", "actorId": "compliance-1"}
    )
    assert response.status_code == 200
    assert response.json()["payload"]["accountNumber"]["__redacted__"] is True


async def test_redact_nonexistent_record_returns_404_over_http(client: AsyncClient) -> None:
    response = await client.post(
        "/audit/events/999/redact", json={"field": "x", "actorId": "compliance-1"}
    )
    assert response.status_code == 404


async def test_retention_sweep_via_http(
    client: AsyncClient, maintenance_session: AsyncSession
) -> None:
    await client.post(
        "/audit/events",
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

    response = await client.post("/audit/retention/sweep", json={"actorId": "cron"})
    assert response.status_code == 200
    assert response.json()["archivedSequenceNumbers"] == [1]


async def test_export_requires_a_filter_over_http(client: AsyncClient) -> None:
    response = await client.get("/audit/export")
    assert response.status_code == 400


async def test_export_returns_signed_bundle_over_http(client: AsyncClient) -> None:
    await client.post(
        "/audit/events",
        json={
            "eventType": "ACCOUNT_VIEWED",
            "actorId": "user-1",
            "resourceType": "ACCOUNT",
            "resourceId": "acct-1",
            "payload": {},
            "timestamp": "2026-01-01T00:00:00Z",
        },
    )

    response = await client.get("/audit/export", params={"resourceId": "acct-1"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["records"]) == 1
    assert body["signature"]
    assert body["signingKeyId"]


async def test_public_key_endpoint_over_http(client: AsyncClient) -> None:
    response = await client.get("/audit/export/public-key")
    assert response.status_code == 200
    body = response.json()
    assert len(body["publicKeyHex"]) == 64


async def test_full_acceptance_flow_over_http(
    client: AsyncClient, admin_session: AsyncSession
) -> None:
    """req 9's acceptance flow through the real HTTP surface end to end: write ->
    query -> verify -> direct-datastore tamper (bypassing the API entirely) ->
    verify again, catching it. Everything else in this suite verifies the pieces;
    this is the one test proving they're wired together correctly as a whole.
    """
    write = await client.post(
        "/audit/events",
        json={
            "eventType": "RECORD_UPDATED",
            "actorId": "user-1",
            "resourceType": "ACCOUNT",
            "resourceId": "acct-1",
            "payload": {"balance": 500},
            "timestamp": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        },
    )
    assert write.status_code == 201

    query = await client.get("/audit/events", params={"resourceId": "acct-1"})
    assert len(query.json()["records"]) == 1

    verify = await client.get("/audit/verify")
    assert verify.json()["intact"] is True

    await admin_session.execute(
        text("UPDATE audit_events SET payload = '{\"balance\": 999999}' WHERE sequence_number = 1")
    )
    await admin_session.commit()

    verify_after_tamper = await client.get("/audit/verify")
    body = verify_after_tamper.json()
    assert body["intact"] is False
    assert body["sequenceNumber"] == 1
    assert body["violationType"] == "CONTENT_MISMATCH"
