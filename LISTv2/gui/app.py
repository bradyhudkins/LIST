"""
LIST GUI — Dash front-end for investigation case management with BIAS gap analysis.
Complete implementation: multi-page routing, case detail, entities, templates, staging,
themes (Dark / Light / High-Contrast), and custom logo support.
"""
from __future__ import annotations
import base64, io, json, logging, os, re, uuid
from datetime import datetime
from pathlib import Path
import json
from urllib.parse import quote
import dash_cytoscape as cyto
from dash import html
import dash_bootstrap_components as dbc
import dash
import requests
from flask import Response, request
from dash import ALL, MATCH, Input, Output, State, callback, dcc, html, no_update
from dash.exceptions import PreventUpdate

log = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
ASSETS_DIR = Path(__file__).parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True)
LOGO_PATH = ASSETS_DIR / "logo.png"
DEFAULT_LOGO_SRC = "/assets/LIST_Logo_transparent.png"

# ── App ────────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
    assets_folder=str(ASSETS_DIR),
)
app.title = "LIST | Investigation Manager"

# ── API helpers ────────────────────────────────────────────────────────────
API_BASE = os.getenv("LIST_API_URL", "http://localhost:8000")


def _bias_connection_class(item: dict) -> str:
    score = float(item.get("transition_score", 0.0) or 0.0)
    conn_class = str(item.get("connection_class") or "").strip().lower()
    if conn_class:
        return conn_class
    if bool(item.get("is_gap", False)):
        return "strong_gap" if score and score < 0.35 else "weak_gap"
    if score >= 0.80:
        return "direct_connection"
    if score >= 0.65:
        return "weak_direct"
    return "indeterminate"


def _bias_connection_label(item: dict) -> str:
    label = str(item.get("connection_label") or "").strip()
    if label:
        return label
    mapping = {
        "direct_connection": "Direct Connection",
        "weak_direct": "Weak Direct",
        "indeterminate": "Indeterminate",
        "weak_gap": "Weak Gap",
        "strong_gap": "Strong Gap",
    }
    return mapping.get(_bias_connection_class(item), "Indeterminate")


def _bias_legend(t: dict) -> html.Div:
    direct_blue = "#2196F3"
    indeterminate_yellow = "#FFE082"
    weak_gap_orange = "#FB8C00"
    strong_gap_red = t.get("red", "#f44336")

    def line_sample(color: str, style: str = "solid", width: str = "3px"):
        return html.Div(style={
            "width": "42px",
            "borderTop": f"{width} {style} {color}",
            "marginRight": "8px",
            "flexShrink": 0,
        })

    def item(label: str, color: str, style: str = "solid", width: str = "3px"):
        return html.Div([
            line_sample(color, style, width),
            html.Span(label, style={"color": t["text"], "fontSize": "12px"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "4px"})

    def node_sample(shape: str, bg: str, border: str):
        base = {
            "width": "18px",
            "height": "18px",
            "marginRight": "8px",
            "flexShrink": 0,
            "backgroundColor": bg,
            "border": f"2px solid {border}",
        }
        if shape == "diamond":
            base["transform"] = "rotate(45deg)"
            base["borderRadius"] = "2px"
        elif shape == "hex":
            base["clipPath"] = "polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%)"
        else:
            base["borderRadius"] = "3px"
        return html.Div(style=base)

    def node_item(label: str, shape: str, bg: str, border: str):
        return html.Div([
            node_sample(shape, bg, border),
            html.Span(label, style={"color": t["text"], "fontSize": "12px"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "4px"})

    return html.Div([
        html.Div("Legend", style={"color": t.get("accent"), "fontWeight": "bold", "marginBottom": "8px"}),
        html.Div([
            item("Direct Connection", "#2196F3", "solid", "3px"),
            item("Weak Direct", direct_blue, "dotted", "3px"),
            item("Indeterminate", indeterminate_yellow, "dotted", "2px"),
            item("Weak Gap", weak_gap_orange, "dotted", "3px"),
            item("Strong Gap", strong_gap_red, "solid", "3px"),
            node_item("Observable", "square", "#0097a7", t.get("accent", "#0097a7")),
            node_item("Bridge Candidate", "diamond", "#bf360c", t.get("orange", "#f57c00")),
            node_item("Multi-hop Path", "hex", "#311b92", t.get("purple", "#bc8cff")),
        ], style={
            "display": "grid",
            "gridTemplateColumns": "repeat(auto-fit, minmax(180px, 1fr))",
            "gap": "10px 16px",
        }),
    ], style={
        "marginBottom": "12px",
        "padding": "12px",
        "backgroundColor": t["card"],
        "border": f"1px solid {t['border']}",
        "borderRadius": "8px",
        "position": "sticky",
        "top": "8px",
        "zIndex": 2,
    })


@app.server.get("/attachment-view/<int:attachment_id>")
def attachment_view_proxy(attachment_id: int):
    token = request.args.get("token", "")
    if not token:
        return Response("Missing token", status=401)
    try:
        r = requests.get(
            f"{API_BASE}/api/attachments/{attachment_id}/download",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        if not r.ok:
            return Response(r.text or "Attachment fetch failed", status=r.status_code)

        content_type = r.headers.get("Content-Type", "application/octet-stream")
        content_disp = r.headers.get("Content-Disposition", "")
        filename = "attachment"
        if 'filename="' in content_disp:
            filename = content_disp.split('filename="', 1)[1].split('"', 1)[0]

        return Response(
            r.content,
            status=r.status_code,
            content_type=content_type,
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    except Exception as exc:
        return Response(f"Attachment proxy error: {exc}", status=500)


def _api(path: str, method: str = "GET", data=None, token: str | None = None,
         files=None, form_data=None):
    """Call the REST API. Returns parsed JSON or None on failure."""
    url = f"{API_BASE}{path}"
    headers: dict = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=15)
        elif method == "POST":
            if files:
                r = requests.post(url, files=files, data=form_data or {}, headers=headers, timeout=30)
            elif form_data is not None:
                r = requests.post(url, data=form_data, headers=headers, timeout=15)
            else:
                headers["Content-Type"] = "application/json"
                r = requests.post(url, json=data, headers=headers, timeout=15)
        elif method == "PATCH":
            headers["Content-Type"] = "application/json"
            r = requests.patch(url, json=data, headers=headers, timeout=15)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers, timeout=10)
        else:
            return None
        if r.ok:
            return r.json() if r.content else {}
        log.warning("API %s %s → %s: %s", method, path, r.status_code, r.text[:300])
        return None
    except Exception as exc:
        log.error("API error %s %s: %s", method, path, exc)
        return None


def _api_bytes(path: str, token: str) -> bytes | None:
    """Download binary content (reports, attachments) from the API."""
    url = f"{API_BASE}{path}"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        return r.content if r.ok else None
    except Exception as exc:
        log.error("API bytes error %s: %s", path, exc)
        return None


def _api_login(username: str, password: str) -> tuple[str | None, str | None]:
    """Authenticate via OAuth2 form-encoded endpoint. Returns (token, username)."""
    url = f"{API_BASE}/api/auth/login"
    try:
        r = requests.post(url, data={"username": username, "password": password}, timeout=10)
        if r.ok:
            d = r.json()
            return d.get("access_token"), username
    except Exception as exc:
        log.error("Login error: %s", exc)
    return None, None


def _current_logo_src() -> str:
    return "/assets/logo.png" if LOGO_PATH.exists() else DEFAULT_LOGO_SRC


# ── Theme definitions ──────────────────────────────────────────────────────
THEMES: dict[str, dict] = {
    "dark": {
        "_name": "Dark", "_key": "dark",
        "accent": "#39D0D8", "orange": "#F0883E", "green": "#3FB950",
        "red": "#F85149", "purple": "#BC8CFF",
        "text": "#E6EDF3", "muted": "#8B949E",
        "bg": "#0D1117", "bg2": "#010409", "card": "#161B22",
        "border": "#30363D", "inp_bg": "#010409", "inp_col": "#E6EDF3",
    },
    "light": {
        "_name": "Light", "_key": "light",
        "accent": "#0969DA", "orange": "#E36209", "green": "#1A7F37",
        "red": "#CF222E", "purple": "#6F42C1",
        "text": "#24292F", "muted": "#57606A",
        "bg": "#FFFFFF", "bg2": "#F6F8FA", "card": "#FFFFFF",
        "border": "#D0D7DE", "inp_bg": "#F6F8FA", "inp_col": "#24292F",
    },
    "contrast": {
        "_name": "High Contrast", "_key": "contrast",
        "accent": "#00FFFF", "orange": "#FF8C00", "green": "#00FF00",
        "red": "#FF4444", "purple": "#FF00FF",
        "text": "#FFFFFF", "muted": "#CCCCCC",
        "bg": "#000000", "bg2": "#000000", "card": "#111111",
        "border": "#FFFFFF", "inp_bg": "#000000", "inp_col": "#FFFFFF",
    },
}


def _t(theme_key: str | None) -> dict:
    return THEMES.get(theme_key or "dark", THEMES["dark"])


def _pipe_to_list(val: str | None) -> list:
    """Convert pipe-separated stored string to a list for multi-select dropdowns."""
    if not val:
        return []
    return [x.strip() for x in val.split("|") if x.strip()]


def _list_to_pipe(val) -> str | None:
    """Convert a list (from multi-select) back to a pipe-separated string for storage."""
    if isinstance(val, list):
        return "|".join(val) if val else None
    return val or None


# ── UI primitives ──────────────────────────────────────────────────────────
def _inp_style(t: dict) -> dict:
    return {"backgroundColor": t["inp_bg"], "color": t["inp_col"],
            "border": f"1px solid {t['border']}"}


def _card(children, title: str = "", t: dict | None = None) -> dbc.Card:
    t = t or THEMES["dark"]
    hdr = ([dbc.CardHeader(title, style={"backgroundColor": t["bg2"], "color": t["accent"],
                                          "borderColor": t["border"], "fontWeight": "600"})]
           if title else [])
    return dbc.Card(
        hdr + [dbc.CardBody(children, style={"backgroundColor": t["card"]})],
        style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "16px"},
    )


def _btn_primary(label: str, btn_id, t: dict | None = None, **kw) -> dbc.Button:
    t = t or THEMES["dark"]
    base_style = {
        "backgroundColor": t["accent"],
        "borderColor": t["accent"],
        "minHeight": "34px",
        "minWidth": "118px",
        "padding": "0.375rem 0.75rem",
        "fontWeight": "600",
    }
    style = dict(base_style)
    style.update(kw.pop("style", {}) or {})
    return dbc.Button(label, id=btn_id, style=style, **kw)


def _btn_secondary(label: str, btn_id, t: dict | None = None, **kw) -> dbc.Button:
    t = t or THEMES["dark"]
    base_style = {
        "backgroundColor": "transparent",
        "borderColor": t["border"],
        "color": t["text"],
        "minHeight": "34px",
        "minWidth": "118px",
        "padding": "0.375rem 0.75rem",
        "fontWeight": "600",
    }
    style = dict(base_style)
    style.update(kw.pop("style", {}) or {})
    return dbc.Button(label, id=btn_id, outline=True, style=style, **kw)


def _reasoning_panel(title: str, subtitle: str, children, t: dict) -> html.Div:
    return html.Div(
        [
            html.Div(title, style={"color": t["accent"], "fontSize": "13px", "fontWeight": "700", "marginBottom": "2px"}),
            html.Div(subtitle, style={"color": t["muted"], "fontSize": "11px", "marginBottom": "10px"}),
            *children,
        ],
        style={
            "backgroundColor": t["bg2"],
            "border": f"1px solid {t['border']}",
            "borderRadius": "10px",
            "padding": "14px",
            "height": "100%",
        },
    )


def _sev_color(sev: str | None, t: dict) -> str:
    s = (sev or "").lower()
    if "critical" in s: return "#7F1D1D"
    if "high" in s: return "#C2410C"
    if "medium" in s: return "#D97706"
    if "low" in s: return "#15803D"
    return t["muted"]


def _sev_badge_style(sev: str | None, t: dict) -> dict:
    bg = _sev_color(sev, t)
    return {
        "background": bg,
        "backgroundColor": bg,
        "borderColor": bg,
        "color": "#FFFFFF",
        "border": f"1px solid {bg}",
        "borderRadius": "999px",
        "display": "inline-block",
        "fontSize": "0.75rem",
        "fontWeight": "700",
        "lineHeight": "1",
        "padding": "0.35em 0.65em",
        "whiteSpace": "nowrap",
    }


def _status_color(status: str | None, t: dict) -> str:
    s = (status or "").lower()
    if "open" in s: return t["accent"]
    if "closed" in s: return t["green"]
    if "hold" in s: return t["orange"]
    return t["muted"]


def _fmt_dt(dt_str: str | None) -> str:
    if not dt_str: return "—"
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt_str)


def _alert_msg(msg: str, ok: bool = True) -> dbc.Alert:
    return dbc.Alert(msg, color="success" if ok else "danger", dismissable=True, duration=5000)


def _case_lock_state(case: dict | None, user: dict | None) -> tuple[bool, str | None]:
    case = case or {}
    user = user or {}
    lock_active = bool(case.get("lock_active"))
    locked_by = (case.get("locked_by") or {}).get("username")
    current = user.get("username")
    locked_by_other = bool(lock_active and locked_by and locked_by != current)
    return locked_by_other, locked_by


def _build_case_lock_banner(case: dict | None, user: dict | None, t: dict):
    locked_by_other, locked_by = _case_lock_state(case, user)
    style = {
        "backgroundColor": t["orange"],
        "border": f"1px solid {t['orange']}",
        "color": "#FFFFFF",
        "fontWeight": "700",
        "fontSize": "12px",
        "padding": "6px 10px",
        "marginBottom": "8px",
        "display": "inline-block",
        "borderRadius": "8px",
    }
    if locked_by_other:
        return html.Div(f"Case open - {locked_by}", style=style)
    if case and case.get("lock_active") and locked_by:
        return html.Div(f"Case open - {locked_by}", style=style)
    return html.Div()


def _summary_chip(label: str, value: str, t: dict, accent: str | None = None,
                  href: str | None = None, value_id: str | None = None) -> html.Div:
    value_props = {
        "style": {"color": accent or t["text"], "fontSize": "15px", "fontWeight": "700", "marginTop": "2px", "lineHeight": "1.1"},
    }
    if value_id:
        value_props["id"] = value_id
    card = dbc.Card(
        dbc.CardBody([
            html.Small(label.upper(), style={"color": t["muted"], "fontSize": "9px", "letterSpacing": "0.06em", "lineHeight": "1"}),
            html.Div(value, **value_props),
        ], style={"backgroundColor": t["card"], "padding": "8px 10px"}),
        style={"backgroundColor": t["card"], "borderColor": t["border"], "height": "100%"},
    )
    if href:
        return html.A(
            card,
            href=href,
            style={"display": "block", "textDecoration": "none"},
        )
    return html.Div(card)


def _parse_case_tasks(checklist_json, default_owner: dict | None = None) -> list[dict]:
    if not checklist_json:
        return []
    try:
        raw = json.loads(checklist_json) if isinstance(checklist_json, str) else checklist_json
    except Exception:
        return []
    tasks = []
    for idx, item in enumerate(raw or []):
        if isinstance(item, str):
            tasks.append({
                "id": f"legacy-{idx}",
                "text": item,
                "status": "open",
                "parent_id": None,
                "owner_id": (default_owner or {}).get("id"),
                "owner_name": (default_owner or {}).get("username"),
                "assignee_id": None,
                "assignee_name": None,
                "due_date": None,
                "created_at": None,
                "completed_at": None,
                "awaiting_owner_close": False,
                "owner_notified_at": None,
            })
        elif isinstance(item, dict) and item.get("text"):
            tasks.append({
                "id": item.get("id") or f"task-{idx}",
                "text": item.get("text"),
                "status": item.get("status") or ("done" if item.get("done") else "open"),
                "parent_id": item.get("parent_id"),
                "owner_id": item.get("owner_id") or (default_owner or {}).get("id"),
                "owner_name": item.get("owner_name") or (default_owner or {}).get("username"),
                "assignee_id": item.get("assignee_id"),
                "assignee_name": item.get("assignee_name"),
                "due_date": item.get("due_date"),
                "created_at": item.get("created_at"),
                "completed_at": item.get("completed_at"),
                "awaiting_owner_close": bool(item.get("awaiting_owner_close")),
                "owner_notified_at": item.get("owner_notified_at"),
            })
    return tasks


def _task_ui_state(case_id: int | None, user: dict | None, theme_key: str | None) -> tuple[dict, list[dict], list[dict], bool]:
    t = _t(theme_key)
    if not case_id or not user or not user.get("token"):
        return t, [], [{"label": "Unassigned", "value": 0}], False
    case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    assignable_users = _api("/api/auth/assignable-users", token=user.get("token")) or []
    assign_opts = [{"label": "Unassigned", "value": 0}] + [
        {"label": f"{u.get('username')} ({u.get('role')})", "value": u.get("id")}
        for u in assignable_users
    ]
    tasks = _parse_case_tasks(case.get("checklist_json"), case.get("owner"))
    return t, tasks, assign_opts, _can_manage_case(user)


def _case_task_lookup(case_id: int | None, task_id: str | None, user: dict | None) -> tuple[dict, dict | None]:
    if not case_id or not task_id or not user or not user.get("token"):
        return {}, None
    case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    tasks = _parse_case_tasks(case.get("checklist_json"), case.get("owner"))
    task = next((item for item in tasks if str(item.get("id")) == str(task_id)), None)
    return case, task


def _parse_dt_like(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _task_children(tasks: list[dict], parent_id: str | None = None) -> list[dict]:
    return [task for task in tasks if str(task.get("parent_id") or "") == str(parent_id or "")]


def _task_can_assign(task: dict, user: dict | None) -> bool:
    if not user or not task:
        return False
    return (
        task.get("owner_id") == user.get("id")
        or ((task.get("owner_name") or "").strip().lower() == (user.get("username") or "").strip().lower())
    )


def _task_can_create_subtask(task: dict, user: dict | None) -> bool:
    if not user or not task:
        return False
    username = (user.get("username") or "").strip().lower()
    return (
        user.get("id") in {task.get("owner_id"), task.get("assignee_id")}
        or username in {
            (task.get("owner_name") or "").strip().lower(),
            (task.get("assignee_name") or "").strip().lower(),
        }
    )


def _task_close_block_reason(task: dict | None, tasks: list[dict], user: dict | None) -> str | None:
    if not task:
        return None
    children = _task_children(tasks, task.get("id"))
    if not children:
        return None
    if not _task_can_assign(task, user):
        return "Only the task owner can close a task with subtasks."
    incomplete = [child for child in children if (child.get("status") or "open").lower() != "done"]
    if incomplete:
        return "All subtasks must be completed before closing the task."
    return None


def _render_note_body(body: str | None, t: dict):
    text = body or ""
    parts = []
    cursor = 0
    for match in re.finditer(r"\[\[([^\[\]]+)\]\]", text):
        start, end = match.span()
        if start > cursor:
            parts.append(text[cursor:start])
        parts.append(
            html.Span(
                [
                    dcc.Link(
                        f"[[{match.group(1)}]]",
                        href="/relationships",
                        className="entity-relationship-link",
                        style={
                            "backgroundColor": t["bg2"],
                            "border": f"1px solid {t['border']}",
                            "borderRadius": "8px",
                            "padding": "1px 6px",
                            "color": t["orange"],
                            "fontWeight": "600",
                            "display": "inline-block",
                            "margin": "0 2px",
                            "cursor": "pointer",
                            "textDecoration": "none",
                        },
                    ),
                    html.Span("Entity Relationships", className="entity-relationship-hover"),
                ],
                className="entity-relationship-wrap",
            )
        )
        cursor = end
    if cursor < len(text):
        parts.append(text[cursor:])
    return html.Div(parts, style={"color": t["text"], "marginBottom": "4px", "whiteSpace": "pre-wrap"})


def _parse_analysis(analysis_json) -> dict:
    default = {
        "who": "",
        "what": "",
        "when": "",
        "where": "",
        "why": "",
        "hypothesis": "",
        "counter_hypothesis": "",
        "supporting_evidence": "",
        "disconfirming_evidence": "",
    }
    if not analysis_json:
        return default
    try:
        raw = json.loads(analysis_json) if isinstance(analysis_json, str) else analysis_json
    except Exception:
        return default
    if not isinstance(raw, dict):
        return default
    return {key: str(raw.get(key) or "") for key in default}


def _split_datetime_local(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    text = str(value).strip()
    if not text:
        return "", ""
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except Exception:
        pass
    if "T" in text:
        date_part, time_part = text.split("T", 1)
        return date_part[:10], time_part[:5]
    if " " in text:
        date_part, time_part = text.split(" ", 1)
        return date_part[:10], time_part[:5]
    return text[:10], ""


def _can_manage_case(user: dict | None) -> bool:
    return (user or {}).get("role", "").lower() in {"admin", "lead", "analyst"}


def _can_assign_case(user: dict | None) -> bool:
    return (user or {}).get("role", "").lower() in {"admin", "lead"}


def _sort_and_filter_cases(cases: list, sort_by: str = "timestamp", status_filter: str = "all") -> list:
    items = list(cases or [])
    if status_filter == "open":
        items = [c for c in items if (c.get("status") or "").lower() == "open"]
    elif status_filter == "in_progress":
        items = [c for c in items if (c.get("status") or "").lower() == "in_progress"]
    elif status_filter == "resolved":
        items = [c for c in items if (c.get("status") or "").lower() == "resolved"]
    elif status_filter == "closed":
        items = [c for c in items if (c.get("status") or "").lower() == "closed"]

    def _val(case: dict, key: str) -> str:
        return str(case.get(key) or "").strip().lower()

    sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}

    if sort_by == "name":
        items.sort(key=lambda c: (_val(c, "title"), _val(c, "case_number")))
    elif sort_by == "description":
        items.sort(key=lambda c: (_val(c, "description"), _val(c, "title")))
    elif sort_by == "assigned":
        items.sort(key=lambda c: (_val(c.get("owner") or {}, "username"), _val(c, "title")))
    elif sort_by == "severity":
        items.sort(key=lambda c: (sev_rank.get((c.get("severity") or "").lower(), 0), _val(c, "title")), reverse=True)
    else:
        items.sort(key=lambda c: (c.get("updated_at") or c.get("created_at") or ""), reverse=True)
    return items


def _build_cases_list(cases: list, t: dict, user: dict, sort_by: str = "timestamp",
                      status_filter: str = "all") -> html.Div:
    can_manage = _can_manage_case(user)
    rows = []
    for case in _sort_and_filter_cases(cases, sort_by=sort_by, status_filter=status_filter):
        cid  = case.get("id", "")
        sev  = (case.get("severity") or "unknown").upper()
        sta  = (case.get("status")   or "unknown").replace("_", " ").upper()
        owner = (case.get("owner") or {}).get("username") or "Unassigned"
        rows.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.P(case.get("title", "—"),
                               style={"color": t["text"], "marginBottom": "2px", "fontSize": "16px", "fontWeight": "600"}),
                        html.Small(case.get("description") or "No description",
                                   style={"color": t["muted"], "display": "block", "marginBottom": "2px"}),
                        html.Small(case.get("case_number", "?"),
                                   style={"color": t["accent"], "display": "block", "fontSize": "12px"}),
                        html.Div([
                            html.Span("Assigned: ",
                                      style={"color": t["muted"], "fontSize": "12px"}),
                            dbc.Badge(
                                owner,
                                style={
                                    "backgroundColor": t["orange"] if owner != "Unassigned" else t["muted"],
                                    "fontSize": "11px",
                                },
                            ),
                        ], style={"marginTop": "6px", "marginBottom": "4px"}),
                        html.Small(f"Updated: {_fmt_dt(case.get('updated_at') or case.get('created_at'))}",
                                   style={"color": t["muted"]}),
                    ], width=True),
                    dbc.Col([
                        html.Span(sev, className="me-1",
                                  style=_sev_badge_style(sev, t)),
                        dbc.Badge(sta,
                                  style={"backgroundColor": _status_color(sta, t)}),
                    ], width="auto", className="me-2"),
                    dbc.Col([
                        dbc.Button("Open", size="sm",
                                   id={"type": "case-open-btn", "index": cid},
                                   style={"backgroundColor": t["accent"],
                                          "borderColor": t["accent"],
                                          "marginRight": "6px"}),
                        dbc.Button("Delete", size="sm",
                                   id={"type": "case-del-btn", "index": cid},
                                   outline=True,
                                   disabled=not can_manage,
                                   style={"borderColor": t["red"], "color": t["red"]}),
                    ], width="auto"),
                ], align="center"),
            ], style={"backgroundColor": t["card"]}),
            style={"backgroundColor": t["card"], "borderColor": t["border"],
                   "marginBottom": "8px"},
        ))
    return html.Div(rows) if rows else html.P("No matching cases.", style={"color": t["muted"]})


def _assigned_cases(cases: list, user: dict | None) -> list[dict]:
    username = ((user or {}).get("username") or "").strip().lower()
    if not username:
        return []
    items = []
    for case in cases or []:
        owner_name = ((case.get("owner") or {}).get("username") or "").strip().lower()
        assigned_tasks = [
            task for task in _parse_case_tasks(case.get("checklist_json"), case.get("owner"))
            if ((task.get("assignee_name") or "").strip().lower() == username)
        ]
        if owner_name == username or assigned_tasks:
            enriched = dict(case)
            enriched["_assigned_tasks"] = assigned_tasks
            items.append(enriched)
    items.sort(key=lambda c: (c.get("updated_at") or c.get("created_at") or ""), reverse=True)
    return items


