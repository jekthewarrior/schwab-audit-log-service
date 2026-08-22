from fastapi import APIRouter, HTTPException, status

from audit_log_service.core.db import MaintenanceSessionDep
from audit_log_service.schemas.event import AuditEventOut
from audit_log_service.schemas.redact import RedactRequest
from audit_log_service.services.redact import (
    FieldAlreadyRedactedError,
    FieldNotFoundError,
    RecordArchivedError,
    RecordNotFoundError,
    redact_field,
)

router = APIRouter(prefix="/audit", tags=["redact"])


@router.post("/events/{sequence_number}/redact", response_model=AuditEventOut)
async def redact(
    sequence_number: int, body: RedactRequest, session: MaintenanceSessionDep
) -> AuditEventOut:
    try:
        event = await redact_field(
            session,
            sequence_number=sequence_number,
            field=body.field,
            actor_id=body.actor_id,
        )
    except RecordNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No record with sequence_number={sequence_number}"
        ) from exc
    except FieldNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Record has no payload field '{body.field}'"
        ) from exc
    except RecordArchivedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Record {sequence_number} is archived; its content is already discarded",
        ) from exc
    except FieldAlreadyRedactedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Field '{body.field}' is already redacted"
        ) from exc

    await session.commit()
    return AuditEventOut.model_validate(event)
