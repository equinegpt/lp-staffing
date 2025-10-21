# app/main.py
from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from app.core.config import ADMIN_WEB_SECRET
from app.core.db import bootstrap_schema
from app.core.templates import mount_static

# Routers
from app.routers.public import router as public_router
from app.routers.admin import router as admin_router
from app.routers.api_staff import router as api_staff_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_schema()
    yield

app = FastAPI(title="LP Staffing API", lifespan=lifespan)

# Middleware
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

# Static
mount_static(app)

# Routers
app.include_router(public_router)
app.include_router(admin_router)
app.include_router(api_staff_router)

# Root
@app.get("/")
def root():
    return RedirectResponse("/admin/login")
