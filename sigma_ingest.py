#!/usr/bin/env python3
"""
Convert Sigma-style detections into LIST ingest payloads.

This helper accepts JSON or YAML files containing one Sigma rule, one Sigma
detection event, or a list of such records. It extracts ATT&CK tags when
present and forwards normalized alerts to LIST's existing `/api/ingest/bulk`
endpoint.
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
    parser = argparse.ArgumentParser(description="Ingest Sigma detections into LIST")
    parser.add_argument("input", help="Path to a Sigma JSON/YAML file")
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
        default="sigma",
        help="Value to send as source_type",
    )
    parser.add_argument(
        "--chain-key-field",
        default="agent.name",
        help="Dot path used to build the chain key when present",
    )
    return parser.parse_args()


def _load_records(path: Path) -> List[Dict[str, Any]]:
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(raw_text)
    else:
        payload = yaml.safe_load(raw_text)

    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("detections"), list):
            return [item for item in payload["detections"] if isinstance(item, dict)]
        return [payload]
    raise ValueError(f"Unsupported payload type in {path}")


def _dig(record: Dict[str, Any], dotted_key: str) -> Optional[Any]:
    current: Any = record
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _coerce_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _extract_attack_tags(record: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    for key in ("tags", "sigma.tags", "rule.tags"):
        value = _dig(record, key)
        tags.extend(_coerce_list(value))

    tcodes: List[str] = []
    for tag in tags:
        lower = tag.lower()
        if lower.startswith("attack.t") and len(lower) >= 9:
            tcodes.append(lower.replace("attack.", "").upper())
    return sorted(set(tcodes))


def _severity_from_sigma(record: Dict[str, Any]) -> str:
    level = str(
        record.get("level")
        or _dig(record, "rule.level")
        or _dig(record, "sigma.level")
        or "medium"
    ).lower()
    mapping = {
        "informational": "low",
        "info": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "critical": "critical",
    }
    return mapping.get(level, "medium")


def _first_present(record: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = _dig(record, key)
        if value not in (None, ""):
            return str(value)
    return None


def _occurred_at(record: Dict[str, Any]) -> str:
    value = _first_present(
        record,
        "timestamp",
        "@timestamp",
        "event.created",
        "event.start",
        "event.end",
    )
    if value:
        return value
    return datetime.now(timezone.utc).isoformat()


def _normalize_record(record: Dict[str, Any], source_type: str, chain_key_field: str) -> Dict[str, Any]:
    tcodes = _extract_attack_tags(record)
    title = _first_present(record, "title", "rule.title", "sigma.title") or "Sigma detection"
    rule_id = _first_present(record, "id", "rule.id", "sigma.id", "event.id")
    chain_key = _first_present(record, chain_key_field, "host.name", "agent.name", "source.ip")

    return {
        "source_type": source_type,
        "so_event_id": rule_id,
        "src_ip": _first_present(record, "src_ip", "source.ip", "event.src_ip"),
        "dst_ip": _first_present(record, "dst_ip", "destination.ip", "event.dst_ip"),
        "hostname": _first_present(record, "hostname", "host.name", "agent.name", "computer_name"),
        "severity": _severity_from_sigma(record),
        "tcode": "|".join(tcodes) if tcodes else None,
        "tname": title,
        "chain_key": chain_key,
        "occurred_at": _occurred_at(record),
        "raw_data": record,
    }


def _post_alerts(api_url: str, token: str, alerts: Iterable[Dict[str, Any]]) -> requests.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = list(alerts)
    return requests.post(
        f"{api_url.rstrip('/')}/api/ingest/bulk",
        headers=headers,
        json=payload,
        timeout=30,
    )


def main() -> int:
    args = parse_args()
    if not args.token:
        print("Missing API token. Pass --token or set LIST_API_TOKEN.", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    records = _load_records(input_path)
    if not records:
        print("No records found to ingest.", file=sys.stderr)
        return 1

    alerts = [
        _normalize_record(record, args.source_type, args.chain_key_field)
        for record in records
    ]
    response = _post_alerts(args.api_url, args.token, alerts)
    if response.ok:
        print(json.dumps(response.json(), indent=2))
        return 0

    print(response.text, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
