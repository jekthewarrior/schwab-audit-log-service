from typing import Annotated

from fastapi import APIRouter, Depends

from audit_log_service.core.auth import require_roles
from audit_log_service.core.config import Principal
from audit_log_service.core.db import SessionDep
from audit_log_service.schemas.verify import VerifyResult
from audit_log_service.services.verify import verify_chain

router = APIRouter(prefix="/audit", tags=["verify"])


@router.get("/verify", response_model=VerifyResult)
async def get_verify(
    session: SessionDep, _principal: Annotated[Principal, Depends(require_roles("reader"))]
) -> VerifyResult:
    """Deliberately not resource-scoped (C12) — discloses no account-specific
    content (only sequenceNumber/violationType), so per-account scoping would fight
    the single global chain design (7a) for no confidentiality benefit.
    """
    return await verify_chain(session)
