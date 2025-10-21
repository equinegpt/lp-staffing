# app/core/templates.py
from __future__ import annotations

from typing import Iterable
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.core.config import TEMPLATES_DIR, STATIC_DIR

# Point Jinja at your repo's templates folder
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _try_names(names: Iterable[str], context: dict):
    """
    Try a list of template names (different paths/variants).
    Return the first that exists; re-raise last error if none exists.
    """
    last_exc = None
    for n in names:
        if not n:
            continue
        try:
            return templates.TemplateResponse(n, context)
        except Exception as e:
            last_exc = e
    raise last_exc


def render_any(primary: str, context: dict, *alts: str):
    """
    Be lenient about template names:
    - Accept foldered ("admin/staff_list.html") or underscored ("admin_staff_list.html")
    - Accept with or without ".html"
    - Accept just the basename ("login") if you saved without an extension
    """
    candidates: list[str] = []

    def add_variants(n: str):
        if not n:
            return
        # exact
        candidates.append(n)
        # with/without .html
        if n.endswith(".html"):
            candidates.append(n[:-5])
        else:
            candidates.append(n + ".html")
        # folder <-> underscore variants
        if "/" in n:
            parts = n.rstrip("/").split("/")
            underscore = "_".join(parts)
            candidates.append(underscore)
            candidates.append(underscore + ".html")
            # also try the basename alone
            last = parts[-1]
            candidates.append(last)
            candidates.append(last + ".html")

    add_variants(primary)
    for a in alts:
        add_variants(a)

    # de-dup while preserving order
    seen: set[str] = set()
    ordered = [x for x in candidates if not (x in seen or seen.add(x))]

    # Helpful log on Render so we can see what was tried
    print("Template lookup chain:", " -> ".join(ordered[:12]), "...")

    return _try_names(ordered, context)


def mount_static(app):
    """Mount /static from the configured STATIC_DIR."""
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
