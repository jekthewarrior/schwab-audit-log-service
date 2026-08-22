from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from audit_log_service.core.db import SessionDep
from audit_log_service.core.signing import SIGNING_KEY_ID, public_key_hex
from audit_log_service.schemas.export import ExportBundle
from audit_log_service.services.export import NoFilterProvidedError, export_bundle

router = APIRouter(prefix="/audit", tags=["export"])


@router.get("/export", response_model=ExportBundle)
async def get_export(
    session: SessionDep,
    actor_id: Annotated[str | None, Query(alias="actorId")] = None,
    resource_type: Annotated[str | None, Query(alias="resourceType")] = None,
    resource_id: Annotated[str | None, Query(alias="resourceId")] = None,
    event_type: Annotated[str | None, Query(alias="eventType")] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query(alias="to")] = None,
) -> ExportBundle:
    try:
        return await export_bundle(
            session,
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            event_type=event_type,
            from_=from_,
            to=to,
        )
    except NoFilterProvidedError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "At least one of actorId or resourceId is required"
        ) from exc


@router.get("/export/public-key")
async def get_export_public_key() -> dict[str, str]:
    """Lets a recipient fetch the current signing public key to verify a bundle
    against, without needing separate out-of-band distribution.
    """
    return {"signingKeyId": SIGNING_KEY_ID, "publicKeyHex": public_key_hex()}
