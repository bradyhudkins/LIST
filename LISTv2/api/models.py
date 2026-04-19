"""
ORM models — Python 3.9 compatible (Optional[X] not X | None).
"""
import enum
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.database import Base


# ── Enums ──────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    viewer  = "viewer"
    analyst = "analyst"
    lead    = "lead"
    admin   = "admin"


class CaseSeverity(str, enum.Enum):
    low      = "low"
    medium   = "medium"
    high     = "high"
    critical = "critical"


class CaseStatus(str, enum.Enum):
    open        = "open"
    in_progress = "in_progress"
    resolved    = "resolved"
    closed      = "closed"


class ChainStatus(str, enum.Enum):
    pending   = "pending"
    promoted  = "promoted"
    dismissed = "dismissed"


class EntityType(str, enum.Enum):
    ip_address = "ip_address"
    hostname   = "hostname"
    domain     = "domain"
    user_account = "user_account"
    file_hash  = "file_hash"
    url        = "url"
    email      = "email"
    other      = "other"


class EntityRole(str, enum.Enum):
    source     = "source"
    target     = "target"
    associated = "associated"


# ── User ───────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.analyst, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    cases: Mapped[List["Case"]] = relationship("Case", back_populates="owner", foreign_keys="Case.owner_id")
    notes: Mapped[List["Note"]] = relationship("Note", back_populates="author")


# ── Case counter ───────────────────────────────────────────────────────────────

class CaseCounter(Base):
    __tablename__ = "case_counter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ── Case template ──────────────────────────────────────────────────────────────

class CaseTemplate(Base):
    __tablename__ = "case_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_severity: Mapped[CaseSeverity] = mapped_column(Enum(CaseSeverity), default=CaseSeverity.medium)

    # JSON array of checklist item strings, e.g. '["Isolate host","Collect memory"]'
    checklist_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Pipe-separated default T-codes, e.g. "T1566|T1059.001"
    initial_tcodes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Pipe-separated default entity type prompts
    entity_type_hints: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ── Case ───────────────────────────────────────────────────────────────────────

class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_number: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    severity: Mapped[CaseSeverity] = mapped_column(Enum(CaseSeverity), default=CaseSeverity.medium)
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus), default=CaseStatus.open)

    # Checklist state (JSON array: [{text, done}])
    checklist_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_note_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    template_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("case_templates.id"), nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    owner: Mapped[Optional["User"]] = relationship("User", back_populates="cases", foreign_keys=[owner_id])
    locked_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    locked_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[locked_by_id])

    # BIAS session context
    bias_platforms:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bias_cli_env:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bias_scripting:   Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bias_network:     Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bias_result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bias_ran_at:      Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    locked_at:        Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    observables: Mapped[List["Observable"]] = relationship("Observable", back_populates="case", cascade="all, delete-orphan")
    notes:       Mapped[List["Note"]]       = relationship("Note",       back_populates="case", cascade="all, delete-orphan")
    timeline:    Mapped[List["TimelineEvent"]] = relationship("TimelineEvent", back_populates="case", cascade="all, delete-orphan")
    attachments: Mapped[List["Attachment"]] = relationship("Attachment", back_populates="case", cascade="all, delete-orphan")
    case_entities: Mapped[List["CaseEntity"]] = relationship("CaseEntity", back_populates="case", cascade="all, delete-orphan")

    @property
    def lock_active(self) -> bool:
        from config import CASE_LOCK_TIMEOUT_SECONDS

        if not self.locked_by_id or not self.locked_at:
            return False
        return (datetime.utcnow() - self.locked_at).total_seconds() < CASE_LOCK_TIMEOUT_SECONDS

    @property
    def lock_expires_at(self) -> Optional[datetime]:
        from config import CASE_LOCK_TIMEOUT_SECONDS

        if not self.locked_at:
            return None
        return self.locked_at + timedelta(seconds=CASE_LOCK_TIMEOUT_SECONDS)


# ── Observable ────────────────────────────────────────────────────────────────

class Observable(Base):
    __tablename__ = "observables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), nullable=False)
    tcode: Mapped[str] = mapped_column(String(32), nullable=False)
    tname: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    case: Mapped["Case"] = relationship("Case", back_populates="observables")


# ── Entity ────────────────────────────────────────────────────────────────────

class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # comma-separated
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    created_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    case_links: Mapped[List["CaseEntity"]] = relationship("CaseEntity", back_populates="entity", cascade="all, delete-orphan")


class CaseEntity(Base):
    """Association between a case and an entity with an investigative role."""
    __tablename__ = "case_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, ForeignKey("entities.id"), nullable=False)
    role: Mapped[EntityRole] = mapped_column(Enum(EntityRole), default=EntityRole.associated)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    added_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    case:   Mapped["Case"]   = relationship("Case",   back_populates="case_entities")
    entity: Mapped["Entity"] = relationship("Entity", back_populates="case_links")


# ── Attachment ────────────────────────────────────────────────────────────────

class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    case: Mapped["Case"] = relationship("Case", back_populates="attachments")


# ── Note ───────────────────────────────────────────────────────────────────────

class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), nullable=False)
    author_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    case: Mapped["Case"] = relationship("Case", back_populates="notes")
    author: Mapped[Optional["User"]] = relationship("User", back_populates="notes")


# ── Timeline event ─────────────────────────────────────────────────────────────

class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    case: Mapped["Case"] = relationship("Case", back_populates="timeline")


# ── Notification config (scaffolded) ──────────────────────────────────────────

class NotificationConfig(Base):
    __tablename__ = "notification_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)  # email | slack | teams
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON blob
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


# ── Alert chain (staging queue) ───────────────────────────────────────────────

class AlertChain(Base):
    __tablename__ = "alert_chains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chain_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    hostname: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    src_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    severity: Mapped[str] = mapped_column(String(32), default="medium")
    status: Mapped[ChainStatus] = mapped_column(Enum(ChainStatus), default=ChainStatus.pending)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    tcode_sequence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    preanalysis_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    promoted_case_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cases.id"), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    alerts: Mapped[List["Alert"]] = relationship("Alert", back_populates="chain", cascade="all, delete-orphan")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chain_id: Mapped[int] = mapped_column(Integer, ForeignKey("alert_chains.id"), nullable=False)
    source_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    so_event_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    src_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    dst_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    hostname: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    severity: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tcode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tname: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    raw_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    chain: Mapped["AlertChain"] = relationship("AlertChain", back_populates="alerts")
