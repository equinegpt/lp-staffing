# app/core/templates.py
from __future__ import annotations  # MUST be the first statement

from pathlib import Path
from datetime import date, datetime
import re

from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


# Where templates live
ROOT_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT_DIR / "app" / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# -------------------- Jinja filters --------------------
def _ordinal(n: int) -> str:
    # 1st / 2nd / 3rd / 4th...
    return "%d%s" % (n, "th" if 11 <= (n % 100) <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def date_long(v):
    """'YYYY-MM-DD' or date -> 'Tuesday October 21st'"""
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
    """+61xxxxxxxxx -> 04xx xxx xxx (leave other formats unchanged)"""
    if not v:
        return v
    digits = re.sub(r"\D+", "", v)
    if digits.startswith("61"):  # convert +61 / 61 to 0-leading
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        return f"{digits[0:4]} {digits[4:7]} {digits[7:10]}"
    return v


# Register filters
templates.env.filters["date_long"] = date_long
templates.env.filters["phone_au"] = phone_au


# -------------------- Static mount helper --------------------
def mount_static(app) -> None:
    """
    Mount /static using app/static. Safe to call once during startup.
    main.py imports and calls this.
    """
    static_dir = ROOT_DIR / "app" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    # If already mounted, Starlette will raise; so unmount first if needed.
    # (Render restarts can reuse the ASGI app instance.)
    try:
        app.routes = [r for r in app.routes if not (getattr(r, "path", None) == "/static")]
    except Exception:
        # If routes not yet populated, ignore.
        pass
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
