from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from audit_log_service.core.db import Base


class AuditEvent(Base):
    """The append-only, hash-chained audit log table.

    sequence_number, content_hash, and prev_hash are permanent once written —
    nothing in this codebase ever updates them. Retention (Scenario B) nulls the
    detail columns below while leaving those three untouched; redaction (Scenario B)
    overwrites individual payload field values while leaving content_hash untouched,
    since content_hash covers payload via payload_field_commitments (per-field
    hashes), not the raw values directly.
    """

    __tablename__ = "audit_events"

    sequence_number: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )

    # Nullable because retention (Scenario B) nulls these on archival; NOT NULL at
    # write time is enforced by the write-path schema/service, not the DB column.
    event_type: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    payload_field_commitments: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
