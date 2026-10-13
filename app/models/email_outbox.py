from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint, Index, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_email_outbox_event_key"),
        Index("ix_email_outbox_pending", "status", "next_attempt_at"),
        Index("ix_email_outbox_lease_expiry", "status", "lease_expires_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_key = Column(String(200), nullable=False)
    recipient = Column(String(255), nullable=False)
    event_type = Column(String(50), nullable=False)
    payload_json = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, server_default="pending")
    attempts = Column(Integer, nullable=False, server_default="0")
    next_attempt_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lease_token = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)


class EmailOutboxAudit(Base):
    __tablename__ = "email_outbox_audit"
    __table_args__ = (Index("ix_email_outbox_audit_event_id", "event_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(Integer, ForeignKey("email_outbox.id"), nullable=False)
    action = Column(String(32), nullable=False)
    actor = Column(String(100), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
