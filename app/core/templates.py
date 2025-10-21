# app/core/templates.py
from __future__ import annotations  # must be first

from pathlib import Path
from datetime import date, datetime
import re

from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


# ----- resolve project root and pick template/static dirs safely -----
ROOT = Path(__file__).resolve().parents[2]  # repo root (…/app/core/templates.py -> …/)
TPL_CANDIDATES = [ROOT / "templates", ROOT / "app" / "templates"]
STATIC_CANDIDATES = [ROOT / "static", ROOT / "app" / "static"]

TEMPLATES_DIR = next((p for p in TPL_CANDIDATES if p.exists()), TPL_CANDIDATES[0])
STATIC_DIR = next((p for p in STATIC_CANDIDATES if p.exists()), STATIC_CANDIDATES[0])

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ----- Jinja filters -----
def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= (n % 100) <= 13 else {1:'st',2:'nd',3:'rd'}.get(n % 10, 'th')}"

def date_long(value):
    """'YYYY-MM-DD' or date -> 'Tuesday October 21st'"""
    if isinstance(value, str):
        try:
            d = datetime.strptime(value, "%Y-%m-%d").date()
        except Exception:
            return value
    elif isinstance(value, date):
        d = value
    else:
        return value
    return f"{d.strftime('%A')} {d.strftime('%B')} {_ordinal(d.day)}"

def phone_au(v: str):
    """+61xxxxxxxxx -> 04xx xxx xxx (leave other formats unchanged)"""
    if not v:
        return v
    digits = re.sub(r"\D+", "", v)
    if digits.startswith("61"):
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        return f"{digits[0:4]} {digits[4:7]} {digits[7:10]}"
    return v

templates.env.filters["date_long"] = date_long
templates.env.filters["phone_au"] = phone_au


# ----- static mount helper (idempotent) -----
def mount_static(app) -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    # remove any previous /static mount to avoid duplicates on hot restarts
    try:
        app.routes = [r for r in app.routes if getattr(r, "path", None) != "/static"]
    except Exception:
        pass
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
