from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from api.auth import require_admin, require_analyst, require_auth
from api.database import get_db
from api.models import CaseTemplate, CaseSeverity, User
from api.schemas import CaseTemplateCreate, CaseTemplateOut

router = APIRouter(prefix="/api/templates", tags=["templates"])


class CaseTemplatePatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_severity: Optional[CaseSeverity] = None
    checklist_json: Optional[str] = None
    initial_tcodes: Optional[str] = None
    entity_type_hints: Optional[str] = None

@router.get("/", response_model=List[CaseTemplateOut])
def list_templates(db: Session = Depends(get_db), _: User = Depends(require_auth)):
    return db.query(CaseTemplate).order_by(CaseTemplate.name).all()

@router.get("/{template_id}", response_model=CaseTemplateOut)
def get_template(template_id: int, db: Session = Depends(get_db), _: User = Depends(require_auth)):
    t = db.query(CaseTemplate).filter(CaseTemplate.id == template_id).first()
    if not t: raise HTTPException(status_code=404, detail="Template not found")
    return t

@router.post("/", response_model=CaseTemplateOut, status_code=201)
def create_template(body: CaseTemplateCreate, db: Session = Depends(get_db),
                    _: User = Depends(require_analyst)):
    if db.query(CaseTemplate).filter(CaseTemplate.name == body.name).first():
        raise HTTPException(status_code=409, detail="Template name already exists")
    t = CaseTemplate(**body.model_dump()); db.add(t); db.commit(); db.refresh(t)
    return t

@router.patch("/{template_id}", response_model=CaseTemplateOut)
def update_template(template_id: int, body: CaseTemplatePatch,
                    db: Session = Depends(get_db), _: User = Depends(require_analyst)):
    t = db.query(CaseTemplate).filter(CaseTemplate.id == template_id).first()
    if not t: raise HTTPException(status_code=404, detail="Template not found")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(t, field, val)
    db.commit(); db.refresh(t)
    return t

@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db),
                    _: User = Depends(require_admin)):
    t = db.query(CaseTemplate).filter(CaseTemplate.id == template_id).first()
    if not t: raise HTTPException(status_code=404, detail="Template not found")
    if t.is_system: raise HTTPException(status_code=403, detail="Cannot delete system templates")
    db.delete(t); db.commit()