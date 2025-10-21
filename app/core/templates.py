# app/core/templates.py
from __future__ import annotations
from datetime import date as _date, datetime as _dt
import re
from pathlib import Path
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def date_long(value) -> str:
    """
    Render 'Tuesday October 21st' from a date or ISO string 'YYYY-MM-DD'.
    Year omitted by design (matches requested heading).
    """
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            d = _date.fromisoformat(value)
        except Exception:
            # try to parse datetimes too
            try:
                d = _dt.fromisoformat(value).date()
            except Exception:
                return str(value)
    elif isinstance(value, _dt):
        d = value.date()
    elif isinstance(value, _date):
        d = value
    else:
        return str(value)
    return f"{d.strftime('%A')} {d.strftime('%B')} {_ordinal(d.day)}"

def phone_au(s) -> str:
    """
    Display Australian mobiles as 0XXX XXX XXX and strip +61 leading code.
    Leaves non-AU strings untouched.
    """
    if not s:
        return ""
    digits = re.sub(r"\D+", "", str(s))
    if digits.startswith("61") and len(digits) >= 11:
        digits = "0" + digits[2:]  # +61XXXXXXXXX -> 0XXXXXXXXX
    if digits.startswith("0") and len(digits) == 10:
        return f"{digits[0:4]} {digits[4:7]} {digits[7:10]}"
    return s  # fallback (don’t mangle landlines etc.)

# register filters
templates.env.filters["date_long"] = date_long
templates.env.filters["phone_au"] = phone_au
