#!/usr/bin/env python3
"""
Convert Security Onion event exports into LIST ingest payloads.

This script accepts JSON, NDJSON, or YAML exports from Security Onion and
normalizes them for LIST's `/api/ingest/bulk` endpoint. It preserves the
original event under `raw_data`, extracts basic host/network context, and
derives ATT&CK technique IDs when possible.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests
import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Security Onion exports into LIST")
    parser.add_argument("input", help="Path to a Security Onion export file")
    parser.add_argument(
        "--api-url",
        default=os.getenv("LIST_API_URL", "http://localhost:8000"),
        help="LIST API base URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("LIST_API_TOKEN"),
        help="Bearer token for LIST API auth",
    )
    parser.add_argument(
        "--source-type",
        default="security_onion",
        help="Value to send as source_type",
    )
    parser.add_argument(
        "--chain-key-mode",
        choices=("host", "src_ip", "host_or_src_ip"),
        default="host_or_src_ip",
        help="How to group alerts into LIST chains",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print normalized LIST payload instead of POSTing it",
    )
    return parser.parse_args()


def _load_records(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    if suffix in {".yaml", ".yml"}:
        payload = yaml.safe_load(text)
        return _coerce_records(payload)

    if suffix == ".json":
        try:
            payload = json.loads(text)
            return _coerce_records(payload)
        except json.JSONDecodeError:
            pass

    records: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return [record for record in records if isinstance(record, dict)]


def _coerce_records(payload: Any) -> List[Dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("hits", "events", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    raise ValueError("Unsupported Security Onion payload shape")


def _dig(record: Dict[str, Any], dotted_key: str) -> Optional[Any]:
    current: Any = record
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _first_present(record: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = _dig(record, key)
        if value not in (None, ""):
            return str(value)
    return None


def _collect_attack_tags(record: Dict[str, Any]) -> List[str]:
    candidates = []
    for key in (
        "tags",
        "event.tags",
        "rule.tags",
        "sigma.tags",
        "rule.mitre_attack",
        "event.mitre_attack",
    ):
        value = _dig(record, key)
        if isinstance(value, list):
            candidates.extend(str(item) for item in value if item is not None)
        elif value not in (None, ""):
            candidates.append(str(value))

    tcodes = []
    for candidate in candidates:
        normalized = candidate.lower().replace("attack.", "t")
        if normalized.startswith("t") and len(normalized) >= 5:
            tcodes.append(normalized.upper())
        elif candidate.lower().startswith("attack.t"):
            tcodes.append(candidate.lower().replace("attack.", "").upper())
    return sorted(set(tcodes))


def _map_severity(record: Dict[str, Any]) -> str:
    raw = (
        _first_present(
            record,
            "event.severity_label",
            "severity_label",
            "alert.severity",
            "event.severity",
            "severity",
            "rule.level",
        )
        or "medium"
    ).lower()
    if raw.isdigit():
        numeric = int(raw)
        if numeric >= 4:
            return "critical"
        if numeric == 3:
            return "high"
        if numeric == 2:
            return "medium"
        return "low"
    mapping = {
        "informational": "low",
        "info": "low",
        "low": "low",
        "medium": "medium",
        "med": "medium",
        "high": "high",
        "critical": "critical",
    }
    return mapping.get(raw, "medium")


def _derive_chain_key(record: Dict[str, Any], mode: str) -> Optional[str]:
    hostname = _first_present(record, "host.name", "observer.name", "event.host", "hostname")
    src_ip = _first_present(record, "source.ip", "src_ip", "event.src_ip")
    if mode == "host":
        return hostname
    if mode == "src_ip":
        return src_ip
    return hostname or src_ip


def _occurred_at(record: Dict[str, Any]) -> str:
    value = _first_present(
        record,
        "@timestamp",
        "timestamp",
        "event.start",
        "event.created",
        "_source.@timestamp",
    )
    return value or datetime.now(timezone.utc).isoformat()


def _normalize_record(record: Dict[str, Any], source_type: str, chain_key_mode: str) -> Dict[str, Any]:
    tcodes = _collect_attack_tags(record)
    title = _first_present(
        record,
        "rule.name",
        "event.title",
        "alert.signature",
        "message",
        "title",
    ) or "Security Onion alert"
    return {
        "source_type": source_type,
        "so_event_id": _first_present(record, "_id", "event.id", "soc_id", "event_uid"),
        "src_ip": _first_present(record, "source.ip", "src_ip", "event.src_ip"),
        "dst_ip": _first_present(record, "destination.ip", "dest_ip", "dst_ip", "event.dst_ip"),
        "hostname": _first_present(record, "host.name", "observer.name", "hostname", "event.host"),
        "severity": _map_severity(record),
        "tcode": "|".join(tcodes) if tcodes else None,
        "tname": title,
        "chain_key": _derive_chain_key(record, chain_key_mode),
        "occurred_at": _occurred_at(record),
        "raw_data": record,
    }


def _post_alerts(api_url: str, token: str, alerts: Iterable[Dict[str, Any]]) -> requests.Response:
    return requests.post(
        f"{api_url.rstrip('/')}/api/ingest/bulk",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=list(alerts),
        timeout=30,
    )


def main() -> int:
    args = parse_args()
    path = Path(args.input)
    if not path.exists():
        print(f"Input file not found: {path}", file=sys.stderr)
        return 1

    records = _load_records(path)
    if not records:
        print("No Security Onion records found.", file=sys.stderr)
        return 1

    alerts = [
        _normalize_record(record, args.source_type, args.chain_key_mode)
        for record in records
    ]

    if args.print_only:
        print(json.dumps(alerts, indent=2))
        return 0

    if not args.token:
        print("Missing API token. Pass --token or set LIST_API_TOKEN.", file=sys.stderr)
        return 1

    response = _post_alerts(args.api_url, args.token, alerts)
    if response.ok:
        print(json.dumps(response.json(), indent=2))
        return 0

    print(response.text, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
