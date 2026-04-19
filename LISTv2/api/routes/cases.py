import json, logging, re
from datetime import datetime
from typing import List, Optional, Tuple
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func
from api import bias_engine, notifier
from api.auth import require_analyst, require_auth
from api.database import SessionLocal, get_db
from api.models import Case, CaseCounter, CaseEntity, CaseTemplate, Entity, EntityRole, EntityType, Note, Observable, TimelineEvent, User, UserRole
from api.schemas import (AnalysisCard, HypothesisCard, BiasRunRequest, CaseCreate, CaseOut, CasePatch, NoteCreate, NoteOut, NotePatch,
                         ObservablesAdd, TaskCreate, TaskPatch)
from config import CASE_LOCK_TIMEOUT_SECONDS

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cases", tags=["cases"])
NOTE_ENTITY_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def _case_query(db: Session):
    return db.query(Case).options(
        selectinload(Case.owner),
        selectinload(Case.locked_by),
        selectinload(Case.observables),
        selectinload(Case.attachments),
        selectinload(Case.notes).selectinload(Note.author),
        selectinload(Case.timeline),
        selectinload(Case.case_entities).selectinload(CaseEntity.entity),
    )


def _lock_is_active(case: Case) -> bool:
    return bool(case.locked_by_id and case.locked_at and (datetime.utcnow() - case.locked_at).total_seconds() < CASE_LOCK_TIMEOUT_SECONDS)


def _lock_message(case: Case) -> str:
    username = (case.locked_by.username if case.locked_by else "another user")
    return f"Case open - {username}"


def require_case_lock(db: Session, case_id: int, current_user: User) -> Case:
    case = _case_query(db).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if _lock_is_active(case) and case.locked_by_id != current_user.id:
        raise HTTPException(status_code=409, detail=_lock_message(case))

    case.locked_by_id = current_user.id
    case.locked_at = datetime.utcnow()
    db.flush()
    return case

def _next_case_number(db: Session) -> str:
    counter = db.query(CaseCounter).filter(CaseCounter.id == 1).with_for_update().first()
    if counter is None:
        counter = CaseCounter(id=1, current=0); db.add(counter)
    counter.current += 1; db.flush()
    return f"CASE-{counter.current:04d}"

def _timeline(db: Session, case_id: int, event_type: str, detail: str = ""):
    db.add(TimelineEvent(case_id=case_id, event_type=event_type, detail=detail))


def _case_tasks(case: Case) -> list[dict]:
    if not case.checklist_json:
        return []
    try:
        raw = json.loads(case.checklist_json)
    except Exception:
        return []
    tasks = []
    changed = False
    for idx, item in enumerate(raw or []):
        if isinstance(item, str):
            tasks.append({
                "id": f"legacy-{idx}",
                "text": item,
                "status": "open",
                "assignee_id": None,
                "assignee_name": None,
                "due_date": None,
                "created_at": None,
                "completed_at": None,
            })
            changed = True
            continue
        if isinstance(item, dict):
            normalized = {
                "id": str(item.get("id") or f"task-{idx}"),
                "text": str(item.get("text") or "").strip(),
                "status": str(item.get("status") or ("done" if item.get("done") else "open")).lower(),
                "parent_id": item.get("parent_id"),
                "owner_id": item.get("owner_id") or case.owner_id,
                "owner_name": item.get("owner_name") or (case.owner.username if case.owner else None),
                "assignee_id": item.get("assignee_id"),
                "assignee_name": item.get("assignee_name"),
                "due_date": item.get("due_date"),
                "created_at": item.get("created_at"),
                "completed_at": item.get("completed_at"),
                "awaiting_owner_close": bool(item.get("awaiting_owner_close")),
                "owner_notified_at": item.get("owner_notified_at"),
            }
            if normalized["text"]:
                tasks.append(normalized)
    if changed:
        case.checklist_json = json.dumps(tasks)
    return tasks


def _save_case_tasks(case: Case, tasks: list[dict]) -> None:
    case.checklist_json = json.dumps(tasks)


def _extract_note_entities(body: str) -> List[str]:
    values: List[str] = []
    seen = set()
    for match in NOTE_ENTITY_RE.findall(body or ""):
        value = match.strip()
        if value and value.lower() not in seen:
            values.append(value)
            seen.add(value.lower())
    return values