def _build_my_work_list(cases: list, t: dict, user: dict | None) -> html.Div:
    username = ((user or {}).get("username") or "").strip().lower()
    cards = []
    for case in _assigned_cases(cases, user):
        cid = case.get("id")
        sev = (case.get("severity") or "unknown").upper()
        sta = (case.get("status") or "unknown").replace("_", " ").upper()
        owner_name = (case.get("owner") or {}).get("username") or "Unassigned"
        assigned_tasks = case.get("_assigned_tasks", [])
        locked_by_other, locked_by = _case_lock_state(case, user)
        owner_is_user = owner_name.strip().lower() == username
        task_count = len(assigned_tasks)
        task_label = "task is" if task_count == 1 else "tasks are"
        if owner_is_user:
            banner_text = (
                "This case is assigned to you. "
                + (f"{task_count} individual {task_label} assigned to you."
                   if task_count else
                   "No individual tasks are assigned to you yet.")
            )
        else:
            banner_text = (
                f"This case is assigned to {owner_name}. "
                f"{task_count} individual {task_label} assigned to you."
            )
        task_rows = []
        for task in assigned_tasks:
            task_status = (task.get("status") or "open").lower()
            task_rows.append(
                dbc.Card(
                    dbc.CardBody([
                        dbc.Row([
                            dbc.Col([
                                html.Div(
                                    dcc.Checklist(
                                        options=[{"label": "", "value": "done"}],
                                        value=["done"] if task_status == "done" else [],
                                        id={"type": "my-work-task-check", "case": cid, "task": task.get("id")},
                                        inputStyle={"marginRight": "0px"},
                                        style={"marginTop": "2px"},
                                    ),
                                    style={
                                        "pointerEvents": "none" if (locked_by_other or not _can_manage_case(user)) else "auto",
                                        "opacity": 0.55 if (locked_by_other or not _can_manage_case(user)) else 1,
                                    },
                                ),
                            ], md=1),
                            dbc.Col([
                                html.Div(task.get("text", "—"), style={"color": t["text"], "fontWeight": "600", "fontSize": "13px"}),
                                html.Small(
                                    "Your task"
                                    + (f"  ·  Due: {_fmt_dt(task.get('due_date'))}" if task.get("due_date") else ""),
                                    style={"color": t["muted"], "display": "block", "marginTop": "4px"},
                                ),
                            ], md=6),
                            dbc.Col([
                                dcc.Dropdown(
                                    [
                                        {"label": "Open", "value": "open"},
                                        {"label": "In Progress", "value": "in_progress"},
                                        {"label": "Done", "value": "done"},
                                    ],
                                    id={"type": "my-work-task-status", "case": cid, "task": task.get("id")},
                                    value=task_status,
                                    clearable=False,
                                    disabled=locked_by_other or not _can_manage_case(user),
                                    style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                                ),
                            ], md=3),
                            dbc.Col([
                                dbc.Button(
                                    "Open Case",
                                    id={"type": "my-work-open-btn", "index": cid},
                                    size="sm",
                                    style={"backgroundColor": t["accent"], "borderColor": t["accent"]},
                                ),
                            ], md=2),
                        ], align="center", className="g-2"),
                    ], style={"backgroundColor": t["bg2"], "padding": "10px"}),
                    style={"backgroundColor": t["bg2"], "borderColor": t["border"], "marginTop": "8px"},
                )
            )
        cards.append(
            html.Div(
                dbc.Card(
                    dbc.CardBody([
                        dbc.Row([
                            dbc.Col([
                                html.P(case.get("title", "—"), style={"color": t["text"], "marginBottom": "4px", "fontSize": "16px", "fontWeight": "600"}),
                                html.Small(case.get("description") or "No description", style={"color": t["muted"], "display": "block", "marginBottom": "2px"}),
                                html.Small(case.get("case_number", "?"), style={"color": t["accent"], "display": "block"}),
                                html.Div([
                                    html.Span("Assigned: ", style={"color": t["muted"], "fontSize": "12px"}),
                                    dbc.Badge(owner_name, style={"backgroundColor": t["orange"] if owner_name != "Unassigned" else t["muted"], "fontSize": "11px"}),
                                    html.Small(f"  Updated: {_fmt_dt(case.get('updated_at') or case.get('created_at'))}", style={"color": t["muted"], "marginLeft": "10px"}),
                                ], style={"marginTop": "6px"}),
                            ], md=7),
                            dbc.Col([
                                html.Div([
                                    html.Span(sev, style=_sev_badge_style(sev, t)),
                                    dbc.Badge(sta, style={"backgroundColor": _status_color(sta, t), "marginLeft": "6px"}),
                                ], className="mb-2"),
                                (dbc.Alert(f"Case open - {locked_by}", color="warning", className="py-1 px-2 mb-2")
                                 if locked_by_other else
                                 html.Small("Ready to update", style={"color": t["muted"], "display": "block", "marginBottom": "8px"})),
                                dbc.Button(
                                    "Open Case",
                                    id={"type": "my-work-open-btn", "index": cid},
                                    size="sm",
                                    style={"backgroundColor": t["accent"], "borderColor": t["accent"]},
                                ),
                            ], md=5, style={"textAlign": "right"}),
                        ], align="start"),
                        html.Hr(style={"borderColor": t["border"], "margin": "12px 0"}),
                        dbc.Alert(
                            banner_text,
                            color="secondary",
                            className="mt-0 mb-2 py-1 px-2",
                            style={"fontSize": "12px"},
                        ),
                        html.Div(task_rows),
                    ], style={"backgroundColor": t["card"]}),
                    style={
                        "backgroundColor": t["card"],
                        "borderColor": t["border"],
                        "marginBottom": "10px",
                    },
                ),
                id={"type": "my-work-card", "index": cid},
                n_clicks=0,
                style={"cursor": "pointer"},
            )
        )
    return html.Div(cards) if cards else html.P("No cases or tasks are currently assigned to you.", style={"color": t["muted"]})


# ── Nav ────────────────────────────────────────────────────────────────────
def _nav(user: dict, t: dict) -> html.Nav:
    logo_src = _current_logo_src()
    logo_el = html.Img(src=logo_src,
                       style={"height": "30px", "marginRight": "8px", "objectFit": "contain"})
    is_admin = (user.get("role") or "").lower() == "admin"
    return html.Nav([
        dbc.Container([
            dbc.Row([
                dbc.Col([logo_el],
                        width="auto", style={"display": "flex", "alignItems": "center"}),
                dbc.Col(width=True),
                dbc.Col([
                    dbc.Nav([
                        dbc.NavLink("My Work",   href="/my-work",   style={"color": t["text"]}),
                        dbc.NavLink("Cases",     href="/cases",     style={"color": t["text"]}),
                        dbc.NavLink("Staging",   href="/staging",   style={"color": t["text"]}),
                        dbc.NavLink("Relationships", href="/relationships", style={"color": t["text"]}),
                        dbc.NavLink("Entities",  href="/entities",  style={"color": t["text"]}),
                        dbc.NavLink("Templates", href="/templates", style={"color": t["text"]}),
                        *([dbc.NavLink("Admin", href="/admin", style={"color": t["text"]})] if is_admin else []),
                    ], navbar=True),
                ], width="auto"),
                dbc.Col([
                    html.Span(user.get("username", ""), className="me-2",
                              style={"color": t["muted"], "fontSize": "13px"}),
                    dbc.Button("Logout", id="btn-logout", size="sm", color="link",
                               style={"color": t["muted"], "padding": "0"}),
                ], width="auto", style={"display": "flex", "alignItems": "center"}),
            ], align="center", className="py-2"),
        ], fluid=True),
    ], style={"backgroundColor": t["bg2"], "borderBottom": f"1px solid {t['border']}",
               "marginBottom": "20px", "padding": "0 16px"})


# ─────────────────────────── PAGE BUILDERS ────────────────────────────────

def _login_page(t: dict) -> dbc.Container:
    logo_src = _current_logo_src()
    return dbc.Container([
        html.Div([
            dbc.Card([
                dbc.CardBody([
                    html.Div([
                        html.Img(src=logo_src,
                                 style={"height": "60px", "marginBottom": "12px", "objectFit": "contain"})
                    ], className="text-center"),
                    html.P("Lightweight Investigation System for Ticketing",
                           className="text-center mb-4",
                           style={"color": t["muted"], "fontSize": "13px"}),
                    dbc.Input(id="login-username", placeholder="Username", className="mb-3",
                              style=_inp_style(t)),
                    dbc.Input(id="login-password", placeholder="Password", type="password",
                              className="mb-3", style=_inp_style(t)),
                    dbc.Button("Login", id="btn-login", className="w-100 mb-3",
                               style={"backgroundColor": t["accent"], "borderColor": t["accent"]}),
                    html.Div(id="login-error", className="text-center",
                             style={"color": t["red"], "minHeight": "20px"}),
                ])
            ], style={"backgroundColor": t["card"], "border": f"1px solid {t['border']}",
                      "width": "100%", "maxWidth": "400px"}),
        ], style={"height": "100vh", "display": "flex", "alignItems": "center",
                  "justifyContent": "center"}),
    ], fluid=True, style={"backgroundColor": t["bg"], "padding": 0})


def _cases_page(user: dict, t: dict) -> dbc.Container:
    cases = _api("/api/cases", token=user.get("token")) or []
    return dbc.Container([
        _nav(user, t),
        dbc.Row([
            dbc.Col(html.H4("Cases", style={"color": t["accent"]}), width=True),
            dbc.Col(
                dbc.Button("+ New Case", href="/new-case",
                           style={"backgroundColor": t["accent"],
                                  "borderColor": t["accent"]}),
                width="auto"),
        ], className="mb-3", align="center"),
        dbc.Row([
            dbc.Col([
                dbc.Label("Sort by", style={"color": t["text"], "fontSize": "12px"}),
                dcc.Dropdown(
                    options=[
                        {"label": "Time / Date stamp", "value": "timestamp"},
                        {"label": "Severity", "value": "severity"},
                        {"label": "Name", "value": "name"},
                        {"label": "Description", "value": "description"},
                        {"label": "Assigned", "value": "assigned"},
                    ],
                    id="cases-sort-by",
                    value="timestamp",
                    clearable=False,
                    style={"backgroundColor": t["inp_bg"]},
                ),
            ], md=4),
            dbc.Col([
                dbc.Label("Show", style={"color": t["text"], "fontSize": "12px"}),
                dcc.Dropdown(
                    options=[
                        {"label": "All Cases", "value": "all"},
                        {"label": "Open Only", "value": "open"},
                        {"label": "In Progress Only", "value": "in_progress"},
                        {"label": "Resolved Only", "value": "resolved"},
                        {"label": "Closed Only", "value": "closed"},
                    ],
                    id="cases-status-filter",
                    value="all",
                    clearable=False,
                    style={"backgroundColor": t["inp_bg"]},
                ),
            ], md=3),
        ], className="mb-3 g-2"),
        html.Div(id="cases-list-feedback"),
        html.Div(id="cases-list", children=_build_cases_list(cases, t, user)),
    ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh",
                           "paddingBottom": "40px"})


def _my_work_page(user: dict, t: dict) -> dbc.Container:
    cases = _api("/api/cases", token=user.get("token")) or []
    assigned_cases = _assigned_cases(cases, user)
    assigned_case_count = len(assigned_cases)
    assigned_task_count = sum(len(case.get("_assigned_tasks", [])) for case in assigned_cases)
    return dbc.Container([
        _nav(user, t),
        html.H4("My Work", style={"color": t["accent"], "marginBottom": "8px"}),
        html.P(
            "Cases and tasks assigned to you. You can jump into the case or move your task forward from here.",
            style={"color": t["muted"], "marginBottom": "16px"},
        ),
        dbc.Row([
            dbc.Col(_summary_chip("Assigned Cases", str(assigned_case_count), t, t["orange"]), md=3, className="mb-2"),
            dbc.Col(_summary_chip("Assigned Tasks", str(assigned_task_count), t, t["accent"]), md=3, className="mb-2"),
        ], className="g-2 mb-3"),
        html.Div(id="my-work-feedback"),
        html.Div(id="my-work-list", children=_build_my_work_list(cases, t, user)),
    ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh",
                           "paddingBottom": "40px"})


def _new_case_page(user: dict, t: dict) -> dbc.Container:
    templates = _api("/api/templates", token=user.get("token")) or []
    tmpl_opts = [{"label": tmpl["name"], "value": tmpl["id"]} for tmpl in templates]
    return dbc.Container([
        _nav(user, t),
        html.H4("New Case", style={"color": t["accent"], "marginBottom": "20px"}),
        _card([
            dbc.Row([
                dbc.Col([
                    dbc.Label("Title *", style={"color": t["text"]}),
                    dbc.Input(id="nc-title", placeholder="Investigation title",
                              style=_inp_style(t)),
                ], md=8),
                dbc.Col([
                    dbc.Label("Severity", style={"color": t["text"]}),
                    dcc.Dropdown(["CRITICAL", "HIGH", "MEDIUM", "LOW"], id="nc-severity",
                                 value="MEDIUM", clearable=False,
                                 style={"backgroundColor": t["inp_bg"]}),
                ], md=4),
            ], className="mb-3"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Description", style={"color": t["text"]}),
                    dbc.Textarea(id="nc-desc", placeholder="Brief description of the incident",
                                 style={**_inp_style(t), "minHeight": "80px"}),
                ]),
            ], className="mb-3"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Template (optional)", style={"color": t["text"]}),
                    dcc.Dropdown(tmpl_opts, id="nc-template", placeholder="Select a template…",
                                 style={"backgroundColor": t["inp_bg"]}),
                ]),
            ], className="mb-3"),
            dbc.Row([
                dbc.Col(
                    _btn_primary("Create Case", "btn-nc-create", t, className="w-100"), md=4),
            ]),
            html.Div(id="nc-feedback", className="mt-3"),
        ], title="Case Details", t=t),
    ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh", "paddingBottom": "40px"})


@callback(
    Output("cases-list", "children"),
    Input("cases-sort-by", "value"),
    Input("cases-status-filter", "value"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=False,
)
def update_cases_list(sort_by, status_filter, user, theme_key):
    if not user or not user.get("token"):
        raise PreventUpdate
    cases = _api("/api/cases", token=user.get("token")) or []
    return _build_cases_list(cases, _t(theme_key), user, sort_by or "timestamp", status_filter or "all")


def _build_observables_list(observables: list, t: dict, can_manage: bool = False) -> html.Div:
    if not observables:
        return html.P("No observables yet.", style={"color": t["muted"], "fontSize": "13px"})
    items = []
    for obs in observables:
        name = obs.get("tname") or ""
        items.append(html.Span([
            dbc.Badge(
                f"{obs['tcode']}" + (f"  {name}" if name else ""),
                className="me-1",
                style={"backgroundColor": t["purple"], "fontSize": "12px"},
            ),
            html.Button(
                "x",
                id={"type": "btn-del-obs", "index": obs.get("id")},
                n_clicks=0,
                title=f"Remove {obs.get('tcode', 'observable')}",
                style={
                    "backgroundColor": "transparent",
                    "border": f"1px solid {t['border']}",
                    "color": t["muted"],
                    "borderRadius": "10px",
                    "fontSize": "10px",
                    "lineHeight": "12px",
                    "padding": "0 5px",
                    "display": "inline-block" if can_manage else "none",
                    "verticalAlign": "middle",
                },
            ),
        ], className="me-2 mb-2", style={"display": "inline-flex", "alignItems": "center"}))
    return html.Div(items)


def _bias_order_row(tcode: str, index: int, total: int, t: dict):
    btn_style = {
        "backgroundColor": t["card"],
        "color": t["text"],
        "border": f"1px solid {t['border']}",
        "padding": "2px 7px",
        "fontSize": "11px",
        "cursor": "pointer",
        "fontFamily": "monospace",
    }
    return html.Div(style={
        "display": "flex",
        "alignItems": "center",
        "gap": "6px",
        "padding": "5px 6px",
        "marginBottom": "4px",
        "border": f"1px solid {t['border']}",
        "backgroundColor": t["card"],
        "borderRadius": "4px",
    }, children=[
        html.Span(f"{index + 1}.", style={"color": t["muted"], "width": "18px", "fontSize": "10px"}),
        html.Span(tcode, style={"color": t["accent"], "fontSize": "12px", "fontWeight": "bold", "flex": 1}),
        html.Button("↑", id={"type": "btn-bias-move-up", "index": tcode}, n_clicks=0,
                    disabled=index == 0, style=btn_style),
        html.Button("↓", id={"type": "btn-bias-move-down", "index": tcode}, n_clicks=0,
                    disabled=index >= total - 1, style=btn_style),
    ])


def _bias_order_panel(tcodes: list[str], t: dict):
    if not tcodes:
        return html.P("Add observables above to set analyst order.",
                      style={"color": t["muted"], "fontSize": "10px", "margin": 0})
    return html.Div([
        html.P("BIAS will follow this analyst-set order.",
               style={"color": t["muted"], "fontSize": "10px", "margin": "0 0 6px"}),
        *[_bias_order_row(tcode, idx, len(tcodes), t) for idx, tcode in enumerate(tcodes)],
    ])


def _bias_snapshot_slot(slot: int, t: dict):
    btn_style = {
        "backgroundColor": t["card"],
        "color": t["text"],
        "border": f"1px solid {t['border']}",
        "padding": "3px 8px",
        "fontSize": "10px",
        "cursor": "pointer",
        "fontFamily": "monospace",
    }
    return html.Div(style={
        "border": f"1px solid {t['border']}",
        "backgroundColor": t["inp_bg"],
        "padding": "6px",
        "marginBottom": "6px",
        "borderRadius": "4px",
    }, children=[
        html.Div(style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "4px"}, children=[
            html.Span(f"Snapshot {slot}", style={"color": t["accent"], "fontSize": "11px", "fontWeight": "bold"}),
            html.Div(style={"display": "flex", "gap": "6px"}, children=[
                html.Button("Save", id={"type": "btn-bias-save-snapshot", "index": slot}, n_clicks=0, style=btn_style),
                html.Button("Load", id={"type": "btn-bias-load-snapshot", "index": slot}, n_clicks=0, style=btn_style),
            ]),
        ]),
        html.Div(id={"type": "bias-snapshot-summary", "index": slot},
                 style={"color": t["muted"], "fontSize": "9px", "lineHeight": "1.35"},
                 children="Empty"),
    ])


def _build_notes_list(notes: list, t: dict, can_manage: bool = False) -> html.Div:
    if not notes:
        return html.P("No notes yet.", style={"color": t["muted"], "fontSize": "13px"})
    items = []
    for note in notes:
        author = (note.get("author") or {}).get("username", "unknown")
        items.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        _render_note_body(note.get("body", ""), t),
                        html.Small(
                            f"{author} · Created {_fmt_dt(note.get('created_at'))}"
                            + (f" · Edited {_fmt_dt(note.get('edited_at'))}" if note.get("edited_at") else ""),
                            style={"color": t["muted"]},
                        ),
                    ], width=True),
                    dbc.Col([
                        html.Button(
                            "Edit",
                            id={"type": "btn-edit-note", "index": note.get("id")},
                            n_clicks=0,
                            style={
                                "backgroundColor": "transparent",
                                "border": f"1px solid {t['border']}",
                                "color": t["muted"],
                                "borderRadius": "10px",
                                "fontSize": "10px",
                                "lineHeight": "12px",
                                "padding": "4px 8px",
                                "display": "inline-block" if can_manage else "none",
                                "marginRight": "6px",
                            },
                        ),
                        html.Button(
                            "x",
                            id={"type": "btn-del-note", "index": note.get("id")},
                            n_clicks=0,
                            title="Delete note",
                            style={
                                "backgroundColor": "transparent",
                                "border": f"1px solid {t['border']}",
                                "color": t["muted"],
                                "borderRadius": "10px",
                                "fontSize": "10px",
                                "lineHeight": "12px",
                                "padding": "0 5px",
                                "display": "inline-block" if can_manage else "none",
                            },
                        ),
                    ], width="auto"),
                ], align="start"),
            ], style={"backgroundColor": t["card"], "padding": "10px"}),
            style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "8px"},
        ))
    return html.Div(items)


def _build_entities_list(case_entities: list, t: dict, can_manage: bool = False) -> html.Div:
    if not case_entities:
        return html.P("No entities linked.", style={"color": t["muted"], "fontSize": "13px"})
    items = []
    for ce in case_entities:
        ent = ce.get("entity", {})
        items.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.Small(ent.get("entity_type", "?").upper(),
                                   style={"color": t["muted"], "fontSize": "11px"}),
                        html.Span([
                            dcc.Link(
                                ent.get("value", "—"),
                                href="/relationships",
                                className="entity-relationship-link",
                                style={
                                    "color": t["text"],
                                    "fontWeight": "500",
                                    "textAlign": "left",
                                    "cursor": "pointer",
                                    "textDecoration": "none",
                                },
                            ),
                            html.Span("Entity Relationships", className="entity-relationship-hover"),
                        ], className="entity-relationship-wrap"),
                        html.Small(ent.get("description") or "",
                                   style={"color": t["muted"]}),
                    ], width=True),
                    dbc.Col([
                        dbc.Badge(ce.get("role", "associated"),
                                  style={"backgroundColor": t["orange"]}),
                        html.Button(
                            "x",
                            id={"type": "btn-del-case-entity", "index": ce.get("id")},
                            n_clicks=0,
                            title=f"Remove {ent.get('value', 'entity')} from case",
                            style={
                                "backgroundColor": "transparent",
                                "border": f"1px solid {t['border']}",
                                "color": t["muted"],
                                "borderRadius": "10px",
                                "fontSize": "10px",
                                "lineHeight": "12px",
                                "padding": "0 5px",
                                "display": "inline-block" if can_manage else "none",
                                "marginLeft": "8px",
                            },
                        ),
                    ], width="auto"),
                ], align="center"),
            ], style={"backgroundColor": t["card"], "padding": "8px"}),
            style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "6px"},
        ))
    return html.Div(items)


def _build_attachments_list(attachments: list, t: dict, token: str | None = None,
                            can_manage: bool = False) -> html.Div:
    if not attachments:
        return html.P("No attachments.", style={"color": t["muted"], "fontSize": "13px"})
    items = []
    for att in attachments:
        size_kb = round(att.get("file_size", 0) / 1024, 1)
        att_id = att.get("id")
        open_href = f"/attachment-view/{att_id}?token={quote(token or '')}" if att_id else None
        items.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.Div(att.get("original_filename", "?"),
                                 style={"color": t["text"], "fontSize": "13px"}),
                        html.Small(f"{size_kb} KB · {att.get('mime_type', '')} · "
                                   f"{_fmt_dt(att.get('uploaded_at'))}",
                                   style={"color": t["muted"]}),
                    ], width=True),
                    dbc.Col([
                        html.A(
                            "Open",
                            href=open_href or "#",
                            target="_blank",
                            style={
                                "display": "inline-block",
                                "padding": "0.25rem 0.5rem",
                                "fontSize": "0.875rem",
                                "lineHeight": "1.5",
                                "border": f"1px solid {t['accent']}",
                                "borderRadius": "0.25rem",
                                "color": t["accent"] if open_href else t["muted"],
                                "textDecoration": "none",
                                "pointerEvents": "auto" if open_href else "none",
                                "opacity": "1" if open_href else "0.65",
                                "whiteSpace": "nowrap",
                            },
                        ),
                        html.Button(
                            "x",
                            id={"type": "btn-del-attachment", "index": att_id},
                            n_clicks=0,
                            title=f"Remove {att.get('original_filename', 'attachment')}",
                            style={
                                "backgroundColor": "transparent",
                                "border": f"1px solid {t['border']}",
                                "color": t["muted"],
                                "borderRadius": "10px",
                                "fontSize": "10px",
                                "lineHeight": "12px",
                                "padding": "0 5px",
                                "display": "inline-block" if can_manage else "none",
                                "marginLeft": "8px",
                            },
                        ),
                    ], width="auto"),
                ], align="center"),
            ], style={"backgroundColor": t["card"], "padding": "8px"}),
            style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "6px"},
        ))
    return html.Div(items)


def _build_tasks_list(tasks: list, t: dict, can_manage: bool = False, assign_opts: list | None = None,
                      user: dict | None = None) -> html.Div:
    if not tasks:
        return html.P("No tasks yet.", style={"color": t["muted"], "fontSize": "13px"})
    assign_opts = assign_opts or [{"label": "Unassigned", "value": 0}]
    status_opts = [
        {"label": "Open", "value": "open"},
        {"label": "In Progress", "value": "in_progress"},
        {"label": "Done", "value": "done"},
    ]
    items = []

    def _render_task(task: dict, depth: int = 0):
        status = (task.get("status") or "open").lower()
        status_color = t["green"] if status == "done" else t["orange"] if status == "in_progress" else t["muted"]
        can_assign = can_manage and _task_can_assign(task, user)
        can_create_subtask = can_manage and _task_can_create_subtask(task, user)
        children = _task_children(tasks, task.get("id"))
        subtitle = [
            f"Owner: {task.get('owner_name') or 'Unknown'}",
            f"Assignee: {task.get('assignee_name') or 'Unassigned'}",
        ]
        if task.get("due_date"):
            subtitle.append(f"Due: {_fmt_dt(task.get('due_date'))}")
        if task.get("awaiting_owner_close"):
            subtitle.append("Awaiting owner closure")
        items.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        dcc.Checklist(
                            options=[{"label": "", "value": "done"}],
                            value=["done"] if status == "done" else [],
                            id={"type": "task-check", "index": task.get("id")},
                            inputStyle={"marginRight": "0px"},
                            style={"marginTop": "2px"},
                        ),
                    ], md=1),
                    dbc.Col([
                        html.Div(task.get("text", "—"),
                                 style={"color": t["text"], "fontWeight": "600", "fontSize": "14px"}),
                        html.Small(
                            "  ·  ".join(subtitle),
                            style={"color": t["muted"], "display": "block", "marginTop": "4px"},
                        ),
                    ], md=4),
                    dbc.Col([
                        dcc.Dropdown(
                            status_opts,
                            id={"type": "task-status", "index": task.get("id")},
                            value=status,
                            clearable=False,
                            disabled=not can_manage,
                            style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                        ),
                    ], md=3),
                    dbc.Col([
                        dcc.Dropdown(
                            assign_opts,
                            id={"type": "task-assignee", "index": task.get("id")},
                            value=task.get("assignee_id") or 0,
                            clearable=False,
                            disabled=not can_assign,
                            style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                        ),
                    ], md=3),
                    dbc.Col([
                        html.Span(status.replace("_", " ").upper(), style={
                            "backgroundColor": status_color,
                            "color": "#FFFFFF",
                            "borderRadius": "999px",
                            "padding": "0.3em 0.65em",
                            "fontSize": "11px",
                            "fontWeight": "700",
                            "display": "inline-block",
                            "marginBottom": "6px",
                        }),
                        html.Button(
                            "x",
                            id={"type": "btn-del-task", "index": task.get("id")},
                            n_clicks=0,
                            title=f"Remove task {task.get('text', '')}",
                            style={
                                "backgroundColor": "transparent",
                                "border": f"1px solid {t['border']}",
                                "color": t["muted"],
                                "borderRadius": "10px",
                                "fontSize": "10px",
                                "lineHeight": "12px",
                                "padding": "0 5px",
                                "display": "inline-block" if can_assign else "none",
                                "marginLeft": "8px",
                            },
                        ),
                    ], md=1),
                ], align="center", className="g-2"),
                html.Div([
                    dbc.Row([
                        dbc.Col([
                            dbc.Input(
                                id={"type": "subtask-text-input", "index": task.get("id")},
                                placeholder="Add subtask…",
                                style=_inp_style(t),
                            ),
                        ], md=5),
                        dbc.Col([
                            dcc.Dropdown(
                                assign_opts,
                                id={"type": "subtask-assignee-input", "index": task.get("id")},
                                value=0,
                                clearable=False,
                                style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                            ),
                        ], md=3),
                        dbc.Col([
                            _btn_primary(
                                "Add Subtask",
                                {"type": "btn-add-subtask", "index": task.get("id")},
                                t,
                                size="sm",
                                className="w-100",
                                style={"minWidth": "0"},
                            ),
                        ], md=4),
                    ], className="g-2 mt-2"),
                ], id={"type": "subtask-form", "index": task.get("id")}, style={"display": "block" if can_create_subtask else "none"}),
                html.Div(id={"type": "subtask-feedback", "index": task.get("id")}, className="mt-2"),
            ], style={"backgroundColor": t["card"], "padding": "10px"}),
            style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "8px", "marginLeft": f"{depth * 18}px"},
        ))
        for child in children:
            _render_task(child, depth + 1)

    for task in _task_children(tasks, None):
        _render_task(task, 0)
    return html.Div(items)

