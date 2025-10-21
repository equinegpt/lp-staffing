# app/main.py
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from starlette.routing import Mount

from app.core.config import ADMIN_WEB_SECRET, STATIC_DIR
from app.core.db import bootstrap_schema
from app.core.templates import templates, mount_static  # templates is imported to ensure Jinja env is initialized

# Routers
from app.routers.public import router as public_router
from app.routers.admin import router as admin_router
from app.routers.api_staff import router as api_staff_router


# --------------------------- lifespan (runs on startup) --------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure DB schema and seeds exist before serving requests (Render cold start)
    bootstrap_schema()
    yield


# --------------------------------- app creation ---------------------------------- #
app = FastAPI(title="Staff Registry", lifespan=lifespan)

# Mount /static and initialize Jinja templates via our helper
mount_static(app)


# If mount_static didn't mount /static (e.g., older helper), mount it defensively once.
def _has_static_mount(_app: FastAPI) -> bool:
    for r in _app.router.routes:
        if isinstance(r, Mount) and getattr(r, "path", None) == "/static":
            return True
    return False


if not _has_static_mount(app):
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------- middleware ----------------------------------- #
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=ADMIN_WEB_SECRET, same_site="lax")


# ----------------------------------- routers ------------------------------------- #
# Include each router exactly once
app.include_router(public_router)      # public HTML (e.g., /healthz if defined there)
app.include_router(admin_router)       # admin HTML + JSON (Accept: application/json)
app.include_router(api_staff_router)   # JSON API under /api/*


# -------------------------------- root redirect ---------------------------------- #
@app.get("/")
def root():
    # Keep admin login as the landing page
    return RedirectResponse("/admin/login")


# ----------------------------- (optional) route log ------------------------------ #
# Prints routes into Render logs for sanity while we stabilize the app wiring
try:
    for r in app.router.routes:
        try:
            print("ROUTE:", getattr(r, "methods", None), getattr(r, "path", None))
        except Exception:
            pass
except Exception:
    pass
