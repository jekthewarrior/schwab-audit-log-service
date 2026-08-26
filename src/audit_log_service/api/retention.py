from typing import Annotated

from fastapi import APIRouter, Depends

from audit_log_service.core.auth import require_roles
from audit_log_service.core.config import Principal
from audit_log_service.core.db import MaintenanceSessionDep
from audit_log_service.schemas.retention import RetentionSweepResult
from audit_log_service.services.retention import sweep_retention

router = APIRouter(prefix="/audit", tags=["retention"])


@router.post("/retention/sweep", response_model=RetentionSweepResult)
async def retention_sweep(
    session: MaintenanceSessionDep,
    principal: Annotated[Principal, Depends(require_roles("scheduler"))],
) -> RetentionSweepResult:
    """No request body — who ran the sweep is derived from the authenticated
    principal (C10), the same reasoning as redact's actorId change.
    """
    archived = await sweep_retention(session, actor_id=principal.principal_id)
    await session.commit()
    return RetentionSweepResult(archived_sequence_numbers=archived)