def _get_cyto_stylesheet(t: dict) -> list:
    PURPLE = t.get("purple", "#9575cd")
    ACCENT = t.get("accent", "#0097a7")
    ORANGE = t.get("orange", "#f57c00")
    BG     = t.get("card", "#1e1e2f")
    DIM    = t.get("muted", "#888888")
    GREEN  = t.get("green", "#4caf50")
    RED    = t.get("red", "#f44336")
    BLUE   = "#2196F3"
    YELLOW = "#FFE082"
    GAP_ORANGE = "#FB8C00"
    SOFT_GREEN = "#81C784"
    SOFT_ORANGE = "#FFB74D"
    SOFT_RED = "#E57373"
    SOFT_PURPLE = "#9575CD"
    
    return [
        {"selector": ".waypoint", "style": {"shape": "ellipse", "width": "4px", "height": "4px", "background-color": PURPLE, "border-width": 0, "events": "no"}},
        {"selector": ".edge-drop", "style": {"line-color": PURPLE, "target-arrow-color": PURPLE, "target-arrow-shape": "triangle", "line-style": "dashed", "width": 2.2, "curve-style": "taxi", "taxi-direction": "vertical"}},
        {"selector": ".edge-hop", "style": {"line-color": PURPLE, "target-arrow-color": PURPLE, "target-arrow-shape": "triangle", "line-style": "dashed", "width": 2.2, "curve-style": "straight"}},
        {"selector": "node", "style": {"label": "data(label)", "text-wrap": "wrap", "text-valign": "center", "text-halign": "center", "font-family": "monospace", "font-size": "10px", "color": "#fff"}},
        {"selector": ".confirmed", "style": {"background-color": "#0097a7", "shape": "rectangle", "width": "130px", "height": "44px", "border-width": 2, "border-color": ACCENT, "font-size": "11px", "font-weight": "bold"}},
        {"selector": ".gap-anchor", "style": {"border-width": 3, "border-color": ORANGE, "border-style": "dashed"}},
        {"selector": ".bridge", "style": {"background-color": "#bf360c", "shape": "diamond", "width": "110px", "height": "50px", "border-width": 1, "border-color": ORANGE, "font-size": "10px"}},
        {"selector": ".chain", "style": {"background-color": "#311b92", "shape": "hexagon", "width": "116px", "height": "50px", "border-width": 2, "border-color": PURPLE, "border-style": "dashed", "font-size": "10px"}},
        {"selector": ".edge-chain", "style": {"line-color": SOFT_PURPLE, "target-arrow-color": SOFT_PURPLE, "line-style": "dashed", "line-opacity": 0.78, "width": 1.8, "curve-style": "straight"}},
        {"selector": ".chain-group", "style": {"background-color": "rgba(49, 27, 146, 0.15)", "shape": "roundrectangle", "border-width": 2, "border-color": PURPLE, "border-style": "solid", "label": "data(label)", "text-valign": "bottom", "text-halign": "center", "font-size": "9px", "color": PURPLE, "font-family": "monospace", "text-background-color": BG, "text-background-opacity": 0.90, "text-background-padding": "3px", "padding": "18px"}},
        {"selector": "edge", "style": {"curve-style": "straight", "target-arrow-shape": "triangle", "arrow-scale": 1.0, "line-color": "#444466", "target-arrow-color": "#444466", "width": 1.5, "label": "data(score_label)", "font-size": "8px", "color": DIM, "font-family": "monospace", "text-background-color": BG, "text-background-opacity": 0.72, "text-background-padding": "1px"}},
        {"selector": ".edge-strong-direct", "style": {"line-color": BLUE, "target-arrow-color": BLUE, "width": 2.8, "line-style": "solid", "color": BLUE, "font-weight": "bold"}},
        {"selector": ".edge-weak-direct", "style": {"line-color": BLUE, "target-arrow-color": BLUE, "width": 2.0, "line-style": "dotted", "line-opacity": 0.88, "color": BLUE}},
        {"selector": ".edge-indeterminate", "style": {"line-color": YELLOW, "target-arrow-color": YELLOW, "width": 1.8, "line-style": "dotted", "line-opacity": 0.95, "color": YELLOW}},
        {"selector": ".edge-weak-gap", "style": {"line-color": GAP_ORANGE, "target-arrow-color": GAP_ORANGE, "width": 2.6, "line-style": "dotted", "line-opacity": 1.0, "color": GAP_ORANGE}},
        {"selector": ".edge-strong-gap", "style": {"line-color": RED, "target-arrow-color": RED, "width": 3.0, "line-style": "solid", "color": RED, "font-weight": "bold"}},
        {"selector": ".edge-high",  "style": {"line-color": SOFT_GREEN,  "target-arrow-color": SOFT_GREEN,  "line-opacity": 0.75, "width": 1.8}},
        {"selector": ".edge-med",   "style": {"line-color": SOFT_ORANGE, "target-arrow-color": SOFT_ORANGE, "line-opacity": 0.72, "width": 1.6}},
        {"selector": ".edge-low",   "style": {"line-color": SOFT_RED,    "target-arrow-color": SOFT_RED,    "line-opacity": 0.70, "width": 1.4}},
        {"selector": ":selected", "style": {"border-width": 4, "border-color": "#fff", "border-opacity": 1}},
    ]


