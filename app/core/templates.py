# app/core/templates.py
from pathlib import Path
from datetime import date as _date
from fastapi.templating import Jinja2Templates

# Resolve /templates folder from repo root
ROOT_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --------------------- Jinja filters & helpers ---------------------

def _ordinal_suffix(n: int) -> str:
    n = int(n)
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

def _ensure_date(v) -> _date:
    if isinstance(v, _date):
        return v
    return _date.fromisoformat(str(v))

def j_long_date(v) -> str:
    """Render like: Tuesday October 21st"""
    d = _ensure_date(v)
    return f"{d.strftime('%A')} {d.strftime('%B')} {d.day}{_ordinal_suffix(d.day)}"

def fmt_au_mobile(s: str | None) -> str:
    """
    Display AU mobile as: 0### ### ###
    Accepts +61xxxxxxxxx, 61xxxxxxxxx or 04xxxxxxxx.
    Falls back to raw if unknown.
    """
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits.startswith("61"):  # handles +61 / 61
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits[0] == "0":
        # 04xx xxx xxx
        return f"{digits[0:4]} {digits[4:7]} {digits[7:10]}"
    return s

# Register filters
templates.env.filters["long_date"] = j_long_date
templates.env.filters["au_mobile"] = fmt_au_mobile
