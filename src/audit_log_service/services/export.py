from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.core.hashing import canonical_json, canonical_timestamp, record_hash
from audit_log_service.core.signing import SIGNING_KEY_ID, sign
from audit_log_service.models import AuditEvent
from audit_log_service.schemas.event import AuditEventOut
from audit_log_service.schemas.export import ChainTailSnapshot, ExportBundle
from audit_log_service.services.query import build_filtered_query


class NoFilterProvidedError(Exception):
    pass


async def export_bundle(
    session: AsyncSession,
    *,
    actor_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    resource_scope: frozenset[str] | None = None,
) -> ExportBundle:
    """Signed, self-contained export bundle. REQUIREMENTS.md Scenario B 5a-5e.

    resource_scope (C12): a `compliance` principal's resourceId allow-list,
    intersected into the query the same way list_events does — see
    services/query.py's build_filtered_query for the shared logic and the
    explicit-resourceId 404 this doesn't itself handle (that's
    core/auth.py's check_resource_access, called before this function).

    Requires at least one of resource_id/actor_id — an unscoped export of the
    entire chain isn't what this endpoint is for (that's /audit/events and
    /audit/verify). eventType/from/to are additional, composable filters — this
    same parameter set satisfies both Scenario B's original ask and Scenario C's
    compliance-reporting extension (see Scenario C's technical design), so there's
    no separate endpoint for that use case.

    Signature covers the canonical serialization of every other field (5a) —
    self-consistency of individual records isn't enough on its own, since an
    attacker able to edit the bundle file could edit a record's content and its
    accompanying hash together. The signature is what actually proves the bundle
    hasn't been altered since export. Records are included regardless of archived
    status and sorted by sequence_number (5c) — cheap, and gives a recipient an
    informal at-a-glance sense of gaps without a second chain construct.
    """
    if actor_id is None and resource_id is None:
        raise NoFilterProvidedError

    tail = await session.scalar(
        select(AuditEvent).order_by(AuditEvent.sequence_number.desc()).limit(1)
    )
    chain_tail_snapshot = (
        ChainTailSnapshot(
            sequence_number=tail.sequence_number,
            record_hash=record_hash(tail.content_hash, tail.prev_hash),
        )
        if tail is not None
        else None
    )

    query = build_filtered_query(
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        from_=from_,
        to=to,
        include_archived=True,
        resource_scope=resource_scope,
    ).order_by(AuditEvent.sequence_number.asc())
    records = list((await session.scalars(query)).all())

    filter_dict: dict[str, str] = {}
    if actor_id is not None:
        filter_dict["actorId"] = actor_id
    if resource_type is not None:
        filter_dict["resourceType"] = resource_type
    if resource_id is not None:
        filter_dict["resourceId"] = resource_id
    if event_type is not None:
        filter_dict["eventType"] = event_type
    if from_ is not None:
        filter_dict["from"] = canonical_timestamp(from_)
    if to is not None:
        filter_dict["to"] = canonical_timestamp(to)

    bundle = ExportBundle(
        exported_at=datetime.now(UTC),
        filter=filter_dict,
        chain_tail_snapshot=chain_tail_snapshot,
        records=[AuditEventOut.model_validate(r) for r in records],
        signing_key_id=SIGNING_KEY_ID,
        signature="",
    )

    # Sign the exact JSON structure the bundle will be served as (everything except
    # the signature field itself) — deriving this from the model's own
    # serialization, rather than hand-building a parallel dict, guarantees the
    # signed bytes and the response body can never drift apart.
    signable = bundle.model_dump(mode="json", by_alias=True, exclude={"signature"})
    bundle.signature = sign(canonical_json(signable)).hex()
    return bundle