def _command_preview(command: str | None, max_len: int = 160) -> str:
    text = " ".join(str(command or "").strip().split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _action_list(actions, t: dict):
    if not actions:
        return html.P("No CALDERA actions mapped to this technique.", style={"color": t["muted"], "margin": 0, "fontSize": "12px"})

    cards = []
    for action in actions:
        meta = " / ".join(x for x in [action.get("platform"), action.get("executor"), action.get("tactic")] if x)
        cards.append(
            dbc.Card(
                dbc.CardBody([
                    html.Div([
                        html.Span(action.get("name") or action.get("technique_id") or "CALDERA Ability", style={"color": t["accent"], "fontWeight": "700", "fontSize": "12px"}),
                        html.Span(action.get("ability_id") or "", style={"color": t["muted"], "fontSize": "10px", "marginLeft": "auto"}),
                    ], style={"display": "flex", "gap": "8px", "alignItems": "baseline", "marginBottom": "6px"}),
                    html.P(meta or "Executor metadata unavailable.", style={"color": t["muted"], "fontSize": "11px", "margin": "0 0 6px"}),
                    html.Pre(
                        _command_preview(action.get("command")) or "(No command body recorded)",
                        style={
                            "backgroundColor": t["bg2"],
                            "border": f"1px solid {t['border']}",
                            "borderRadius": "6px",
                            "color": t["text"],
                            "fontSize": "11px",
                            "margin": "0 0 6px",
                            "padding": "8px",
                            "whiteSpace": "pre-wrap",
                            "wordBreak": "break-word",
                        },
                    ),
                    html.P(
                        f"Cleanup: {_command_preview(action.get('cleanup'))}" if action.get("cleanup") else "Cleanup: none recorded",
                        style={"color": t["muted"], "fontSize": "11px", "margin": 0},
                    ),
                ], style={"backgroundColor": t["card"], "padding": "10px"}),
                style={"backgroundColor": t["card"], "borderColor": t["border"], "marginTop": "8px"},
            )
        )
    return html.Div(cards)


def _build_caldera_action_deck(case: dict, t: dict) -> html.Div | None:
    bias_json = case.get("bias_result_json")
    if not bias_json:
        return None
    try:
        result = json.loads(bias_json)
    except Exception:
        return None

    technique_map = ((result.get("caldera") or {}).get("techniques") or {})
    if not technique_map:
        return None

    observable_cards = []
    for obs in case.get("observables", []) or []:
        tcode = str(obs.get("tcode") or "").strip().upper()
        actions = technique_map.get(tcode) or []
        if not actions:
            continue
        observable_cards.append(
            dbc.Card(
                dbc.CardBody([
                    html.Div([
                        html.Span(tcode, style={"color": t["accent"], "fontWeight": "700", "fontSize": "14px", "marginRight": "8px"}),
                        html.Span(obs.get("tname") or "", style={"color": t["text"], "fontSize": "12px"}),
                        html.Span(f"{len(actions)} action(s)", style={"color": t["muted"], "fontSize": "11px", "marginLeft": "auto"}),
                    ], style={"display": "flex", "gap": "6px", "alignItems": "baseline", "marginBottom": "8px"}),
                    _action_list(actions[:2], t),
                ], style={"backgroundColor": t["card"]}),
                style={"backgroundColor": t["card"], "borderColor": t["border"]},
            )
        )

    bridge_cards = []
    seen_bridges = set()
    for gap in result.get("gaps", []) or []:
        for candidate in gap.get("candidates", []) or []:
            tcode = str(candidate.get("tcode") or "").strip().upper()
            if not tcode or tcode in seen_bridges:
                continue
            seen_bridges.add(tcode)
            actions = candidate.get("caldera_actions") or technique_map.get(tcode) or []
            if not actions:
                continue
            bridge_cards.append(
                dbc.Card(
                    dbc.CardBody([
                        html.Div([
                            html.Span(tcode, style={"color": t["orange"], "fontWeight": "700", "fontSize": "14px", "marginRight": "8px"}),
                            html.Span(candidate.get("tname") or candidate.get("name") or "", style={"color": t["text"], "fontSize": "12px"}),
                            html.Span(f"{gap.get('from_tcode', '?')} -> {gap.get('to_tcode', '?')}", style={"color": t["muted"], "fontSize": "11px", "marginLeft": "auto"}),
                        ], style={"display": "flex", "gap": "6px", "alignItems": "baseline", "marginBottom": "8px"}),
                        _action_list(actions[:2], t),
                    ], style={"backgroundColor": t["card"]}),
                    style={"backgroundColor": t["card"], "borderColor": t["border"]},
                )
            )

    if not observable_cards and not bridge_cards:
        return None

    children = [
        html.H6("CALDERA Action Deck", style={"color": t.get("accent"), "marginBottom": "10px", "marginTop": "14px"}),
        html.P(
            "Matched CALDERA abilities for the current observables and BIAS bridge candidates, keyed by ATT&CK technique id.",
            style={"color": t["muted"], "fontSize": "12px", "marginBottom": "10px"},
        ),
    ]
    if observable_cards:
        children.append(
            html.Div([
                html.Div("Observable Actions", style={"color": t["accent"], "fontWeight": "700", "marginBottom": "8px"}),
                html.Div(observable_cards, style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(280px, 1fr))", "gap": "10px"}),
            ], style={"marginBottom": "12px"})
        )
    if bridge_cards:
        children.append(
            html.Div([
                html.Div("Bridge Candidate Actions", style={"color": t["orange"], "fontWeight": "700", "marginBottom": "8px"}),
                html.Div(bridge_cards, style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(280px, 1fr))", "gap": "10px"}),
            ])
        )
    return html.Div(children)

def _build_bias_display(case: dict, t: dict, can_edit_case: bool = False) -> html.Div:
        import json
        import re
        bias_json = case.get("bias_result_json")
        if not bias_json:
            return html.P("No BIAS results.")

        try:
            result = json.loads(bias_json)
            gaps = result.get("gaps", [])
            observables = case.get("observables", [])
            caldera_map = ((result.get("caldera") or {}).get("techniques") or {})

            elements = []
            obs_coords = {}
            obs_aliases = {}
            obs_ids = []
            x_step = 360
            bridge_row_gap = 96
            chain_row_gap = 110
            top_base_y = -150
            bottom_base_y = 170

            def _canon_tcode(value):
                tcode = str(value or "").strip().upper()
                if not tcode:
                    return ""
                match = re.search(r"T\d{4}(?:\.\d{3})?", tcode)
                if not match:
                    return ""
                return match.group(0).split(".")[0]

            def _normalize_tcode(value):
                tcode = str(value or "").strip().upper()
                if not tcode:
                    return ""
                match = re.search(r"T\d{4}(?:\.\d{3})?", tcode)
                return match.group(0) if match else ""

            def _resolve_obs_id(value):
                raw = _normalize_tcode(value)
                if not raw:
                    return None
                if raw in obs_coords:
                    return raw
                return obs_aliases.get(_canon_tcode(raw))

            def _edge_class(conn_class):
                mapping = {
                    "direct_connection": "edge-strong-direct",
                    "weak_direct": "edge-weak-direct",
                    "indeterminate": "edge-indeterminate",
                    "weak_gap": "edge-weak-gap",
                    "strong_gap": "edge-strong-gap",
                }
                return mapping.get(conn_class, "edge-indeterminate")

            for i, obs in enumerate(observables):
                raw_tcode = obs.get("tcode") or ""
                tcode = _normalize_tcode(raw_tcode)
                if not tcode:
                    continue
                node_id = f"obs_{i}_{tcode}"
                x_pos = i * x_step
                obs_coords[node_id] = x_pos
                obs_ids.append(node_id)
                obs_aliases.setdefault(_canon_tcode(tcode), node_id)
                label = tcode
                if obs.get("tname"):
                    label = f"{tcode}\n{obs.get('tname')}"
                caldera_actions = caldera_map.get(tcode) or []
                elements.append({
                    "data": {
                        "id": node_id,
                        "label": label,
                        "tcode": tcode,
                        "fullname": obs.get("tname") or tcode,
                        "caldera_actions": caldera_actions,
                        "caldera_count": len(caldera_actions),
                    },
                    "classes": "confirmed",
                    "position": {"x": x_pos, "y": 0},
                })

            max_bridge_rows = 0
            max_chain_rows = 0
            anchor_ids = set()

            gap_num = 0
            for i, gap in enumerate(gaps):
                if i >= len(obs_ids) - 1:
                    continue

                src = obs_ids[i]
                tgt = obs_ids[i + 1]

                if src not in obs_coords or tgt not in obs_coords:
                    continue

                x_src = obs_coords[src]
                x_tgt = obs_coords[tgt]
                x_mid = (x_src + x_tgt) / 2
                anchor_ids.update([src, tgt])
                tactic_distance = int(gap.get("tactic_distance", 0) or 0)
                is_gap = bool(gap.get("is_gap", False))

                candidates = gap.get("candidates", []) if isinstance(gap.get("candidates", []), list) else []
                candidates = candidates[:3]
                raw_chains = gap.get("chains") or gap.get("bridges", {}).get("chains") or gap.get("solutions", [])
                chains_list = raw_chains[:1] if isinstance(raw_chains, list) else []
                if is_gap:
                    gap_num += 1

                conn_class = _bias_connection_class(gap)
                conn_label = _bias_connection_label(gap)
                edge_label = f"Gap {gap_num}" if is_gap else conn_label
                edge_class = _edge_class(conn_class)
                elements.append({
                    "data": {
                        "id": f"gap_{i}",
                        "source": src,
                        "target": tgt,
                        "score_label": edge_label,
                        "description": f"Relationship: {conn_label}. Tactic Distance: {tactic_distance}",
                    },
                    "classes": edge_class,
                })

                if not is_gap:
                    continue

                max_bridge_rows = max(max_bridge_rows, len(candidates))
                for r, cand in enumerate(candidates):
                    cand_tcode = (cand.get("tcode") or "Unknown").strip()
                    score = float(cand.get("plausibility_score", cand.get("score", 0)) or 0)
                    support_score = float(cand.get("support_score", score) or 0)
                    possibility_score = float(cand.get("possibility_score", 0) or 0)
                    downstream_score = float(cand.get("downstream_coherence_score", 0) or 0)
                    nid = f"br_{i}_{r}_{cand_tcode}"
                    tactics = ", ".join(cand.get("tactics", [])) or "---"
                    platforms = ", ".join(cand.get("platforms", [])) or "---"
                    conf = cand.get("confidence_label", "")
                    reasons = cand.get("reasons") or "---"
                    support_reasons = cand.get("support_reasons") or reasons
                    caldera_actions = cand.get("caldera_actions") or caldera_map.get(cand_tcode) or []
                    elements.append({
                        "data": {
                            "id": nid,
                            "label": cand_tcode,
                            "tcode": cand_tcode,
                            "ntype": "bridge",
                            "score": score,
                            "plausibility_score": score,
                            "support_score": support_score,
                            "possibility_score": possibility_score,
                            "downstream_coherence_score": downstream_score,
                            "fullname": cand.get("tname") or cand_tcode,
                            "rank": r + 1,
                            "gap_num": i + 1,
                            "gap_from": gap.get("from_tcode", "?"),
                            "gap_to": gap.get("to_tcode", "?"),
                            "reasons": reasons,
                            "support_reasons": support_reasons,
                            "conf": conf,
                            "tactics": tactics,
                            "platforms": platforms,
                            "caldera_actions": caldera_actions,
                            "caldera_count": len(caldera_actions),
                        },
                        "classes": "bridge",
                        "position": {"x": x_mid, "y": top_base_y - (r * bridge_row_gap)},
                    })
                    edge_class = "edge-high" if score >= 0.60 else "edge-med" if score >= 0.38 else "edge-low"
                    elements.append({"data": {"id": f"edge_{src}_{nid}", "source": src, "target": nid}, "classes": edge_class})
                    elements.append({"data": {"id": f"edge_{nid}_{tgt}", "source": nid, "target": tgt}, "classes": edge_class})

                show_dependent_chain = conn_class == "strong_gap"
                if show_dependent_chain:
                    max_chain_rows = max(max_chain_rows, len(chains_list))

                for c_idx, chain in enumerate(chains_list if show_dependent_chain else []):
                    path = chain.get("path")
                    if not isinstance(path, list) or not path:
                        continue
                    hop_details = chain.get("hops", []) if isinstance(chain.get("hops", []), list) else []
                    path_str = " -> ".join(path)

                    group_id = f"group_{i}_{c_idx}"
                    group_y = bottom_base_y + (c_idx * chain_row_gap)

                    prev_id = src
                    for h_idx, hop_tcode in enumerate(path):
                        hop_tcode = str(hop_tcode).strip()
                        if not hop_tcode:
                            continue
                        h_id = f"hop_{i}_{c_idx}_{h_idx}_{hop_tcode}"
                        progress = (h_idx + 1) / (len(path) + 1)
                        h_x = x_src + ((x_tgt - x_src) * progress)
                        hop_meta = hop_details[h_idx] if h_idx < len(hop_details) else {}
                        tactics = ", ".join(hop_meta.get("tactics", [])) or "---"
                        platforms = ", ".join(hop_meta.get("platforms", [])) or "---"
                        fullname = hop_meta.get("tname") or hop_tcode
                        caldera_actions = hop_meta.get("caldera_actions") or caldera_map.get(hop_tcode) or []

                        elements.append({
                            "data": {
                                "id": h_id,
                                "label": hop_tcode,
                                "tcode": hop_tcode,
                                "ntype": "chain",
                                "score": float(chain.get("score", 0) or 0),
                                "fullname": fullname,
                                "gap_num": i + 1,
                                "gap_from": gap.get("from_tcode", "?"),
                                "gap_to": gap.get("to_tcode", "?"),
                                "hop_index": h_idx + 1,
                                "hop_total": len(path),
                                "full_path": path_str,
                                "tactics": tactics,
                                "platforms": platforms,
                                "caldera_actions": caldera_actions,
                                "caldera_count": len(caldera_actions),
                            },
                            "classes": "chain",
                            "position": {"x": h_x, "y": group_y},
                        })
                        elements.append({
                            "data": {"id": f"edge_{prev_id}_{h_id}", "source": prev_id, "target": h_id},
                            "classes": "edge-chain",
                        })
                        prev_id = h_id

                    elements.append({
                        "data": {"id": f"edge_{prev_id}_{tgt}", "source": prev_id, "target": tgt},
                        "classes": "edge-chain",
                    })

            for element in elements:
                data = element.get("data", {})
                if data.get("id") in anchor_ids and element.get("classes") == "confirmed":
                    data["ntype"] = "confirmed"
                    data["is_anchor"] = True

            graph_height = 320 + (max_bridge_rows * 64) + (max_chain_rows * 72)
            graph_height = max(400, min(graph_height, 760))

            if not elements:
                return html.P("BIAS returned no graphable data.", style={"color": t["muted"]})

            summary = []
            if observables:
                summary.append(f"{len(obs_coords)} observable(s)")
            actual_gaps = sum(1 for gap in gaps if gap.get("is_gap", False))
            if actual_gaps:
                summary.append(f"{actual_gaps} gap(s)")
            if not actual_gaps:
                summary.append("no gaps identified")

            pivots = _recommended_pivots(case, t, can_edit_case=can_edit_case)
            caldera_deck = _build_caldera_action_deck(case, t)
            return html.Div([
                html.Div([
                    html.H6("Attack Path Analysis", style={"color": t.get("accent"), "marginBottom": "4px"}),
                    html.Small(" | ".join(summary), style={"color": t.get("muted")}),
                ], style={"marginBottom": "10px"}),
                _bias_legend(t),
                cyto.Cytoscape(
                    id="cyto-final",
                    elements=elements,
                    layout={"name": "preset", "fit": True, "padding": 90, "animate": False},
                    style={"width": "100%", "height": f"{graph_height}px", "backgroundColor": t.get("card"),
                           "border": f"1px solid {t.get('border', '#30363D')}",
                           "borderRadius": "8px"},
                    stylesheet=_get_cyto_stylesheet(t),
                    autoRefreshLayout=True,
                    userZoomingEnabled=True,
                    userPanningEnabled=True,
                    minZoom=0.15,
                    maxZoom=3.0,
                ),
                html.Div(
                    id="case-bias-detail-panel",
                    children=html.P("Click a graph node to inspect.", style={"color": t["muted"], "margin": 0}),
                    style={"marginTop": "12px", "padding": "12px", "backgroundColor": t["card"],
                           "border": f"1px solid {t['border']}", "borderRadius": "8px"},
                ),
                *([caldera_deck] if caldera_deck else []),
                *( [pivots] if pivots else [] ),
            ])
        except Exception as e:
            return html.P(f"Layout Error: {str(e)}", style={"color": "red"})


def _pivot_reason_text(cand: dict) -> str:
    reasons = cand.get("support_reasons") or cand.get("reasons") or []
    if isinstance(reasons, str):
        return reasons
    if isinstance(reasons, list) and reasons:
        return " · ".join(str(r) for r in reasons[:2])
    return "Strong bridge fit from current observable path."


def _pivot_check_text(cand: dict, gap: dict) -> str:
    tcode = str(cand.get("tcode") or "").upper()
    if tcode.startswith("T1078"):
        return "Check successful logons, remote authentication, and account-use artifacts."
    if tcode.startswith("T1071"):
        return "Check outbound control traffic, protocol use, and staging behavior."
    if tcode.startswith("T1105"):
        return "Check payload delivery, tool transfer, and staging artifacts."
    if tcode.startswith("T1059"):
        return "Check interpreter execution, parent-child process chains, and command history."
    if tcode.startswith("T1550"):
        return "Check alternate-auth use, token material, and remote auth pivots."
    return f"Check evidence that would confirm or disprove {cand.get('tcode', 'this bridge')} between {gap.get('from_tcode', '?')} and {gap.get('to_tcode', '?')}."


def _recommended_pivots(case: dict, t: dict, can_edit_case: bool) -> html.Div | None:
    pivots = _extract_recommended_pivots(case)
    if not pivots:
        return None

    cards = []
    for idx, (gap, cand) in enumerate(pivots):
        bridge = str(cand.get("tcode") or "?")
        bridge_name = cand.get("tname") or cand.get("name") or bridge
        reason = _pivot_reason_text(cand)
        check = _pivot_check_text(cand, gap)
        score = float(cand.get("plausibility_score", cand.get("score", 0)) or 0)
        cards.append(
            dbc.Card(
                dbc.CardBody([
                    html.Div([
                        html.Span(bridge, style={"color": t["orange"], "fontWeight": "bold", "fontSize": "14px", "marginRight": "8px"}),
                        html.Span(bridge_name, style={"color": t["text"], "fontSize": "12px"}),
                        html.Span(f"{score * 100:.3f}%", style={"color": t["muted"], "fontSize": "11px", "marginLeft": "auto"}),
                    ], style={"display": "flex", "alignItems": "baseline", "gap": "6px", "marginBottom": "6px"}),
                    html.Div(f"Why: {reason}", style={"color": t["muted"], "fontSize": "12px", "marginBottom": "4px"}),
                    html.Div(f"Check: {check}", style={"color": t["muted"], "fontSize": "12px", "marginBottom": "10px"}),
                    html.Div([
                        _btn_primary(
                            "Add as Task",
                            {"type": "btn-bias-pivot-task", "index": idx},
                            n_clicks=0,
                            size="sm",
                            disabled=not can_edit_case,
                            style={"marginRight": "8px"},
                        ),
                        _btn_primary(
                            "Add to Notes",
                            {"type": "btn-bias-pivot-note", "index": idx},
                            n_clicks=0,
                            size="sm",
                            disabled=not can_edit_case,
                            style={},
                        ),
                    ]),
                ], style={"backgroundColor": t["card"]}),
                style={"backgroundColor": t["card"], "borderColor": t["border"]},
            )
        )

    return html.Div([
        html.H6("Recommended Next Pivots", style={"color": t.get("accent"), "marginBottom": "10px", "marginTop": "14px"}),
        html.Div(cards, style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(260px, 1fr))", "gap": "10px"}),
    ])


def _extract_recommended_pivots(case: dict) -> list[tuple[dict, dict]]:
    bias_json = case.get("bias_result_json")
    if not bias_json:
        return []
    try:
        result = json.loads(bias_json)
    except Exception:
        return []

    pivots: list[tuple[dict, dict]] = []
    for gap in result.get("gaps", []):
        if not gap.get("is_gap"):
            continue
        candidates = gap.get("candidates", []) or []
        if not candidates:
            continue
        pivots.append((gap, candidates[0]))
        if len(pivots) >= 3:
            break
    return pivots


def _build_bias_detail_panel(data, t: dict):
    if not data:
        return html.P("Click a graph node to inspect.", style={"color": t["muted"], "margin": 0})

    ntype = data.get("ntype", "unknown")
    color = {
        "confirmed": t["accent"],
        "bridge": t["orange"],
        "chain": t["purple"],
    }.get(ntype, t["text"])
    type_label = {
        "confirmed": "CONFIRMED OBSERVABLE",
        "bridge": "SINGLE BRIDGE CANDIDATE",
        "chain": "A* MULTI-HOP NODE",
    }.get(ntype, ntype.upper())

    rows = [
        html.Div([
            html.Span(data.get("tcode", data.get("label", "UNKNOWN")),
                      style={"color": color, "fontSize": "16px", "fontWeight": "bold", "marginRight": "10px"}),
            html.Span(data.get("fullname", ""), style={"color": t["text"], "fontSize": "12px"}),
            html.Span(f"[{type_label}]",
                      style={"color": t["muted"], "fontSize": "10px", "float": "right"}),
        ], style={"marginBottom": "8px"}),
        html.Hr(style={"borderColor": t["border"], "margin": "6px 0"}),
    ]
    caldera_actions = data.get("caldera_actions") or []

    if ntype == "bridge":
        score = float(data.get("plausibility_score", data.get("score", 0)) or 0)
        support_score = float(data.get("support_score", score) or 0)
        possibility_score = float(data.get("possibility_score", 0) or 0)
        downstream_score = float(data.get("downstream_coherence_score", 0) or 0)
        conf = data.get("conf", "")
        reasons = data.get("reasons", "---")
        rows.extend([
            html.P(f"Score: {score * 100:.3f}% ({score:.5f}) {f'[{conf}]' if conf else ''}", style={"color": color, "margin": "0 0 6px"}),
            html.P(
                f"Plausibility: {score:.5f}  |  Support: {support_score:.5f}  |  Possibility: {possibility_score:.5f}  |  Downstream: {downstream_score:.5f}",
                style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"},
            ),
            html.P(f"Gap {data.get('gap_num', '?')}: {data.get('gap_from', '?')} -> {data.get('gap_to', '?')}",
                   style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"}),
            html.P(f"Rank #{data.get('rank', '?')} of candidates", style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"}),
            html.P(f"Signals: {reasons}", style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"}),
            html.P("Bridge lane — stacked above spine at gap midpoint", style={"color": t["orange"], "margin": "0 0 4px", "fontSize": "12px", "fontStyle": "italic"}),
            html.P(f"Tactics: {data.get('tactics', '---')}", style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"}),
            html.P(f"Platforms: {data.get('platforms', '---')}", style={"color": t["muted"], "margin": 0, "fontSize": "12px"}),
        ])
    elif ntype == "chain":
        score = float(data.get("score", 0) or 0)
        rows.extend([
            html.P(f"Chain avg: {score:.2f}", style={"color": color, "margin": "0 0 6px"}),
            html.P(f"Gap {data.get('gap_num', '?')}: {data.get('gap_from', '?')} -> {data.get('gap_to', '?')}",
                   style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"}),
            html.P(f"Hop {data.get('hop_index', '?')} of {data.get('hop_total', '?')}",
                   style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"}),
            html.P(f"Full path: {data.get('full_path', '---')}", style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"}),
            html.P("Multi-hop lane — inside A* path compound container", style={"color": t["purple"], "margin": "0 0 4px", "fontSize": "12px", "fontStyle": "italic"}),
            html.P(f"Tactics: {data.get('tactics', '---')}", style={"color": t["muted"], "margin": "0 0 4px", "fontSize": "12px"}),
            html.P(f"Platforms: {data.get('platforms', '---')}", style={"color": t["muted"], "margin": 0, "fontSize": "12px"}),
        ])
    elif ntype == "confirmed":
        rows.append(
            html.Div([
                html.Span("Confirmed observable — confidence 100%", style={"color": t["green"], "marginRight": "14px"}),
                html.Span("Gap anchor — bridges and multi-hop paths connect here" if data.get("is_anchor") else "",
                          style={"color": t["orange"]}),
            ], style={"fontSize": "12px"})
        )
    else:
        rows.append(html.P(data.get("description", "No details available."),
                           style={"color": t["muted"], "margin": 0, "fontSize": "12px"}))

    rows.extend([
        html.Hr(style={"borderColor": t["border"], "margin": "10px 0 8px"}),
        html.Div(
            f"CALDERA actions: {len(caldera_actions)} matched"
            if caldera_actions else
            "CALDERA actions: none mapped for this technique",
            style={"color": t["accent"], "fontSize": "12px", "fontWeight": "700", "marginBottom": "6px"},
        ),
        _action_list(caldera_actions[:3], t),
    ])

    return rows
    
def _case_detail_page(user: dict, case_id: int, t: dict) -> dbc.Container:
    token = user.get("token", "")
    can_manage = _can_manage_case(user)
    can_assign = _can_assign_case(user)
    case = _api(f"/api/cases/{case_id}", token=token)
    if can_manage and case:
        locked_case = _api(f"/api/cases/{case_id}/lock", method="POST", token=token)
        if locked_case:
            case = locked_case
    if not case:
        return dbc.Container([_nav(user, t),
                              html.P("Case not found.", style={"color": t["red"]})],
                             fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh"})

    notes = _api(f"/api/cases/{case_id}/notes", token=token) or []
    observables = case.get("observables", [])
    case_entities = case.get("case_entities", [])
    attachments = case.get("attachments", [])
    tasks = _parse_case_tasks(case.get("checklist_json"), case.get("owner"))
    analysis = _parse_analysis(case.get("analysis_json"))
    analysis_when_date, analysis_when_time = _split_datetime_local(analysis.get("when"))
    sev = (case.get("severity") or "UNKNOWN").upper()
    sta = (case.get("status") or "UNKNOWN").replace("_", " ").upper()
    owner_name = (case.get("owner") or {}).get("username") or "Unassigned"
    note_count = len(notes)
    observable_count = len(observables)
    entity_count = len(case_entities)
    attachment_count = len(attachments)
    task_count = len(tasks)
    all_cases = _api("/api/cases", token=token) or []
    relationship_map, _rel_elements, _strongest_case_id = _build_relationship_data(all_cases)
    related_cases = sorted(
        (relationship_map.get(str(case_id)) or {}).get("relationships", []),
        key=lambda rel: (-rel.get("shared_count", 0), -rel.get("probability", 0)),
    )[:3]
    bias_is_stale = _bias_is_stale(case)
    bias_status = "Ready"
    if bias_is_stale and observable_count >= 2:
        bias_status = "Stale"
    elif case.get("bias_result_json"):
        bias_status = "Complete"
    elif observable_count >= 2:
        bias_status = "Pending Run"
    else:
        bias_status = "Need 2+ Tcodes"
    locked_by_other, _locked_by = _case_lock_state(case, user)
    can_edit_case = can_manage and not locked_by_other
    assignable_users = _api("/api/auth/assignable-users", token=token) if can_manage else []
    assign_opts = [{"label": "Unassigned", "value": 0}] + [
        {"label": f"{u.get('username')} ({u.get('role')})", "value": u.get("id")}
        for u in (assignable_users or [])
    ]

    entity_type_options = ["ip_address", "hostname", "domain", "email",
                           "url", "user_account", "file_hash", "other"]

    status_opts  = ["open", "in_progress", "resolved", "closed"]
    severity_opts = ["low", "medium", "high", "critical"]

    return dbc.Container([
        _nav(user, t),
        html.Div(id="case-lock-banner", children=_build_case_lock_banner(case, user, t)),
        # ── Header ──────────────────────────────────────────────────────
        html.Div([
            _card([
                dbc.Row([
                    dbc.Col([
                        html.Div(case.get("title", "—"),
                                 style={"color": t["text"], "marginBottom": "4px", "fontSize": "18px", "fontWeight": "600", "lineHeight": "1.2"}),
                        html.Div(case.get("description") or "No case description yet.",
                                 style={"color": t["muted"], "marginBottom": "8px", "fontSize": "12px", "lineHeight": "1.3"}),
                        html.Div(case.get("case_number", "—"),
                                 style={"color": t["accent"], "marginBottom": "8px", "fontSize": "12px", "lineHeight": "1.3"}),
                        html.Div([
                            html.Span(sev, style=_sev_badge_style(sev, t)),
                            dbc.Badge(sta, style={"backgroundColor": _status_color(sta, t)}),
                            html.Span([
                                html.Span("Assigned: ", style={"color": t["muted"], "marginRight": "4px"}),
                                dbc.Badge(owner_name, style={"backgroundColor": t["orange"] if owner_name != "Unassigned" else t["muted"]}),
                            ]),
                            dbc.Badge(
                                f"BIAS {bias_status}",
                                style={"backgroundColor": t["orange"] if bias_status == "Stale" else t["green"] if bias_status == "Complete" else t["orange"] if bias_status == "Pending Run" else t["muted"]},
                            ),
                        ], style={"display": "flex", "flexWrap": "wrap", "gap": "8px", "alignItems": "center", "marginBottom": "8px"}),
                        html.Div([
                            html.Small(f"Opened {_fmt_dt(case.get('created_at'))}", style={"color": t["muted"]}),
                            html.Small(f"Updated {_fmt_dt(case.get('updated_at'))}", style={"color": t["muted"]}),
                            html.Small(
                                f"Lock: {(_locked_by or 'none') if case.get('lock_active') else 'available'}",
                                style={"color": t["muted"]},
                            ),
                        ], style={"display": "flex", "flexWrap": "wrap", "gap": "12px"}),
                        html.Div(
                            [
                                html.Small("Related Cases:", style={"color": t["muted"], "marginRight": "6px"}),
                                *(
                                    [
                                        dcc.Link(
                                            f"{rel.get('case_number')} ({rel.get('shared_count', 0)} shared)",
                                            href=f"/case/{rel.get('case_id')}",
                                            style={
                                                "color": t["orange"],
                                                "textDecoration": "none",
                                                "fontSize": "12px",
                                                "marginRight": "10px",
                                            },
                                        )
                                        for rel in related_cases
                                    ]
                                    or [html.Small("No related cases yet.", style={"color": t["muted"]})]
                                ),
                            ],
                            style={"display": "flex", "flexWrap": "wrap", "alignItems": "center", "gap": "4px", "marginTop": "8px"},
                        ),
                    ], md=7),
                    dbc.Col([
                        dbc.ButtonGroup([
                            dbc.Button("Edit", id="btn-case-edit-toggle", size="sm", outline=True,
                                       disabled=not can_edit_case,
                                       style={"borderColor": t["accent"], "color": t["accent"]}),
                            dbc.Button("PDF", id="btn-export-pdf", size="sm", outline=True,
                                       style={"borderColor": t["accent"], "color": t["accent"]}),
                            dbc.Button("DOCX", id="btn-export-docx", size="sm", outline=True,
                                       style={"borderColor": t["accent"], "color": t["accent"]}),
                            dbc.Button("Delete", id="btn-case-delete", size="sm", outline=True,
                                       disabled=not can_edit_case,
                                       style={"borderColor": t["red"], "color": t["red"]}),
                        ], className="mb-2", size="sm"),
                        dbc.Row([
                            dbc.Col(_summary_chip("Observables", str(observable_count), t, t["purple"], "#case-observables"), md=4, className="mb-1"),
                            dbc.Col(_summary_chip("Entities", str(entity_count), t, t["orange"], "#case-entities"), md=4, className="mb-1"),
                            dbc.Col(_summary_chip("Tasks", str(task_count), t, t["accent"], "#case-tasks", "case-task-count-value"), md=4, className="mb-1"),
                            dbc.Col(_summary_chip("Notes", str(note_count), t, t["accent"], "#case-notes"), md=6, className="mb-1"),
                            dbc.Col(_summary_chip("Attachments", str(attachment_count), t, t["green"], "#case-attachments"), md=6, className="mb-1"),
                        ], className="g-1"),
                    ], md=5),
                ], align="start", className="g-2"),
            ], t=t),
        ], style={"marginBottom": "8px"}),

        # ── Inline edit panel (collapsed by default) ─────────────────────
        dbc.Collapse(
            _card([
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Title", style={"color": t["text"]}),
                        dbc.Input(id="edit-case-title",
                                  value=case.get("title", ""),
                                  disabled=not can_edit_case,
                                  style=_inp_style(t)),
                    ], md=6),
                    dbc.Col([
                        dbc.Label("Description", style={"color": t["text"]}),
                        dbc.Input(id="edit-case-desc",
                                  value=case.get("description") or "",
                                  disabled=not can_edit_case,
                                  style=_inp_style(t)),
                    ], md=6),
                ], className="mb-2 g-2"),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Severity", style={"color": t["text"]}),
                        dcc.Dropdown(severity_opts, id="edit-case-severity",
                                     value=(case.get("severity") or "medium").lower(),
                                     disabled=not can_edit_case,
                                     clearable=False,
                                     style={"backgroundColor": t["inp_bg"]}),
                    ], md=3),
                    dbc.Col([
                        dbc.Label("Status", style={"color": t["text"]}),
                        dcc.Dropdown(status_opts, id="edit-case-status",
                                     value=(case.get("status") or "open").lower(),
                                     disabled=not can_edit_case,
                                     clearable=False,
                                     style={"backgroundColor": t["inp_bg"]}),
                    ], md=3),
                    dbc.Col([
                        dbc.Label("Assigned", style={"color": t["text"]}),
                        dcc.Dropdown(
                            assign_opts,
                            id="edit-case-owner",
                            value=(case.get("owner") or {}).get("id") or 0,
                            disabled=not (can_edit_case and can_assign),
                            clearable=False,
                            style={"backgroundColor": t["inp_bg"]},
                        ),
                    ], md=3),
                    dbc.Col([
                        dbc.Label(" ", style={"color": "transparent"}),
                        _btn_primary("Save Changes", "btn-case-edit-save", t,
                                     disabled=not can_edit_case,
                                     className="w-100"),
                    ], md=3),
                ], className="g-2"),
                html.Div(id="case-edit-feedback", className="mt-2"),
            ], t=t),
            id="collapse-case-edit", is_open=False,
        ),

        dcc.Interval(id="case-refresh-interval", interval=5000, n_intervals=0),
        dcc.Interval(id="case-lock-interval", interval=15000, n_intervals=0),
        html.Div(id="case-action-feedback"),
        html.Div(
            dbc.ButtonGroup([
                _btn_secondary("Analysis", "btn-jump-analysis", t, size="sm",
                               className="quick-jump-btn", style={"minWidth": "92px"}),
                _btn_secondary("Hypotheses", "btn-jump-hypotheses", t, size="sm",
                               className="quick-jump-btn", style={"minWidth": "104px"}),
                _btn_secondary("Notes", "btn-jump-notes", t, size="sm",
                               className="quick-jump-btn", style={"minWidth": "84px"}),
                _btn_secondary("Tasks", "btn-jump-tasks", t, size="sm",
                               className="quick-jump-btn", style={"minWidth": "84px"}),
                _btn_secondary("BIAS", "btn-jump-bias", t, size="sm",
                               className="quick-jump-btn", style={"minWidth": "84px"}),
            ], size="sm"),
            style={
                "position": "sticky",
                "top": "8px",
                "zIndex": 20,
                "display": "flex",
                "justifyContent": "center",
                "padding": "6px 0 10px",
                "marginBottom": "4px",
                "backdropFilter": "blur(8px)",
            },
        ),

        # ── Main columns ─────────────────────────────────────────────────
        dbc.Row(id="case-analysis", style={"scrollMarginTop": "84px"}, children=[
            dbc.Col([
                html.Div(_card([
                    html.Div(
                        "Use this as your structured case frame: who is involved, what happened, where and when it matters, and why this case deserves attention.",
                        style={"color": t["muted"], "fontSize": "12px", "marginBottom": "14px"},
                    ),
                    dbc.Row([
                        dbc.Col([
                            _reasoning_panel(
                                "Actors And Activity",
                                "Capture the people, systems, or accounts involved and the main activity under investigation.",
                                [
                                    dbc.Row([
                                        dbc.Col([
                                            dbc.Label("Who", style={"color": t["text"], "fontSize": "12px"}),
                                            dbc.Textarea(
                                                id="analysis-who",
                                                value=analysis.get("who", ""),
                                                disabled=not can_edit_case,
                                                style={**_inp_style(t), "minHeight": "84px"},
                                            ),
                                        ], md=6),
                                        dbc.Col([
                                            dbc.Label("What", style={"color": t["text"], "fontSize": "12px"}),
                                            dbc.Textarea(
                                                id="analysis-what",
                                                value=analysis.get("what", ""),
                                                disabled=not can_edit_case,
                                                style={**_inp_style(t), "minHeight": "84px"},
                                            ),
                                        ], md=6),
                                    ], className="g-2"),
                                ],
                                t,
                            ),
                        ], md=6, className="mb-3"),
                        dbc.Col([
                            _reasoning_panel(
                                "Time And Location",
                                "Anchor the case in time and place so the rest of the investigation has a stable frame.",
                                [
                                    dbc.Row([
                                        dbc.Col([
                                            dbc.Label("When", style={"color": t["text"], "fontSize": "12px"}),
                                            dbc.Row([
                                                dbc.Col([
                                                    dbc.Button(
                                                        analysis_when_date or "Select Date",
                                                        id="btn-analysis-when-date-picker",
                                                        disabled=not can_edit_case,
                                                        className="w-100",
                                                        style={"backgroundColor": t["accent"], "borderColor": t["accent"]},
                                                    ),
                                                    dbc.Input(
                                                        id="analysis-when-date",
                                                        type="date",
                                                        value=analysis_when_date,
                                                        disabled=not can_edit_case,
                                                        style={
                                                            "position": "absolute",
                                                            "opacity": 0,
                                                            "pointerEvents": "none",
                                                            "width": "1px",
                                                            "height": "1px",
                                                            "padding": 0,
                                                            "border": 0,
                                                        },
                                                    ),
                                                ], md=6),
                                                dbc.Col([
                                                    dbc.Button(
                                                        analysis_when_time or "Select Time",
                                                        id="btn-analysis-when-time-picker",
                                                        disabled=not can_edit_case,
                                                        className="w-100",
                                                        style={"backgroundColor": t["accent"], "borderColor": t["accent"]},
                                                    ),
                                                    dbc.Input(
                                                        id="analysis-when-time",
                                                        type="time",
                                                        step=60,
                                                        value=analysis_when_time,
                                                        disabled=not can_edit_case,
                                                        style={
                                                            "position": "absolute",
                                                            "opacity": 0,
                                                            "pointerEvents": "none",
                                                            "width": "1px",
                                                            "height": "1px",
                                                            "padding": 0,
                                                            "border": 0,
                                                        },
                                                    ),
                                                ], md=6),
                                            ], className="g-2 mb-2"),
                                            html.Small(
                                                "Choose the analysis date/time using the pickers.",
                                                style={"color": t["muted"], "display": "block", "marginTop": "4px"},
                                            ),
                                        ], md=6),
                                        dbc.Col([
                                            dbc.Label("Where", style={"color": t["text"], "fontSize": "12px"}),
                                            dbc.Textarea(
                                                id="analysis-where",
                                                value=analysis.get("where", ""),
                                                disabled=not can_edit_case,
                                                style={**_inp_style(t), "minHeight": "84px"},
                                            ),
                                        ], md=6),
                                    ], className="g-2"),
                                ],
                                t,
                            ),
                        ], md=6, className="mb-3"),
                    ], className="g-3"),
                    _reasoning_panel(
                        "Case Rationale",
                        "Record why this activity matters and what makes it notable enough to investigate.",
                        [
                            dbc.Label("Why", style={"color": t["text"], "fontSize": "12px"}),
                            dbc.Textarea(
                                id="analysis-why",
                                value=analysis.get("why", ""),
                                disabled=not can_edit_case,
                                style={**_inp_style(t), "minHeight": "92px"},
                            ),
                        ],
                        t,
                    ),
                    html.Div(
                        [
                            _btn_primary("Save Analysis", "btn-save-analysis", t, size="sm",
                                         disabled=not can_edit_case),
                            html.Div(id="analysis-feedback", style={"minHeight": "34px", "display": "flex", "alignItems": "center"}),
                        ],
                        style={
                            "display": "flex",
                            "justifyContent": "space-between",
                            "alignItems": "center",
                            "gap": "12px",
                            "paddingTop": "12px",
                        },
                    ),
                ], title="Analysis Card", t=t)),
            ], md=12),
        ], className="g-3"),
        dbc.Row(id="case-hypotheses", style={"scrollMarginTop": "84px"}, children=[
            dbc.Col([
                html.Div(_card([
                    html.Div(
                        "Use this as a living theory board: keep your best explanation and best alternative side by side, then pressure both with the evidence you actually have.",
                        style={"color": t["muted"], "fontSize": "12px", "marginBottom": "14px"},
                    ),
                    dbc.Row([
                        dbc.Col([
                            _reasoning_panel(
                                "Working Hypothesis",
                                "Your best current explanation of the case.",
                                [
                                    dbc.Textarea(
                                        id="analysis-hypothesis",
                                        value=analysis.get("hypothesis", ""),
                                        placeholder="What do you currently think is happening?",
                                        disabled=not can_edit_case,
                                        style={**_inp_style(t), "minHeight": "96px"},
                                    ),
                                ],
                                t,
                            ),
                        ], md=6, className="mb-3"),
                        dbc.Col([
                            _reasoning_panel(
                                "Counter Hypothesis",
                                "The strongest realistic alternative you still need to rule out.",
                                [
                                    dbc.Textarea(
                                        id="analysis-counter-hypothesis",
                                        value=analysis.get("counter_hypothesis", ""),
                                        placeholder="What else could plausibly explain the same activity?",
                                        disabled=not can_edit_case,
                                        style={**_inp_style(t), "minHeight": "96px"},
                                    ),
                                ],
                                t,
                            ),
                        ], md=6, className="mb-3"),
                    ], className="g-3"),
                    dbc.Row([
                        dbc.Col([
                            _reasoning_panel(
                                "Supporting Evidence",
                                "Facts or observations that currently strengthen the working hypothesis.",
                                [
                                    dbc.Textarea(
                                        id="analysis-supporting-evidence",
                                        value=analysis.get("supporting_evidence", ""),
                                        placeholder="What evidence is pushing you toward the working hypothesis?",
                                        disabled=not can_edit_case,
                                        style={**_inp_style(t), "minHeight": "108px"},
                                    ),
                                ],
                                t,
                            ),
                        ], md=6, className="mb-3"),
                        dbc.Col([
                            _reasoning_panel(
                                "Disconfirming Evidence",
                                "Signals that weaken the working hypothesis or strengthen the counter hypothesis.",
                                [
                                    dbc.Textarea(
                                        id="analysis-disconfirming-evidence",
                                        value=analysis.get("disconfirming_evidence", ""),
                                        placeholder="What is conflicting, missing, or still pulling the case another direction?",
                                        disabled=not can_edit_case,
                                        style={**_inp_style(t), "minHeight": "108px"},
                                    ),
                                ],
                                t,
                            ),
                        ], md=6, className="mb-3"),
                    ], className="g-3"),
                    html.Div(
                        [
                            _btn_primary("Save Hypotheses", "btn-save-hypotheses", t, size="sm",
                                         disabled=not can_edit_case),
                            dcc.Checklist(
                                id="hypothesis-log-change",
                                options=[{"label": " Log this change to Notes", "value": "log"}],
                                value=[],
                                inputStyle={"marginRight": "6px"},
                                style={"color": t["muted"], "fontSize": "12px", "margin": 0},
                            ),
                            html.Div(id="hypotheses-feedback", style={"minHeight": "34px", "display": "flex", "alignItems": "center", "marginLeft": "auto"}),
                        ],
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "gap": "12px",
                            "padding": "12px 14px",
                            "backgroundColor": t["bg2"],
                            "border": f"1px solid {t['border']}",
                            "borderRadius": "10px",
                            "marginBottom": "8px",
                        },
                    ),
                ], title="Investigative Hypotheses", t=t)),
            ], md=12),
        ], className="g-3"),
        dbc.Row([
            # Left: Notes
            dbc.Col([
                # Notes
                html.Div(_card([
                    dcc.Store(id="note-edit-id", data=None),
                    dbc.Row([
                        dbc.Col(
                            dbc.Textarea(id="note-body-input",
                                         placeholder="Add an investigation note… Use [[Entity Name]] to create/link entities from note text.",
                                         disabled=not can_edit_case,
                                         style={**_inp_style(t), "minHeight": "70px"}),
                            width=True),
                    ], className="mb-2"),
                    html.Small("Entity markup: [[Host-A]] or [[Suspicious Domain]]. Existing shared entities will feed Relationships automatically.",
                               style={"color": t["muted"], "display": "block", "marginBottom": "8px"}),
                    dbc.Row([
                        dbc.Col(_btn_primary("Save Note", "btn-save-note", t, size="sm",
                                             disabled=not can_edit_case, className="mb-3"), width="auto"),
                        dbc.Col(
                            _btn_secondary("Cancel Edit", "btn-cancel-note-edit", t, size="sm",
                                           disabled=not can_edit_case, className="mb-3",
                                           style={"color": t["muted"]}),
                            width="auto",
                        ),
                    ], className="g-2"),
                    html.Div(id="case-notes-list",
                             children=_build_notes_list(notes, t, can_manage=can_edit_case)),
                ], title="Notes", t=t), id="case-notes", style={"scrollMarginTop": "84px"}),

            ], md=7),

            # Right: Tasks, Attachments, Entities, Observables
            dbc.Col([
                # Tasks
                html.Div(_card([
                    dbc.Row([
                        dbc.Col([
                            dbc.Input(
                                id="task-text-input",
                                placeholder="Add a task for this case…",
                                disabled=not can_edit_case,
                                style=_inp_style(t),
                            ),
                        ], md=5),
                        dbc.Col([
                            dcc.Dropdown(
                                assign_opts,
                                id="task-assignee-input",
                                value=0,
                                clearable=False,
                                disabled=not can_edit_case,
                                placeholder="Assignee",
                                style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                            ),
                        ], md=3),
                        dbc.Col([
                            dbc.Label(" ", style={"color": "transparent", "marginBottom": "6px"}),
                            _btn_primary("Add Task", "btn-add-task", t, size="sm",
                                         disabled=not can_edit_case, className="w-100",
                                         style={"minWidth": "0"}),
                        ], md=4),
                    ], className="g-2 mb-3"),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Due Date", style={"color": t["text"], "fontSize": "12px", "marginBottom": "6px"}),
                            dbc.Row([
                                dbc.Col([
                                    dbc.Button(
                                        "Select Date",
                                        id="btn-task-date-picker",
                                        disabled=not can_edit_case,
                                        className="w-100",
                                        style={"backgroundColor": t["accent"], "borderColor": t["accent"]},
                                    ),
                                    dbc.Input(
                                        id="task-due-date",
                                        type="date",
                                        disabled=not can_edit_case,
                                        style={
                                            "position": "absolute",
                                            "opacity": 0,
                                            "pointerEvents": "none",
                                            "width": "1px",
                                            "height": "1px",
                                            "padding": 0,
                                            "border": 0,
                                        },
                                    ),
                                ], md=6),
                                dbc.Col([
                                    dbc.Button(
                                        "Select Time",
                                        id="btn-task-time-picker",
                                        disabled=not can_edit_case,
                                        className="w-100",
                                        style={"backgroundColor": t["accent"], "borderColor": t["accent"]},
                                    ),
                                    dbc.Input(
                                        id="task-due-time",
                                        type="time",
                                        step=60,
                                        disabled=not can_edit_case,
                                        style={
                                            "position": "absolute",
                                            "opacity": 0,
                                            "pointerEvents": "none",
                                            "width": "1px",
                                            "height": "1px",
                                            "padding": 0,
                                            "border": 0,
                                        },
                                    ),
                                ], md=6),
                            ], className="g-2"),
                            html.Small(
                                "Date and time must be selected from the pickers.",
                                style={"color": t["muted"], "display": "block", "marginTop": "4px"},
                            ),
                        ], md=6),
                    ], className="g-2 mb-3"),
                    html.Div(id="case-tasks-feedback", className="mb-2"),
                    html.Div(
                        id="case-tasks-list",
                        children=_build_tasks_list(tasks, t, can_manage=can_edit_case, assign_opts=assign_opts, user=user),
                    ),
                ], title="Tasks", t=t), id="case-tasks", style={"scrollMarginTop": "84px"}),

                # Attachments
                html.Div(_card([
                    dbc.Row([
                        dbc.Col(
                            html.Small(
                                "Attachments stay visible below. Expand the uploader only when you need to add one.",
                                style={"color": t["muted"], "fontSize": "10px", "display": "block"},
                            ),
                            width=True,
                        ),
                        dbc.Col(
                            _btn_secondary("Add Attachment", "btn-attachments-toggle", t, size="sm",
                                           disabled=not can_edit_case, style={"minWidth": "0"}),
                            width="auto",
                        ),
                    ], className="g-2 align-items-center mb-2"),
                    dbc.Collapse([
                        dcc.Upload(
                            id="upload-attachment",
                            disabled=not can_edit_case,
                            children=html.Div([
                                html.Span("Drag & drop or "),
                                html.A("browse", style={"color": t["accent"]}),
                                html.Span(" to upload"),
                            ], style={"color": t["muted"], "fontSize": "13px"}),
                            style={"border": f"2px dashed {t['border']}", "borderRadius": "6px",
                                   "padding": "16px", "textAlign": "center", "marginBottom": "12px",
                                   "cursor": "pointer", "backgroundColor": t["inp_bg"]},
                            multiple=False,
                        ),
                        html.Div(id="upload-feedback", className="mb-2"),
                    ], id="collapse-attachments-upload", is_open=False),
                    html.Div(id="case-attachments-list",
                             children=_build_attachments_list(attachments, t, token=token,
                                                              can_manage=can_edit_case)),
                ], title="Attachments", t=t), id="case-attachments"),

                # Entities
                html.Div(_card([
                    dbc.Row([
                        dbc.Col(
                            html.Small(
                                "Keep the entity list visible; expand the entry form only when you need to add or link one.",
                                style={"color": t["muted"], "fontSize": "10px", "display": "block"},
                            ),
                            width=True,
                        ),
                        dbc.Col(
                            _btn_secondary("Add / Link Entity", "btn-entities-toggle", t, size="sm",
                                           disabled=not can_edit_case, style={"minWidth": "0"}),
                            width="auto",
                        ),
                    ], className="g-2 align-items-center mb-2"),
                    dbc.Collapse([
                        dbc.Row([
                            dbc.Col([
                                dcc.Dropdown(
                                    entity_type_options,
                                    id="ent-type-input",
                                    placeholder="Type",
                                    disabled=not can_edit_case,
                                    style={"backgroundColor": t["inp_bg"], "fontSize": "13px"},
                                ),
                            ], md=5),
                            dbc.Col([
                                dbc.Input(id="ent-value-input", placeholder="Value",
                                          disabled=not can_edit_case,
                                          style=_inp_style(t), size="sm"),
                            ], md=7),
                        ], className="mb-2 g-2"),
                        dbc.Row([
                            dbc.Col([
                                dbc.Input(id="ent-desc-input", placeholder="Description (optional)",
                                          disabled=not can_edit_case,
                                          style=_inp_style(t), size="sm"),
                            ]),
                        ], className="mb-2"),
                        dbc.Row([
                            dbc.Col([
                                dcc.Dropdown(["source", "target", "associated"],
                                             id="ent-role-input", placeholder="Role",
                                             value="associated",
                                             disabled=not can_edit_case,
                                             style={"backgroundColor": t["inp_bg"], "fontSize": "13px"}),
                            ], md=6),
                            dbc.Col(
                                _btn_primary("Add Entity", "btn-add-entity", t,
                                             disabled=not can_edit_case,
                                             size="sm", className="w-100"),
                                md=6),
                        ], className="mb-3 g-2"),
                    ], id="collapse-entities-entry", is_open=False),
                    html.Div(id="case-entities-list",
                             children=_build_entities_list(case_entities, t, can_manage=can_edit_case)),
                ], title="Entities", t=t), id="case-entities"),

            ], md=5),
        ]),
        dbc.Row([
            dbc.Col([
                _card([
                    dcc.Store(id="case-bias-observables-store", data=[obs.get("tcode") for obs in observables if obs.get("tcode")]),
                    dcc.Store(id="case-bias-order-store", data=[obs.get("tcode") for obs in observables if obs.get("tcode")]),
                    dcc.Store(id="case-bias-snapshots-store", data={"1": [], "2": [], "3": []}),
                    html.Div(
                        dbc.Alert(
                            f"BIAS output is older than the current observables. Re-run BIAS to sync the graph and pivots. Last run: {_fmt_dt(case.get('bias_ran_at'))}",
                            color="warning",
                            className="mb-3 py-2",
                        ),
                        style={"display": "block" if bias_is_stale and observable_count >= 2 else "none"},
                    ),
                    html.Div(id="case-observables", children=[
                        dbc.Row([
                            dbc.Col([
                                dbc.Label("Observables / Tcodes", style={"color": t["muted"], "fontSize": "12px"}),
                                html.Small(
                                    "These are the confirmed observables BIAS will reason over. Add the techniques here, then set analyst order below.",
                                    style={"color": t["muted"], "display": "block", "fontSize": "10px", "marginBottom": "8px"},
                                ),
                            ], width=True),
                        ], className="mb-1"),
                        dbc.Row([
                            dbc.Col(
                                dbc.Input(id="obs-tcode-input",
                                          placeholder="e.g. T1059, T1078, T1003.001",
                                          disabled=not can_edit_case,
                                          style=_inp_style(t)),
                                width=True),
                            dbc.Col(
                                _btn_primary("Add", "btn-add-obs", t, size="sm",
                                             disabled=not can_edit_case),
                                width="auto"),
                            dbc.Col(
                                dbc.Button("Clear All", id="btn-clear-observables",
                                           disabled=not can_edit_case,
                                           size="sm", outline=True,
                                           style={"borderColor": t["red"],
                                                  "color": t["red"]}),
                                width="auto"),
                        ], className="mb-3 g-2"),
                        html.Small("Separate multiple tcodes with commas.",
                                   style={"color": t["muted"], "fontSize": "10px", "display": "block", "opacity": 0.85,
                                          "marginBottom": "10px"}),
                        html.Div(id="case-observables-list",
                                 children=_build_observables_list(observables, t, can_manage=can_edit_case)),
                    ], style={"scrollMarginTop": "84px", "marginBottom": "16px"}),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Analyst Order", style={"color": t["muted"], "fontSize": "12px"}),
                            html.Div(id="case-bias-order-panel",
                                     children=_bias_order_panel([str(obs.get("tcode")).upper() for obs in observables if obs.get("tcode")], t)),
                        ], md=6),
                        dbc.Col([
                            dbc.Label("Snapshots", style={"color": t["muted"], "fontSize": "12px"}),
                            html.P("Save up to three observable-order snapshots and reload them later.",
                                   style={"color": t["muted"], "fontSize": "10px", "margin": "0 0 8px"}),
                            html.Div([_bias_snapshot_slot(i, t) for i in range(1, 4)]),
                            html.Div(id="case-bias-snapshot-feedback",
                                     style={"color": t["muted"], "fontSize": "10px", "marginBottom": "8px"}),
                        ], md=6),
                    ], className="mb-3 g-3"),
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Platforms", style={"color": t["muted"], "fontSize": "12px"}),
                            dcc.Dropdown(
                                id="bias-platforms", multi=True,
                                options=["windows","linux","macos","android","ios",
                                         "azure-ad","office-365","saas","iaas",
                                         "gcp","aws","azure","network","containers"],
                                value=_pipe_to_list(case.get("bias_platforms")),
                                placeholder="Select…",
                                style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                            ),
                        ], md=3),
                        dbc.Col([
                            dbc.Label("CLI Environment", style={"color": t["muted"], "fontSize": "12px"}),
                            dcc.Dropdown(
                                id="bias-cli-env", multi=True,
                                options=["cmd","powershell","bash","sh","zsh","fish","wsl"],
                                value=_pipe_to_list(case.get("bias_cli_env")),
                                placeholder="Select…",
                                style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                            ),
                        ], md=3),
                        dbc.Col([
                            dbc.Label("Scripting", style={"color": t["muted"], "fontSize": "12px"}),
                            dcc.Dropdown(
                                id="bias-scripting", multi=True,
                                options=["python","powershell","vbscript","jscript",
                                         "bash","ruby","perl","lua","batch","hta"],
                                value=_pipe_to_list(case.get("bias_scripting")),
                                placeholder="Select…",
                                style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                            ),
                        ], md=3),
                        dbc.Col([
                            dbc.Label("Network", style={"color": t["muted"], "fontSize": "12px"}),
                            dcc.Dropdown(
                                id="bias-network", multi=True,
                                options=["smb","http","https","dns","ftp","ssh",
                                         "rdp","ldap","winrm","smtp","imap","icmp"],
                                value=_pipe_to_list(case.get("bias_network")),
                                placeholder="Select…",
                                style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                            ),
                        ], md=3),
                    ], className="mb-2 g-2"),
                    dbc.Row([
                        dbc.Col(
                            _btn_primary("Run BIAS", "btn-run-bias", t, size="sm",
                                         disabled=not can_edit_case),
                            width="auto"),
                        dbc.Col(
                            dbc.Button("Auto-fill from Entities", id="btn-bias-autofill",
                                       disabled=not can_edit_case,
                                       size="sm", outline=True,
                                       style={"borderColor": t["purple"],
                                              "color": t["purple"]}),
                            width="auto"),
                    ], className="mb-3 g-2"),
                    html.Div(id="case-bias-results",
                             children=_build_bias_display(case, t, can_edit_case=can_edit_case)),
                ], title="BIAS Gap Analysis", t=t),
            ], md=12, id="case-bias", style={"scrollMarginTop": "84px"}),
        ], className="mt-3"),
    ], fluid=True,
       style={"backgroundColor": t["bg"], "minHeight": "100vh", "paddingBottom": "40px"})


