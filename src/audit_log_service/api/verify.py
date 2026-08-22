from fastapi import APIRouter

from audit_log_service.core.db import SessionDep
from audit_log_service.schemas.verify import VerifyResult
from audit_log_service.services.verify import verify_chain

router = APIRouter(prefix="/audit", tags=["verify"])


@router.get("/verify", response_model=VerifyResult)
async def get_verify(session: SessionDep) -> VerifyResult:
    return await verify_chain(session)
