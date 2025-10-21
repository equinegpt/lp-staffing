# app/core/templates.py
from __future__ import annotations

from pathlib import Path
from datetime import date as _date, datetime as _dt
from typing import Iterable, Dict, Any, List

from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from jinja2 import TemplateNotFound

# --- Paths (stable on Render and locally) -----------------------------------
APP_DIR = Path(__file__).resolve().parents[1]    # .../app
TEMPLATES_DIR = APP_DIR / "templates"            # app/templates
STATIC_DIR = APP_DIR / "static"                  # app/static

# --- Jinja environment -------------------------------------------------------
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ----------------------------- Jinja filters --------------------------------
def _ordinal(n: int) -> str:
    # 1st, 2nd, 3rd, 4th...
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"

def date_long(value: Any) -> str:
    """
    Render a date as 'Tuesday October 21st' (no year), accepting date/datetime/'YYYY-MM-DD'.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            d = _date.fromisoformat(value)
        except Exception:
            return str(value)
    elif isinstance(value, _dt):
        d = value.date()
    elif isinstance(value, _date):
        d = value
    else:
        return str(value)

    return f"{d.strftime('%A')} {d.strftime('%B')} {_ordinal(d.day)}"

def phone_au(value: Any) -> str:
    """
    Display AU mobiles like '0### ### ###' and strip a leading +61 -> 0.
    Leaves unknown formats untouched.
    """
    s = "".join(ch for ch in str(value) if ch.isdigit())
    if not s:
        return str(value)
    # normalize +61 -> 0
    if s.startswith("61"):
        s = "0" + s[2:]
    # mobile: 10 digits starting with 0 (e.g., 04xxxxxxxx)
    if len(s) == 10 and s.startswith("0"):
        return f"{s[0:4]} {s[4:7]} {s[7:10]}"
    return str(value)

templates.env.filters["date_long"] = date_long
templates.env.filters["phone_au"] = phone_au

# ---------------------------- Static mounting -------------------------------
def mount_static(app):
    """
    Mount /static from app/static regardless of the working directory.
    """
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app

# ----------------------- Flexible template rendering ------------------------
def _try_names(candidates: Iterable[str], context: Dict[str, Any]):
    """
    Try candidates in order; return the first that exists. Re-raise the last TemplateNotFound
    if none are found (so you still get a clear error in logs).
    """
    last_exc: TemplateNotFound | None = None
    for name in candidates:
        try:
            return templates.TemplateResponse(name, context)
        except TemplateNotFound as e:
            last_exc = e
            continue
    if last_exc:
        raise last_exc
    # Should never happen, but just in case:
    raise TemplateNotFound("(no template candidates provided)")

def _expand_variants(base: str) -> List[str]:
    """
    Expand a requested logical name into a handful of filename variants to improve
    resilience to small path/name differences during refactors.
      e.g. "admin/staff_list" -> ["admin/staff_list", "admin/staff_list.html", "admin_staff_list.html"]
            "admin_staff_list.html" -> [..., "admin/staff_list.html"]
            "login" -> ["login", "login.html"]
    """
    out: List[str] = []
    def add(x: str):
        if x not in out:
            out.append(x)

    add(base)
    # add .html variant
    if not base.endswith(".html"):
        add(f"{base}.html")

    # map folder <-> underscore forms
    if "/" in base:
        prefix, rest = base.split("/", 1)
        # admin/staff_list -> admin_staff_list(.html)
        add(f"{prefix}_{rest}")
        if not rest.endswith(".html"):
            add(f"{prefix}_{rest}.html")
    else:
        if base.startswith("admin_"):
            rest = base[len("admin_"):]
            if rest.endswith(".html"):
                add(f"admin/{rest}")
            else:
                add(f"admin/{rest}.html")
        if base.startswith("partials_"):
            rest = base[len("partials_"):]
            if rest.endswith(".html"):
                add(f"partials/{rest}")
            else:
                add(f"partials/{rest}.html")

    return out

def render_any(name: str, context: Dict[str, Any], *alts: str):
    """
    Preferred helper for routes:
      return render_any("admin/staff_list", {...}, "admin_staff_list.html")
    We’ll try smart variants and any explicit fallbacks you pass.
    """
    candidates = _expand_variants(name)
    for alt in alts:
        candidates.extend(_expand_variants(alt))
    return _try_names(candidates, context)
