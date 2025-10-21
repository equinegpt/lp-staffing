# app/main.py
from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import ADMIN_WEB_SECRET, STATIC_DIR
from app.core.db import bootstrap_schema
from app.core.templates import templates, mount_static
mount_static(app)

# Import router *objects* (not modules)
from app.routers.public import router as public_router
from app.routers.admin import router as admin_router
from app.routers.api_staff import router as api_staff_router  # keep if you want /api/*

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure schema exists when the app starts on Render
    bootstrap_schema()
    yield

app = FastAPI(title="LP Staffing API", lifespan=lifespan)
app = FastAPI(title="Staff Registry", lifespan=lifespan)
mount_static(app)  # ensure /static is mounted from repo root

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=ADMIN_WEB_SECRET, same_site="lax")

# --- Static files ---
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# --- Routers (include each exactly once) ---
app.include_router(public_router)     # HTML/public pages
app.include_router(admin_router)      # Admin HTML + JSON (Accept: application/json)
app.include_router(api_staff_router)  # Pure JSON API under /api/* (optional)

# --- Root redirect ---
@app.get("/")
def root():
    return RedirectResponse("/admin/login")

# (optional) List routes in Render logs for sanity
for r in app.router.routes:
    try:
        print("ROUTE:", r.methods, getattr(r, "path", None))
    except Exception:
        pass
