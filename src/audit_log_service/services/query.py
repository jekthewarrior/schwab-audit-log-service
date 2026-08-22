from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from audit_log_service.models import AuditEvent


def build_filtered_query(
    *,
    actor_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    include_archived: bool = True,
) -> Select[tuple[AuditEvent]]:
    """Shared filter-building logic (REQUIREMENTS.md 4a-4c: AND semantics,
    resourceType/resourceId independently valid, from/to against the
    caller-supplied timestamp) — used by both list_events (paginated, archived
    excluded by default) and the export service (Scenario B 5a-5e: unpaginated,
    archived records included by default, since a compliance export is meant to
    show the complete history for a resource/actor, not just the active subset).

    Note on include_archived combined with other filters: archived records have
    every detail column nulled (Scenario B 2a/2b), including actor_id, resource_*,
    event_type, and timestamp. A NULL never satisfies an equality or range
    comparison in SQL, so an archived record simply won't match any of those
    filters, with or without include_archived=True. In practice this means
    include_archived mostly surfaces archived records when no other filter narrows
    them out — consistent with 1e's documented limitation that archived records'
    content isn't meaningfully retrievable/searchable anymore, only their existence
    and chain-integrity fields are.
    """
    query = select(AuditEvent)

    if actor_id is not None:
        query = query.where(AuditEvent.actor_id == actor_id)
    if resource_type is not None:
        query = query.where(AuditEvent.resource_type == resource_type)
    if resource_id is not None:
        query = query.where(AuditEvent.resource_id == resource_id)
    if event_type is not None:
        query = query.where(AuditEvent.event_type == event_type)
    if from_ is not None:
        query = query.where(AuditEvent.timestamp >= from_)
    if to is not None:
        query = query.where(AuditEvent.timestamp <= to)
    if not include_archived:
        query = query.where(AuditEvent.archived.is_(False))

    return query


async def list_events(
    session: AsyncSession,
    *,
    actor_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    event_type: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    include_archived: bool = False,
    cursor: int | None = None,
    limit: int = 50,
) -> tuple[list[AuditEvent], int | None]:
    """Filtered, cursor-paginated query. 5a/5d/5b: keyset pagination on
    sequence_number, descending (newest first). Scenario B 1e: archived records
    excluded by default (opposite default from the export service — see
    build_filtered_query's docstring for why).

    Returns (page_of_records, next_cursor). next_cursor is None once there's
    nothing more to page through.
    """
    query = build_filtered_query(
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        from_=from_,
        to=to,
        include_archived=include_archived,
    ).order_by(AuditEvent.sequence_number.desc())

    if cursor is not None:
        query = query.where(AuditEvent.sequence_number < cursor)

    # Fetch one extra row to determine whether there's a next page, without a
    # separate COUNT query.
    query = query.limit(limit + 1)

    rows = list((await session.scalars(query)).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].sequence_number if has_more else None
    return page, next_cursor
