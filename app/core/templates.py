from __future__ import annotations
from pathlib import Path
from datetime import date as _date, datetime as _dt
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# --- Locate project root that actually contains /templates and /static ---
_here = Path(__file__).resolve()
# try repo root: .../app/core -> parents[2] == repo root
_candidates = [
    _here.parents[2],      # repo root (expected)
    _here.parents[1],      # /app
    _here.parents[0],      # /app/core
]

TEMPLATES_DIR = None
STATIC_DIR = None
for base in _candidates:
    t = base / "templates"
    s = base / "static"
    if t.exists():
        TEMPLATES_DIR = t
        STATIC_DIR = s if s.exists() else None
        break

if TEMPLATES_DIR is None:
    # last resort – avoid crashing; Jinja will still 404 clearly
    TEMPLATES_DIR = _here.parents[1] / "templates"

print(f"[templates] Using templates dir: {TEMPLATES_DIR}")
if STATIC_DIR:
    print(f"[templates] Using static dir:    {STATIC_DIR}")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------------- Jinja filters ----------------

def _ordinal(n: int) -> str:
    # 1st, 2nd, 3rd, 4th...
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

def date_long(value) -> str:
    """
    Render as 'Tuesday October 21st'. Accepts date, datetime or ISO string.
    """
    d: _date
    if isinstance(value, _date):
        d = value
    elif isinstance(value, _dt):
        d = value.date()
    elif isinstance(value, str):
        try:
            d = _date.fromisoformat(value)
        except Exception:
            return value
    else:
        return str(value)

    return f"{d.strftime('%A')} {d.strftime('%B')} {_ordinal(d.day)}"

def phone_au(raw: str | None) -> str:
    """
    Render Australian mobiles: '+61412XXXXXX' -> '0412 345 678'
    Leaves other numbers untouched.
    """
    if not raw:
        return ""
    s = str(raw).strip().replace(" ", "")
    if s.startswith("+61"):
        s = "0" + s[3:]
    if len(s) == 10 and s.startswith("0"):
        return f"{s[0:4]} {s[4:7]} {s[7:10]}"
    return raw

templates.env.filters["date_long"] = date_long
templates.env.filters["phone_au"] = phone_au

# --------------- helper to mount /static ----------------

def mount_static(app):
    """
    Mount /static if we found a static directory at project root.
    Call from main.py after app = FastAPI(...).
    """
    if STATIC_DIR and STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
