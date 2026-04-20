"""Optional CALDERA ability loader keyed by ATT&CK technique id."""
from __future__ import annotations

import copy
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency at runtime
    yaml = None

_ready = False
_index: dict[str, list[dict]] = {}

_TCODE_RE = re.compile(r"T\d{4}(?:\.\d{3})?")


def _normalize_tcode(value: str | None) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = _TCODE_RE.search(text)
    return match.group(0) if match else ""


def _base_tcode(value: str | None) -> str:
    tcode = _normalize_tcode(value)
    return tcode.split(".")[0] if tcode else ""


def _ability_paths(caldera_path: str) -> list[Path]:
    root = Path(caldera_path)
    candidates = [
        root / "data" / "abilities",
        root / "plugins" / "stockpile" / "data" / "abilities",
        root / "stockpile" / "data" / "abilities",
    ]
    plugins_dir = root / "plugins"
    if plugins_dir.is_dir():
        for plugin_dir in plugins_dir.iterdir():
            ability_dir = plugin_dir / "data" / "abilities"
            if ability_dir.is_dir():
                candidates.append(ability_dir)
    return [path for path in candidates if path.is_dir()]


def _iter_ability_files(caldera_path: str) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for base in _ability_paths(caldera_path):
        for pattern in ("*.yml", "*.yaml"):
            for path in sorted(base.rglob(pattern)):
                key = str(path.resolve())
                if key not in seen:
                    seen.add(key)
                    files.append(path)
    return files


def _extract_executor_rows(platforms: dict | None) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(platforms, dict):
        return rows
    for platform_name, executors in platforms.items():
        if not isinstance(executors, dict):
            continue
        for executor_name, executor_meta in executors.items():
            if not isinstance(executor_meta, dict):
                continue
            command = str(executor_meta.get("command") or "").strip()
            cleanup = str(executor_meta.get("cleanup") or "").strip()
            if not command and not cleanup:
                continue
            rows.append(
                {
                    "platform": str(platform_name or "").strip(),
                    "executor": str(executor_name or "").strip(),
                    "command": command,
                    "cleanup": cleanup,
                    "timeout": executor_meta.get("timeout"),
                    "payloads": list(executor_meta.get("payloads") or []),
                    "uploads": list(executor_meta.get("uploads") or []),
                }
            )
    return rows


def _normalize_ability(item: dict, source_file: Path, plugin_name: str) -> list[tuple[str, dict]]:
    technique = item.get("technique") or {}
    technique_id = _normalize_tcode(
        technique.get("attack_id")
        or item.get("technique_id")
        or item.get("attack_id")
        or item.get("tcode")
    )
    if not technique_id:
        return []

    tactic = item.get("tactic")
    name = str(item.get("name") or item.get("ability_name") or technique.get("name") or technique_id).strip()
    description = str(item.get("description") or "").strip()
    technique_name = str(technique.get("name") or item.get("technique_name") or "").strip()
    ability_id = str(item.get("ability_id") or item.get("id") or "").strip()
    rows = _extract_executor_rows(item.get("platforms"))

    if not rows:
        rows = [{"platform": "", "executor": "", "command": "", "cleanup": "", "timeout": None, "payloads": [], "uploads": []}]

    normalized_rows: list[tuple[str, dict]] = []
    for row in rows:
        normalized = {
            "ability_id": ability_id,
            "name": name,
            "description": description,
            "tactic": str(tactic or "").strip(),
            "technique_id": technique_id,
            "technique_name": technique_name,
            "plugin": plugin_name,
            "source_file": str(source_file),
            "platform": row.get("platform", ""),
            "executor": row.get("executor", ""),
            "command": row.get("command", ""),
            "cleanup": row.get("cleanup", ""),
            "timeout": row.get("timeout"),
            "payloads": row.get("payloads") or [],
            "uploads": row.get("uploads") or [],
        }
        normalized_rows.append((technique_id, normalized))
    return normalized_rows


def init_caldera(caldera_path: str) -> None:
    global _ready, _index

    _ready = False
    _index = {}

    if not yaml:
        log.warning("PyYAML not available; CALDERA ability lookup disabled")
        return

    files = _iter_ability_files(caldera_path)
    if not files:
        log.warning("No CALDERA ability files found under %s", caldera_path)
        return

    index: dict[str, list[dict]] = {}
    loaded = 0
    for path in files:
        plugin_name = "core"
        if "plugins" in path.parts:
            try:
                plugin_name = path.parts[path.parts.index("plugins") + 1]
            except Exception:
                plugin_name = "plugin"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("Skipping CALDERA ability file %s: %s", path, exc)
            continue
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            for technique_id, normalized in _normalize_ability(item, path, plugin_name):
                index.setdefault(technique_id, []).append(normalized)
                loaded += 1

    for technique_id, actions in index.items():
        actions.sort(
            key=lambda item: (
                0 if item.get("command") else 1,
                item.get("platform") or "",
                item.get("executor") or "",
                item.get("name") or "",
            )
        )
        index[technique_id] = actions

    _index = index
    _ready = bool(index)
    if _ready:
        log.info("CALDERA ability index loaded from %s (%d techniques, %d action rows)", caldera_path, len(index), loaded)
    else:
        log.warning("CALDERA ability lookup disabled; no ATT&CK-mapped actions were indexed from %s", caldera_path)


def is_ready() -> bool:
    return _ready


def lookup_actions(tcode: str, limit: int = 5) -> list[dict]:
    normalized = _normalize_tcode(tcode)
    if not normalized:
        return []

    actions: list[dict] = []

    def append_unique(rows: list[dict]) -> None:
        for action in rows:
            if action not in actions:
                actions.append(action)

    append_unique(_index.get(normalized) or [])
    if "." in normalized:
        base = _base_tcode(normalized)
        append_unique(_index.get(base, []) or [])
    else:
        child_prefix = f"{normalized}."
        for child_tcode in sorted(key for key in _index if key.startswith(child_prefix)):
            append_unique(_index.get(child_tcode) or [])
    return copy.deepcopy(actions[:limit])


def enrich_bias_result(result: dict | None, observed_tcodes: list[str] | None = None) -> dict:
    safe_result = copy.deepcopy(result or {"archetype": {}, "gaps": []})
    technique_map: dict[str, list[dict]] = {}
    tcodes: set[str] = set()

    for raw in observed_tcodes or []:
        normalized = _normalize_tcode(raw)
        if normalized:
            tcodes.add(normalized)

    for gap in safe_result.get("gaps", []) or []:
        for key in ("from_tcode", "to_tcode"):
            normalized = _normalize_tcode(gap.get(key))
            if normalized:
                tcodes.add(normalized)
        for candidate in gap.get("candidates", []) or []:
            normalized = _normalize_tcode(candidate.get("tcode"))
            if not normalized:
                continue
            tcodes.add(normalized)
            candidate["caldera_actions"] = lookup_actions(normalized)
            candidate["caldera_count"] = len(candidate["caldera_actions"])
        for chain in gap.get("chains", []) or []:
            for hop in chain.get("hops", []) or []:
                normalized = _normalize_tcode(hop.get("tcode"))
                if not normalized:
                    continue
                tcodes.add(normalized)
                hop["caldera_actions"] = lookup_actions(normalized, limit=3)
                hop["caldera_count"] = len(hop["caldera_actions"])

    for tcode in sorted(tcodes):
        actions = lookup_actions(tcode)
        if actions:
            technique_map[tcode] = actions

    safe_result["caldera"] = {
        "available": _ready,
        "techniques": technique_map,
    }
    return safe_result
