import hashlib
import json
import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from api import bias_engine
from api.auth import require_auth
from api.database import SessionLocal, get_db
from api.models import Alert, AlertChain, ChainStatus, User
from api.schemas import AlertIn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingest", tags=["ingest"])


def _make_chain_key(alert: AlertIn) -> str:
    if alert.chain_key:
        return alert.chain_key
    raw = f"{alert.hostname or ''}|{alert.src_ip or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _bias_preanalyse(chain_id: int, session_factory):
    db = session_factory()
    try:
        chain = db.query(AlertChain).filter(AlertChain.id == chain_id).first()
        if not chain or not chain.tcode_sequence:
            return
        tcodes = bias_engine.normalize_tcodes(chain.tcode_sequence)
        if len(tcodes) < 2:
            return
        result = bias_engine.run_bias(tcodes=tcodes)
        if result:
            chain.preanalysis_json = json.dumps(result)
            db.commit()
    except Exception as exc:
        log.error("Pre-analysis failed for chain %s: %s", chain_id, exc)
        db.rollback()
    finally:
        db.close()


def _ingest_one(alert: AlertIn, db: Session, background_tasks: BackgroundTasks):
    key = _make_chain_key(alert)

    chain = db.query(AlertChain).filter(AlertChain.chain_key == key).first()
    if chain is None:
        chain = AlertChain(
            chain_key=key,
            hostname=alert.hostname,
            src_ip=alert.src_ip,
            severity=alert.severity or "medium",
            status=ChainStatus.pending,
            alert_count=0,
            tcode_sequence="",
        )
        db.add(chain)
        db.flush()

    # Accumulate unique T-codes
    existing_tcodes = set(bias_engine.normalize_tcodes(chain.tcode_sequence))
    incoming_tcodes = bias_engine.normalize_tcodes(alert.tcode)
    if incoming_tcodes:
        existing_tcodes.update(incoming_tcodes)
        chain.tcode_sequence = "|".join(sorted(existing_tcodes))

    chain.alert_count += 1
    chain.last_seen = datetime.utcnow()
    if alert.hostname and not chain.hostname:
        chain.hostname = alert.hostname
    if alert.src_ip and not chain.src_ip:
        chain.src_ip = alert.src_ip

    # Severity escalation
    sev_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    incoming = sev_rank.get(alert.severity or "medium", 2)
    current = sev_rank.get(chain.severity or "medium", 2)
    if incoming > current:
        chain.severity = alert.severity

    raw_json = json.dumps(alert.raw_data) if alert.raw_data else None
    db.add(Alert(
        chain_id=chain.id,
        source_type=alert.source_type,
        so_event_id=alert.so_event_id,
        src_ip=alert.src_ip,
        dst_ip=alert.dst_ip,
        hostname=alert.hostname,
        severity=alert.severity,
        tcode=alert.tcode,
        tname=alert.tname,
        raw_data=raw_json,
        occurred_at=alert.occurred_at,
    ))

    db.commit()

    # Kick off pre-analysis when chain has ≥2 distinct T-codes
    tcodes = bias_engine.normalize_tcodes(chain.tcode_sequence)
    if len(tcodes) >= 2 and bias_engine.is_ready():
        background_tasks.add_task(_bias_preanalyse, chain.id, SessionLocal)

    return chain


@router.post("/alert", status_code=202)
def ingest_alert(
    alert: AlertIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    chain = _ingest_one(alert, db, background_tasks)
    return {"chain_id": chain.id, "chain_key": chain.chain_key}


@router.post("/bulk", status_code=202)
def ingest_bulk(
    alerts: List[AlertIn],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(require_auth),
):
    results = []
    for alert in alerts:
        chain = _ingest_one(alert, db, background_tasks)
        results.append({"chain_id": chain.id, "chain_key": chain.chain_key})
    return {"ingested": len(results), "chains": results}
