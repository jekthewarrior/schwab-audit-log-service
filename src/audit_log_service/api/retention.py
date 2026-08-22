from fastapi import APIRouter

from audit_log_service.core.db import MaintenanceSessionDep
from audit_log_service.schemas.retention import RetentionSweepRequest, RetentionSweepResult
from audit_log_service.services.retention import sweep_retention

router = APIRouter(prefix="/audit", tags=["retention"])


@router.post("/retention/sweep", response_model=RetentionSweepResult)
async def retention_sweep(
    body: RetentionSweepRequest, session: MaintenanceSessionDep
) -> RetentionSweepResult:
    archived = await sweep_retention(session, actor_id=body.actor_id)
    await session.commit()
    return RetentionSweepResult(archived_sequence_numbers=archived)
