from fastapi import APIRouter, status

from audit_log_service.core.db import SessionDep
from audit_log_service.schemas.event import AuditEventCreate, AuditEventOut
from audit_log_service.services.append import append_event

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