def _staging_page(user: dict, t: dict) -> dbc.Container:
    chains = _api("/api/staging/", token=user.get("token")) or []
    can_manage = _can_manage_case(user)
    rows = []
    for ch in chains:
        tcodes = ch.get("tcode_sequence") or "—"
        rows.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.Strong(ch.get("hostname") or ch.get("src_ip") or "Unknown host",
                                    style={"color": t["text"]}),
                        html.Small(f"  ·  chain: {ch.get('chain_key','?')}",
                                   style={"color": t["muted"]}),
                        html.Br(),
                        html.Small(f"Tcodes: {tcodes}",
                                   style={"color": t["muted"], "fontSize": "11px"}),
                        html.Br(),
                        html.Small(f"First seen: {_fmt_dt(ch.get('first_seen'))}  "
                                   f"Last seen: {_fmt_dt(ch.get('last_seen'))}",
                                   style={"color": t["muted"], "fontSize": "11px"}),
                    ], width=True),
                    dbc.Col([
                        html.Span(ch.get("severity", "?").upper(),
                                  style={**_sev_badge_style(ch.get("severity"), t),
                                         "marginBottom": "4px", "display": "block"}),
                        dbc.Badge(f"{ch.get('alert_count',0)} alerts",
                                  style={"backgroundColor": t["muted"]}),
                    ], width="auto"),
                    dbc.Col([
                        dbc.Button("Promote →", size="sm",
                                   id={"type": "chain-promote", "index": ch.get("id", 0)},
                                   style={"backgroundColor": t["green"], "borderColor": t["green"],
                                          "marginBottom": "4px", "display": "block",
                                          "width": "90px"}),
                        dbc.Button("Dismiss", size="sm",
                                   id={"type": "chain-dismiss", "index": ch.get("id", 0)},
                                   style={"backgroundColor": t["red"], "borderColor": t["red"],
                                          "width": "90px"}),
                    ], width="auto"),
                ], align="center"),
            ], style={"backgroundColor": t["card"]}),
            style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "8px"},
        ))

    empty = _card([
        html.H6("No alert chains are waiting in staging.", style={"color": t["text"], "marginBottom": "8px"}),
        html.P(
            "Staging fills when LIST ingests alerts and groups them into pending chains. "
            "Those chains can then be reviewed, promoted into cases, or dismissed.",
            style={"color": t["muted"], "marginBottom": "10px"},
        ),
        html.Div([
            html.Div("How it works", style={"color": t["accent"], "fontWeight": "600", "marginBottom": "6px"}),
            html.Ul([
                html.Li("Alerts are ingested into LIST and grouped by host / source context."),
                html.Li("Each group appears here as a pending staging item."),
                html.Li("Promote creates a new case from that chain and carries its T-codes forward."),
                html.Li("Dismiss removes the chain from the pending queue."),
            ], style={"color": t["muted"], "paddingLeft": "18px", "marginBottom": "12px"}),
        ]),
        html.P(
            "For demos or testing, you can create a sample staging item below without any external alert source.",
            style={"color": t["muted"], "marginBottom": "12px"},
        ),
        dbc.Button(
            "Create Test Staging Item",
            id="btn-create-test-staging",
            disabled=not can_manage,
            style={"backgroundColor": t["purple"], "borderColor": t["purple"]},
        ),
    ], t=t)
    return dbc.Container([
        _nav(user, t),
        html.H4("Staging Queue", style={"color": t["accent"], "marginBottom": "4px"}),
        html.P(
            "Staging is the holding area for ingested alert chains before they become full cases. "
            "Review a chain here, promote it into a case, or dismiss it from the queue.",
            style={"color": t["muted"], "marginBottom": "12px", "fontSize": "13px"},
        ),
        dbc.Alert(
            "This page is populated by alert ingestion, not by manually creating cases.",
            color="info",
            className="mb-3",
        ),
        html.Div(id="staging-feedback"),
        html.Div(rows) if rows else empty,
    ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh", "paddingBottom": "40px"})


def _entities_page(user: dict, t: dict) -> dbc.Container:
    token = user.get("token", "")
    entities = _api("/api/entities/", token=token) or []
    cases    = _api("/api/cases",     token=token) or []
    entity_type_opts = ["ip_address", "hostname", "domain", "email",
                        "url", "user_account", "file_hash", "other"]
    case_opts = [{"label": f"{c['title']} — {c['case_number']}", "value": c["id"]}
                 for c in cases]
    role_opts = ["source", "target", "associated"]

    # BIAS context mapping hint per entity type
    _bias_hint = {
        "hostname":   "→ Platform",  "ip_address": "→ Network",
        "domain":     "→ Network",   "url":        "→ Network",
        "email":      "→ Network",   "file_hash":  "IOC",
        "user_account": "Identity",  "other":      "",
    }
    case_links_by_entity = {}
    for case in cases:
        for ce in case.get("case_entities", []) or []:
            ent = ce.get("entity", {}) or {}
            entity_id = ent.get("id")
            if not entity_id:
                continue
            case_links_by_entity.setdefault(entity_id, []).append({
                "id": case.get("id"),
                "title": case.get("title"),
                "case_number": case.get("case_number"),
                "role": ce.get("role"),
            })

    rows = []
    for ent in entities:
        eid   = ent.get("id", "")
        etype = ent.get("entity_type", "?")
        hint  = _bias_hint.get(etype, "")
        related_cases = ent.get("related_cases") or case_links_by_entity.get(eid) or []
        related_case_names = ", ".join(
            rc.get("title") or rc.get("case_number") or "Untitled case"
            for rc in related_cases
        )
        rows.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    # Type + value + description
                    dbc.Col([
                        dbc.Row([
                            dbc.Col(dbc.Badge(etype, style={"backgroundColor": t["purple"]}),
                                    width="auto"),
                            dbc.Col(dbc.Badge(hint, style={"backgroundColor": t["orange"],
                                                            "opacity": "0.7"}),
                                    width="auto") if hint else html.Span(),
                        ], className="g-1 mb-1"),
                        html.Span([
                            dcc.Link(
                                ent.get("value", "—"),
                                href="/relationships",
                                className="entity-relationship-link",
                                style={
                                    "color": t["text"],
                                    "fontWeight": "500",
                                    "fontSize": "14px",
                                    "textAlign": "left",
                                    "cursor": "pointer",
                                    "textDecoration": "none",
                                },
                            ),
                            html.Span("Entity Relationships", className="entity-relationship-hover"),
                        ], className="entity-relationship-wrap"),
                        html.Small(ent.get("description") or "",
                                   style={"color": t["muted"]}),
                        html.Small(
                            (
                                f"Linked cases: {related_case_names}"
                                if related_cases else
                                "Linked cases: none yet"
                            ),
                            style={
                                "color": t["muted"],
                                "display": "block",
                                "marginTop": "4px",
                                "fontSize": "11px",
                            },
                        ),
                        html.Small(f"  Created {_fmt_dt(ent.get('created_at'))}",
                                   style={"color": t["muted"], "fontSize": "11px"}),
                    ], width=True),
                    # Link-to-case inline panel
                    dbc.Col([
                        html.Div(
                            dbc.Button(
                                "Link to Case", size="sm", outline=True,
                                id={"type": "ent-link-toggle", "index": eid},
                                style={
                                    "borderColor": t["accent"],
                                    "color": t["accent"],
                                    "fontSize": "11px",
                                },
                            ),
                            className="d-flex justify-content-end",
                        ),
                        dbc.Collapse([
                            dbc.Row([
                                dbc.Col(dcc.Dropdown(
                                    case_opts, placeholder="Select case…",
                                    id={"type": "ent-case-dd", "index": eid},
                                    style={"backgroundColor": t["inp_bg"],
                                           "fontSize": "12px", "minWidth": "220px"},
                                ), width=True),
                                dbc.Col(dcc.Dropdown(
                                    role_opts, value="associated",
                                    id={"type": "ent-role-dd", "index": eid},
                                    clearable=False,
                                    style={"backgroundColor": t["inp_bg"],
                                           "fontSize": "12px", "minWidth": "120px"},
                                ), width="auto"),
                            ], className="g-1 mt-2"),
                        ], id={"type": "ent-link-collapse", "index": eid}, is_open=False),
                    ], width="auto", style={"minWidth": "360px"}),
                ], align="center"),
                html.Div(id={"type": "ent-link-feedback", "index": eid}),
            ], style={"backgroundColor": t["card"]}),
            style={"backgroundColor": t["card"], "borderColor": t["border"],
                   "marginBottom": "6px"},
        ))

    return dbc.Container([
        _nav(user, t),
        html.H4("Entity Library", style={"color": t["accent"], "marginBottom": "16px"}),
        html.P("Entities can be linked to cases directly here, or from within a case. "
               "BIAS context badges (→ Platform, → Network) indicate how each entity "
               "type informs BIAS gap analysis.",
               style={"color": t["muted"], "fontSize": "13px", "marginBottom": "16px"}),
        _card([
            dbc.Row([
                dbc.Col([
                    dbc.Label("Type", style={"color": t["text"]}),
                    dcc.Dropdown(entity_type_opts, id="glob-ent-type",
                                 placeholder="Select type…",
                                 style={"backgroundColor": t["inp_bg"]}),
                ], md=2),
                dbc.Col([
                    dbc.Label("Value", style={"color": t["text"]}),
                    dbc.Input(id="glob-ent-value", placeholder="Entity value",
                              style=_inp_style(t)),
                ], md=5),
                dbc.Col([
                    dbc.Label("Description", style={"color": t["text"]}),
                    dbc.Input(id="glob-ent-desc", placeholder="Optional",
                              style=_inp_style(t)),
                ], md=3),
                dbc.Col([
                    html.Div(
                        _btn_primary(
                            "Create", "btn-glob-ent-create", t,
                            className="w-100",
                            style={"minWidth": "0"},
                        ),
                        className="d-flex align-items-end h-100",
                    ),
                ], md=2, className="d-flex"),
            ], className="g-2", align="end"),
            html.Div(id="glob-ent-feedback", className="mt-2"),
        ], title="Create Entity", t=t),
        html.Div(id="ent-lib-feedback"),
        html.Div(rows) if rows else html.P("No entities yet.", style={"color": t["muted"]}),
    ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh",
                           "paddingBottom": "40px"})


def _relationship_probability(shared_count: int, count_a: int, count_b: int) -> int:
    max_count = max(count_a, count_b, 1)
    raw = 0.15 + (0.18 * shared_count) + (0.42 * (shared_count / max_count))
    return max(1, min(99, round(raw * 100)))


def _build_relationship_data(cases: list) -> tuple[dict, list, str | None]:
    relationships = {}
    elements = []
    case_nodes = []
    normalized_cases = []

    for case in cases:
        entities = case.get("case_entities", []) or []
        observables = case.get("observables", []) or []
        entity_map = {}
        observable_set = set()
        for ce in entities:
            ent = ce.get("entity", {}) or {}
            etype = (ent.get("entity_type") or "").strip().lower()
            value = (ent.get("value") or "").strip()
            if not etype or not value:
                continue
            key = f"{etype}:{value.lower()}"
            entity_map[key] = {
                "type": etype,
                "value": value,
                "role": ce.get("role") or "associated",
            }
        for obs in observables:
            tcode = str(obs.get("tcode") or "").strip().upper()
            if tcode:
                observable_set.add(tcode)
        normalized_cases.append({
            "id": case.get("id"),
            "case_number": case.get("case_number", "CASE"),
            "title": case.get("title", "Untitled"),
            "entity_map": entity_map,
            "observable_set": observable_set,
        })

    for case in normalized_cases:
        cid = str(case["id"])
        relationships[cid] = {
            "case_id": case["id"],
            "case_number": case["case_number"],
            "title": case["title"],
            "relationships": [],
            "relationship_strength": 0,
        }
        case_nodes.append({
            "data": {
                "id": cid,
                "label": f"{case['title'][:36]}\n{case['case_number']}",
                "case_number": case["case_number"],
                "title": case["title"],
                "ntype": "case",
                "entity_count": len(case["entity_map"]),
            }
        })

    for i, left in enumerate(normalized_cases):
        for right in normalized_cases[i + 1:]:
            shared_keys = sorted(set(left["entity_map"]).intersection(right["entity_map"]))
            if not shared_keys:
                continue
            shared_entities = []
            for key in shared_keys:
                left_ent = left["entity_map"][key]
                right_ent = right["entity_map"][key]
                shared_entities.append({
                    "type": left_ent["type"],
                    "value": left_ent["value"],
                    "left_role": left_ent["role"],
                    "right_role": right_ent["role"],
                })
            shared_count = len(shared_entities)
            shared_observable_count = len(set(left["observable_set"]).intersection(right["observable_set"]))
            probability = _relationship_probability(
                shared_count,
                len(left["entity_map"]),
                len(right["entity_map"]),
            )
            edge_id = f"rel-{left['id']}-{right['id']}"
            elements.append({
                "data": {
                    "id": edge_id,
                    "source": str(left["id"]),
                    "target": str(right["id"]),
                    "label": f"{shared_count} shared",
                    "weight": shared_count,
                    "probability": probability,
                }
            })
            rel_to_right = {
                "case_id": right["id"],
                "case_number": right["case_number"],
                "title": right["title"],
                "shared_count": shared_count,
                "shared_observable_count": shared_observable_count,
                "probability": probability,
                "shared_entities": shared_entities,
            }
            rel_to_left = {
                "case_id": left["id"],
                "case_number": left["case_number"],
                "title": left["title"],
                "shared_count": shared_count,
                "shared_observable_count": shared_observable_count,
                "probability": probability,
                "shared_entities": shared_entities,
            }
            relationships[str(left["id"])]["relationships"].append(rel_to_right)
            relationships[str(right["id"])]["relationships"].append(rel_to_left)
            relationships[str(left["id"])]["relationship_strength"] += shared_count
            relationships[str(right["id"])]["relationship_strength"] += shared_count

    strongest_case_id = None
    if relationships:
        strongest_case_id = max(
            relationships.keys(),
            key=lambda cid: (
                relationships[cid].get("relationship_strength", 0),
                len(relationships[cid].get("relationships", [])),
            ),
        )
    return relationships, (case_nodes + elements), strongest_case_id


def _build_relationship_detail(case_id: str | None, relationships: dict, t: dict):
    if not case_id or not relationships:
        return html.P("Click a case node to inspect shared entities.", style={"color": t["muted"], "margin": 0})
    data = relationships.get(str(case_id)) or {}
    rels = sorted(data.get("relationships", []), key=lambda r: (-r.get("shared_count", 0), -r.get("probability", 0)))
    if not rels:
        return html.Div([
            html.H6(data.get("title", "Case"), style={"color": t["text"], "marginBottom": "4px"}),
            html.P(data.get("case_number", ""), style={"color": t["accent"]}),
            html.P("This case does not currently share entities with any other cases.", style={"color": t["muted"], "margin": 0}),
        ])

    blocks = [
        html.Div([
            html.H6(data.get("title", "Case"), style={"color": t["text"], "marginBottom": "4px"}),
            html.P(data.get("case_number", ""), style={"color": t["accent"], "marginBottom": "4px"}),
            html.Small(
                f"Relationship strength: {data.get('relationship_strength', 0)}",
                style={"color": t["muted"], "display": "block", "marginBottom": "12px"},
            ),
        ])
    ]
    for rel in rels:
        shared_labels = [
            f"{ent.get('type', 'entity')}: {ent.get('value', '—')}"
            for ent in rel.get("shared_entities", [])
        ]
        blocks.append(
            dbc.Card(
                dbc.CardBody([
                    html.Div(rel.get("title", "Untitled"), style={"color": t["text"], "fontWeight": "600"}),
                    html.Small(rel.get("case_number", "CASE"), style={"color": t["orange"], "display": "block", "marginBottom": "8px"}),
                    html.Div(
                        f"Probability related: {rel.get('probability', 0)}%",
                        style={"color": t["text"], "fontWeight": "600", "marginBottom": "6px"},
                    ),
                    html.Div(
                        f"Shared entities: {rel.get('shared_count', 0)}",
                        style={"color": t["muted"], "fontSize": "12px", "marginBottom": "6px"},
                    ),
                    html.Div(
                        f"Shared observables: {rel.get('shared_observable_count', 0)}",
                        style={"color": t["muted"], "fontSize": "12px", "marginBottom": "8px"},
                    ),
                    html.Div([
                        dbc.Badge(label, className="me-1 mb-1",
                                  style={"backgroundColor": t["purple"], "fontSize": "11px"})
                        for label in shared_labels
                    ]),
                ], style={"backgroundColor": t["card"]}),
                style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "10px"},
            )
        )
    return blocks


def _bias_is_stale(case: dict) -> bool:
    bias_ran_at = _parse_dt_like(case.get("bias_ran_at"))
    observables = case.get("observables", []) or []
    confirmed_obs = [obs for obs in observables if obs.get("confirmed", True)]
    if len(confirmed_obs) < 2:
        return False
    if not case.get("bias_result_json") or not bias_ran_at:
        return False
    latest_obs = max((_parse_dt_like(obs.get("added_at")) for obs in confirmed_obs), default=None)
    return bool(latest_obs and latest_obs > bias_ran_at)


def _build_relationships_page(user: dict, t: dict) -> dbc.Container:
    cases = _api("/api/cases", token=user.get("token")) or []
    relationships, elements, strongest_case_id = _build_relationship_data(cases)
    focus_options = [
        {
            "label": f"{item.get('title', 'Untitled')} — {item.get('case_number', 'CASE')}",
            "value": cid,
        }
        for cid, item in sorted(
            relationships.items(),
            key=lambda kv: (-kv[1].get("relationship_strength", 0), kv[1].get("title", "")),
        )
    ]

    stylesheet = [
        {"selector": "node", "style": {
            "background-color": t["accent"],
            "label": "data(label)",
            "text-wrap": "wrap",
            "text-max-width": "150px",
            "text-valign": "center",
            "text-halign": "center",
            "color": "#ffffff",
            "font-size": "10px",
            "font-weight": "bold",
            "width": "145px",
            "height": "58px",
            "shape": "roundrectangle",
            "border-width": 2,
            "border-color": t["border"],
        }},
        {"selector": "edge", "style": {
            "line-color": t["orange"],
            "target-arrow-color": t["orange"],
            "target-arrow-shape": "none",
            "curve-style": "bezier",
            "width": "mapData(weight, 1, 6, 2, 8)",
            "label": "data(label)",
            "font-size": "9px",
            "color": t["muted"],
            "text-background-color": t["card"],
            "text-background-opacity": 0.9,
            "text-background-padding": "2px",
        }},
        {"selector": ":selected", "style": {
            "border-width": 4,
            "border-color": t["purple"],
        }},
    ]

    empty_state = html.P(
        "No case-to-case relationships yet. Link the same entities to multiple cases to build the network.",
        style={"color": t["muted"]},
    )

    return dbc.Container([
        _nav(user, t),
        dcc.Store(id="relationships-store", data={"relationships": relationships, "strongest_case_id": strongest_case_id}),
        html.H4("Relationships", style={"color": t["accent"], "marginBottom": "8px"}),
        html.P(
            "Cases are connected when they share one or more entities. More shared entities produce a stronger relationship. Shared observables are reported as supporting context only.",
            style={"color": t["muted"], "fontSize": "13px", "marginBottom": "16px"},
        ),
        dbc.Row([
            dbc.Col([
                dcc.Dropdown(
                    options=focus_options,
                    value=strongest_case_id,
                    id="relationships-focus-case",
                    clearable=False,
                    placeholder="Choose a case to inspect…",
                    style={"backgroundColor": t["inp_bg"], "fontSize": "12px"},
                ),
            ], md=8),
            dbc.Col([
                _btn_secondary("Focus Strongest Case", "btn-relationships-focus-strongest", t, className="w-100", size="sm"),
            ], md=4),
        ], className="g-2 mb-3"),
        dbc.Row([
            dbc.Col([
                _card([
                    cyto.Cytoscape(
                        id="relationships-graph",
                        elements=elements,
                        layout={"name": "cose", "animate": False, "padding": 40},
                        style={"width": "100%", "height": "720px", "backgroundColor": t["card"]},
                        stylesheet=stylesheet,
                        userZoomingEnabled=True,
                        userPanningEnabled=True,
                        minZoom=0.3,
                        maxZoom=2.5,
                    ) if elements else empty_state,
                ], title="Case Relationship Graph", t=t),
            ], md=8),
            dbc.Col([
                _card([
                    html.Div(
                        id="relationships-detail-panel",
                        children=_build_relationship_detail(strongest_case_id, relationships, t),
                    )
                ], title="Relationship Detail", t=t),
            ], md=4),
        ]),
    ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh", "paddingBottom": "40px"})


def _templates_page(user: dict, t: dict) -> dbc.Container:
    templates = _api("/api/templates", token=user.get("token")) or []

    rows = []
    for tmpl in templates:
        rows.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.H6(tmpl.get("name", "?"),
                                style={"color": t["accent"], "marginBottom": "2px"}),
                        html.Small(tmpl.get("description") or "—",
                                   style={"color": t["muted"]}),
                        html.Br(),
                        html.Small(f"Default severity: {tmpl.get('default_severity','?').upper()}  "
                                   f"{'· System template' if tmpl.get('is_system') else ''}",
                                   style={"color": t["muted"], "fontSize": "11px"}),
                    ], width=True),
                    dbc.Col([
                        dbc.Button("Edit", size="sm",
                                   id={"type": "tmpl-edit-btn", "index": tmpl["id"]},
                                   outline=True,
                                   style={"borderColor": t["accent"], "color": t["accent"],
                                          "marginRight": "4px"}),
                        dbc.Button("Delete", size="sm",
                                   id={"type": "tmpl-del-btn", "index": tmpl["id"]},
                                   outline=True,
                                   style={"borderColor": t["red"], "color": t["red"]},
                                   disabled=tmpl.get("is_system", False)),
                    ], width="auto"),
                ], align="center"),
            ], style={"backgroundColor": t["card"]}),
            style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "6px"},
        ))

    severity_opts = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

    # Edit modal (hidden by default; populated by callback)
    edit_modal = dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Edit Template",
                                       style={"color": t["accent"]}),
                        style={"backgroundColor": t["bg2"], "borderColor": t["border"]}),
        dbc.ModalBody([
            dbc.Input(id="tmpl-edit-id", type="hidden"),
            dbc.Label("Name", style={"color": t["text"]}),
            dbc.Input(id="tmpl-edit-name", style=_inp_style(t), className="mb-2"),
            dbc.Label("Description", style={"color": t["text"]}),
            dbc.Input(id="tmpl-edit-desc", style=_inp_style(t), className="mb-2"),
            dbc.Label("Default Severity", style={"color": t["text"]}),
            dcc.Dropdown(severity_opts, id="tmpl-edit-severity", clearable=False,
                         style={"backgroundColor": t["inp_bg"]}, className="mb-2"),
            dbc.Label("Initial Tcodes (| separated)", style={"color": t["text"]}),
            dbc.Input(id="tmpl-edit-tcodes", placeholder="e.g. T1059|T1078",
                      style=_inp_style(t), className="mb-2"),
            dbc.Label("Checklist (JSON list of strings)", style={"color": t["text"]}),
            dbc.Textarea(id="tmpl-edit-checklist", placeholder='["Step 1", "Step 2"]',
                         style={**_inp_style(t), "minHeight": "80px"}),
            html.Div(id="tmpl-edit-feedback", className="mt-2"),
        ], style={"backgroundColor": t["card"]}),
        dbc.ModalFooter([
            _btn_primary("Save", "btn-tmpl-edit-save", t),
            dbc.Button("Cancel", id="btn-tmpl-edit-cancel", color="secondary", className="ms-2"),
        ], style={"backgroundColor": t["bg2"], "borderColor": t["border"]}),
    ], id="modal-tmpl-edit", is_open=False,
       style={"backgroundColor": "rgba(0,0,0,0.7)"})

    return dbc.Container([
        _nav(user, t),
        html.H4("Templates", style={"color": t["accent"], "marginBottom": "16px"}),
        html.Div(id="templates-feedback"),
        edit_modal,

        # Create template section
        _card([
            dbc.Row([
                dbc.Col([
                    dbc.Label("Name *", style={"color": t["text"]}),
                    dbc.Input(id="tmpl-new-name", style=_inp_style(t)),
                ], md=4),
                dbc.Col([
                    dbc.Label("Description", style={"color": t["text"]}),
                    dbc.Input(id="tmpl-new-desc", style=_inp_style(t)),
                ], md=4),
                dbc.Col([
                    dbc.Label("Default Severity", style={"color": t["text"]}),
                    dcc.Dropdown(severity_opts, id="tmpl-new-severity",
                                 value="MEDIUM", clearable=False,
                                 style={"backgroundColor": t["inp_bg"]}),
                ], md=2),
                dbc.Col([
                    html.Div(
                        _btn_primary("Create", "btn-tmpl-create", t, className="w-100"),
                        className="d-flex align-items-end h-100",
                    ),
                ], md=2, className="d-flex"),
            ], className="g-2", align="end"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Initial Tcodes (| separated)", style={"color": t["text"],
                                                                       "marginTop": "8px"}),
                    dbc.Input(id="tmpl-new-tcodes", placeholder="e.g. T1059|T1078",
                              style=_inp_style(t)),
                ], md=6),
                dbc.Col([
                    dbc.Label("Checklist items (comma-separated)",
                              style={"color": t["text"], "marginTop": "8px"}),
                    dbc.Input(id="tmpl-new-checklist",
                              placeholder="e.g. Collect IOCs, Notify stakeholders",
                              style=_inp_style(t)),
                ], md=6),
            ], className="mt-2 g-2"),
        ], title="Create New Template", t=t),

        html.H5(f"Existing Templates ({len(templates)})",
                style={"color": t["text"], "marginBottom": "12px"}),
        html.Div(rows) if rows else html.P("No templates.", style={"color": t["muted"]}),
    ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh", "paddingBottom": "40px"})


