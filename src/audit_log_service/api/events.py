from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from audit_log_service.core.db import SessionDep
from audit_log_service.schemas.event import AuditEventCreate, AuditEventOut
from audit_log_service.schemas.query import AuditEventPage
from audit_log_service.services.append import append_event
from audit_log_service.services.query import list_events

router = APIRouter(prefix="/audit", tags=["events"])


@router.post("/events", status_code=status.HTTP_201_CREATED, response_model=AuditEventOut)
async def create_event(body: AuditEventCreate, session: SessionDep) -> AuditEventOut:
    """Append-only: no update or delete route exists anywhere in this API (req 2)."""
    event = await append_event(
        session,
        event_type=body.event_type,
        actor_id=body.actor_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        payload=body.payload,
        timestamp=body.timestamp,
    )
    await session.commit()
    return AuditEventOut.model_validate(event)


@router.get("/events", response_model=AuditEventPage)
async def query_events(
    session: SessionDep,
    actor_id: Annotated[str | None, Query(alias="actorId")] = None,
    resource_type: Annotated[str | None, Query(alias="resourceType")] = None,
    resource_id: Annotated[str | None, Query(alias="resourceId")] = None,
    event_type: Annotated[str | None, Query(alias="eventType")] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query(alias="to")] = None,
    include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
    cursor: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> AuditEventPage:
    """Filters combine with AND (4b); resourceType/resourceId are independently
    valid (4c); from/to filter the caller-supplied timestamp (4a); cursor pagination
    descending by sequence_number, newest first (5a/5b/5d).
    """
    records, next_cursor = await list_events(
        session,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        event_type=event_type,
        from_=from_,
        to=to,
        include_archived=include_archived,
        cursor=cursor,
        limit=limit,
    )
    return AuditEventPage(
        records=[AuditEventOut.model_validate(r) for r in records],
        next_cursor=next_cursor,
    )
