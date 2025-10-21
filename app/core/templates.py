from __future__ import annotations

from pathlib import Path
import datetime as dt
import re

from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi import FastAPI

# Paths
APP_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------- Jinja filters ----------

def _ordinal(n: int) -> str:
    # 1st, 2nd, 3rd, 4th … with 11th/12th/13th exception
    if 11 <= (n % 100) <= 13:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def date_long(value) -> str:
    """Show 'Tuesday October 21st' given a date or ISO string."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = dt.date.fromisoformat(value)
        except Exception:
            return value
    if isinstance(value, (dt.datetime, )):
        value = value.date()
    if not isinstance(value, dt.date):
        return str(value)
    return f"{value.strftime('%A')} {value.strftime('%B')} {_ordinal(value.day)}"

def phone_au(v) -> str:
    """Format AU mobiles:
       '+61458…' or '61458…' -> '0458 589 404'; keep other inputs unchanged.
    """
    if not v:
        return ""
    digits = re.sub(r"\D+", "", str(v))
    if digits.startswith("61") and len(digits) >= 11:
        digits = "0" + digits[2:]
    # 04xx xxx xxx
    if len(digits) == 10 and digits.startswith("0"):
        return f"{digits[0:4]} {digits[4:7]} {digits[7:10]}"
    return str(v)

templates.env.filters["date_long"] = date_long
templates.env.filters["phone_au"] = phone_au

# ---------- Static mount helper ----------
def mount_static(app: FastAPI) -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