def _admin_page(user: dict, t: dict) -> dbc.Container:
    users = _api("/api/auth/users", token=user.get("token")) or []
    attachment_settings = _api("/api/attachments/settings", token=user.get("token")) or {"max_mb": 250}
    attachment_max_mb = int(attachment_settings.get("max_mb") or 250)
    role_opts = ["admin", "lead", "analyst", "viewer"]

    # Build user rows
    user_rows = []
    for u in users:
        is_self = u.get("username") == user.get("username")
        role_badge_color = (
            t["accent"] if u.get("role") == "admin"
            else t["purple"] if u.get("role") == "lead"
            else t["orange"] if u.get("role") == "analyst"
            else t["muted"]
        )
        active_badge = (dbc.Badge("Active", style={"backgroundColor": t["green"]})
                        if u.get("is_active")
                        else dbc.Badge("Disabled", style={"backgroundColor": t["red"]}))
        user_rows.append(dbc.Card(
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.Strong(u.get("username", "?"),
                                    style={"color": t["text"]}),
                        html.Span(f"  {u.get('email','')}", style={"color": t["muted"],
                                                                     "fontSize": "12px"}),
                        html.Br(),
                        dbc.Badge(u.get("role","?"), className="me-1",
                                  style={"backgroundColor": role_badge_color}),
                        active_badge,
                        html.Span("  (you)", style={"color": t["muted"], "fontSize": "11px"})
                        if is_self else html.Span(),
                    ], width=True),
                    dbc.Col([
                        dbc.Button("Edit", size="sm",
                                   id={"type": "user-edit-btn", "index": u["id"]},
                                   outline=True,
                                   style={"borderColor": t["accent"], "color": t["accent"],
                                          "marginRight": "4px"}),
                        dbc.Button("Delete", size="sm",
                                   id={"type": "user-del-btn", "index": u["id"]},
                                   outline=True,
                                   style={"borderColor": t["red"], "color": t["red"]},
                                   disabled=is_self),
                    ], width="auto"),
                ], align="center"),
            ], style={"backgroundColor": t["card"]}),
            style={"backgroundColor": t["card"], "borderColor": t["border"], "marginBottom": "6px"},
        ))

    # Edit user modal
    user_edit_modal = dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Edit User", style={"color": t["accent"]}),
                        style={"backgroundColor": t["bg2"], "borderColor": t["border"]}),
        dbc.ModalBody([
            dbc.Input(id="user-edit-id", type="hidden"),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Username", style={"color": t["text"]}),
                    dbc.Input(id="user-edit-username", style=_inp_style(t), className="mb-2"),
                ], md=6),
                dbc.Col([
                    dbc.Label("Email", style={"color": t["text"]}),
                    dbc.Input(id="user-edit-email", style=_inp_style(t), className="mb-2"),
                ], md=6),
            ]),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Role", style={"color": t["text"]}),
                    dcc.Dropdown(role_opts, id="user-edit-role", clearable=False,
                                 style={"backgroundColor": t["inp_bg"]}, className="mb-2"),
                ], md=4),
                dbc.Col([
                    dbc.Label("Status", style={"color": t["text"]}),
                    dcc.Dropdown([{"label": "Active", "value": True},
                                  {"label": "Disabled", "value": False}],
                                 id="user-edit-active", clearable=False,
                                 style={"backgroundColor": t["inp_bg"]}, className="mb-2"),
                ], md=4),
                dbc.Col([
                    dbc.Label("New Password", style={"color": t["text"]}),
                    dbc.Input(id="user-edit-password", type="password",
                              placeholder="Leave blank to keep current",
                              style=_inp_style(t), className="mb-2"),
                ], md=4),
            ]),
            html.Div(id="user-edit-feedback", className="mt-1"),
        ], style={"backgroundColor": t["card"]}),
        dbc.ModalFooter([
            _btn_primary("Save", "btn-user-edit-save", t),
            dbc.Button("Cancel", id="btn-user-edit-cancel", color="secondary", className="ms-2"),
        ], style={"backgroundColor": t["bg2"], "borderColor": t["border"]}),
    ], id="modal-user-edit", is_open=False)

    return dbc.Container([
        _nav(user, t),
        html.H4("Admin", style={"color": t["accent"], "marginBottom": "20px"}),

        # ── User Management ──────────────────────────────────────────────
        user_edit_modal,
        html.Div(id="admin-user-feedback"),
        _card([
            # Create user form
            dbc.Row([
                dbc.Col([
                    dbc.Label("Username *", style={"color": t["text"]}),
                    dbc.Input(id="new-user-username", style=_inp_style(t)),
                ], md=3),
                dbc.Col([
                    dbc.Label("Email *", style={"color": t["text"]}),
                    dbc.Input(id="new-user-email", type="email", style=_inp_style(t)),
                ], md=3),
                dbc.Col([
                    dbc.Label("Password *", style={"color": t["text"]}),
                    dbc.Input(id="new-user-password", type="password", style=_inp_style(t)),
                ], md=3),
                dbc.Col([
                    dbc.Label("Role", style={"color": t["text"]}),
                    dcc.Dropdown(role_opts, id="new-user-role", value="analyst",
                                 clearable=False,
                                 style={"backgroundColor": t["inp_bg"]}),
                ], md=2),
                dbc.Col([
                    dbc.Label(" ", style={"color": "transparent"}),
                    _btn_primary("Create", "btn-user-create", t, className="w-100"),
                ], md=1),
            ], className="g-2 mb-2"),
            html.Div(id="new-user-feedback", className="mb-3"),
            html.Hr(style={"borderColor": t["border"]}),
            html.Div(user_rows) if user_rows else html.P("No users found.",
                                                          style={"color": t["muted"]}),
        ], title=f"User Management  ({len(users)} users)", t=t),

        # ── Appearance & Backup ──────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                _card([
                    html.P("Theme", style={"color": t["text"], "fontWeight": "600",
                                           "marginBottom": "8px"}),
                    dcc.Dropdown(
                        options=[{"label": "🌙  Dark",          "value": "dark"},
                                 {"label": "☀️  Light",         "value": "light"},
                                 {"label": "⚡  High Contrast", "value": "contrast"}],
                        id="theme-selector",
                        value="dark",
                        clearable=False,
                        style={"backgroundColor": t["inp_bg"]},
                    ),
                    html.Hr(style={"borderColor": t["border"], "margin": "16px 0"}),
                    html.P("Custom Logo", style={"color": t["text"], "fontWeight": "600",
                                                  "marginBottom": "4px"}),
                    html.P("Upload a PNG or SVG to replace the LIST wordmark in the nav.",
                           style={"color": t["muted"], "fontSize": "12px",
                                  "marginBottom": "8px"}),
                    dcc.Upload(
                        id="upload-logo",
                        children=html.Div([
                            html.Span("Drag & drop or "),
                            html.A("browse", style={"color": t["accent"]}),
                        ], style={"color": t["muted"], "fontSize": "13px"}),
                        style={"border": f"2px dashed {t['border']}", "borderRadius": "6px",
                               "padding": "14px", "textAlign": "center", "cursor": "pointer",
                               "backgroundColor": t["inp_bg"]},
                        accept="image/*",
                    ),
                    dbc.Button(
                        "Clear Logo",
                        id="btn-clear-logo",
                        color="secondary",
                        outline=True,
                        className="mt-2 w-100",
                    ),
                    html.Div(id="logo-feedback", className="mt-2"),
                ], title="Appearance", t=t),
            ], md=5),
            dbc.Col([
                _card([
                    html.P("Export a portable zip of all cases, entities, templates, "
                           "and attachments.", style={"color": t["muted"], "marginBottom": "12px"}),
                    _btn_primary("Export Backup", "btn-backup", t, className="w-100"),
                    html.Div(id="backup-feedback", className="mt-2"),
                    html.Hr(style={"borderColor": t["border"], "margin": "16px 0"}),
                    html.P("Import a LIST backup zip to restore cases, entities, links, templates, alerts, and attachments. Existing user accounts are preserved.",
                           style={"color": t["muted"], "marginBottom": "8px"}),
                    dcc.Upload(
                        id="upload-backup",
                        children=html.Div([
                            html.Span("Drag & drop a backup zip or "),
                            html.A("browse", style={"color": t["accent"]}),
                        ], style={"color": t["muted"], "fontSize": "13px"}),
                        style={"border": f"2px dashed {t['border']}", "borderRadius": "6px",
                               "padding": "14px", "textAlign": "center", "cursor": "pointer",
                               "backgroundColor": t["inp_bg"]},
                        accept=".zip,application/zip",
                    ),
                    html.Div(id="import-feedback", className="mt-2"),
                ], title="Backup & Export", t=t),
            ], md=4),
            dbc.Col([
                _card([
                    html.P("Reset LIST to a clean state. This clears cases, alerts, entities, attachments, reports, backups, and reseeds system templates. Your current admin account is preserved.",
                           style={"color": t["muted"], "marginBottom": "12px"}),
                    dbc.Button("Reset LIST", id="btn-reset-list", className="w-100",
                               style={"backgroundColor": t["red"], "borderColor": t["red"]}),
                    html.Div(id="reset-feedback", className="mt-2"),
                ], title="Danger Zone", t=t),
            ], md=3),
        ]),

        _card([
            dbc.Row([
                dbc.Col([
                    dbc.Label("Per-file attachment limit (MB)", style={"color": t["text"]}),
                    dbc.Input(
                        id="attachment-limit-mb",
                        type="number",
                        min=1,
                        step=50,
                        value=attachment_max_mb,
                        style=_inp_style(t),
                    ),
                ], md=3),
                dbc.Col([
                    dbc.Label(" ", style={"color": "transparent"}),
                    _btn_primary("Save Limit", "btn-save-attachment-limit", t, className="w-100"),
                ], md=2),
            ], className="g-2"),
            html.P(
                "Default is 250 MB per file. Raising this toward 1 GB or higher increases memory usage, "
                "backup size, restore time, and the impact of simultaneous uploads.",
                style={"color": t["muted"], "fontSize": "12px", "marginTop": "10px", "marginBottom": "0"},
            ),
            html.Div(id="attachment-limit-feedback", className="mt-2"),
        ], title="Attachment Limits", t=t),
    ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh", "paddingBottom": "40px"})


# ── App layout ─────────────────────────────────────────────────────────────
app.layout = dbc.Container([
    dcc.Store(id="store-user", storage_type="session"),
    dcc.Store(id="store-theme", data="dark"),
    dcc.Store(id="store-case-id", data=None),
    dcc.Store(id="scroll-to-section", data=None),
    dcc.Location(id="url", refresh=False),
    dcc.Download(id="download-report"),
    dcc.Download(id="download-backup"),
    html.Div(id="scroll-trigger", style={"display": "none"}),
    html.Div(id="page-content",
             style={"minHeight": "100vh"}),
], fluid=True, style={"padding": 0})


# ── Routing callback ────────────────────────────────────────────────────────
@callback(
    Output("page-content", "children"),
    Output("page-content", "style"),
    Input("url", "pathname"),
    Input("store-theme", "data"),
    State("store-user", "data"),
)
def route(pathname: str, theme_key: str, user: dict):
    t = _t(theme_key)
    bg_style = {"minHeight": "100vh", "backgroundColor": t["bg"]}

    if not user or not user.get("token"):
        return _login_page(t), bg_style

    pathname = pathname or "/"

    if pathname == "/my-work" or pathname == "/":
        return _my_work_page(user, t), bg_style
    if pathname == "/cases":
        return _cases_page(user, t), bg_style
    if pathname == "/new-case":
        return _new_case_page(user, t), bg_style
    if pathname.startswith("/case/"):
        try:
            case_id = int(pathname.split("/case/")[1])
            return _case_detail_page(user, case_id, t), bg_style
        except (ValueError, IndexError):
            pass
    if pathname == "/staging":
        return _staging_page(user, t), bg_style
    if pathname == "/relationships":
        return _build_relationships_page(user, t), bg_style
    if pathname == "/entities":
        return _entities_page(user, t), bg_style
    if pathname == "/templates":
        return _templates_page(user, t), bg_style
    if pathname == "/admin":
        if (user.get("role") or "").lower() == "admin":
            return _admin_page(user, t), bg_style
        return dbc.Container([
            _nav(user, t),
            dbc.Alert("Admin access required.", color="danger"),
        ], fluid=True, style={"backgroundColor": t["bg"], "minHeight": "100vh"}), bg_style

    return dbc.Container([html.P("Page not found.", style={"color": t["muted"]})],
                         fluid=True), bg_style


@callback(
    Output("store-case-id", "data"),
    Input("url", "pathname"),
)
def sync_case_id_from_url(pathname: str):
    if pathname and pathname.startswith("/case/"):
        try:
            return int(pathname.split("/case/")[1])
        except (TypeError, ValueError, IndexError):
            return None
    return None


app.clientside_callback(
    """
    function(target) {
        if (!target) {
            return window.dash_clientside.no_update;
        }
        const run = () => {
            const section = document.getElementById(target);
            if (section) {
                section.scrollIntoView({behavior: "smooth", block: "start"});
            }
            if (target === "case-notes") {
                const noteBox = document.getElementById("note-body-input");
                if (noteBox) {
                    noteBox.focus({preventScroll: true});
                }
            } else if (target === "case-tasks") {
                const taskBox = document.getElementById("task-text-input");
                if (taskBox) {
                    taskBox.focus({preventScroll: true});
                }
            }
        };
        window.setTimeout(run, 50);
        return "";
    }
    """,
    Output("scroll-trigger", "children"),
    Input("scroll-to-section", "data"),
    prevent_initial_call=True,
)


@callback(
    Output("relationships-focus-case", "value"),
    Input("btn-relationships-focus-strongest", "n_clicks"),
    State("relationships-store", "data"),
    prevent_initial_call=True,
)
def focus_strongest_relationship_case(n_clicks, relationships_store):
    if not n_clicks or not isinstance(relationships_store, dict):
        raise PreventUpdate
    strongest_case_id = relationships_store.get("strongest_case_id")
    if not strongest_case_id:
        raise PreventUpdate
    return strongest_case_id


