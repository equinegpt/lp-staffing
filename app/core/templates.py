from __future__ import annotations
from pathlib import Path
from datetime import date as _date, datetime as _dt
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from __future__ import annotations
import re

ROOT_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT_DIR / "app" / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
    # 1st 2nd 3rd 4th...
    return "%d%s" % (n, "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))

def date_long(v):
    """ 'YYYY-MM-DD' or date -> 'Tuesday October 21st' """
    if isinstance(v, str):
        try:
            d = datetime.strptime(v, "%Y-%m-%d").date()
        except Exception:
            return v
    elif isinstance(v, date):
        d = v
    else:
        return v
    return f"{d.strftime('%A')} {d.strftime('%B')} {_ordinal(d.day)}"

def phone_au(v: str):
    """ +61xxxxxxx -> 04xx xxx xxx  (non-+61 left as-is when unsure) """
    if not v:
        return v
    digits = re.sub(r"\D+", "", v)
    # strip +61/61 -> leading 0
    if digits.startswith("61"):
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        return f"{digits[0:4]} {digits[4:7]} {digits[7:10]}"
    return v

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