def _link_note_entities(db: Session, case_id: int, body: str, current_user: User, note_id: Optional[int] = None) -> int:
    linked = 0
    for value in _extract_note_entities(body):
        entity = db.query(Entity).filter(
            Entity.entity_type == EntityType.other,
            func.lower(Entity.value) == value.lower(),
        ).first()
        if not entity:
            entity = Entity(entity_type=EntityType.other, value=value, created_by_id=current_user.id)
            db.add(entity)
            db.flush()
        existing_link = db.query(CaseEntity).filter(
            CaseEntity.case_id == case_id,
            CaseEntity.entity_id == entity.id,
        ).first()
        if existing_link:
            continue
        db.add(CaseEntity(
            case_id=case_id,
            entity_id=entity.id,
            role=EntityRole.associated,
            notes=f"Referenced from note #{note_id}" if note_id else "Referenced from note markup",
            added_by_id=current_user.id,
        ))
        linked += 1
    return linked


def _task_children(tasks: List[dict], parent_id: Optional[str]) -> List[dict]:
    return [task for task in tasks if str(task.get("parent_id") or "") == str(parent_id or "")]


def _task_descendants(tasks: List[dict], parent_id: str) -> List[dict]:
    descendants: List[dict] = []
    for child in _task_children(tasks, parent_id):
        descendants.append(child)
        descendants.extend(_task_descendants(tasks, str(child.get("id"))))
    return descendants


def _can_create_subtask(task: dict, current_user: User) -> bool:
    return current_user.id in {task.get("owner_id"), task.get("assignee_id")}


def _analysis_note_body(fields: dict) -> str:
    rows = ["[Analysis Card]"]
    for key in (
        "who",
        "what",
        "when",
        "where",
        "why",
        "hypothesis",
        "counter_hypothesis",
        "supporting_evidence",
        "disconfirming_evidence",
    ):
        value = (fields.get(key) or "").strip()
        if value:
            rows.append(f"{key.replace('_', ' ').title()}: {value}")
    return "\n".join(rows)


def _analysis_fields_from_case(case: Case) -> dict:
    default = {
        "who": None,
        "what": None,
        "when": None,
        "where": None,
        "why": None,
        "hypothesis": None,
        "counter_hypothesis": None,
        "supporting_evidence": None,
        "disconfirming_evidence": None,
    }
    raw = case.analysis_json
    if not raw:
        return default
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return default
    if not isinstance(parsed, dict):
        return default
    merged = dict(default)
    for key in merged:
        value = parsed.get(key)
        merged[key] = (value.strip() if isinstance(value, str) else value) or None
    return merged


def _resolve_task_assignee(db: Session, assignee_id: Optional[int]) -> Tuple[Optional[int], Optional[str]]:
    if assignee_id in (None, 0, "0"):
        return None, None
    user = db.query(User).filter(User.id == assignee_id, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="Task assignee not found")
    if user.role not in (UserRole.analyst, UserRole.lead, UserRole.admin):
        raise HTTPException(status_code=400, detail="Tasks may only be assigned to analysts, leads, or admins")
    return user.id, user.username

def _run_bias_for_case(case_id: int, session_factory, ordered_tcodes: Optional[List[str]] = None):
    db = session_factory()
    try:
        case = db.query(Case).filter(Case.id == case_id).first()
        if not case: return
        tcodes = [o.tcode for o in case.observables if o.confirmed]
        if ordered_tcodes:
            observed = {str(t).strip().upper(): t for t in tcodes}
            requested = []
            seen = set()
            for raw in ordered_tcodes:
                key = str(raw or "").strip().upper()
                if not key or key in seen or key not in observed:
                    continue
                requested.append(observed[key])
                seen.add(key)
            for tcode in tcodes:
                key = str(tcode).strip().upper()
                if key not in seen:
                    requested.append(tcode)
            tcodes = requested
        if len(tcodes) < 2: return
        # In api/routes/cases.py
        result = bias_engine.run_bias(
            tcodes=tcodes, 
            platforms=case.bias_platforms,
            cli_env=case.bias_cli_env, 
            scripting=case.bias_scripting,
            network=case.bias_network
        )
        if result:
            case.bias_result_json = json.dumps(result)
            case.bias_ran_at = datetime.utcnow()
            gap_count = sum(1 for gap in result.get("gaps", []) if gap.get("is_gap", False))
            
            # --- ADD THIS LINE TO PEEK AT THE DATA ---
            log.info("BIAS SUCCESS: Found %s gaps. Snippet: %s", gap_count, case.bias_result_json[:500])
            # -----------------------------------------
            
            _timeline(db, case_id, "bias_complete", f"{len(tcodes)} observables, {gap_count} gaps")
            db.commit(); notifier.notify_bias_complete(case.case_number, gap_count)
    except Exception as exc: 
        log.error("BIAS failed for case %s: %s", case_id, exc)
        db.rollback()
    finally: 
        db.close()

