#!/usr/bin/env python3
"""
seed.py — initialise the LIST database.
Creates tables, admin user, and built-in case templates.
Safe to re-run: existing records are not overwritten.

Usage:
    python seed.py
    ADMIN_PASSWORD=mysecret python seed.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from api.models import Base, CaseCounter, CaseSeverity, CaseTemplate, User, UserRole
from api.auth import hash_password

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL",    "admin@list.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

SYSTEM_TEMPLATES = [
    {
        "name": "Phishing Response",
        "description": "Triage and containment for a suspected phishing incident.",
        "default_severity": CaseSeverity.high,
        "initial_tcodes": "T1566|T1204.002|T1059.001|T1071.001",
        "entity_type_hints": "email|user_account|domain|ip_address",
        "checklist_json": json.dumps([
            "Identify and quarantine phishing email",
            "Identify all recipients",
            "Check for link/attachment detonation",
            "Identify any users who clicked",
            "Reset credentials for affected users",
            "Block sender domain / URL in email gateway",
            "Check proxy/DNS logs for C2 callbacks",
            "Notify affected users",
            "Document IOCs",
            "Close or escalate",
        ]),
    },
    {
        "name": "Ransomware Investigation",
        "description": "Contain, investigate, and recover from ransomware deployment.",
        "default_severity": CaseSeverity.critical,
        "initial_tcodes": "T1486|T1490|T1083|T1059|T1021.002",
        "entity_type_hints": "hostname|ip_address|file_hash|user_account",
        "checklist_json": json.dumps([
            "Isolate affected host(s) from network",
            "Preserve memory image and disk image",
            "Identify ransomware family",
            "Identify initial access vector",
            "Identify patient zero host",
            "Map lateral movement path",
            "Check backup integrity",
            "Notify legal / management",
            "File law enforcement report if required",
            "Begin recovery from clean backups",
            "Patch and harden before reconnecting",
            "Document full attack chain",
        ]),
    },
    {
        "name": "Lateral Movement",
        "description": "Track and contain adversary movement across the network.",
        "default_severity": CaseSeverity.high,
        "initial_tcodes": "T1021.001|T1021.002|T1550.002|T1078|T1040",
        "entity_type_hints": "hostname|ip_address|user_account",
        "checklist_json": json.dumps([
            "Identify source host and account",
            "Map all hosts accessed",
            "Collect authentication logs (Event 4624/4625)",
            "Identify tools used (PsExec, WMI, etc.)",
            "Check for credential dumping",
            "Identify data staged or exfiltrated",
            "Disable compromised accounts",
            "Isolate affected hosts",
            "Reset credentials across blast radius",
            "Document movement timeline",
        ]),
    },
    {
        "name": "Credential Compromise",
        "description": "Respond to stolen or abused credentials.",
        "default_severity": CaseSeverity.high,
        "initial_tcodes": "T1078|T1110|T1003|T1558|T1539",
        "entity_type_hints": "user_account|ip_address|hostname",
        "checklist_json": json.dumps([
            "Identify affected accounts",
            "Check for impossible travel / anomalous logins",
            "Disable / reset compromised accounts immediately",
            "Revoke active sessions and tokens",
            "Check for MFA bypass or enrollment changes",
            "Review email forwarding rules",
            "Check for new admin accounts created",
            "Audit privileged access changes",
            "Notify affected users",
            "Implement additional monitoring on accounts",
        ]),
    },
    {
        "name": "Malware Triage",
        "description": "Initial triage of a suspected malware infection.",
        "default_severity": CaseSeverity.medium,
        "initial_tcodes": "T1059|T1055|T1071|T1547|T1036",
        "entity_type_hints": "hostname|file_hash|ip_address|domain",
        "checklist_json": json.dumps([
            "Isolate host if warranted",
            "Collect running process list",
            "Collect network connections",
            "Hash suspicious files",
            "Submit hashes to VirusTotal / sandbox",
            "Identify persistence mechanisms",
            "Identify C2 channels",
            "Check for lateral movement from host",
            "Remediate or reimage",
            "Document IOCs and TTPs",
        ]),
    },
    {
        "name": "Data Exfiltration",
        "description": "Investigate suspected data theft or exfiltration.",
        "default_severity": CaseSeverity.critical,
        "initial_tcodes": "T1041|T1048|T1567|T1052|T1030",
        "entity_type_hints": "hostname|ip_address|domain|user_account",
        "checklist_json": json.dumps([
            "Identify exfiltration channel (HTTP/S, DNS, email, cloud)",
            "Estimate volume of data transferred",
            "Identify data classification of exfiltrated data",
            "Block exfiltration destination",
            "Preserve network logs",
            "Identify source account / process",
            "Check for staging behaviour on host",
            "Notify legal / DPO if PII involved",
            "Regulatory notification assessment",
            "Document full scope",
        ]),
    },
]


def run():
    connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    engine = create_engine(DATABASE_URL, connect_args=connect_args)

    print("Creating database tables …")
    Base.metadata.create_all(bind=engine)
    print("  ✓ Tables OK")

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        counter = db.query(CaseCounter).filter(CaseCounter.id == 1).first()
        if counter is None:
            db.add(CaseCounter(id=1, current=0))
            db.commit()
            print("  ✓ Case counter initialised")
        else:
            print(f"  – Case counter already exists (current={counter.current})")

        existing = db.query(User).filter(User.username == ADMIN_USERNAME).first()
        if existing:
            print(f"  – Admin '{ADMIN_USERNAME}' already exists — skipping")
        else:
            db.add(User(
                username=ADMIN_USERNAME,
                email=ADMIN_EMAIL,
                hashed_password=hash_password(ADMIN_PASSWORD),
                role=UserRole.admin,
                is_active=True,
            ))
            db.commit()
            print(f"  ✓ Admin created  (username: {ADMIN_USERNAME}  password: {ADMIN_PASSWORD})")

        print("Loading case templates …")
        for tmpl in SYSTEM_TEMPLATES:
            if db.query(CaseTemplate).filter(CaseTemplate.name == tmpl["name"]).first():
                print(f"  – Template '{tmpl['name']}' already exists")
            else:
                db.add(CaseTemplate(is_system=True, **tmpl))
                print(f"  ✓ Template '{tmpl['name']}'")
        db.commit()

    except Exception as exc:
        db.rollback()
        print(f"\n✗ Seed failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()

    print("\nDone. Run  python start.py  to launch LIST.")


if __name__ == "__main__":
    run()