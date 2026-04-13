"""
backup.py — portable investigation backup.

GET  /api/backup/download  — streams a zip containing:
  - list.db  (SQLite database)
  - attachments/  (all uploaded files)
  - manifest.json (metadata + timestamp)

Restore: extract the zip, replace list.db and attachments/, restart.
"""
import io
import json
import os
import sqlite3
import shutil
import tempfile
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from api.auth import require_admin, require_analyst
from api.database import get_db
from api.models import (Alert, AlertChain, Attachment, Case, CaseCounter, CaseEntity,
                        CaseTemplate, Entity, Note, NotificationConfig, Observable,
                        TimelineEvent, User)
from config import ATTACHMENTS_DIR, BACKUP_DIR, DATABASE_URL, REPORTS_DIR
from seed import SYSTEM_TEMPLATES

router = APIRouter(prefix="/api/backup", tags=["backup"])


def _db_file_path() -> str:
    """Extract the filesystem path from the SQLite DATABASE_URL."""
    if DATABASE_URL.startswith("sqlite:///"):
        path = DATABASE_URL[len("sqlite:///"):]
        return os.path.abspath(path)
    return ""


def _clear_dir(path: str) -> None:
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        return
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except FileNotFoundError:
                pass


def _copy_tree(src: str, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    if not os.path.isdir(src):
        return
    for root, _, files in os.walk(src):
        rel_root = os.path.relpath(root, src)
        target_root = dest if rel_root == "." else os.path.join(dest, rel_root)
        os.makedirs(target_root, exist_ok=True)
        for fname in files:
            shutil.copy2(os.path.join(root, fname), os.path.join(target_root, fname))


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [row[1] for row in rows]


def _copy_table(source: sqlite3.Connection, dest: sqlite3.Connection, table_name: str) -> int:
    if not _table_exists(source, table_name) or not _table_exists(dest, table_name):
        return 0

    source_cols = _table_columns(source, table_name)
    dest_cols = set(_table_columns(dest, table_name))
    columns = [col for col in source_cols if col in dest_cols]
    if not columns:
        return 0

    col_sql = ", ".join(f'"{col}"' for col in columns)
    rows = source.execute(f'SELECT {col_sql} FROM "{table_name}"').fetchall()
    if not rows:
        return 0

    placeholders = ", ".join(["?"] * len(columns))
    dest.executemany(
        f'INSERT INTO "{table_name}" ({col_sql}) VALUES ({placeholders})',
        rows,
    )
    return len(rows)


@router.get("/download")
def download_backup(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_name = f"list_backup_{ts}.zip"

    # Count cases for manifest
    case_count = db.query(Case).count()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:

        # 1. Manifest
        manifest = {
            "created_at": datetime.utcnow().isoformat(),
            "version": "1.0",
            "case_count": case_count,
            "restore_instructions": (
                "1. Stop LIST (Ctrl+C in start.py).\n"
                "2. Extract this zip.\n"
                "3. Replace list.db with the extracted list.db.\n"
                "4. Replace the attachments/ folder with the extracted attachments/.\n"
                "5. Restart: python start.py"
            ),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        # 2. SQLite database
        db_path = _db_file_path()
        if db_path and os.path.exists(db_path):
            zf.write(db_path, arcname="list.db")

        # 3. Attachments directory
        if os.path.isdir(ATTACHMENTS_DIR):
            for root, dirs, files in os.walk(ATTACHMENTS_DIR):
                for fname in files:
                    full = os.path.join(root, fname)
                    arcname = os.path.join(
                        "attachments",
                        os.path.relpath(full, ATTACHMENTS_DIR),
                    )
                    zf.write(full, arcname=arcname)

    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@router.post("/reset")
def reset_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        for model in (Attachment, Note, TimelineEvent, Observable, CaseEntity, Alert, AlertChain, Entity, Case):
            db.query(model).delete()

        db.query(CaseTemplate).delete()

        counter = db.query(CaseCounter).filter(CaseCounter.id == 1).first()
        if counter is None:
            db.add(CaseCounter(id=1, current=0))
        else:
            counter.current = 0

        for tmpl in SYSTEM_TEMPLATES:
            db.add(CaseTemplate(is_system=True, **tmpl))

        db.commit()

        _clear_dir(ATTACHMENTS_DIR)
        _clear_dir(REPORTS_DIR)
        _clear_dir(BACKUP_DIR)

        return {
            "detail": "LIST data reset complete",
            "preserved_admin": current_user.username,
            "templates_loaded": len(SYSTEM_TEMPLATES),
        }
    except Exception:
        db.rollback()
        raise


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    _: User = Depends(require_analyst),
    db: Session = Depends(get_db),
):
    db.close()

    db_path = _db_file_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="Backup restore currently supports SQLite only")

    try:
        payload = await file.read()
        with zipfile.ZipFile(io.BytesIO(payload), "r") as zf, tempfile.TemporaryDirectory() as tmpdir:
            names = set(zf.namelist())
            if "list.db" not in names:
                raise HTTPException(status_code=400, detail="Backup zip is missing list.db")

            restored_attachments = os.path.join(tmpdir, "attachments")
            os.makedirs(restored_attachments, exist_ok=True)
            for member in names:
                if member.startswith("attachments/") and not member.endswith("/"):
                    zf.extract(member, tmpdir)

            source_db_path = os.path.join(tmpdir, "list.db")
            with open(source_db_path, "wb") as fh:
                fh.write(zf.read("list.db"))

            source = sqlite3.connect(source_db_path)
            dest = sqlite3.connect(db_path)
            try:
                source.execute("PRAGMA foreign_keys=OFF")
                dest.execute("PRAGMA foreign_keys=OFF")

                for table_name in (
                    "attachments",
                    "notes",
                    "timeline_events",
                    "observables",
                    "case_entities",
                    "alerts",
                    "alert_chains",
                    "entities",
                    "cases",
                    "case_templates",
                    "notification_configs",
                    "case_counter",
                ):
                    if _table_exists(dest, table_name):
                        dest.execute(f'DELETE FROM "{table_name}"')

                imported = {}
                for table_name in (
                    "case_counter",
                    "case_templates",
                    "cases",
                    "entities",
                    "case_entities",
                    "observables",
                    "notes",
                    "timeline_events",
                    "attachments",
                    "alert_chains",
                    "alerts",
                    "notification_configs",
                ):
                    imported[table_name] = _copy_table(source, dest, table_name)

                dest.commit()
            except Exception as exc:
                dest.rollback()
                raise HTTPException(status_code=400, detail=f"Backup import failed: {exc}") from exc
            finally:
                source.close()
                dest.close()

            _clear_dir(ATTACHMENTS_DIR)
            _clear_dir(REPORTS_DIR)
            _clear_dir(BACKUP_DIR)
            _copy_tree(restored_attachments, ATTACHMENTS_DIR)

        return {
            "detail": "Backup import complete",
            "imported": imported,
            "users_preserved": True,
        }
    except HTTPException:
        raise
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid backup zip file") from exc