@router.get("/", response_model=List[CaseOut])
def list_cases(db: Session = Depends(get_db), _: User = Depends(require_auth)):
    return _case_query(db).order_by(Case.id.desc()).all()

@router.post("/", response_model=CaseOut, status_code=201)
def create_case(body: CaseCreate, db: Session = Depends(get_db),
                current_user: User = Depends(require_analyst)):
    case_number = _next_case_number(db)
    template = None; checklist_json = None; initial_tcodes = []
    if body.template_id:
        template = db.query(CaseTemplate).filter(CaseTemplate.id == body.template_id).first()
        if template:
            if template.checklist_json:
                try:
                    raw = json.loads(template.checklist_json)
                    checklist_json = json.dumps([
                        {"text": item, "done": False} if isinstance(item, str) else item
                        for item in raw])
                except Exception: checklist_json = template.checklist_json
            if template.initial_tcodes:
                initial_tcodes = [t.strip() for t in template.initial_tcodes.split("|") if t.strip()]
    case = Case(case_number=case_number, title=body.title, description=body.description,
                severity=body.severity if not template else template.default_severity,
                owner_id=current_user.id, template_id=body.template_id, checklist_json=checklist_json)
    db.add(case); db.flush()
    for tcode in initial_tcodes:
        tname = bias_engine.get_tname(tcode)
        db.add(Observable(case_id=case.id, tcode=tcode, tname=tname, confirmed=True))
    _timeline(db, case.id, "created",
              f"by {current_user.username}" + (f" from template '{template.name}'" if template else ""))
    db.commit(); db.refresh(case)
    notifier.notify_case_created(case.case_number, case.title, case.severity.value)
    return case

@router.get("/{case_id}", response_model=CaseOut)
def get_case(case_id: int, db: Session = Depends(get_db), _: User = Depends(require_auth)):
    case = _case_query(db).filter(Case.id == case_id).first()
    if not case: raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.post("/{case_id}/lock", response_model=CaseOut)
def acquire_case_lock(case_id: int, db: Session = Depends(get_db),
                      current_user: User = Depends(require_analyst)):
    case = _case_query(db).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if _lock_is_active(case) and case.locked_by_id != current_user.id:
        raise HTTPException(status_code=409, detail=_lock_message(case))

    is_new_owner = case.locked_by_id != current_user.id
    case.locked_by_id = current_user.id
    case.locked_at = datetime.utcnow()
    if is_new_owner:
        _timeline(db, case_id, "lock_acquired", f"by {current_user.username}")
    db.commit()
    return _case_query(db).filter(Case.id == case_id).first()


@router.delete("/{case_id}/lock", status_code=204)
def release_case_lock(case_id: int, db: Session = Depends(get_db),
                      current_user: User = Depends(require_analyst)):
    case = _case_query(db).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.locked_by_id == current_user.id:
        case.locked_by_id = None
        case.locked_at = None
        _timeline(db, case_id, "lock_released", f"by {current_user.username}")
        db.commit()

