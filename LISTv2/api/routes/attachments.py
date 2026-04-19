import mimetypes
import os
import uuid
from typing import List

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from api.auth import require_admin, require_analyst, require_auth
from api.database import get_db
from api.models import Attachment, Case, NotificationConfig, User
from api.routes.cases import require_case_lock
from api.schemas import AttachmentOut
from config import ATTACHMENTS_DIR, DEFAULT_MAX_ATTACHMENT_MB

router = APIRouter(prefix="/api/attachments", tags=["attachments"])

SETTINGS_CHANNEL = "attachments"


def _case_dir(case_id: int) -> str:
    d = os.path.join(ATTACHMENTS_DIR, str(case_id))
    os.makedirs(d, exist_ok=True)
    return d


def _attachment_settings_row(db: Session) -> NotificationConfig:
    row = db.query(NotificationConfig).filter(NotificationConfig.channel == SETTINGS_CHANNEL).first()
    if row is None:
        row = NotificationConfig(
            channel=SETTINGS_CHANNEL,
            enabled=True,
            config_json=json.dumps({"max_mb": DEFAULT_MAX_ATTACHMENT_MB}),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def get_attachment_settings(db: Session) -> dict:
    row = _attachment_settings_row(db)
    try:
        cfg = json.loads(row.config_json or "{}")
    except Exception:
        cfg = {}
    max_mb = int(cfg.get("max_mb") or DEFAULT_MAX_ATTACHMENT_MB)
    if max_mb < 1:
        max_mb = DEFAULT_MAX_ATTACHMENT_MB
    return {"max_mb": max_mb}


def get_max_attachment_bytes(db: Session) -> int:
    return get_attachment_settings(db)["max_mb"] * 1024 * 1024


@router.get("/settings")
def attachment_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    settings = get_attachment_settings(db)
    return {
        "max_mb": settings["max_mb"],
        "warning": (
            "Large attachment limits increase memory use, backup size, restore time, "
            "and multi-user upload pressure."
        ),
    }


@router.patch("/settings")
def update_attachment_settings(
    body: dict,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    max_mb = int(body.get("max_mb") or 0)
    if max_mb < 1:
        raise HTTPException(status_code=400, detail="Attachment limit must be at least 1 MB")
    row = _attachment_settings_row(db)
    row.enabled = True
    row.config_json = json.dumps({"max_mb": max_mb})
    db.commit()
    return {
        "max_mb": max_mb,
        "warning": (
            "Large attachment limits increase memory use, backup size, restore time, "
            "and multi-user upload pressure."
        ),
    }


@router.get("/case/{case_id}", response_model=List[AttachmentOut])
def list_attachments(
    case_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    return db.query(Attachment).filter(Attachment.case_id == case_id).order_by(Attachment.id).all()


@router.post("/case/{case_id}", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    case_id: int,
    file: UploadFile = File(...),
    description: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    require_case_lock(db, case_id, current_user)

    content = await file.read()
    max_mb = get_attachment_settings(db)["max_mb"]
    if len(content) > get_max_attachment_bytes(db):
        raise HTTPException(status_code=413, detail=f"File too large (max {max_mb} MB)")

    ext = os.path.splitext(file.filename or "")[1]
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(_case_dir(case_id), stored_name)

    with open(dest, "wb") as f:
        f.write(content)

    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"

    att = Attachment(
        case_id=case_id,
        original_filename=file.filename or stored_name,
        stored_filename=stored_name,
        file_path=dest,
        file_size=len(content),
        mime_type=mime,
        description=description or None,
        uploaded_by_id=current_user.id,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return att


@router.get("/{attachment_id}/download")
def download_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    att = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if not os.path.exists(att.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(
        att.file_path,
        media_type=att.mime_type or "application/octet-stream",
        filename=att.original_filename,
    )


@router.delete("/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    att = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    require_case_lock(db, att.case_id, current_user)
    try:
        if os.path.exists(att.file_path):
            os.remove(att.file_path)
    except OSError:
        pass
    db.delete(att)
    db.commit()
