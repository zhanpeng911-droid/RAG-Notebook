"""Durable note-vector synchronization outbox.

The note row and its vector task are committed in the same MySQL transaction.
Immediate Celery delivery is only a fast path; Beat retries pending/failed
rows, so a broker outage or API crash between commit and dispatch cannot leave
a note permanently unsynchronized.
"""
from sqlalchemy import (
    Column, DateTime, Integer, String, Text, UniqueConstraint, Index, func,
)

from app.models.chat_history import Base


class NoteVectorTaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class NoteVectorAction:
    UPSERT = "upsert"
    DELETE = "delete"


class NoteVectorTask(Base):
    __tablename__ = "note_vector_tasks"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "note_id", name="uq_note_vector_owner_note"),
        Index("ix_note_vector_due", "status", "next_retry_at"),
        Index("ix_note_vector_owner", "owner_user_id", "note_id"),
    )

    id = Column(String(36), primary_key=True)
    owner_user_id = Column(String(36), nullable=False, index=True)
    note_id = Column(String(36), nullable=False, index=True)
    action = Column(String(10), nullable=False, default=NoteVectorAction.UPSERT,
                    server_default=NoteVectorAction.UPSERT)
    status = Column(String(20), nullable=False, default=NoteVectorTaskStatus.PENDING,
                    server_default=NoteVectorTaskStatus.PENDING)
    # Incremented by every note save/delete. A worker may only mark done while
    # its claimed version is still current, so a newer save is never lost.
    version = Column(Integer, nullable=False, default=1, server_default="1")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    claim_token = Column(String(36), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