@router.patch("/{case_id}", response_model=CaseOut)
def update_case(case_id: int, body: CasePatch, db: Session = Depends(get_db),
                current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    payload = body.model_dump(exclude_none=True)
    if "owner_id" in payload:
        if current_user.role not in (UserRole.lead, UserRole.admin):
            raise HTTPException(status_code=403, detail="Only lead or admin can assign cases")
        owner_id = payload.pop("owner_id")
        if owner_id is None:
            case.owner_id = None
        else:
            owner = db.query(User).filter(User.id == owner_id, User.is_active == True).first()
            if not owner:
                raise HTTPException(status_code=404, detail="Assigned user not found")
            if owner.role not in (UserRole.analyst, UserRole.lead):
                raise HTTPException(status_code=400, detail="Cases may only be assigned to analysts or leads")
            case.owner_id = owner.id
    for field, val in payload.items(): setattr(case, field, val)
    _timeline(db, case_id, "updated", f"by {current_user.username}")
    db.commit(); return _case_query(db).filter(Case.id == case_id).first()


@router.post("/{case_id}/analysis", response_model=CaseOut)
def save_analysis(case_id: int, body: AnalysisCard, db: Session = Depends(get_db),
                  current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    fields = _analysis_fields_from_case(case)
    fields.update({
        "who": (body.who or "").strip() or None,
        "what": (body.what or "").strip() or None,
        "when": (body.when or "").strip() or None,
        "where": (body.where or "").strip() or None,
        "why": (body.why or "").strip() or None,
    })
    case.analysis_json = json.dumps(fields)
    note_body = _analysis_note_body(fields)
    note = None
    if case.analysis_note_id:
        note = db.query(Note).filter(Note.id == case.analysis_note_id, Note.case_id == case_id).first()
    if note:
        note.body = note_body
        note.edited_at = datetime.utcnow()
    else:
        note = Note(case_id=case_id, author_id=current_user.id, body=note_body)
        db.add(note)
        db.flush()
        case.analysis_note_id = note.id
    linked = _link_note_entities(db, case_id, note_body, current_user, note.id)
    _timeline(
        db,
        case_id,
        "analysis_updated",
        f"by {current_user.username}" + (f"; linked {linked} entities" if linked else ""),
    )
    db.commit()
    return _case_query(db).filter(Case.id == case_id).first()


@router.post("/{case_id}/hypotheses", response_model=CaseOut)
def save_hypotheses(case_id: int, body: HypothesisCard, db: Session = Depends(get_db),
                    current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    fields = _analysis_fields_from_case(case)
    fields.update({
        "hypothesis": (body.hypothesis or "").strip() or None,
        "counter_hypothesis": (body.counter_hypothesis or "").strip() or None,
        "supporting_evidence": (body.supporting_evidence or "").strip() or None,
        "disconfirming_evidence": (body.disconfirming_evidence or "").strip() or None,
    })
    case.analysis_json = json.dumps(fields)
    note_body = _analysis_note_body(fields)
    note = None
    if case.analysis_note_id:
        note = db.query(Note).filter(Note.id == case.analysis_note_id, Note.case_id == case_id).first()
    if note:
        note.body = note_body
        note.edited_at = datetime.utcnow()
    else:
        note = Note(case_id=case_id, author_id=current_user.id, body=note_body)
        db.add(note)
        db.flush()
        case.analysis_note_id = note.id
    linked = _link_note_entities(db, case_id, note_body, current_user, note.id)
    _timeline(
        db,
        case_id,
        "hypotheses_updated",
        f"by {current_user.username}" + (f"; linked {linked} entities" if linked else ""),
    )
    db.commit()
    return _case_query(db).filter(Case.id == case_id).first()

@router.post("/{case_id}/observables", response_model=CaseOut)
def add_observables(case_id: int, body: ObservablesAdd, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db), current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    existing = {o.tcode for o in case.observables}; added = []
    for tcode in bias_engine.normalize_tcodes(body.tcodes):
        if tcode and tcode not in existing:
            tname = bias_engine.get_tname(tcode)
            db.add(Observable(case_id=case_id, tcode=tcode, tname=tname, confirmed=True))
            existing.add(tcode); added.append(tcode)
    if added:
        _timeline(db, case_id, "observables_added", ", ".join(added))
        db.commit(); db.refresh(case)
        background_tasks.add_task(_run_bias_for_case, case_id, SessionLocal)
    return case


@router.delete("/{case_id}/observables/{observable_id}", response_model=CaseOut)
def delete_observable(case_id: int, observable_id: int, db: Session = Depends(get_db),
                      current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    obs = db.query(Observable).filter(Observable.id == observable_id, Observable.case_id == case_id).first()
    if not obs:
        raise HTTPException(status_code=404, detail="Observable not found")

    removed_tcode = obs.tcode
    db.delete(obs)
    case.bias_result_json = None
    case.bias_ran_at = None
    _timeline(db, case_id, "observable_deleted", f"{removed_tcode} by {current_user.username}")
    _timeline(db, case_id, "bias_reset", "Observable set changed; rerun BIAS")
    db.commit()
    db.refresh(case)
    return case

@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    db.delete(case); db.commit()

@router.post("/{case_id}/bias-run", status_code=202)
def trigger_bias(case_id: int, background_tasks: BackgroundTasks,
                 body: Optional[BiasRunRequest] = None,
                 db: Session = Depends(get_db), current_user: User = Depends(require_analyst)):
    require_case_lock(db, case_id, current_user)
    background_tasks.add_task(_run_bias_for_case, case_id, SessionLocal, (body.ordered_tcodes if body else None))
    db.commit()
    return {"detail": "BIAS analysis queued"}

@router.get("/{case_id}/notes", response_model=List[NoteOut])
def list_notes(case_id: int, db: Session = Depends(get_db), _: User = Depends(require_auth)):
    return db.query(Note).filter(Note.case_id == case_id).order_by(Note.id).all()

@router.post("/{case_id}/notes", response_model=NoteOut, status_code=201)
def add_note(case_id: int, body: NoteCreate, db: Session = Depends(get_db),
             current_user: User = Depends(require_analyst)):
    require_case_lock(db, case_id, current_user)
    note = Note(case_id=case_id, author_id=current_user.id, body=body.body)
    db.add(note)
    db.flush()
    linked = _link_note_entities(db, case_id, body.body, current_user, note.id)
    _timeline(
        db,
        case_id,
        "note_added",
        f"by {current_user.username}" + (f"; linked {linked} entities" if linked else ""),
    )
    db.commit()
    db.refresh(note)
    return note


@router.patch("/{case_id}/notes/{note_id}", response_model=NoteOut)
def update_note(case_id: int, note_id: int, body: NotePatch, db: Session = Depends(get_db),
                current_user: User = Depends(require_analyst)):
    require_case_lock(db, case_id, current_user)
    note = db.query(Note).filter(Note.id == note_id, Note.case_id == case_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    body_text = (body.body or "").strip()
    if not body_text:
        raise HTTPException(status_code=400, detail="Note body is required")
    note.body = body_text
    note.edited_at = datetime.utcnow()
    linked = _link_note_entities(db, case_id, body_text, current_user, note.id)
    _timeline(
        db,
        case_id,
        "note_updated",
        f"note #{note.id} by {current_user.username}" + (f"; linked {linked} entities" if linked else ""),
    )
    db.commit()
    db.refresh(note)
    return note


@router.delete("/{case_id}/notes/{note_id}", status_code=204)
def delete_note(case_id: int, note_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(require_analyst)):
    require_case_lock(db, case_id, current_user)
    note = db.query(Note).filter(Note.id == note_id, Note.case_id == case_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete(note)
    _timeline(db, case_id, "note_deleted", f"note #{note_id} by {current_user.username}")
    db.commit()


@router.post("/{case_id}/tasks", response_model=CaseOut)
def add_task(case_id: int, body: TaskCreate, db: Session = Depends(get_db),
             current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Task text is required")
    tasks = _case_tasks(case)
    parent_id = str(body.parent_id).strip() if body.parent_id else None
    if parent_id:
        parent = next((task for task in tasks if str(task.get("id")) == parent_id), None)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent task not found")
        if not _can_create_subtask(parent, current_user):
            raise HTTPException(status_code=403, detail="Only the task owner or assignee may create subtasks")
    assignee_id, assignee_name = _resolve_task_assignee(db, body.assignee_id)
    tasks.append({
        "id": f"task-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
        "text": text,
        "status": "open",
        "parent_id": parent_id,
        "owner_id": current_user.id,
        "owner_name": current_user.username,
        "assignee_id": assignee_id,
        "assignee_name": assignee_name,
        "due_date": body.due_date,
        "created_at": datetime.utcnow().isoformat(),
        "completed_at": None,
        "awaiting_owner_close": False,
        "owner_notified_at": None,
    })
    _save_case_tasks(case, tasks)
    _timeline(
        db,
        case_id,
        "task_added",
        f"{'subtask' if parent_id else 'task'} '{text}' by {current_user.username}",
    )
    db.commit()
    return _case_query(db).filter(Case.id == case_id).first()


@router.patch("/{case_id}/tasks/{task_id}", response_model=CaseOut)
def update_task(case_id: int, task_id: str, body: TaskPatch, db: Session = Depends(get_db),
                current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    tasks = _case_tasks(case)
    task = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    child_tasks = _task_children(tasks, str(task_id))

    if body.text is not None:
        if current_user.id != task.get("owner_id"):
            raise HTTPException(status_code=403, detail="Only the task owner can edit task text")
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Task text is required")
        task["text"] = text
    if body.status is not None:
        status = str(body.status).lower().strip()
        if status not in {"open", "in_progress", "done"}:
            raise HTTPException(status_code=400, detail="Task status must be open, in_progress, or done")
        if status == "done" and child_tasks:
            if current_user.id != task.get("owner_id"):
                raise HTTPException(status_code=403, detail="Only the task owner can close a task with subtasks")
            incomplete = [child for child in child_tasks if (child.get("status") or "open") != "done"]
            if incomplete:
                raise HTTPException(status_code=409, detail="All subtasks must be completed before closing the task")
        task["status"] = status
        task["completed_at"] = datetime.utcnow().isoformat() if status == "done" else None
        task["awaiting_owner_close"] = False if status == "done" else bool(task.get("awaiting_owner_close"))
        if status == "done":
            interested = [task.get("owner_name")]
            if task.get("assignee_name") and task.get("assignee_name") not in interested:
                interested.append(task.get("assignee_name"))
            interested = [name for name in interested if name]
            if interested:
                _timeline(
                    db,
                    case_id,
                    "task_completed",
                    f"{task.get('text', 'Task')} completed; notify {', '.join(interested)}",
                )
    if body.assignee_id is not None:
        if current_user.id != task.get("owner_id"):
            raise HTTPException(status_code=403, detail="Only the task owner can assign the task")
        assignee_id, assignee_name = _resolve_task_assignee(db, body.assignee_id)
        task["assignee_id"] = assignee_id
        task["assignee_name"] = assignee_name
    if body.due_date is not None:
        if current_user.id != task.get("owner_id"):
            raise HTTPException(status_code=403, detail="Only the task owner can change the due date")
        task["due_date"] = body.due_date or None

    if task.get("parent_id") and (task.get("status") == "done"):
        parent = next((item for item in tasks if str(item.get("id")) == str(task.get("parent_id"))), None)
        if parent:
            siblings = _task_children(tasks, str(parent.get("id")))
            if siblings and all((sibling.get("status") or "open") == "done" for sibling in siblings):
                parent["awaiting_owner_close"] = True
                parent["owner_notified_at"] = datetime.utcnow().isoformat()
                _timeline(db, case_id, "task_owner_notified", f"{parent.get('text', 'Task')} ready for owner closure")

    _save_case_tasks(case, tasks)
    _timeline(db, case_id, "task_updated", f"{task.get('text', 'task')} by {current_user.username}")
    db.commit()
    return _case_query(db).filter(Case.id == case_id).first()


@router.delete("/{case_id}/tasks/{task_id}", response_model=CaseOut)
def delete_task(case_id: int, task_id: str, db: Session = Depends(get_db),
                current_user: User = Depends(require_analyst)):
    case = require_case_lock(db, case_id, current_user)
    tasks = _case_tasks(case)
    task = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if current_user.id != task.get("owner_id"):
        raise HTTPException(status_code=403, detail="Only the task owner can delete the task")
    blocked_ids = {str(task_id)}
    blocked_ids.update(str(item.get("id")) for item in _task_descendants(tasks, str(task_id)))
    remaining = [t for t in tasks if str(t.get("id")) not in blocked_ids]
    _save_case_tasks(case, remaining)
    _timeline(db, case_id, "task_deleted", f"{task_id} by {current_user.username}")
    db.commit()
    return _case_query(db).filter(Case.id == case_id).first()
