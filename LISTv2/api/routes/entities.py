from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from api.auth import require_analyst, require_auth
from api.database import get_db
from api.models import Case, CaseEntity, Entity, EntityType, User
from api.routes.cases import require_case_lock
from api.schemas import CaseEntityOut, EntityCreate, EntityOut, LinkEntityRequest

router = APIRouter(prefix="/api/entities", tags=["entities"])


def _normalize_entity_value(value: Optional[str]) -> str:
    return (value or "").strip()


def _find_existing_entity(db: Session, entity_type: EntityType, value: str) -> Optional[Entity]:
    normalized = _normalize_entity_value(value)
    if not normalized:
        return None
    return db.query(Entity).filter(
        Entity.entity_type == entity_type,
        func.lower(Entity.value) == normalized.lower(),
    ).first()


def _entity_out(entity: Entity) -> dict:
    related_cases = []
    for link in getattr(entity, "case_links", []) or []:
        case = getattr(link, "case", None)
        if not case:
            continue
        related_cases.append({
            "id": case.id,
            "title": case.title,
            "case_number": case.case_number,
            "role": link.role.value,
        })
    related_cases.sort(key=lambda item: ((item.get("title") or "").lower(), item.get("case_number") or ""))
    return {
        "id": entity.id,
        "entity_type": entity.entity_type.value,
        "value": entity.value,
        "description": entity.description,
        "tags": entity.tags,
        "created_at": entity.created_at,
        "related_cases": related_cases,
    }


@router.get("/", response_model=List[EntityOut])
def list_entities(
    entity_type: Optional[EntityType] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    q = db.query(Entity).options(selectinload(Entity.case_links).selectinload(CaseEntity.case))
    if entity_type:
        q = q.filter(Entity.entity_type == entity_type)
    if search:
        q = q.filter(Entity.value.ilike(f"%{search}%"))
    return [_entity_out(entity) for entity in q.order_by(Entity.id.desc()).all()]


@router.post("/", response_model=EntityOut, status_code=201)
def create_entity(
    body: EntityCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    normalized_value = _normalize_entity_value(body.value)
    if not normalized_value:
        raise HTTPException(status_code=400, detail="Entity value is required")
    # Deduplicate on type + normalized value
    existing = _find_existing_entity(db, body.entity_type, normalized_value)
    if existing:
        db.refresh(existing)
        return _entity_out(existing)
    entity = Entity(
        entity_type=body.entity_type,
        value=normalized_value,
        description=body.description,
        tags=body.tags,
        created_by_id=current_user.id,
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return _entity_out(entity)


@router.get("/{entity_id}", response_model=EntityOut)
def get_entity(entity_id: int, db: Session = Depends(get_db), _: User = Depends(require_auth)):
    e = (
        db.query(Entity)
        .options(selectinload(Entity.case_links).selectinload(CaseEntity.case))
        .filter(Entity.id == entity_id)
        .first()
    )
    if not e:
        raise HTTPException(status_code=404, detail="Entity not found")
    return _entity_out(e)


@router.get("/{entity_id}/relationships")
def get_entity_relationships(entity_id: int, db: Session = Depends(get_db), _: User = Depends(require_auth)):
    entity = db.query(Entity).filter(Entity.id == entity_id).first()
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    links = (
        db.query(CaseEntity)
        .filter(CaseEntity.entity_id == entity_id)
        .all()
    )
    case_ids = [link.case_id for link in links]
    cases = db.query(Case).filter(Case.id.in_(case_ids)).all() if case_ids else []
    role_map = {link.case_id: link.role.value for link in links}
    note_map = {link.case_id: link.notes for link in links}
    return {
        "entity": {
            "id": entity.id,
            "entity_type": entity.entity_type.value,
            "value": entity.value,
            "description": entity.description,
        },
        "cases": [
            {
                "id": case.id,
                "case_number": case.case_number,
                "title": case.title,
                "status": case.status.value,
                "severity": case.severity.value,
                "role": role_map.get(case.id, "associated"),
                "link_notes": note_map.get(case.id),
            }
            for case in cases
        ],
    }


@router.get("/lookup/exact")
def lookup_entity_exact(
    entity_type: EntityType,
    value: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    entity = _find_existing_entity(db, entity_type, value)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {
        "id": entity.id,
        "entity_type": entity.entity_type.value,
        "value": entity.value,
        "description": entity.description,
    }


# ── Case ↔ Entity linking ─────────────────────────────────────────────────────

@router.get("/case/{case_id}", response_model=List[CaseEntityOut])
def list_case_entities(
    case_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    return db.query(CaseEntity).filter(CaseEntity.case_id == case_id).all()


@router.post("/case/{case_id}", response_model=CaseEntityOut, status_code=201)
def link_entity_to_case(
    case_id: int,
    body: LinkEntityRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    require_case_lock(db, case_id, current_user)

    # Resolve entity — use existing or create new
    if body.entity_id:
        entity = db.query(Entity).filter(Entity.id == body.entity_id).first()
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
    elif body.create:
        c = body.create
        normalized_value = _normalize_entity_value(c.value)
        if not normalized_value:
            raise HTTPException(status_code=400, detail="Entity value is required")
        entity = _find_existing_entity(db, c.entity_type, normalized_value)
        if not entity:
            entity = Entity(
                entity_type=c.entity_type,
                value=normalized_value,
                description=c.description,
                tags=c.tags,
                created_by_id=current_user.id,
            )
            db.add(entity)
            db.flush()
    else:
        raise HTTPException(status_code=400, detail="Provide entity_id or create")

    # Prevent duplicate links
    existing_link = db.query(CaseEntity).filter(
        CaseEntity.case_id == case_id,
        CaseEntity.entity_id == entity.id,
    ).first()
    if existing_link:
        # Update role/notes if already linked
        existing_link.role = body.role
        existing_link.notes = body.notes
        db.commit()
        db.refresh(existing_link)
        return existing_link

    link = CaseEntity(
        case_id=case_id,
        entity_id=entity.id,
        role=body.role,
        notes=body.notes,
        added_by_id=current_user.id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.delete("/case/{case_id}/{link_id}", status_code=204)
def unlink_entity(
    case_id: int,
    link_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    require_case_lock(db, case_id, current_user)
    link = db.query(CaseEntity).filter(
        CaseEntity.id == link_id,
        CaseEntity.case_id == case_id,
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Entity link not found")
    db.delete(link)
    db.commit()
