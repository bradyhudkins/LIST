import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _discover_bias_path() -> str:
    explicit = os.getenv("BIAS_PATH", "").strip()
    if explicit:
        return explicit

    candidates = (
        os.path.join(BASE_DIR, "BIAS"),
        os.path.join(os.path.dirname(BASE_DIR), "BIAS"),
        os.path.join(BASE_DIR, "bias"),
        os.path.join(os.path.dirname(BASE_DIR), "bias"),
    )
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return ""


def _discover_caldera_path() -> str:
    explicit = os.getenv("CALDERA_PATH", "").strip()
    if explicit:
        return explicit

    candidates = (
        os.path.join(BASE_DIR, "stockpile"),
        os.path.join(BASE_DIR, "CALDERA"),
        os.path.join(BASE_DIR, "caldera"),
        os.path.join(BASE_DIR, "BIASv2", "stockpile"),
        os.path.join(os.path.dirname(BASE_DIR), "stockpile"),
        os.path.join(os.path.dirname(BASE_DIR), "CALDERA"),
        os.path.join(os.path.dirname(BASE_DIR), "caldera"),
        os.path.join(os.path.dirname(BASE_DIR), "BIASv2", "stockpile"),
    )
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return ""

# ── Ports ─────────────────────────────────────────────────────────────────────
API_PORT = int(os.getenv("LIST_API_PORT", "8000"))
GUI_PORT = int(os.getenv("LIST_GUI_PORT", "8050"))

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "LIST_DATABASE_URL",
    f"sqlite:///{os.path.join(BASE_DIR, 'list.db')}",
)

# ── Auth ──────────────────────────────────────────────────────────────────────
SECRET_KEY  = os.getenv("LIST_SECRET_KEY") or secrets.token_urlsafe(32)
ALGORITHM   = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours
CASE_LOCK_TIMEOUT_SECONDS = int(os.getenv("LIST_CASE_LOCK_TIMEOUT_SECONDS", "45"))

# ── Storage ───────────────────────────────────────────────────────────────────
ATTACHMENTS_DIR = os.getenv(
    "LIST_ATTACHMENTS_DIR",
    os.path.join(BASE_DIR, "attachments"),
)
REPORTS_DIR = os.getenv(
    "LIST_REPORTS_DIR",
    os.path.join(BASE_DIR, "reports"),
)
BACKUP_DIR = os.getenv(
    "LIST_BACKUP_DIR",
    os.path.join(BASE_DIR, "backups"),
)
DEFAULT_MAX_ATTACHMENT_MB = int(os.getenv("LIST_MAX_ATTACHMENT_MB", "250"))

# ── BIAS ──────────────────────────────────────────────────────────────────────
BIAS_PATH = _discover_bias_path()

# ── CALDERA ───────────────────────────────────────────────────────────────────
CALDERA_PATH = _discover_caldera_path()

# ── Notifications (scaffolded — disabled until configured) ────────────────────
NOTIFY_EMAIL_ENABLED  = os.getenv("LIST_NOTIFY_EMAIL", "false").lower() == "true"
NOTIFY_SLACK_ENABLED  = os.getenv("LIST_NOTIFY_SLACK", "false").lower() == "true"
NOTIFY_TEAMS_ENABLED  = os.getenv("LIST_NOTIFY_TEAMS", "false").lower() == "true"

SMTP_HOST     = os.getenv("LIST_SMTP_HOST", "")
SMTP_PORT     = int(os.getenv("LIST_SMTP_PORT", "587"))
SMTP_USER     = os.getenv("LIST_SMTP_USER", "")
SMTP_PASSWORD = os.getenv("LIST_SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("LIST_SMTP_FROM", "list@localhost")

SLACK_WEBHOOK_URL = os.getenv("LIST_SLACK_WEBHOOK", "")
TEAMS_WEBHOOK_URL = os.getenv("LIST_TEAMS_WEBHOOK", "")
