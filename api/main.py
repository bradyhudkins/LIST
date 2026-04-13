import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from api import bias_engine
from api.database import Base, engine
from api.routes import auth, cases, ingest, staging
from api.routes import entities, templates, reports, attachments, backup
from config import ATTACHMENTS_DIR, BACKUP_DIR, BIAS_PATH, REPORTS_DIR

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
log = logging.getLogger(__name__)


def _ensure_case_lock_columns() -> None:
    inspector = inspect(engine)
    if "cases" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("cases")}
    with engine.begin() as conn:
        if "locked_by_id" not in columns:
            conn.execute(text("ALTER TABLE cases ADD COLUMN locked_by_id INTEGER"))
        if "locked_at" not in columns:
            conn.execute(text("ALTER TABLE cases ADD COLUMN locked_at DATETIME"))
        if "analysis_json" not in columns:
            conn.execute(text("ALTER TABLE cases ADD COLUMN analysis_json TEXT"))
        if "analysis_note_id" not in columns:
            conn.execute(text("ALTER TABLE cases ADD COLUMN analysis_note_id INTEGER"))


def _ensure_note_columns() -> None:
    inspector = inspect(engine)
    if "notes" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("notes")}
    with engine.begin() as conn:
        if "edited_at" not in columns:
            conn.execute(text("ALTER TABLE notes ADD COLUMN edited_at DATETIME"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Creating database tables …")
    Base.metadata.create_all(bind=engine)
    _ensure_case_lock_columns()
    _ensure_note_columns()

    for d in (ATTACHMENTS_DIR, REPORTS_DIR, BACKUP_DIR):
        os.makedirs(d, exist_ok=True)

    if BIAS_PATH:
        log.info("Loading BIAS from %s …", BIAS_PATH)
        bias_engine.init_bias(BIAS_PATH)
    else:
        log.info("No BIAS_PATH configured or discovered; gap analysis disabled")
    yield


app = FastAPI(
    title="LIST API",
    description="Lightweight Investigation System for Ticketing",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    auth.router,
    cases.router,
    staging.router,
    ingest.router,
    entities.router,
    templates.router,
    reports.router,
    attachments.router,
    backup.router,
):
    app.include_router(router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "bias_ready": bias_engine.is_ready()}