@callback(
    Output("scroll-to-section", "data", allow_duplicate=True),
    Input("btn-jump-analysis", "n_clicks"),
    Input("btn-jump-hypotheses", "n_clicks"),
    Input("btn-jump-notes", "n_clicks"),
    Input("btn-jump-tasks", "n_clicks"),
    Input("btn-jump-bias", "n_clicks"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def jump_to_case_section(n_analysis, n_hypotheses, n_notes, n_tasks, n_bias):
    triggered = dash.ctx.triggered_id
    mapping = {
        "btn-jump-analysis": "case-analysis",
        "btn-jump-hypotheses": "case-hypotheses",
        "btn-jump-notes": "case-notes",
        "btn-jump-tasks": "case-tasks",
        "btn-jump-bias": "case-bias",
    }
    target = mapping.get(triggered)
    if not target:
        raise PreventUpdate
    return target


# ── Theme ──────────────────────────────────────────────────────────────────
@callback(
    Output("store-theme", "data"),
    Input("theme-selector", "value"),
    prevent_initial_call=True,
)
def change_theme(theme_key: str):
    if theme_key not in THEMES:
        raise PreventUpdate
    return theme_key


# ── Logo upload ─────────────────────────────────────────────────────────────
@callback(
    Output("logo-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("upload-logo", "contents"),
    State("upload-logo", "filename"),
    State("url", "pathname"),
    prevent_initial_call=True,
)
def upload_logo(contents: str | None, filename: str | None, pathname: str):
    if not contents or not filename:
        raise PreventUpdate
    try:
        _, b64 = contents.split(",", 1)
        data = base64.b64decode(b64)
        LOGO_PATH.write_bytes(data)
        return dbc.Alert(f"Logo updated ({filename}). Reloading…", color="success",
                         duration=3000), pathname
    except Exception as exc:
        return dbc.Alert(f"Failed to save logo: {exc}", color="danger"), no_update


@callback(
    Output("logo-feedback", "children", allow_duplicate=True),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-clear-logo", "n_clicks"),
    State("url", "pathname"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def clear_logo(n_clicks, pathname: str):
    if not n_clicks:
        raise PreventUpdate
    try:
        if LOGO_PATH.exists():
            LOGO_PATH.unlink()
            return dbc.Alert("Custom logo cleared. Reloading…", color="success",
                             duration=3000), pathname
        return dbc.Alert("No custom logo is set.", color="warning", duration=3000), no_update
    except Exception as exc:
        return dbc.Alert(f"Failed to clear logo: {exc}", color="danger"), no_update


# ── Login ──────────────────────────────────────────────────────────────────
@callback(
    Output("store-user", "data"),
    Output("login-error", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-login", "n_clicks"),
    State("login-username", "value"),
    State("login-password", "value"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def do_login(n_clicks, username, password):
    if not n_clicks:
        raise PreventUpdate
    token, uname = _api_login(username or "", password or "")
    if not token:
        return no_update, "Invalid credentials", no_update
    me = _api("/api/auth/me", token=token) or {}
    return {
        "id": me.get("id"),
        "token": token,
        "username": me.get("username", uname),
        "role": me.get("role"),
        "email": me.get("email"),
    }, "", "/my-work"


@callback(
    Output("store-user", "data", allow_duplicate=True),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-logout", "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def do_logout(n_clicks, case_id, user):
    if not n_clicks:
        raise PreventUpdate
    if case_id and user and user.get("token"):
        _api(f"/api/cases/{case_id}/lock", method="DELETE", token=user.get("token"))
    return {}, "/"


# ── Case list → open case ───────────────────────────────────────────────────
@callback(
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "case-open-btn", "index": ALL}, "n_clicks"),
    State({"type": "case-open-btn", "index": ALL}, "id"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def open_case(n_clicks_list, ids):
    for n, id_dict in zip(n_clicks_list or [], ids or []):
        if n:
            return f"/case/{id_dict['index']}"
    raise PreventUpdate


@callback(
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "my-work-open-btn", "index": ALL}, "n_clicks"),
    State({"type": "my-work-open-btn", "index": ALL}, "id"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def open_case_from_my_work(n_clicks_list, ids):
    for n, id_dict in zip(n_clicks_list or [], ids or []):
        if n:
            return f"/case/{id_dict['index']}"
    raise PreventUpdate


@callback(
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "my-work-card", "index": ALL}, "n_clicks"),
    State({"type": "my-work-card", "index": ALL}, "id"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def open_case_from_my_work_card(n_clicks_list, ids):
    for n, id_dict in zip(n_clicks_list or [], ids or []):
        if n:
            return f"/case/{id_dict['index']}"
    raise PreventUpdate


# ── Case list → delete case ─────────────────────────────────────────────────
@callback(
    Output("cases-list-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "case-del-btn", "index": ALL}, "n_clicks"),
    State({"type": "case-del-btn", "index": ALL}, "id"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_case_from_list(n_clicks_list, ids, user):
    for n, id_dict in zip(n_clicks_list or [], ids or []):
        if n:
            case_id = id_dict["index"]
            r = requests.delete(
                f"{API_BASE}/api/cases/{case_id}",
                headers={"Authorization": f"Bearer {user.get('token','')}"},
                timeout=10,
            )
            if r.status_code == 204:
                return no_update, "/cases"
            return _alert_msg("Delete failed.", ok=False), no_update
    raise PreventUpdate


# ── New case create ─────────────────────────────────────────────────────────
@callback(
    Output("nc-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-nc-create", "n_clicks"),
    State("nc-title", "value"),
    State("nc-desc", "value"),
    State("nc-severity", "value"),
    State("nc-template", "value"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def create_case(n_clicks, title, desc, severity, template_id, user):
    if not n_clicks:
        raise PreventUpdate
    if not title:
        return _alert_msg("Title is required.", ok=False), no_update
    result = _api("/api/cases", method="POST", token=user.get("token"), data={
        "title": title, "description": desc,
        "severity": (severity or "MEDIUM").lower(),
        "template_id": template_id,
    })
    if result and result.get("id"):
        return no_update, f"/case/{result['id']}"
    return _alert_msg("Failed to create case.", ok=False), no_update


# ── Case detail: toggle edit panel ──────────────────────────────────────────
@callback(
    Output("collapse-case-edit", "is_open"),
    Input("btn-case-edit-toggle", "n_clicks"),
    State("collapse-case-edit", "is_open"),
    prevent_initial_call=True,
)
def toggle_case_edit(n_clicks, is_open):
    if n_clicks:
        return not is_open
    raise PreventUpdate


@callback(
    Output("collapse-attachments-upload", "is_open"),
    Output("btn-attachments-toggle", "children"),
    Input("btn-attachments-toggle", "n_clicks"),
    State("collapse-attachments-upload", "is_open"),
    prevent_initial_call=True,
)
def toggle_attachment_uploader(n_clicks, is_open):
    if n_clicks:
        next_open = not is_open
        return next_open, ("Hide Uploader" if next_open else "Add Attachment")
    raise PreventUpdate


@callback(
    Output("collapse-entities-entry", "is_open"),
    Output("btn-entities-toggle", "children"),
    Input("btn-entities-toggle", "n_clicks"),
    State("collapse-entities-entry", "is_open"),
    prevent_initial_call=True,
)
def toggle_entity_entry(n_clicks, is_open):
    if n_clicks:
        next_open = not is_open
        return next_open, ("Hide Entity Form" if next_open else "Add / Link Entity")
    raise PreventUpdate


@callback(
    Output("case-lock-banner", "children"),
    Input("case-lock-interval", "n_intervals"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
)
def refresh_case_lock(_n_intervals, case_id, user, theme_key):
    if not case_id:
        raise PreventUpdate
    can_manage = _can_manage_case(user)
    if can_manage:
        case = _api(f"/api/cases/{case_id}/lock", method="POST", token=user.get("token"))
        if not case:
            case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    else:
        case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    return _build_case_lock_banner(case, user, _t(theme_key))


# ── Case detail: save edits ──────────────────────────────────────────────────
@callback(
    Output("case-edit-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-case-edit-save", "n_clicks"),
    State("edit-case-title", "value"),
    State("edit-case-desc", "value"),
    State("edit-case-severity", "value"),
    State("edit-case-status", "value"),
    State("edit-case-owner", "value"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def save_case_edit(n_clicks, title, desc, severity, status, owner_id, case_id, user):
    if not n_clicks or not case_id:
        raise PreventUpdate
    payload = {
        "title": title or None,
        "description": desc or None,
        "severity": severity or None,
        "status": status or None,
    }
    if _can_assign_case(user):
        payload["owner_id"] = None if owner_id in (None, 0, "0") else owner_id
    result = _api(f"/api/cases/{case_id}", method="PATCH",
                  token=user.get("token"), data=payload)
    if result and result.get("id"):
        # Reload the page to reflect changes
        return no_update, f"/case/{case_id}"
    return _alert_msg("Save failed.", ok=False), no_update


# ── Case detail: delete ───────────────────────────────────────────────────────
@callback(
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-case-delete", "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_case_from_detail(n_clicks, case_id, user):
    if not n_clicks or not case_id:
        raise PreventUpdate
    r = requests.delete(
        f"{API_BASE}/api/cases/{case_id}",
        headers={"Authorization": f"Bearer {user.get('token','')}"},
        timeout=10,
    )
    if r.status_code == 204:
        return no_update, "/cases"
    return _alert_msg("Delete failed.", ok=False), no_update


# ── Add observables ─────────────────────────────────────────────────────────
@callback(
    Output("case-observables-list", "children"),
    Output("case-action-feedback", "children"),
    Output("obs-tcode-input", "value"),
    Output("case-bias-observables-store", "data"),
    Input("btn-add-obs", "n_clicks"),
    State("obs-tcode-input", "value"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
)
def add_observables(n_clicks, raw_tcodes, case_id, user, theme_key):
    if not n_clicks or not raw_tcodes or not case_id:
        raise PreventUpdate
    t = _t(theme_key)
    tcodes = [x.strip().upper() for x in raw_tcodes.replace(",", " ").split() if x.strip()]
    if not tcodes:
        return no_update, _alert_msg("Enter at least one tcode.", ok=False), no_update, no_update
    result = _api(f"/api/cases/{case_id}/observables", method="POST",
                  token=user.get("token"), data={"tcodes": tcodes})
    if result:
        obs = result.get("observables", [])
        can_manage = _can_manage_case(user)
        return _build_observables_list(obs, t, can_manage=can_manage), _alert_msg(f"Added: {', '.join(tcodes)}"), "", [o.get("tcode") for o in obs if o.get("tcode")]
    return no_update, _alert_msg("Failed to add observables.", ok=False), no_update, no_update


@callback(
    Output("case-observables-list", "children", allow_duplicate=True),
    Output("case-bias-results", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("case-bias-observables-store", "data", allow_duplicate=True),
    Input({"type": "btn-del-obs", "index": ALL}, "n_clicks"),
    State({"type": "btn-del-obs", "index": ALL}, "id"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_observable(n_clicks, ids, case_id, user, theme_key):
    if not case_id or not ids or not any(n_clicks):
        raise PreventUpdate
    t = _t(theme_key)
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    observable_id = triggered.get("index")
    if observable_id is None:
        raise PreventUpdate

    result = _api(f"/api/cases/{case_id}/observables/{observable_id}", method="DELETE", token=user.get("token"))
    if not result:
        return no_update, no_update, _alert_msg("Failed to remove observable.", ok=False), no_update

    can_manage = _can_manage_case(user)
    observables = result.get("observables", [])
    bias_children = html.P("BIAS results were cleared after the observable set changed. Rerun BIAS.",
                           style={"color": t["orange"]})
    return (
        _build_observables_list(observables, t, can_manage=can_manage),
        bias_children,
        _alert_msg("Observable removed. Rerun BIAS to refresh results."),
        [o.get("tcode") for o in observables if o.get("tcode")],
    )


@callback(
    Output("case-observables-list", "children", allow_duplicate=True),
    Output("case-bias-results", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("obs-tcode-input", "value", allow_duplicate=True),
    Output("case-bias-observables-store", "data", allow_duplicate=True),
    Output("case-bias-order-store", "data", allow_duplicate=True),
    Output("case-bias-snapshots-store", "data", allow_duplicate=True),
    Input("btn-clear-observables", "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
)
def clear_all_observables(n_clicks, case_id, user, theme_key):
    if not n_clicks or not case_id:
        raise PreventUpdate
    t = _t(theme_key)
    case = _api(f"/api/cases/{case_id}", token=user.get("token"))
    if not case:
        return no_update, no_update, _alert_msg("Failed to load case observables.", ok=False), no_update, no_update, no_update, no_update

    observables = case.get("observables", []) or []
    if not observables:
        empty_msg = html.P("BIAS input is empty. Add observables before running BIAS again.",
                           style={"color": t["muted"]})
        return (
            _build_observables_list([], t, can_manage=_can_manage_case(user)),
            empty_msg,
            _alert_msg("No observables to clear."),
            "",
            [],
            [],
            {"1": [], "2": [], "3": []},
        )

    for obs in observables:
        observable_id = obs.get("id")
        if observable_id is None:
            continue
        result = _api(f"/api/cases/{case_id}/observables/{observable_id}", method="DELETE", token=user.get("token"))
        if not result:
            return no_update, no_update, _alert_msg("Failed to clear all observables.", ok=False), no_update, no_update, no_update, no_update

    cleared_msg = html.P("BIAS results were cleared after all observables were removed.",
                         style={"color": t["orange"]})
    return (
        _build_observables_list([], t, can_manage=_can_manage_case(user)),
        cleared_msg,
        _alert_msg("Cleared all observables."),
        "",
        [],
        [],
        {"1": [], "2": [], "3": []},
    )


@callback(
    Output("case-bias-order-store", "data"),
    Output("case-bias-order-panel", "children"),
    Output("case-bias-snapshots-store", "data"),
    Output("case-bias-snapshot-feedback", "children"),
    Output({"type": "bias-snapshot-summary", "index": ALL}, "children"),
    Input("case-bias-observables-store", "data"),
    Input({"type": "btn-bias-move-up", "index": ALL}, "n_clicks"),
    Input({"type": "btn-bias-move-down", "index": ALL}, "n_clicks"),
    Input({"type": "btn-bias-save-snapshot", "index": ALL}, "n_clicks"),
    Input({"type": "btn-bias-load-snapshot", "index": ALL}, "n_clicks"),
    State("case-bias-order-store", "data"),
    State("case-bias-snapshots-store", "data"),
    State("store-theme", "data"),
    prevent_initial_call=False,
)
def manage_case_bias_order(observable_tcodes, _up_clicks, _down_clicks, _save_clicks, _load_clicks,
                           stored_order, snapshots, theme_key):
    t = _t(theme_key)
    parsed = []
    seen = set()
    for raw in (observable_tcodes or []):
        tcode = str(raw or "").strip().upper()
        if tcode and tcode not in seen:
            parsed.append(tcode)
            seen.add(tcode)

    stored_order = [str(v).strip().upper() for v in (stored_order or []) if str(v).strip()]
    snapshots = {str(k): [str(v).strip().upper() for v in (vals or []) if str(v).strip()]
                 for k, vals in (snapshots or {"1": [], "2": [], "3": []}).items()}
    for slot in ("1", "2", "3"):
        snapshots.setdefault(slot, [])

    current = [tcode for tcode in stored_order if tcode in parsed]
    for tcode in parsed:
        if tcode not in current:
            current.append(tcode)

    trigger = dash.ctx.triggered_id
    feedback = ""

    if isinstance(trigger, dict):
        slot = str(trigger.get("index", ""))
        trigger_type = trigger.get("type")
        if trigger_type in ("btn-bias-move-up", "btn-bias-move-down"):
            tcode = str(trigger.get("index") or "").strip().upper()
            if tcode in current:
                idx = current.index(tcode)
                if trigger_type == "btn-bias-move-up" and idx > 0:
                    current[idx - 1], current[idx] = current[idx], current[idx - 1]
                elif trigger_type == "btn-bias-move-down" and idx < len(current) - 1:
                    current[idx + 1], current[idx] = current[idx], current[idx + 1]
        elif trigger_type == "btn-bias-save-snapshot":
            if current:
                snapshots[slot] = current[:]
                feedback = f"Snapshot {slot} saved ({len(current)} observable(s))."
            else:
                feedback = "Add observables before saving a snapshot."
        elif trigger_type == "btn-bias-load-snapshot":
            if snapshots.get(slot):
                current = [tcode for tcode in snapshots[slot] if tcode in parsed]
                for tcode in parsed:
                    if tcode not in current:
                        current.append(tcode)
                feedback = f"Snapshot {slot} loaded."
            else:
                feedback = f"Snapshot {slot} is empty."

    summaries = []
    for idx in range(1, 4):
        vals = snapshots.get(str(idx), [])
        if vals:
            preview = " -> ".join(vals[:3])
            if len(vals) > 3:
                preview += f"  +{len(vals) - 3} more"
            summaries.append([
                html.Div(f"{len(vals)} observable(s) saved", style={"color": t["text"]}),
                html.Div(preview, style={"color": t["muted"], "fontSize": "9px", "marginTop": "2px"}),
            ])
        else:
            summaries.append(html.Div("Empty", style={"color": t["muted"]}))

    return current, _bias_order_panel(current, t), snapshots, feedback, summaries


# ── Run BIAS ────────────────────────────────────────────────────────────────
@callback(
    Output("case-bias-results", "children"),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Input("btn-run-bias", "n_clicks"),
    State("store-case-id", "data"),
    State("bias-platforms", "value"),
    State("bias-cli-env", "value"),
    State("bias-scripting", "value"),
    State("bias-network", "value"),
    State("case-bias-order-store", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def run_bias(n_clicks, case_id, platforms, cli_env, scripting, network, ordered_tcodes, user, theme_key):
    import time
    if not n_clicks or not case_id:
        raise PreventUpdate
    t = _t(theme_key)
    token = user.get("token", "")

    # Check BIAS availability
    health = _api("/health") or {}
    if not health.get("bias_ready"):
        return (html.P("BIAS engine is not available. Check BIAS_PATH in config.py.",
                       style={"color": t["red"]}),
                _alert_msg("BIAS not available — see config.py.", ok=False))

    # Save context parameters
    _api(f"/api/cases/{case_id}", method="PATCH", token=token, data={
        "bias_platforms": _list_to_pipe(platforms),
        "bias_cli_env":   _list_to_pipe(cli_env),
        "bias_scripting": _list_to_pipe(scripting),
        "bias_network":   _list_to_pipe(network),
    })

    # Record the current bias_ran_at so we can detect when it changes
    case_before = _api(f"/api/cases/{case_id}", token=token) or {}
    old_ran_at = case_before.get("bias_ran_at")

    # Trigger background analysis
    _api(f"/api/cases/{case_id}/bias-run", method="POST", token=token, data={
        "ordered_tcodes": [str(t).strip().upper() for t in (ordered_tcodes or []) if str(t).strip()]
    })

    # Poll until the result lands (up to 30 s)
    for _ in range(60):
        time.sleep(0.5)
        updated = _api(f"/api/cases/{case_id}", token=token) or {}
        if updated.get("bias_ran_at") and updated.get("bias_ran_at") != old_ran_at:
            return (_build_bias_display(updated, t, can_edit_case=_can_manage_case(user)),
                    _alert_msg("BIAS analysis complete."))

    return (html.P("BIAS is taking longer than expected — refresh the page in a moment.",
                   style={"color": t["orange"]}),
            _alert_msg("BIAS still running.", ok=False))


@callback(
    Output("case-bias-detail-panel", "children"),
    Input("cyto-final", "tapNodeData"),
    State("store-theme", "data"),
    prevent_initial_call=True,
)
def inspect_bias_node(data, theme_key):
    return _build_bias_detail_panel(data, _t(theme_key))


# ── BIAS auto-fill from entities ─────────────────────────────────────────────
@callback(
    Output("bias-platforms", "value"),
    Output("bias-network",   "value"),
    Input("btn-bias-autofill", "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    prevent_initial_call=True,
)
def bias_autofill(n_clicks, case_id, user):
    if not n_clicks or not case_id:
        raise PreventUpdate
    case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    case_entities = case.get("case_entities", [])
    platforms, network = set(), set()
    for ce in case_entities:
        ent = ce.get("entity", {})
        etype = ent.get("entity_type", "")
        value = (ent.get("value") or "").lower()
        if etype == "hostname":
            if any(x in value for x in ["linux", "ubuntu", "debian", "centos", "rhel", "fedora"]):
                platforms.add("linux")
            elif any(x in value for x in ["mac", "apple", "darwin"]):
                platforms.add("macos")
            else:
                platforms.add("windows")   # safest default for corp environments
        elif etype == "ip_address":
            network.update(["smb", "rdp", "http"])
        elif etype in ("domain", "url"):
            network.update(["dns", "http", "https"])
        elif etype == "email":
            network.update(["smtp", "imap"])
    return (list(platforms) or no_update, list(network) or no_update)


# ── Notes ───────────────────────────────────────────────────────────────────
@callback(
    Output("case-notes-list", "children"),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("note-body-input", "value"),
    Output("note-edit-id", "data"),
    Output("btn-save-note", "children"),
    Input("btn-save-note", "n_clicks"),
    State("note-body-input", "value"),
    State("note-edit-id", "data"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def save_note(n_clicks, body, edit_id, case_id, user, theme_key):
    if not n_clicks or not body or not case_id:
        raise PreventUpdate
    t = _t(theme_key)
    if edit_id:
        result = _api(
            f"/api/cases/{case_id}/notes/{edit_id}",
            method="PATCH",
            token=user.get("token"),
            data={"body": body},
        )
        ok_msg = "Note updated."
        fail_msg = "Failed to update note."
    else:
        result = _api(
            f"/api/cases/{case_id}/notes",
            method="POST",
            token=user.get("token"),
            data={"body": body},
        )
        ok_msg = "Note added."
        fail_msg = "Failed to add note."
    if result:
        notes = _api(f"/api/cases/{case_id}/notes", token=user.get("token")) or []
        return (
            _build_notes_list(notes, t, can_manage=_can_manage_case(user)),
            _alert_msg(ok_msg),
            "",
            None,
            "Save Note",
        )
    return no_update, _alert_msg(fail_msg, ok=False), no_update, no_update, no_update


@callback(
    Output("case-tasks-list", "children", allow_duplicate=True),
    Output("case-tasks-feedback", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("case-task-count-value", "children", allow_duplicate=True),
    Output("task-text-input", "value", allow_duplicate=True),
    Output("scroll-to-section", "data", allow_duplicate=True),
    Input({"type": "btn-bias-pivot-task", "index": ALL}, "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def add_bias_pivot_task(n_clicks, case_id, user, theme_key):
    if not case_id or not any(n_clicks):
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    pivot_idx = triggered.get("index")
    case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    pivots = _extract_recommended_pivots(case)
    if pivot_idx is None or pivot_idx >= len(pivots):
        raise PreventUpdate
    gap, cand = pivots[pivot_idx]
    task_text = f"Investigate {cand.get('tcode', '?')} between {gap.get('from_tcode', '?')} and {gap.get('to_tcode', '?')}: {_pivot_check_text(cand, gap)}"
    result = _api(
        f"/api/cases/{case_id}/tasks",
        method="POST",
        token=user.get("token"),
        data={"text": task_text, "assignee_id": None, "due_date": None},
    )
    if result:
        t, tasks, assign_opts, can_manage = _task_ui_state(case_id, user, theme_key)
        return (
            _build_tasks_list(tasks, t, can_manage=can_manage, assign_opts=assign_opts, user=user),
            _alert_msg("Pivot added as task."),
            _alert_msg("Pivot added as task."),
            str(len(tasks)),
            "",
            "case-tasks",
        )
    return no_update, _alert_msg("Failed to add pivot task.", ok=False), _alert_msg("Failed to add pivot task.", ok=False), no_update, no_update, no_update


@callback(
    Output("note-body-input", "value", allow_duplicate=True),
    Output("note-edit-id", "data", allow_duplicate=True),
    Output("btn-save-note", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("scroll-to-section", "data", allow_duplicate=True),
    Input({"type": "btn-bias-pivot-note", "index": ALL}, "n_clicks"),
    State("note-body-input", "value"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def add_bias_pivot_note(n_clicks, current_note, case_id, user):
    if not case_id or not any(n_clicks):
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    pivot_idx = triggered.get("index")
    case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    pivots = _extract_recommended_pivots(case)
    if pivot_idx is None or pivot_idx >= len(pivots):
        raise PreventUpdate
    gap, cand = pivots[pivot_idx]
    addition = (
        f"Pivot: {cand.get('tcode', '?')} ({cand.get('tname') or cand.get('name') or cand.get('tcode', '?')})\n"
        f"Gap: {gap.get('from_tcode', '?')} -> {gap.get('to_tcode', '?')}\n"
        f"Why: {_pivot_reason_text(cand)}\n"
        f"Check: {_pivot_check_text(cand, gap)}"
    )
    new_body = f"{(current_note or '').rstrip()}\n\n{addition}".strip()
    return new_body, None, "Save Note", _alert_msg("Pivot added to note draft."), "case-notes"


@callback(
    Output("analysis-feedback", "children"),
    Output("case-notes-list", "children", allow_duplicate=True),
    Output("case-entities-list", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("note-body-input", "value", allow_duplicate=True),
    Output("note-edit-id", "data", allow_duplicate=True),
    Output("btn-save-note", "children", allow_duplicate=True),
    Input("btn-save-analysis", "n_clicks"),
    State("analysis-who", "value"),
    State("analysis-what", "value"),
    State("analysis-when-date", "value"),
    State("analysis-when-time", "value"),
    State("analysis-where", "value"),
    State("analysis-why", "value"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def save_analysis_card(n_clicks, who, what, when_date, when_time, where, why, case_id, user, theme_key):
    if not n_clicks or not case_id:
        raise PreventUpdate
    t = _t(theme_key)
    when_value = None
    if when_date:
        when_value = f"{when_date}T{(when_time or '00:00')}"
    result = _api(
        f"/api/cases/{case_id}/analysis",
        method="POST",
        token=user.get("token"),
        data={
            "who": who,
            "what": what,
            "when": when_value,
            "where": where,
            "why": why,
        },
    )
    if result:
        msg = f"Analysis saved at {datetime.now().strftime('%I:%M %p').lstrip('0')}."
        notes = _api(f"/api/cases/{case_id}/notes", token=user.get("token")) or []
        case = _api(f"/api/cases/{case_id}", token=user.get("token")) or result
        return (
            _alert_msg(msg),
            _build_notes_list(notes, t, can_manage=_can_manage_case(user)),
            _build_entities_list(case.get("case_entities", []), t, can_manage=_can_manage_case(user)),
            _alert_msg(msg),
            "",
            None,
            "Save Note",
        )
    return no_update, no_update, no_update, _alert_msg("Failed to save analysis.", ok=False), no_update, no_update, no_update


@callback(
    Output("hypotheses-feedback", "children"),
    Output("case-notes-list", "children", allow_duplicate=True),
    Output("case-entities-list", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("note-body-input", "value", allow_duplicate=True),
    Output("note-edit-id", "data", allow_duplicate=True),
    Output("btn-save-note", "children", allow_duplicate=True),
    Input("btn-save-hypotheses", "n_clicks"),
    State("analysis-hypothesis", "value"),
    State("analysis-counter-hypothesis", "value"),
    State("analysis-supporting-evidence", "value"),
    State("analysis-disconfirming-evidence", "value"),
    State("hypothesis-log-change", "value"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def save_hypotheses_card(n_clicks, hypothesis, counter_hypothesis, supporting_evidence, disconfirming_evidence, log_change, case_id, user, theme_key):
    if not n_clicks or not case_id:
        raise PreventUpdate
    t = _t(theme_key)
    case_before = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    prior = _parse_analysis(case_before.get("analysis_json"))
    result = _api(
        f"/api/cases/{case_id}/hypotheses",
        method="POST",
        token=user.get("token"),
        data={
            "hypothesis": hypothesis,
            "counter_hypothesis": counter_hypothesis,
            "supporting_evidence": supporting_evidence,
            "disconfirming_evidence": disconfirming_evidence,
        },
    )
    if not result:
        result = _api(
            f"/api/cases/{case_id}/analysis",
            method="POST",
            token=user.get("token"),
            data={
                "who": prior.get("who"),
                "what": prior.get("what"),
                "when": prior.get("when"),
                "where": prior.get("where"),
                "why": prior.get("why"),
                "hypothesis": hypothesis,
                "counter_hypothesis": counter_hypothesis,
                "supporting_evidence": supporting_evidence,
                "disconfirming_evidence": disconfirming_evidence,
            },
        )
    if result:
        logged = False
        if "log" in (log_change or []):
            new_state = {
                "hypothesis": (hypothesis or "").strip(),
                "counter_hypothesis": (counter_hypothesis or "").strip(),
                "supporting_evidence": (supporting_evidence or "").strip(),
                "disconfirming_evidence": (disconfirming_evidence or "").strip(),
            }
            old_state = {
                "hypothesis": (prior.get("hypothesis") or "").strip(),
                "counter_hypothesis": (prior.get("counter_hypothesis") or "").strip(),
                "supporting_evidence": (prior.get("supporting_evidence") or "").strip(),
                "disconfirming_evidence": (prior.get("disconfirming_evidence") or "").strip(),
            }
            changes = []
            labels = {
                "hypothesis": "Hypothesis",
                "counter_hypothesis": "Counter Hypothesis",
                "supporting_evidence": "Supporting Evidence",
                "disconfirming_evidence": "Disconfirming Evidence",
            }
            for key, label in labels.items():
                if old_state[key] != new_state[key]:
                    changes.append(
                        f"{label}:\n"
                        f"Previous: {old_state[key] or '[empty]'}\n"
                        f"Current: {new_state[key] or '[empty]'}"
                    )
            if changes:
                note_body = "[Hypothesis Change]\n" + "\n\n".join(changes)
                note_result = _api(
                    f"/api/cases/{case_id}/notes",
                    method="POST",
                    token=user.get("token"),
                    data={"body": note_body},
                )
                logged = note_result is not None
        notes = _api(f"/api/cases/{case_id}/notes", token=user.get("token")) or []
        case = _api(f"/api/cases/{case_id}", token=user.get("token")) or result
        stamp = datetime.now().strftime('%I:%M %p').lstrip('0')
        msg = f"Hypotheses saved at {stamp} and change logged." if logged else f"Hypotheses saved at {stamp}."
        return (
            _alert_msg(msg),
            _build_notes_list(notes, t, can_manage=_can_manage_case(user)),
            _build_entities_list(case.get("case_entities", []), t, can_manage=_can_manage_case(user)),
            _alert_msg(msg),
            "",
            None,
            "Save Note",
        )
    return no_update, no_update, no_update, _alert_msg("Failed to save hypotheses.", ok=False), no_update, no_update, no_update


@callback(
    Output("note-body-input", "value", allow_duplicate=True),
    Output("note-edit-id", "data", allow_duplicate=True),
    Output("btn-save-note", "children", allow_duplicate=True),
    Input({"type": "btn-edit-note", "index": ALL}, "n_clicks"),
    State({"type": "btn-edit-note", "index": ALL}, "id"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def start_note_edit(n_clicks, ids, case_id, user):
    if not case_id:
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    note_id = triggered.get("index")
    notes = _api(f"/api/cases/{case_id}/notes", token=user.get("token")) or []
    note = next((item for item in notes if item.get("id") == note_id), None)
    if not note:
        raise PreventUpdate
    return note.get("body", ""), note_id, "Save Edit"


@callback(
    Output("note-body-input", "value", allow_duplicate=True),
    Output("note-edit-id", "data", allow_duplicate=True),
    Output("btn-save-note", "children", allow_duplicate=True),
    Input("btn-cancel-note-edit", "n_clicks"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def cancel_note_edit(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    return "", None, "Save Note"


@callback(
    Output("case-notes-list", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Input({"type": "btn-del-note", "index": ALL}, "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_note(n_clicks, case_id, user, theme_key):
    if not case_id or not any(n_clicks):
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    note_id = triggered.get("index")
    t = _t(theme_key)
    result = _api(f"/api/cases/{case_id}/notes/{note_id}", method="DELETE", token=user.get("token"))
    if result is not None:
        notes = _api(f"/api/cases/{case_id}/notes", token=user.get("token")) or []
        return _build_notes_list(notes, t, can_manage=_can_manage_case(user)), _alert_msg("Note deleted.")
    return no_update, _alert_msg("Failed to delete note.", ok=False)


@callback(
    Output("case-tasks-list", "children"),
    Output("case-tasks-feedback", "children"),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("case-task-count-value", "children"),
    Output("task-text-input", "value"),
    Output("task-due-date", "value"),
    Output("task-due-time", "value"),
    Input("btn-add-task", "n_clicks"),
    State("task-text-input", "value"),
    State("task-assignee-input", "value"),
    State("task-due-date", "value"),
    State("task-due-time", "value"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def add_task(n_clicks, text, assignee_id, due_date, due_time, case_id, user, theme_key):
    if not n_clicks or not case_id:
        raise PreventUpdate
    cleaned_text = (text or "").strip()
    if not cleaned_text:
        return no_update, _alert_msg("Task text is required.", ok=False), no_update, no_update, no_update, no_update, no_update
    due_value = None
    if due_date:
        due_value = f"{due_date}T{(due_time or '00:00')}"
    result = _api(
        f"/api/cases/{case_id}/tasks",
        method="POST",
        token=user.get("token"),
        data={"text": cleaned_text, "assignee_id": None if assignee_id in (None, 0, "0") else assignee_id, "due_date": due_value},
    )
    if result:
        t, tasks, assign_opts, can_manage = _task_ui_state(case_id, user, theme_key)
        return (
            _build_tasks_list(tasks, t, can_manage=can_manage, assign_opts=assign_opts, user=user),
            _alert_msg("Task added."),
            _alert_msg("Task added."),
            str(len(tasks)),
            "",
            "",
            "",
        )
    return no_update, _alert_msg("Failed to add task.", ok=False), _alert_msg("Failed to add task.", ok=False), no_update, no_update, no_update, no_update


@callback(
    Output("btn-task-date-picker", "children"),
    Output("btn-task-time-picker", "children"),
    Input("task-due-date", "value"),
    Input("task-due-time", "value"),
)
def update_task_due_picker_labels(due_date, due_time):
    date_label = due_date or "Select Date"
    time_label = due_time or "Select Time"
    return date_label, time_label


@callback(
    Output("btn-analysis-when-date-picker", "children"),
    Output("btn-analysis-when-time-picker", "children"),
    Input("analysis-when-date", "value"),
    Input("analysis-when-time", "value"),
)
def update_analysis_when_picker_labels(when_date, when_time):
    return when_date or "Select Date", when_time or "Select Time"


@callback(
    Output("case-tasks-list", "children", allow_duplicate=True),
    Output("case-tasks-feedback", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("case-task-count-value", "children", allow_duplicate=True),
    Input({"type": "task-check", "index": ALL}, "value"),
    State({"type": "task-check", "index": ALL}, "id"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def check_task_done(values, ids, case_id, user, theme_key):
    if not case_id or not ids:
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    task_id = triggered.get("index")
    if task_id is None:
        raise PreventUpdate
    selected = next((value for value, id_dict in zip(values or [], ids or []) if id_dict.get("index") == task_id), None)
    desired_status = "done" if selected and "done" in selected else "open"
    _case, current_task = _case_task_lookup(case_id, task_id, user)
    current_tasks = _parse_case_tasks((_case or {}).get("checklist_json"), (_case or {}).get("owner"))
    current_status = (current_task.get("status") or "open").lower() if current_task else "open"
    if current_status == desired_status:
        raise PreventUpdate
    if desired_status == "done":
        block_reason = _task_close_block_reason(current_task, current_tasks, user)
        if block_reason:
            return no_update, _alert_msg(block_reason, ok=False), _alert_msg(block_reason, ok=False), no_update
    result = _api(f"/api/cases/{case_id}/tasks/{task_id}", method="PATCH", token=user.get("token"), data={"status": desired_status})
    if result:
        t, tasks, assign_opts, can_manage = _task_ui_state(case_id, user, theme_key)
        msg = "Checklist updated."
        return _build_tasks_list(tasks, t, can_manage=can_manage, assign_opts=assign_opts, user=user), _alert_msg(msg), _alert_msg(msg), str(len(tasks))
    return no_update, _alert_msg("Failed to update checklist item.", ok=False), _alert_msg("Failed to update checklist item.", ok=False), no_update


@callback(
    Output("case-tasks-list", "children", allow_duplicate=True),
    Output("case-tasks-feedback", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("case-task-count-value", "children", allow_duplicate=True),
    Input({"type": "task-status", "index": ALL}, "value"),
    State({"type": "task-status", "index": ALL}, "id"),
    State({"type": "task-assignee", "index": ALL}, "value"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def update_task_status(values, ids, assignees, case_id, user, theme_key):
    if not case_id or not ids:
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    task_id = triggered.get("index")
    if task_id is None:
        raise PreventUpdate
    selected_status = next((value for value, id_dict in zip(values or [], ids or []) if id_dict.get("index") == task_id), None)
    if selected_status is None:
        raise PreventUpdate
    _case, current_task = _case_task_lookup(case_id, task_id, user)
    current_tasks = _parse_case_tasks((_case or {}).get("checklist_json"), (_case or {}).get("owner"))
    if current_task and (current_task.get("status") or "open").lower() == str(selected_status).lower():
        raise PreventUpdate
    if str(selected_status).lower() == "done":
        block_reason = _task_close_block_reason(current_task, current_tasks, user)
        if block_reason:
            return no_update, _alert_msg(block_reason, ok=False), _alert_msg(block_reason, ok=False), no_update
    result = _api(f"/api/cases/{case_id}/tasks/{task_id}", method="PATCH", token=user.get("token"), data={"status": selected_status})
    if result:
        t, tasks, assign_opts, can_manage = _task_ui_state(case_id, user, theme_key)
        return _build_tasks_list(tasks, t, can_manage=can_manage, assign_opts=assign_opts, user=user), _alert_msg("Task updated."), _alert_msg("Task updated."), str(len(tasks))
    return no_update, _alert_msg("Failed to update task.", ok=False), _alert_msg("Failed to update task.", ok=False), no_update


@callback(
    Output("case-tasks-list", "children", allow_duplicate=True),
    Output("case-tasks-feedback", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("case-task-count-value", "children", allow_duplicate=True),
    Input({"type": "task-assignee", "index": ALL}, "value"),
    State({"type": "task-assignee", "index": ALL}, "id"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def update_task_assignee(values, ids, case_id, user, theme_key):
    if not case_id or not ids:
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    task_id = triggered.get("index")
    if task_id is None:
        raise PreventUpdate
    assignee = next((value for value, id_dict in zip(values or [], ids or []) if id_dict.get("index") == task_id), None)
    _case, current_task = _case_task_lookup(case_id, task_id, user)
    current_assignee = current_task.get("assignee_id") if current_task else None
    normalized_assignee = None if assignee in (None, 0, "0") else assignee
    if current_task and current_assignee == normalized_assignee:
        raise PreventUpdate
    result = _api(
        f"/api/cases/{case_id}/tasks/{task_id}",
        method="PATCH",
        token=user.get("token"),
        data={"assignee_id": normalized_assignee},
    )
    if result:
        t, tasks, assign_opts, can_manage = _task_ui_state(case_id, user, theme_key)
        return _build_tasks_list(tasks, t, can_manage=can_manage, assign_opts=assign_opts, user=user), _alert_msg("Task assignee updated."), _alert_msg("Task assignee updated."), str(len(tasks))
    return no_update, _alert_msg("Failed to update task assignee.", ok=False), _alert_msg("Failed to update task assignee.", ok=False), no_update


@callback(
    Output("case-tasks-list", "children", allow_duplicate=True),
    Output("case-tasks-feedback", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("case-task-count-value", "children", allow_duplicate=True),
    Input({"type": "btn-del-task", "index": ALL}, "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_task(n_clicks, case_id, user, theme_key):
    if not case_id or not any(n_clicks):
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    task_id = triggered.get("index")
    if task_id is None:
        raise PreventUpdate
    result = _api(f"/api/cases/{case_id}/tasks/{task_id}", method="DELETE", token=user.get("token"))
    if result:
        t, tasks, assign_opts, can_manage = _task_ui_state(case_id, user, theme_key)
        return _build_tasks_list(tasks, t, can_manage=can_manage, assign_opts=assign_opts, user=user), _alert_msg("Task removed."), _alert_msg("Task removed."), str(len(tasks))
    return no_update, _alert_msg("Failed to remove task.", ok=False), _alert_msg("Failed to remove task.", ok=False), no_update


@callback(
    Output("case-tasks-list", "children", allow_duplicate=True),
    Output("case-task-count-value", "children", allow_duplicate=True),
    Input("case-refresh-interval", "n_intervals"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def refresh_case_tasks(_n_intervals, case_id, user, theme_key):
    # Keep task/subtask inputs stable while the user is typing. The task section
    # already refreshes immediately after task add/update/delete actions.
    raise PreventUpdate


@callback(
    Output("case-tasks-list", "children", allow_duplicate=True),
    Output("case-tasks-feedback", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("case-task-count-value", "children", allow_duplicate=True),
    Input({"type": "btn-add-subtask", "index": ALL}, "n_clicks"),
    State({"type": "subtask-text-input", "index": ALL}, "value"),
    State({"type": "subtask-assignee-input", "index": ALL}, "value"),
    State({"type": "btn-add-subtask", "index": ALL}, "id"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def add_subtask(n_clicks_list, texts, assignee_ids, btn_ids, case_id, user, theme_key):
    if not case_id or not any(n_clicks_list):
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    parent_id = triggered.get("index")
    if parent_id is None:
        raise PreventUpdate
    cleaned_text = next(
        (str(text or "").strip() for text, btn_id in zip(texts or [], btn_ids or []) if btn_id.get("index") == parent_id),
        "",
    )
    assignee_id = next(
        (value for value, btn_id in zip(assignee_ids or [], btn_ids or []) if btn_id.get("index") == parent_id),
        None,
    )
    if not cleaned_text:
        return no_update, _alert_msg("Subtask text is required.", ok=False), no_update, no_update
    result = _api(
        f"/api/cases/{case_id}/tasks",
        method="POST",
        token=user.get("token"),
        data={
            "text": cleaned_text,
            "assignee_id": None if assignee_id in (None, 0, "0") else assignee_id,
            "parent_id": parent_id,
        },
    )
    if result:
        t, tasks, assign_opts, can_manage = _task_ui_state(case_id, user, theme_key)
        return (
            _build_tasks_list(tasks, t, can_manage=can_manage, assign_opts=assign_opts, user=user),
            _alert_msg("Subtask added."),
            _alert_msg("Subtask added."),
            str(len(tasks)),
        )
    return no_update, _alert_msg("Failed to add subtask.", ok=False), _alert_msg("Failed to add subtask.", ok=False), no_update


@callback(
    Output("my-work-list", "children"),
    Output("my-work-feedback", "children"),
    Input({"type": "my-work-task-check", "case": ALL, "task": ALL}, "value"),
    Input({"type": "my-work-task-status", "case": ALL, "task": ALL}, "value"),
    State({"type": "my-work-task-check", "case": ALL, "task": ALL}, "id"),
    State({"type": "my-work-task-status", "case": ALL, "task": ALL}, "id"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
)
def update_my_work_task_status(check_values, values, check_ids, ids, user, theme_key):
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    case_id = triggered.get("case")
    task_id = triggered.get("task")
    if not case_id or not task_id:
        raise PreventUpdate
    if triggered.get("type") == "my-work-task-check":
        selected = next(
            (
                value for value, id_dict in zip(check_values or [], check_ids or [])
                if id_dict.get("case") == case_id and id_dict.get("task") == task_id
            ),
            None,
        )
        selected_status = "done" if selected and "done" in selected else "open"
    else:
        selected_status = next(
            (
                value for value, id_dict in zip(values or [], ids or [])
                if id_dict.get("case") == case_id and id_dict.get("task") == task_id
            ),
            None,
        )
        if selected_status is None:
            raise PreventUpdate
    case, current_task = _case_task_lookup(case_id, task_id, user)
    current_tasks = _parse_case_tasks((case or {}).get("checklist_json"), (case or {}).get("owner"))
    if current_task and (current_task.get("status") or "open").lower() == str(selected_status).lower():
        raise PreventUpdate
    if str(selected_status).lower() == "done":
        block_reason = _task_close_block_reason(current_task, current_tasks, user)
        if block_reason:
            cases = _api("/api/cases", token=user.get("token")) or []
            return _build_my_work_list(cases, _t(theme_key), user), _alert_msg(block_reason, ok=False)
    locked_by_other, locked_by = _case_lock_state(case, user)
    if locked_by_other:
        cases = _api("/api/cases", token=user.get("token")) or []
        return _build_my_work_list(cases, _t(theme_key), user), _alert_msg(f"Case open - {locked_by}", ok=False)
    lock_result = _api(f"/api/cases/{case_id}/lock", method="POST", token=user.get("token"))
    if not lock_result:
        refreshed_case = _api(f"/api/cases/{case_id}", token=user.get("token")) or case
        locked_by_other, locked_by = _case_lock_state(refreshed_case, user)
        msg = f"Case open - {locked_by}" if locked_by_other else "Failed to lock case for task update."
        cases = _api("/api/cases", token=user.get("token")) or []
        return _build_my_work_list(cases, _t(theme_key), user), _alert_msg(msg, ok=False)
    result = _api(
        f"/api/cases/{case_id}/tasks/{task_id}",
        method="PATCH",
        token=user.get("token"),
        data={"status": selected_status},
    )
    cases = _api("/api/cases", token=user.get("token")) or []
    if result:
        return _build_my_work_list(cases, _t(theme_key), user), _alert_msg("Task updated.")
    return _build_my_work_list(cases, _t(theme_key), user), _alert_msg("Failed to update task.", ok=False)


# ── Add entity to case ──────────────────────────────────────────────────────
@callback(
    Output("case-entities-list", "children"),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Output("ent-value-input", "value"),
    Output("ent-desc-input", "value"),
    Input("btn-add-entity", "n_clicks"),
    State("ent-type-input", "value"),
    State("ent-value-input", "value"),
    State("ent-desc-input", "value"),
    State("ent-role-input", "value"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def add_entity(n_clicks, ent_type, ent_value, ent_desc, ent_role, case_id, user, theme_key):
    if not n_clicks or not ent_type or not ent_value or not case_id:
        raise PreventUpdate
    t = _t(theme_key)
    result = _api(f"/api/entities/case/{case_id}", method="POST",
                  token=user.get("token"), data={
                      "create": {"entity_type": ent_type, "value": ent_value,
                                 "description": ent_desc or None},
                      "role": ent_role or "associated",
                  })
    if result:
        case = _api(f"/api/cases/{case_id}", token=user.get("token"))
        entities = (case or {}).get("case_entities", [])
        can_manage = _can_manage_case(user)
        return (_build_entities_list(entities, t, can_manage=can_manage),
                _alert_msg(f"Entity '{ent_value}' added."), "", "")
    return no_update, _alert_msg("Failed to add entity.", ok=False), no_update, no_update


@callback(
    Output("case-entities-list", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Input({"type": "btn-del-case-entity", "index": ALL}, "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_case_entity(n_clicks, case_id, user, theme_key):
    if not case_id or not any(n_clicks):
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    link_id = triggered.get("index")
    if link_id is None:
        raise PreventUpdate

    t = _t(theme_key)
    result = _api(f"/api/entities/case/{case_id}/{link_id}", method="DELETE", token=user.get("token"))
    if result is None:
        return no_update, _alert_msg("Failed to remove entity from case.", ok=False)
    case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    can_manage = _can_manage_case(user)
    return _build_entities_list(case.get("case_entities", []), t, can_manage=can_manage), _alert_msg("Entity removed from case.")


@callback(
    Output("case-entities-list", "children", allow_duplicate=True),
    Input("case-refresh-interval", "n_intervals"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def refresh_case_entities(_n_intervals, case_id, user, theme_key):
    if not case_id:
        raise PreventUpdate
    case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    t = _t(theme_key)
    can_manage = _can_manage_case(user)
    return _build_entities_list(case.get("case_entities", []), t, can_manage=can_manage)


@callback(
    Output("relationships-detail-panel", "children"),
    Input("relationships-graph", "tapNodeData"),
    Input("relationships-focus-case", "value"),
    Input("btn-relationships-focus-strongest", "n_clicks"),
    State("relationships-store", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
)
def show_relationship_detail(node_data, selected_case_id, _focus_strongest_clicks, relationships_store, theme_key):
    t = _t(theme_key)
    if not relationships_store:
        return html.P("No relationship details available.", style={"color": t["muted"], "margin": 0})
    rel_store = relationships_store.get("relationships", {}) if isinstance(relationships_store, dict) else {}
    strongest_case_id = relationships_store.get("strongest_case_id") if isinstance(relationships_store, dict) else None
    triggered = dash.ctx.triggered_id
    if isinstance(triggered, str) and triggered == "btn-relationships-focus-strongest":
        target_case_id = strongest_case_id
    elif isinstance(triggered, str) and triggered == "relationships-focus-case":
        target_case_id = str(selected_case_id or strongest_case_id or "")
    elif isinstance(triggered, str) and triggered == "relationships-graph" and node_data and isinstance(node_data, dict) and node_data.get("id"):
        target_case_id = str(node_data.get("id"))
    else:
        target_case_id = str(selected_case_id or strongest_case_id or "")
    return _build_relationship_detail(target_case_id, rel_store, t)


# ── Upload attachment ───────────────────────────────────────────────────────
@callback(
    Output("case-attachments-list", "children"),
    Output("upload-feedback", "children"),
    Input("upload-attachment", "contents"),
    State("upload-attachment", "filename"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
)
def upload_attachment(contents, filename, case_id, user, theme_key):
    if not contents or not filename or not case_id:
        raise PreventUpdate
    t = _t(theme_key)
    try:
        _, b64 = contents.split(",", 1)
        file_bytes = base64.b64decode(b64)
        mime = contents.split(";")[0].split(":")[1] if ":" in contents else "application/octet-stream"
        result = _api(f"/api/attachments/case/{case_id}", method="POST",
                      token=user.get("token"),
                      files={"file": (filename, io.BytesIO(file_bytes), mime)})
        if result:
            case = _api(f"/api/cases/{case_id}", token=user.get("token"))
            attachments = (case or {}).get("attachments", [])
            can_manage = _can_manage_case(user)
            return (_build_attachments_list(attachments, t, token=user.get("token"),
                                            can_manage=can_manage),
                    _alert_msg(f"Uploaded: {filename}"))
        return no_update, _alert_msg("Upload failed.", ok=False)
    except Exception as exc:
        return no_update, _alert_msg(f"Error: {exc}", ok=False)


@callback(
    Output("case-attachments-list", "children", allow_duplicate=True),
    Output("case-action-feedback", "children", allow_duplicate=True),
    Input({"type": "btn-del-attachment", "index": ALL}, "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    State("store-theme", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_attachment(n_clicks, case_id, user, theme_key):
    if not case_id or not any(n_clicks):
        raise PreventUpdate
    triggered = dash.ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        raise PreventUpdate
    attachment_id = triggered.get("index")
    if attachment_id is None:
        raise PreventUpdate

    t = _t(theme_key)
    result = _api(f"/api/attachments/{attachment_id}", method="DELETE", token=user.get("token"))
    if result is None:
        return no_update, _alert_msg("Failed to remove attachment.", ok=False)

    case = _api(f"/api/cases/{case_id}", token=user.get("token")) or {}
    can_manage = _can_manage_case(user)
    return (
        _build_attachments_list(case.get("attachments", []), t, token=user.get("token"),
                                can_manage=can_manage),
        _alert_msg("Attachment removed."),
    )


# ── Export reports ──────────────────────────────────────────────────────────
@callback(
    Output("download-report", "data"),
    Input("btn-export-pdf", "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    prevent_initial_call=True,
)
def export_pdf(n_clicks, case_id, user):
    if not n_clicks or not case_id:
        raise PreventUpdate
    content = _api_bytes(f"/api/reports/{case_id}/pdf", token=user.get("token", ""))
    if content:
        return dcc.send_bytes(content, f"case_{case_id}_report.pdf")
    raise PreventUpdate


@callback(
    Output("download-report", "data", allow_duplicate=True),
    Input("btn-export-docx", "n_clicks"),
    State("store-case-id", "data"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def export_docx(n_clicks, case_id, user):
    if not n_clicks or not case_id:
        raise PreventUpdate
    content = _api_bytes(f"/api/reports/{case_id}/docx", token=user.get("token", ""))
    if content:
        return dcc.send_bytes(content, f"case_{case_id}_report.docx")
    raise PreventUpdate


# ── Staging: promote / dismiss ──────────────────────────────────────────────
@callback(
    Output("staging-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "chain-promote", "index": ALL}, "n_clicks"),
    State({"type": "chain-promote", "index": ALL}, "id"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def promote_chain(n_clicks_list, ids, user):
    for n, id_dict in zip(n_clicks_list or [], ids or []):
        if n:
            chain_id = id_dict["index"]
            result = _api(f"/api/staging/{chain_id}/promote", method="POST",
                          token=user.get("token"))
            if result and result.get("id"):
                return no_update, f"/case/{result['id']}"
            return _alert_msg("Failed to promote chain.", ok=False), no_update
    raise PreventUpdate


@callback(
    Output("staging-feedback", "children", allow_duplicate=True),
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "chain-dismiss", "index": ALL}, "n_clicks"),
    State({"type": "chain-dismiss", "index": ALL}, "id"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def dismiss_chain(n_clicks_list, ids, user):
    for n, id_dict in zip(n_clicks_list or [], ids or []):
        if n:
            chain_id = id_dict["index"]
            _api(f"/api/staging/{chain_id}", method="DELETE", token=user.get("token"))
            return _alert_msg("Chain dismissed."), "/staging"
    raise PreventUpdate


@callback(
    Output("staging-feedback", "children", allow_duplicate=True),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-create-test-staging", "n_clicks"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def create_test_staging_item(n_clicks, user):
    if not n_clicks:
        raise PreventUpdate
    seed = datetime.utcnow().strftime("%H%M%S")
    payload = {
        "source_type": "list_demo",
        "hostname": f"demo-host-{seed}",
        "src_ip": f"10.20.{int(seed[-2:]) % 20}.{(int(seed[-2:]) % 200) + 10}",
        "severity": "medium",
        "tcode": "T1059|T1082|T1071",
        "tname": "LIST demo chain",
        "chain_key": f"demo-chain-{uuid.uuid4().hex[:10]}",
        "raw_data": {"demo": True, "created_by": user.get("username")},
    }
    result = _api("/api/ingest/alert", method="POST", token=user.get("token"), data=payload)
    if result and result.get("chain_id"):
        return _alert_msg("Test staging item created. You can now promote or dismiss it."), "/staging"
    return _alert_msg("Failed to create test staging item.", ok=False), no_update


# ── Templates: create ───────────────────────────────────────────────────────
@callback(
    Output("templates-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-tmpl-create", "n_clicks"),
    State("tmpl-new-name", "value"),
    State("tmpl-new-desc", "value"),
    State("tmpl-new-severity", "value"),
    State("tmpl-new-tcodes", "value"),
    State("tmpl-new-checklist", "value"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def create_template(n_clicks, name, desc, severity, tcodes, checklist_raw, user):
    if not n_clicks or not name:
        raise PreventUpdate
    checklist_json = None
    if checklist_raw:
        items = [s.strip() for s in checklist_raw.split(",") if s.strip()]
        checklist_json = json.dumps(items)
    result = _api("/api/templates", method="POST", token=user.get("token"), data={
        "name": name, "description": desc or None,
        "default_severity": (severity or "MEDIUM").lower(),
        "initial_tcodes": tcodes or None,
        "checklist_json": checklist_json,
    })
    if result and result.get("id"):
        return no_update, "/templates"
    return _alert_msg("Failed to create template (name may already exist).", ok=False), no_update


# ── Templates: open edit modal ──────────────────────────────────────────────
@callback(
    Output("modal-tmpl-edit", "is_open"),
    Output("tmpl-edit-id", "value"),
    Output("tmpl-edit-name", "value"),
    Output("tmpl-edit-desc", "value"),
    Output("tmpl-edit-severity", "value"),
    Output("tmpl-edit-tcodes", "value"),
    Output("tmpl-edit-checklist", "value"),
    Input({"type": "tmpl-edit-btn", "index": ALL}, "n_clicks"),
    Input("btn-tmpl-edit-cancel", "n_clicks"),
    State({"type": "tmpl-edit-btn", "index": ALL}, "id"),
    State("store-user", "data"),
    prevent_initial_call=True,
)
def toggle_edit_modal(edit_clicks, cancel_click, ids, user):
    from dash import ctx
    triggered = ctx.triggered_id
    if triggered == "btn-tmpl-edit-cancel" or not any(edit_clicks or []):
        return False, no_update, no_update, no_update, no_update, no_update, no_update
    for n, id_dict in zip(edit_clicks or [], ids or []):
        if n:
            tmpl = _api(f"/api/templates/{id_dict['index']}", token=user.get("token"))
            if tmpl:
                return (True, str(tmpl["id"]), tmpl.get("name", ""),
                        tmpl.get("description") or "",
                        (tmpl.get("default_severity") or "medium").upper(),
                        tmpl.get("initial_tcodes") or "",
                        tmpl.get("checklist_json") or "")
    return False, no_update, no_update, no_update, no_update, no_update, no_update


# ── Templates: save edit ────────────────────────────────────────────────────
@callback(
    Output("tmpl-edit-feedback", "children"),
    Output("modal-tmpl-edit", "is_open", allow_duplicate=True),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-tmpl-edit-save", "n_clicks"),
    State("tmpl-edit-id", "value"),
    State("tmpl-edit-name", "value"),
    State("tmpl-edit-desc", "value"),
    State("tmpl-edit-severity", "value"),
    State("tmpl-edit-tcodes", "value"),
    State("tmpl-edit-checklist", "value"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def save_template_edit(n_clicks, tmpl_id, name, desc, severity, tcodes, checklist, user):
    if not n_clicks or not tmpl_id:
        raise PreventUpdate
    result = _api(f"/api/templates/{tmpl_id}", method="PATCH", token=user.get("token"), data={
        "name": name, "description": desc or None,
        "default_severity": (severity or "MEDIUM").lower(),
        "initial_tcodes": tcodes or None,
        "checklist_json": checklist or None,
    })
    if result and result.get("id"):
        return no_update, False, "/templates"
    return _alert_msg("Save failed.", ok=False), True, no_update


# ── Templates: delete ────────────────────────────────────────────────────────
@callback(
    Output("templates-feedback", "children", allow_duplicate=True),
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "tmpl-del-btn", "index": ALL}, "n_clicks"),
    State({"type": "tmpl-del-btn", "index": ALL}, "id"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_template(n_clicks_list, ids, user):
    for n, id_dict in zip(n_clicks_list or [], ids or []):
        if n:
            _api(f"/api/templates/{id_dict['index']}", method="DELETE", token=user.get("token"))
            return no_update, "/templates"
    raise PreventUpdate


# ── Entity Library: toggle link-to-case panel ────────────────────────────────
@callback(
    Output({"type": "ent-link-collapse", "index": MATCH}, "is_open"),
    Input({"type":  "ent-link-toggle",   "index": MATCH}, "n_clicks"),
    State({"type":  "ent-link-collapse", "index": MATCH}, "is_open"),
    prevent_initial_call=True,
)
def toggle_ent_link_panel(n_clicks, is_open):
    if n_clicks:
        return not is_open
    raise PreventUpdate


# ── Entity Library: link entity to case ──────────────────────────────────────
@callback(
    Output({"type": "ent-link-feedback", "index": MATCH}, "children"),
    Output({"type": "ent-link-collapse", "index": MATCH}, "is_open",
           allow_duplicate=True),
    Input({"type":  "ent-case-dd",       "index": MATCH}, "value"),
    State({"type":  "ent-role-dd",       "index": MATCH}, "value"),
    State({"type":  "ent-case-dd",       "index": MATCH}, "id"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def link_entity_to_case(case_id, role, dd_id, user):
    if not case_id:
        raise PreventUpdate
    entity_id = dd_id["index"]
    result = _api(f"/api/entities/case/{case_id}", method="POST",
                  token=user.get("token"), data={
                      "entity_id": entity_id,
                      "role": role or "associated",
                  })
    if result and result.get("id"):
        return _alert_msg("Entity Linked"), False
    return _alert_msg("Link failed (may already be linked).", ok=False), True


# ── Global entity create ─────────────────────────────────────────────────────
@callback(
    Output("glob-ent-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-glob-ent-create", "n_clicks"),
    State("glob-ent-type", "value"),
    State("glob-ent-value", "value"),
    State("glob-ent-desc", "value"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def create_global_entity(n_clicks, ent_type, ent_value, ent_desc, user):
    if not n_clicks or not ent_type or not ent_value:
        raise PreventUpdate
    result = _api("/api/entities/", method="POST", token=user.get("token"), data={
        "entity_type": ent_type, "value": ent_value, "description": ent_desc or None,
    })
    if result and result.get("id"):
        return no_update, "/entities"
    return _alert_msg("Failed to create entity.", ok=False), no_update


# ── Backup ───────────────────────────────────────────────────────────────────
@callback(
    Output("download-backup", "data"),
    Output("backup-feedback", "children"),
    Input("btn-backup", "n_clicks"),
    State("store-user", "data"),
    prevent_initial_call=True,
)
def trigger_backup(n_clicks, user):
    if not n_clicks:
        raise PreventUpdate
    content = _api_bytes("/api/backup/download", token=user.get("token", ""))
    if content:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return dcc.send_bytes(content, f"list_backup_{ts}.zip"), _alert_msg("Backup ready — downloading.")
    return no_update, _alert_msg("Backup failed. (Admin role required.)", ok=False)


@callback(
    Output("import-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("upload-backup", "contents"),
    State("upload-backup", "filename"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def import_backup(contents, filename, user):
    if not contents or not filename:
        raise PreventUpdate
    try:
        _, b64 = contents.split(",", 1)
        file_bytes = base64.b64decode(b64)
        result = _api(
            "/api/backup/restore",
            method="POST",
            token=user.get("token"),
            files={"file": (filename, io.BytesIO(file_bytes), "application/zip")},
        )
        if result and result.get("detail"):
            return _alert_msg("Backup imported. Cases, entities, and attachments have been restored."), "/cases"
        return _alert_msg("Import failed. Analyst or admin role required, and the file must be a valid LIST backup zip.", ok=False), no_update
    except Exception as exc:
        return _alert_msg(f"Import error: {exc}", ok=False), no_update


@callback(
    Output("reset-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-reset-list", "n_clicks"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def trigger_reset_list(n_clicks, user):
    if not n_clicks:
        raise PreventUpdate
    url = f"{API_BASE}/api/backup/reset"
    headers = {"Authorization": f"Bearer {user.get('token', '')}"}
    try:
        r = requests.post(url, headers=headers, timeout=30)
        if r.ok:
            return _alert_msg("LIST data reset complete."), "/cases"
        if r.status_code == 401:
            return _alert_msg("Reset failed: your session is no longer valid. Log out and back in, then try again.", ok=False), no_update
        if r.status_code == 403:
            return _alert_msg("Reset failed: admin role required.", ok=False), no_update
        if r.status_code == 404:
            return _alert_msg("Reset failed: the API does not have the reset route loaded yet. Restart the API and try again.", ok=False), no_update
        try:
            detail = (r.json() or {}).get("detail")
        except Exception:
            detail = None
        return _alert_msg(f"Reset failed: {detail or r.text[:200] or 'unknown error'}", ok=False), no_update
    except Exception as exc:
        return _alert_msg(f"Reset failed: {exc}", ok=False), no_update


@callback(
    Output("attachment-limit-feedback", "children"),
    Input("btn-save-attachment-limit", "n_clicks"),
    State("attachment-limit-mb", "value"),
    State("store-user", "data"),
    prevent_initial_call=True,
)
def save_attachment_limit(n_clicks, max_mb, user):
    if not n_clicks:
        raise PreventUpdate
    try:
        max_mb = int(max_mb or 0)
    except Exception:
        return _alert_msg("Attachment limit must be a whole number of MB.", ok=False)
    if max_mb < 1:
        return _alert_msg("Attachment limit must be at least 1 MB.", ok=False)

    result = _api("/api/attachments/settings", method="PATCH", token=user.get("token"), data={"max_mb": max_mb})
    if not result:
        return _alert_msg("Failed to save attachment limit. Admin role required.", ok=False)
    if max_mb >= 1024:
        return _alert_msg(f"Attachment limit saved at {max_mb} MB. Warning: 1 GB+ uploads can significantly tax LIST during upload, backup, restore, and concurrent use.")
    if max_mb > 250:
        return _alert_msg(f"Attachment limit saved at {max_mb} MB. Larger files increase memory, storage, and backup overhead.")
    return _alert_msg(f"Attachment limit saved at {max_mb} MB.")


# ── User management: create ──────────────────────────────────────────────────
@callback(
    Output("new-user-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-user-create", "n_clicks"),
    State("new-user-username", "value"),
    State("new-user-email", "value"),
    State("new-user-password", "value"),
    State("new-user-role", "value"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def create_user(n_clicks, username, email, password, role, user):
    if not n_clicks:
        raise PreventUpdate
    if not username or not email or not password:
        return _alert_msg("Username, email, and password are required.", ok=False), no_update
    result = _api("/api/auth/register", method="POST", token=user.get("token"), data={
        "username": username, "email": email, "password": password,
        "role": role or "analyst",
    })
    if result and result.get("id"):
        return no_update, "/admin"
    return _alert_msg("Failed to create user (username or email may already exist).",
                      ok=False), no_update


# ── User management: open edit modal ─────────────────────────────────────────
@callback(
    Output("modal-user-edit", "is_open"),
    Output("user-edit-id", "value"),
    Output("user-edit-username", "value"),
    Output("user-edit-email", "value"),
    Output("user-edit-role", "value"),
    Output("user-edit-active", "value"),
    Output("user-edit-password", "value"),
    Input({"type": "user-edit-btn", "index": ALL}, "n_clicks"),
    Input("btn-user-edit-cancel", "n_clicks"),
    State({"type": "user-edit-btn", "index": ALL}, "id"),
    State("store-user", "data"),
    prevent_initial_call=True,
)
def toggle_user_edit_modal(edit_clicks, cancel_click, ids, user):
    from dash import ctx
    if ctx.triggered_id == "btn-user-edit-cancel" or not any(edit_clicks or []):
        return False, no_update, no_update, no_update, no_update, no_update, no_update
    for n, id_dict in zip(edit_clicks or [], ids or []):
        if n:
            users = _api("/api/auth/users", token=user.get("token")) or []
            u = next((x for x in users if x["id"] == id_dict["index"]), None)
            if u:
                return (True, str(u["id"]), u.get("username", ""),
                        u.get("email", ""), u.get("role", "analyst"),
                        u.get("is_active", True), "")
    return False, no_update, no_update, no_update, no_update, no_update, no_update


# ── User management: save edit ────────────────────────────────────────────────
@callback(
    Output("user-edit-feedback", "children"),
    Output("modal-user-edit", "is_open", allow_duplicate=True),
    Output("url", "pathname", allow_duplicate=True),
    Input("btn-user-edit-save", "n_clicks"),
    State("user-edit-id", "value"),
    State("user-edit-username", "value"),
    State("user-edit-email", "value"),
    State("user-edit-role", "value"),
    State("user-edit-active", "value"),
    State("user-edit-password", "value"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def save_user_edit(n_clicks, user_id, username, email, role, is_active, password, user):
    if not n_clicks or not user_id:
        raise PreventUpdate
    patch = {"username": username, "email": email, "role": role, "is_active": is_active}
    if password:
        patch["password"] = password
    result = _api(f"/api/auth/users/{user_id}", method="PATCH",
                  token=user.get("token"), data=patch)
    if result and result.get("id"):
        return no_update, False, "/admin"
    return _alert_msg("Save failed. The last admin cannot be demoted.", ok=False), True, no_update


# ── User management: delete ───────────────────────────────────────────────────
@callback(
    Output("admin-user-feedback", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input({"type": "user-del-btn", "index": ALL}, "n_clicks"),
    State({"type": "user-del-btn", "index": ALL}, "id"),
    State("store-user", "data"),
    prevent_initial_call=True,
    allow_duplicate=True,
)
def delete_user(n_clicks_list, ids, user):
    for n, id_dict in zip(n_clicks_list or [], ids or []):
        if n:
            user_id = id_dict["index"]
            url_path = f"/api/auth/users/{user_id}"
            # DELETE returns 204 (no content) on success
            import requests as _req
            r = _req.delete(
                f"{API_BASE}{url_path}",
                headers={"Authorization": f"Bearer {user.get('token','')}"},
                timeout=10,
            )
            if r.status_code == 204:
                return no_update, "/admin"
            try:
                detail = r.json().get("detail", "Delete failed.")
            except Exception:
                detail = "Delete failed."
            return _alert_msg(detail, ok=False), no_update
    raise PreventUpdate


if __name__ == "__main__":
    gui_port = int(os.getenv("LIST_GUI_PORT", "8050"))
    app.run(debug=True, host="0.0.0.0", port=gui_port)
