import json
import logging
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from api import bias_engine, notifier
from api.auth import require_analyst, require_auth
from api.database import SessionLocal, get_db
from api.models import AlertChain, Case, CaseCounter, ChainStatus, Observable, User
from api.routes.cases import _next_case_number, _run_bias_for_case, _timeline
from api.schemas import AlertChainOut, CaseOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/staging", tags=["staging"])


@router.get("/", response_model=List[AlertChainOut])
def list_chains(db: Session = Depends(get_db), _: User = Depends(require_auth)):
    return (
        db.query(AlertChain)
        .filter(AlertChain.status == ChainStatus.pending)
        .order_by(AlertChain.last_seen.desc())
        .all()
    )


@router.get("/{chain_id}", response_model=AlertChainOut)
def get_chain(chain_id: int, db: Session = Depends(get_db), _: User = Depends(require_auth)):
    chain = db.query(AlertChain).filter(AlertChain.id == chain_id).first()
    if not chain:
        raise HTTPException(status_code=404, detail="Alert chain not found")
    return chain


@router.post("/{chain_id}/promote", response_model=CaseOut, status_code=201)
def promote_chain(
    chain_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    chain = db.query(AlertChain).filter(AlertChain.id == chain_id).first()
    if not chain:
        raise HTTPException(status_code=404, detail="Alert chain not found")
    if chain.status != ChainStatus.pending:
        raise HTTPException(status_code=409, detail=f"Chain is already {chain.status.value}")

    host_label = chain.hostname or chain.src_ip or f"chain-{chain_id}"
    case_number = _next_case_number(db)
    case = Case(
        case_number=case_number,
        title=f"Alert cluster: {host_label}",
        description=f"Promoted from staging chain {chain.chain_key}",
        severity=chain.severity or "medium",
        owner_id=current_user.id,
    )
    db.add(case)
    db.flush()

    if chain.tcode_sequence:
        seen = set()
        for tcode in bias_engine.normalize_tcodes(chain.tcode_sequence):
            if tcode and tcode not in seen:
                tname = bias_engine.get_tname(tcode)
                db.add(Observable(case_id=case.id, tcode=tcode, tname=tname, confirmed=True))
                seen.add(tcode)

    chain.status = ChainStatus.promoted
    chain.promoted_case_id = case.id
    _timeline(db, case.id, "promoted",
              f"from staging chain {chain_id} by {current_user.username}")
    db.commit()
    db.refresh(case)

    notifier.notify_case_promoted(chain.chain_key, case.case_number)
    background_tasks.add_task(_run_bias_for_case, case.id, SessionLocal)
    return case


@router.delete("/{chain_id}", status_code=204)
def dismiss_chain(
    chain_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_analyst),
):
    chain = db.query(AlertChain).filter(AlertChain.id == chain_id).first()
    if not chain:
        raise HTTPException(status_code=404, detail="Alert chain not found")
    chain.status = ChainStatus.dismissed
    db.commit()
