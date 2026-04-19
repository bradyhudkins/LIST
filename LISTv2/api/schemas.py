"""Pydantic v2 request/response schemas."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from api.models import CaseSeverity, CaseStatus, ChainStatus, EntityRole, EntityType, UserRole

class _Base(BaseModel):
    model_config = {"from_attributes": True}

class UserCreate(BaseModel):
    username: str; email: str; password: str; role: UserRole = UserRole.analyst

class UserPatch(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None   # if provided, re-hash and update

class UserOut(_Base):
    id: int; username: str; email: str; role: UserRole; is_active: bool; created_at: datetime

class Token(BaseModel):
    access_token: str; token_type: str = "bearer"

class CaseTemplateCreate(BaseModel):
    name: str; description: Optional[str] = None
    default_severity: CaseSeverity = CaseSeverity.medium
    checklist_json: Optional[str] = None; initial_tcodes: Optional[str] = None
    entity_type_hints: Optional[str] = None

class CaseTemplateOut(_Base):
    id: int; name: str; description: Optional[str]; default_severity: CaseSeverity
    checklist_json: Optional[str]; initial_tcodes: Optional[str]
    entity_type_hints: Optional[str]; is_system: bool

class ObservableOut(_Base):
    id: int; tcode: str; tname: Optional[str]; confirmed: bool; added_at: datetime

class ObservablesAdd(BaseModel):
    tcodes: List[str]

class EntityCreate(BaseModel):
    entity_type: EntityType; value: str
    description: Optional[str] = None; tags: Optional[str] = None

class EntityOut(_Base):
    id: int; entity_type: EntityType; value: str
    description: Optional[str]; tags: Optional[str]; created_at: datetime
    related_cases: List[Dict[str, Any]] = []

class CaseEntityOut(_Base):
    id: int; entity: EntityOut; role: EntityRole; notes: Optional[str]; added_at: datetime

class LinkEntityRequest(BaseModel):
    entity_id: Optional[int] = None; create: Optional[EntityCreate] = None
    role: EntityRole = EntityRole.associated; notes: Optional[str] = None

class AttachmentOut(_Base):
    id: int; original_filename: str; file_size: int
    mime_type: Optional[str]; description: Optional[str]; uploaded_at: datetime

class NoteCreate(BaseModel):
    body: str


class NotePatch(BaseModel):
    body: str


class AnalysisCard(BaseModel):
    who: Optional[str] = None
    what: Optional[str] = None
    when: Optional[str] = None
    where: Optional[str] = None
    why: Optional[str] = None


class HypothesisCard(BaseModel):
    hypothesis: Optional[str] = None
    counter_hypothesis: Optional[str] = None
    supporting_evidence: Optional[str] = None
    disconfirming_evidence: Optional[str] = None


class TaskItem(BaseModel):
    id: str
    text: str
    status: str = "open"
    parent_id: Optional[str] = None
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    assignee_id: Optional[int] = None
    assignee_name: Optional[str] = None
    due_date: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    awaiting_owner_close: bool = False
    owner_notified_at: Optional[str] = None


class TaskCreate(BaseModel):
    text: str
    assignee_id: Optional[int] = None
    due_date: Optional[str] = None
    parent_id: Optional[str] = None


class TaskPatch(BaseModel):
    text: Optional[str] = None
    status: Optional[str] = None
    assignee_id: Optional[int] = None
    due_date: Optional[str] = None

class NoteOut(_Base):
    id: int; body: str; created_at: datetime; edited_at: Optional[datetime]; author: Optional[UserOut]

class TimelineEventOut(_Base):
    id: int; event_type: str; detail: Optional[str]; occurred_at: datetime

class CaseCreate(BaseModel):
    title: str; description: Optional[str] = None
    severity: CaseSeverity = CaseSeverity.medium; template_id: Optional[int] = None

class CasePatch(BaseModel):
    title: Optional[str] = None; description: Optional[str] = None
    severity: Optional[CaseSeverity] = None; status: Optional[CaseStatus] = None
    owner_id: Optional[int] = None
    checklist_json: Optional[str] = None; analysis_json: Optional[str] = None; bias_platforms: Optional[str] = None
    bias_cli_env: Optional[str] = None; bias_scripting: Optional[str] = None
    bias_network: Optional[str] = None

class BiasRunRequest(BaseModel):
    ordered_tcodes: Optional[List[str]] = None

class CaseOut(_Base):
    id: int; case_number: str; title: str; description: Optional[str]
    severity: CaseSeverity; status: CaseStatus; checklist_json: Optional[str]; analysis_json: Optional[str]
    template_id: Optional[int]; bias_ran_at: Optional[datetime]
    template_id: Optional[int]; bias_ran_at: Optional[datetime]; bias_result_json: Optional[str]
    created_at: datetime; updated_at: datetime; owner: Optional[UserOut]
    locked_by: Optional[UserOut] = None
    locked_at: Optional[datetime] = None
    lock_active: bool = False
    lock_expires_at: Optional[datetime] = None
    observables: List[ObservableOut] = []
    attachments: List[AttachmentOut] = []
    case_entities: List[CaseEntityOut] = []

class AlertChainOut(_Base):
    id: int; chain_key: str; hostname: Optional[str]; src_ip: Optional[str]
    severity: str; status: ChainStatus; alert_count: int
    tcode_sequence: Optional[str]; preanalysis_json: Optional[str]
    first_seen: datetime; last_seen: datetime

class AlertIn(BaseModel):
    source_type: Optional[str] = "security_onion"
    so_event_id: Optional[str] = None; src_ip: Optional[str] = None
    dst_ip: Optional[str] = None; hostname: Optional[str] = None
    severity: Optional[str] = "medium"; tcode: Optional[str] = None
    tname: Optional[str] = None; chain_key: Optional[str] = None
    occurred_at: Optional[datetime] = None; raw_data: Optional[Dict[str, Any]] = None

class NotificationConfigOut(_Base):
    id: int; channel: str; enabled: bool; config_json: Optional[str]; updated_at: datetime
