"""BIAS engine singleton. Loaded once at API startup via init_bias()."""
import json
import re
import sys, logging
from typing import Optional
from api import caldera_abilities

log = logging.getLogger(__name__)
_kb = None
_bias_ready = False

def init_bias(bias_path: str) -> None:
    """Load the BIAS knowledge base.

    Handles two common layouts:
      A)  /Documents/BIAS/bias/__init__.py   → add /Documents/BIAS to sys.path
      B)  /Documents/BIAS/__init__.py        → add /Documents      to sys.path
    Both paths are injected so either layout works automatically.
    """
    global _kb, _bias_ready
    import os
    from pathlib import Path

    candidates = [bias_path, str(Path(bias_path).parent)]
    for p in candidates:
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        from bias_cli import AttackKnowledgeBase
        _kb = AttackKnowledgeBase()
        _bias_ready = True
        log.info("BIAS KB loaded from %s", bias_path)
    except Exception as exc:
        log.warning("BIAS not available (%s) — gap analysis disabled", exc)
        _bias_ready = False

def is_ready() -> bool:
    return _bias_ready

def get_tname(tcode: str) -> Optional[str]:
    if not _bias_ready or _kb is None:
        return None
    try:
        tech = _kb.get(tcode)
        return tech.get("name") if tech else None
    except Exception:
        return None


