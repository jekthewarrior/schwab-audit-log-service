from fastapi import FastAPI

from audit_log_service.api.events import router as events_router
from audit_log_service.api.verify import router as verify_router

app = FastAPI(title="Audit Log Service")
app.include_router(events_router)
app.include_router(verify_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
