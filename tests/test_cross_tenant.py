"""Cross-account denial (REQUIREMENTS.md C12, task C3.6): a resourceScope-scoped
`compliance`/`reader` principal must be denied another account's data — both when
naming it explicitly (404, not 403 or an empty 200 that would still confirm the
account exists) and when omitting the resourceId filter entirely, proving
enforcement happens server-side rather than only against what the caller thought
to ask for.

The default dev API keys (config.py) are deliberately unscoped — scoping is
opt-in per principal, so this test injects two scoped keys via monkeypatch rather
than adding test-only fixtures to production config.
"""

from typing import Any

from httpx import AsyncClient
from pytest import MonkeyPatch

from audit_log_service.core.config import Principal, settings
from tests.conftest import auth_headers

SCOPED_KEY_A = "test-scoped-key-acct-a"
SCOPED_KEY_B = "test-scoped-key-acct-b"


async def _seed_events(client: AsyncClient) -> None:
    for resource_id in ("acct-a", "acct-b"):
        response = await client.post(
            "/audit/events",
            headers=auth_headers("writer"),
            json={
                "eventType": "ACCOUNT_VIEWED",
                "actorId": "user-1",
                "resourceType": "ACCOUNT",
                "resourceId": resource_id,
                "payload": {},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        )
        assert response.status_code == 201


def _register_scoped_keys(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setitem(
        settings.api_keys,
        SCOPED_KEY_A,
        Principal(
            principal_id="compliance-acct-a",
            roles=frozenset({"reader", "compliance"}),
            resource_scope=frozenset({"acct-a"}),
        ),
    )
    monkeypatch.setitem(
        settings.api_keys,
        SCOPED_KEY_B,
        Principal(
            principal_id="compliance-acct-b",
            roles=frozenset({"reader", "compliance"}),
            resource_scope=frozenset({"acct-b"}),
        ),
    )


async def test_scoped_principal_reaches_only_its_own_account(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    _register_scoped_keys(monkeypatch)
    await _seed_events(client)
    headers_a = {"X-API-Key": SCOPED_KEY_A}

    query = await client.get("/audit/events", headers=headers_a, params={"resourceId": "acct-a"})
    assert query.status_code == 200
    assert [r["resourceId"] for r in query.json()["records"]] == ["acct-a"]

    export = await client.get(
        "/audit/export", headers=headers_a, params={"resourceId": "acct-a"}
    )
    assert export.status_code == 200
    assert [r["resourceId"] for r in export.json()["records"]] == ["acct-a"]


async def test_scoped_principal_denied_other_account_by_explicit_id(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    _register_scoped_keys(monkeypatch)
    await _seed_events(client)
    headers_a = {"X-API-Key": SCOPED_KEY_A}

    query = await client.get("/audit/events", headers=headers_a, params={"resourceId": "acct-b"})
    assert query.status_code == 404

    export = await client.get(
        "/audit/export", headers=headers_a, params={"resourceId": "acct-b"}
    )
    assert export.status_code == 404


async def test_scoped_principal_denied_other_account_even_without_naming_it(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    """The explicit-id check (previous test) alone wouldn't stop a scoped caller
    from just omitting resourceId to see everyone — this confirms the scope is
    intersected into the query itself, not merely checked against what was asked.
    """
    _register_scoped_keys(monkeypatch)
    await _seed_events(client)
    headers_a = {"X-API-Key": SCOPED_KEY_A}

    query: dict[str, Any] = (
        await client.get("/audit/events", headers=headers_a)
    ).json()
    assert {r["resourceId"] for r in query["records"]} == {"acct-a"}

    export: dict[str, Any] = (
        await client.get("/audit/export", headers=headers_a, params={"actorId": "user-1"})
    ).json()
    assert {r["resourceId"] for r in export["records"]} == {"acct-a"}


async def test_denial_is_symmetric_across_both_scoped_principals(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    """Guards against the check accidentally only working in one direction (e.g.
    a bug that only excludes a hardcoded account rather than genuinely
    intersecting each principal's own scope).
    """
    _register_scoped_keys(monkeypatch)
    await _seed_events(client)
    headers_b = {"X-API-Key": SCOPED_KEY_B}

    query = await client.get("/audit/events", headers=headers_b, params={"resourceId": "acct-a"})
    assert query.status_code == 404


async def test_scope_intersects_with_other_filters_not_just_resource_id(
    client: AsyncClient, monkeypatch: MonkeyPatch
) -> None:
    """Both seeded accounts share resourceType=ACCOUNT — confirms the scope
    intersection is keyed on resourceId specifically, not accidentally
    satisfied by matching some other filter the caller supplied instead.
    """
    _register_scoped_keys(monkeypatch)
    await _seed_events(client)
    headers_a = {"X-API-Key": SCOPED_KEY_A}

    query = await client.get(
        "/audit/events", headers=headers_a, params={"resourceType": "ACCOUNT"}
    )
    assert query.status_code == 200
    assert {r["resourceId"] for r in query.json()["records"]} == {"acct-a"}
