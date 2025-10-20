from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from app.core.config import ADMIN_WEB_SECRET, STATIC_DIR
from app.core.db import bootstrap_schema
from app.routers import public as public_router
from app.routers import admin as admin_router
from fastapi.staticfiles import StaticFiles
from app.routers.admin import router as admin_router
from app.routers.public import router as public_router  # if you have it
from app.routers.api_staff import router as api_staff_router

print("DB driver -> postgresql+psycopg")
print("Session secret source:", "env")

@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap_schema()
    yield

app = FastAPI(title="LP Staffing API")

app = FastAPI(title="Staff Registry", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000","http://127.0.0.1:3000",
        "http://localhost:5173","http://127.0.0.1:5173",
    ],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=ADMIN_WEB_SECRET, same_site="lax")

# Static
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Routers
app.include_router(public_router.router)
app.include_router(admin_router.router)
app.include_router(api_staff_router)   # ← JSON API (no login)
app.include_router(admin_router)       # ← HTML/admin
app.include_router(public_router)      # ← any public HTML, optional

# Root redirect
@app.get("/")
def root():
    return RedirectResponse("/admin/login")

for r in app.router.routes:
    try:
        print("ROUTE:", r.methods, getattr(r, "path", None))
    except Exception:
        pass