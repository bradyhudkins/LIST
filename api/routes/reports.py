import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from api.auth import require_auth
from api.database import get_db
from api.models import Case, User
from api import report_builder
from config import REPORTS_DIR

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _report_dir(case_id: int) -> str:
    d = os.path.join(REPORTS_DIR, str(case_id))
    os.makedirs(d, exist_ok=True)
    return d


def _load_case(case_id: int, db: Session) -> Case:
    case = (
        db.query(Case)
        .filter(Case.id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/{case_id}/pdf")
def generate_pdf(
    case_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    case = _load_case(case_id, db)
    path = os.path.join(_report_dir(case_id), f"{case.case_number}.pdf")
    try:
        report_builder.build_pdf(case, path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"{case.case_number}_report.pdf",
    )


@router.get("/{case_id}/docx")
def generate_docx(
    case_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    case = _load_case(case_id, db)
    path = os.path.join(_report_dir(case_id), f"{case.case_number}.docx")
    try:
        report_builder.build_docx(case, path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DOCX generation failed: {exc}")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{case.case_number}_report.docx",
    )