# ── Universal attribute extractor ─────────────────────────────────────────────
def _get(obj, key, default=None):
    """Get a value from a dict OR an object attribute."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_tcode(obj) -> str:
    """
    Robustly extract a tcode string from whatever the BIAS library returns.
    Tries common attribute/key names before falling back to str().
    This is the fix for Bug 1: from_obs/to_obs tcode extraction.
    """
    if obj is None:
        return "?"
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, dict):
        for key in ("tcode", "technique_id", "id", "name"):
            val = obj.get(key)
            if val:
                return str(val).strip()
    # Object: try common attribute names
    for attr in ("tcode", "technique_id", "id"):
        val = getattr(obj, attr, None)
        if val and isinstance(val, str):
            return val.strip()
    # Last resort: str() of the object (works if Observable.__str__ returns tcode)
    s = str(obj).strip()
    log.debug("_to_tcode fell back to str() for %r → %r", type(obj).__name__, s)
    return s


def _to_score(obj, key="score") -> float:
    """Safely extract a float score."""
    try:
        val = _get(obj, key, None)
        if val is None:
            # try alternate names
            for alt in ("confidence", "weight", "probability"):
                val = _get(obj, alt, None)
                if val is not None:
                    break
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _connection_class(is_gap: bool, transition_score: float, explicit_class: Optional[str] = None) -> str:
    if explicit_class:
        explicit = str(explicit_class).strip().lower()
        if explicit:
            return explicit
    if is_gap:
        return "strong_gap" if transition_score and transition_score < 0.35 else "weak_gap"
    if transition_score >= 0.80:
        return "direct_connection"
    if transition_score >= 0.65:
        return "weak_direct"
    return "indeterminate"


def _connection_label(connection_class: str) -> str:
    return {
        "direct_connection": "Direct Connection",
        "weak_direct": "Weak Direct",
        "indeterminate": "Indeterminate",
        "weak_gap": "Weak Gap",
        "strong_gap": "Strong Gap",
    }.get(connection_class, "Indeterminate")


def normalize_tcodes(raw_tcodes) -> list[str]:
    """Normalize tcodes from lists, pipe strings, or JSON-like pasted input."""
    if raw_tcodes is None:
        return []

    if isinstance(raw_tcodes, str):
        text = raw_tcodes.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            raw_items = parsed
        else:
            raw_items = re.split(r"[\s,|]+", text)
    elif isinstance(raw_tcodes, (list, tuple, set)):
        raw_items = list(raw_tcodes)
    else:
        raw_items = [raw_tcodes]

    cleaned = []
    seen = set()
    for item in raw_items:
        text = str(item or "").strip().upper()
        if not text:
            continue
        match = re.search(r"T\d{4}(?:\.\d{3})?", text)
        if not match:
            continue
        tcode = match.group(0)
        if tcode not in seen:
            seen.add(tcode)
            cleaned.append(tcode)
    return cleaned


def _iter_list(obj, *keys):
    """
    Try multiple keys/attrs on obj to find a list.
    Fix for Bug 2: candidates/chains may live under different names.
    """
    for key in keys:
        val = _get(obj, key, None)
        if isinstance(val, list) and val:
            return val
    return []


def _gap_bridges(gap):
    """Return (single, chains) from gap.bridges regardless of object or dict form."""
    bridges = _get(gap, "bridges", None)
    if isinstance(bridges, dict):
        return bridges.get("single", []) or [], bridges.get("chains", []) or []
    if isinstance(bridges, list):
        return bridges, []
    if bridges is not None:
        return _get(bridges, "single", []) or [], _get(bridges, "chains", []) or []
    return [], []


def run_bias(tcodes: list, platforms=None, cli_env=None, scripting=None, network=None, top_n=3):
    tcodes = normalize_tcodes(tcodes)
    if not _bias_ready or not tcodes:
        return None

    try:
        from observable import SessionContext, Observable
        from analyst_infer import analyst_infer

        # 1. Build the context
        ctx = SessionContext(
            platforms=[p.strip() for p in platforms.split("|")] if platforms else [],
            cli_env=cli_env.strip() if cli_env else "",
            scripting=[s.strip() for s in scripting.split("|")] if scripting else [],
            network=network.strip() if network else "",
        )

        # 2. Build the node list — log which tcodes are/aren't in the KB
        node_list = []
        for t in tcodes:
            kb_entry = _kb.get(t)
            if kb_entry:
                node_list.append(Observable(t))
                log.info("BIAS KB lookup OK: %s", t)
            else:
                log.warning("BIAS KB lookup MISS (tcode not in KB): %s", t)

        log.info("BIAS node_list has %d/%d tcodes after KB filter", len(node_list), len(tcodes))

        if len(node_list) < 2:
            log.warning("BIAS needs at least 2 tcodes in KB — only %d found. "
                        "Check that your tcodes match the ATT&CK version loaded by BIAS.", len(node_list))
            return caldera_abilities.enrich_bias_result({"archetype": {}, "gaps": []}, tcodes)

        # 3. Call the engine
        log.info("BIAS calling analyst_infer with %d nodes: %s",
                 len(node_list), [_to_tcode(n) for n in node_list])
        raw_result = analyst_infer(node_list, ctx, _kb, top_n=top_n)
        log.info("BIAS analyst_infer returned type=%s value=%s",
                 type(raw_result).__name__, str(raw_result)[:300])

        if not raw_result:
            return caldera_abilities.enrich_bias_result({"archetype": {}, "gaps": []}, tcodes)

        # ── DEBUG: log the raw result structure so we can see what BIAS returns ──
        log.info("BIAS raw_result type: %s", type(raw_result).__name__)
        try:
            raw_gaps = _get(raw_result, "gaps", None)
            if raw_gaps is None:
                # Try alternate attribute names for the gap list
                raw_gaps = _get(raw_result, "gap_list", None) or _get(raw_result, "results", [])
            log.info("BIAS raw_gaps count: %s", len(raw_gaps) if raw_gaps else 0)
            if raw_gaps:
                g0 = raw_gaps[0]
                log.info("BIAS gap[0] type: %s  attrs/keys: %s",
                         type(g0).__name__,
                         list(g0.keys()) if isinstance(g0, dict) else [a for a in dir(g0) if not a.startswith("_")])
                from_obs = _get(g0, "from_obs", None)
                log.info("BIAS gap[0].from_obs type: %s  attrs/keys: %s",
                         type(from_obs).__name__ if from_obs else "None",
                         list(from_obs.keys()) if isinstance(from_obs, dict)
                         else [a for a in dir(from_obs) if not a.startswith("_")] if from_obs else [])
                log.info("BIAS gap[0].from_obs _to_tcode: %s", _to_tcode(from_obs))
        except Exception as dbg_exc:
            log.info("BIAS debug introspection failed: %s", dbg_exc)
        # ─────────────────────────────────────────────────────────────────────────

        safe_result = {"archetype": {}, "gaps": []}

        # ── Archetype ────────────────────────────────────────────────────────────
        arch = _get(raw_result, "archetype_match") or _get(raw_result, "archetype")
        if arch:
            safe_result["archetype"] = {
                "name": str(_get(arch, "name", "Unknown")),
                "confidence": _to_score(arch, "confidence"),
            }

        # ── Gaps ─────────────────────────────────────────────────────────────────
        raw_gaps = _iter_list(raw_result, "gaps", "gap_list", "results")
        for i, gap in enumerate(raw_gaps):

            from_obs = _get(gap, "from_obs")
            to_obs   = _get(gap, "to_obs")

            # BUG 1 FIX: use _to_tcode() which tries multiple attribute names
            from_tcode = _to_tcode(from_obs)
            to_tcode   = _to_tcode(to_obs)

            # BUG 2 FIX: use _iter_list() which tries multiple key/attr names
            # Candidates (single-hop bridges)
            cands = []
            raw_cands = _iter_list(gap, "candidates", "single", "suggestions")
            if not raw_cands:
                raw_cands, _ = _gap_bridges(gap)
            for c in raw_cands:
                c_tcode = _to_tcode(c)
                c_tech = _kb.get(c_tcode) if _kb else None
                plausibility = _to_score(c, "plausibility_score")
                if plausibility <= 0.0:
                    plausibility = _to_score(c, "score")
                support = _to_score(c, "support_score")
                if support <= 0.0:
                    support = _to_score(c, "local_support_score")
                cands.append({
                    "tcode":   c_tcode,
                    "name":    str(_get(c, "name", "") or _get(c, "tname", "")),
                    "tname":   str(_get(c, "name", "") or _get(c, "tname", "")),
                    "score":   plausibility,
                    "plausibility_score": plausibility,
                    "support_score": support,
                    "reasons": _get(c, "reasons") or _get(c, "signals") or "---",
                    "support_reasons": _get(c, "support_reasons") or _get(c, "reasons") or _get(c, "signals") or "---",
                    "confidence_label": str(_get(c, "confidence_label", "")),
                    "left_transition": _to_score(c, "left_transition"),
                    "right_transition": _to_score(c, "right_transition"),
                    "diamond_delta": _to_score(c, "diamond_delta"),
                    "possibility_score": _to_score(c, "possibility_score"),
                    "downstream_coherence_score": _to_score(c, "downstream_coherence_score"),
                    "tactics": list((c_tech or {}).get("tactic", [])),
                    "platforms": list((c_tech or {}).get("platforms", [])),
                })

            # Chains (multi-hop A* paths)
            chains = []
            raw_chains = _iter_list(gap, "chains", "multi", "paths", "chain_list")
            if not raw_chains:
                _, raw_chains = _gap_bridges(gap)
            for ch in raw_chains:
                path_raw = _iter_list(ch, "path", "chain", "hops", "nodes")
                if not path_raw:
                    # Some versions store the path directly as a list on the chain object
                    path_raw = ch if isinstance(ch, list) else []

                # BUG 3 FIX: _to_tcode handles strings, dicts, and objects uniformly
                clean_path = [_to_tcode(p) for p in path_raw]
                # Filter out any "?" placeholders from bad extractions
                clean_path = [p for p in clean_path if p and p != "?"]

                if clean_path:
                    hop_details = []
                    for hop_tcode in clean_path:
                        hop_tech = _kb.get(hop_tcode) if _kb else None
                        hop_details.append({
                            "tcode": hop_tcode,
                            "tname": str((hop_tech or {}).get("name", "")),
                            "tactics": list((hop_tech or {}).get("tactic", [])),
                            "platforms": list((hop_tech or {}).get("platforms", [])),
                        })
                    chains.append({
                        "path":  clean_path,
                        "score": _to_score(ch),
                        "reasons": _get(ch, "reasons") or _get(ch, "signals") or "---",
                        "weakest_hop": _to_score(ch, "weakest_hop"),
                        "endpoint_fit": _to_score(ch, "endpoint_fit"),
                        "hops": hop_details,
                    })

            is_gap = bool(_get(gap, "is_gap", False) or _get(gap, "has_gap", False))
            transition_score = _to_score(gap, "transition_score")
            explicit_conn_class = _get(gap, "connection_class", None)
            connection_class = _connection_class(is_gap, transition_score, explicit_conn_class)
            explicit_conn_label = _get(gap, "connection_label", None)
            safe_result["gaps"].append({
                "gap_number": i + 1,
                "from_tcode": from_tcode,
                "to_tcode":   to_tcode,
                "is_gap": is_gap,
                "tactic_distance": int(_get(gap, "tactic_distance", 0) or 0),
                "missing_tactics": list(_get(gap, "missing_tactics", []) or []),
                "transition_score": transition_score,
                "transition_label": str(_get(gap, "transition_label", "")),
                "transition_reasons": list(_get(gap, "transition_reasons", []) or []),
                "connection_class": connection_class,
                "connection_label": str(explicit_conn_label or _connection_label(connection_class)),
                "candidates": cands,
                "chains":     chains,
            })

        log.info("BIAS extracted: %d gaps, first gap from=%s to=%s, cands=%d, chains=%d",
                 len(safe_result["gaps"]),
                 safe_result["gaps"][0]["from_tcode"] if safe_result["gaps"] else "n/a",
                 safe_result["gaps"][0]["to_tcode"]   if safe_result["gaps"] else "n/a",
                 len(safe_result["gaps"][0]["candidates"]) if safe_result["gaps"] else 0,
                 len(safe_result["gaps"][0]["chains"])     if safe_result["gaps"] else 0)

        return caldera_abilities.enrich_bias_result(safe_result, tcodes)

    except Exception as exc:
        log.error("run_bias error: %s", exc)
        import traceback
        log.error(traceback.format_exc())
        return caldera_abilities.enrich_bias_result({"archetype": {}, "gaps": []}, tcodes)


def extract_bridges(result: dict) -> list:
    bridges = []
    if not result:
        return bridges
    for gap in result.get("gaps", []):
        gap_num  = gap.get("gap_number", 0)
        from_obs = gap.get("from_tcode", "")
        to_obs   = gap.get("to_tcode",   "")
        for cand in gap.get("candidates", []):
            bridges.append({"tcode": cand.get("tcode", ""), "tname": cand.get("tname", ""),
                            "confidence": cand.get("score", 0.0), "gap_number": gap_num,
                            "chain_from": from_obs, "chain_to": to_obs,
                            "is_multihop": False, "hop_chain": None})
        for chain in gap.get("chains", []):
            tcode_list = chain.get("path", [])
            bridges.append({"tcode": tcode_list[0] if tcode_list else "",
                            "tname": "", "confidence": chain.get("score", 0.0),
                            "gap_number": gap_num, "chain_from": from_obs, "chain_to": to_obs,
                            "is_multihop": True, "hop_chain": "|".join(tcode_list)})
    return bridges


def extract_archetype(result: dict):
    if not result:
        return (None, 0.0)
    arch = result.get("archetype", {})
    return (arch.get("name"), arch.get("confidence", 0.0))
