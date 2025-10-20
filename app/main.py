# app/main.py
from pathlib import Path
from typing import Optional, List, Dict
from datetime import date as _date, timedelta
import os, secrets, io, csv
from uuid import UUID as _UUID

import sqlalchemy as sa
from fastapi import (
    FastAPI, Query, Header, HTTPException, Depends,
    Request, Form, APIRouter
)
from starlette import status as http_status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, field_validator
from dotenv import load_dotenv

# ----------------------- ENV & DB -----------------------
ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)  # ok if missing on Render

def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if (v is not None and v != "") else default

DATABASE_URL = _env("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

# Normalize to psycopg v3 driver for SQLAlchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
if DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

ADMIN_API_KEY      = _env("ADMIN_API_KEY", "")
ADMIN_WEB_PASSWORD = _env("ADMIN_WEB_PASSWORD", "")

# Always define this: use env if present; else generate one for this process.
ADMIN_WEB_SECRET = _env("ADMIN_WEB_SECRET") or os.environ.setdefault(
    "ADMIN_WEB_SECRET", secrets.token_urlsafe(32)
)

print("DB driver ->", "postgresql+psycopg" if "+psycopg" in DATABASE_URL else "postgresql")
print("Session secret source:", "env" if os.getenv("ADMIN_WEB_SECRET") else "generated")

# Create the engine BEFORE any route uses it
engine = sa.create_engine(DATABASE_URL, pool_pre_ping=True)

# --- bootstrap schema on first run (idempotent) ---
def _bootstrap_schema():
    with engine.begin() as c:
        # UUIDs
        c.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

        # core tables
        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS role (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          code TEXT UNIQUE NOT NULL,
          label TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS location (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          code TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL,
          timezone TEXT NOT NULL DEFAULT 'Australia/Melbourne',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS staff (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          given_name  TEXT NOT NULL,
          family_name TEXT NOT NULL,
          display_name TEXT NOT NULL,
          mobile TEXT NOT NULL UNIQUE,
          email  TEXT,
          start_date DATE NOT NULL,
          end_date   DATE,
          status TEXT NOT NULL DEFAULT 'ACTIVE',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS staff_role_assignment (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
          role_id  UUID NOT NULL REFERENCES role(id),
          location_id UUID REFERENCES location(id),
          effective_start DATE NOT NULL,
          effective_end   DATE,
          priority INTEGER NOT NULL DEFAULT 0,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
        c.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS device (
          id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
          staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
          platform TEXT NOT NULL CHECK (platform IN ('iOS','Android')),
          token TEXT NOT NULL UNIQUE,
          last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
        # helpful indexes
        c.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sra_staff_dates  ON staff_role_assignment (staff_id, effective_start, effective_end)")
        c.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sra_role        ON staff_role_assignment (role_id)")
        c.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sra_location    ON staff_role_assignment (location_id)")

        # seed minimal data (safe to re-run)
        c.exec_driver_sql("""
        INSERT INTO role (code,label) VALUES
          ('RIDER','Rider'),
          ('STRAPPER','Strapper'),
          ('TRACKWORK','Trackwork')
        ON CONFLICT (code) DO NOTHING
        """)
        c.exec_driver_sql("""
        INSERT INTO location (code,name,timezone) VALUES
          ('CRANBOURNE','Cranbourne','Australia/Melbourne'),
          ('FLEMINGTON','Flemington','Australia/Melbourne')
        ON CONFLICT (code) DO NOTHING
        """)

@app.on_event("startup")
def _startup_bootstrap():
    _bootstrap_schema()

# ----------------------- APP & MIDDLEWARE -----------------------
app = FastAPI(title="Staff Registry")

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

# ----------------------- STATIC & TEMPLATES -----------------------
STATIC_DIR = ROOT_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

TEMPLATES_DIR = ROOT_DIR / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ----------------------- HELPERS & MODELS -----------------------
def require_admin(x_api_key: str = Header(default="")):
    if not ADMIN_API_KEY or x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

def _validate_uuid(s: str) -> str:
    try:
        _UUID(s)  # just validate; we still return the original string
        return s
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")

class StaffCreate(BaseModel):
    given_name: str
    family_name: str
    mobile: str
    start_date: _date
    email: Optional[EmailStr] = None
    primary_role_code: Optional[str] = None
    location_code: Optional[str] = None

    @field_validator("mobile")
    @classmethod
    def _strip_mobile(cls, v: str) -> str:
        return v.strip()

class StaffEnd(BaseModel):
    end_date: _date

class DeviceCreate(BaseModel):
    staff_id: str
    platform: str
    token: str

    @field_validator("platform")
    @classmethod
    def _valid_platform(cls, v: str) -> str:
        v2 = v.strip()
        if v2 not in ("iOS", "Android"):
            raise ValueError("platform must be 'iOS' or 'Android'")
        return v2

class RoleAssignCreate(BaseModel):
    role_code: str
    location_code: Optional[str] = None
    effective_start: _date
    effective_end: Optional[_date] = None
    priority: int = 0

    @field_validator("role_code", "location_code")
    @classmethod
    def _strip_codes(cls, v):
        return v.strip() if v else v

class EndAssignment(BaseModel):
    end_date: _date  # default provided in route

# ----------------------- JSON API -----------------------
@app.get("/healthz")
def healthz():
    with engine.connect() as c:
        c.exec_driver_sql("SELECT 1")
    return {"ok": True}

@app.get("/roles")
def get_roles() -> List[Dict]:
    sql = sa.text("SELECT code, label FROM role ORDER BY code")
    with engine.connect() as c:
        return [dict(r) for r in c.execute(sql).mappings().all()]

@app.get("/locations")
def get_locations() -> List[Dict]:
    sql = sa.text("SELECT code, name, timezone FROM location ORDER BY code")
    with engine.connect() as c:
        return [dict(r) for r in c.execute(sql).mappings().all()]

@app.get("/staff")
def get_staff(
    d: _date = Query(..., description="Date to evaluate activity (YYYY-MM-DD)"),
    role: Optional[str] = Query(None, description="Role code e.g., RIDER"),
    location: Optional[str] = Query(None, description="Location code e.g., CRANBOURNE"),
) -> List[Dict]:
    sql = sa.text("""
    SELECT
      s.id, s.given_name, s.family_name, s.display_name, s.mobile, s.email,
      s.status, s.start_date, s.end_date
    FROM staff s
    WHERE s.start_date <= :D
      AND (s.end_date IS NULL OR :D <= s.end_date)
      AND EXISTS (
        SELECT 1
        FROM staff_role_assignment a
        JOIN role r ON r.id = a.role_id
        LEFT JOIN location l ON l.id = a.location_id
        WHERE a.staff_id = s.id
          AND a.effective_start <= :D
          AND (a.effective_end IS NULL OR :D <= a.effective_end)
          AND COALESCE(:role_code, r.code) = r.code
          AND COALESCE(:loc_code,  l.code) = l.code
      )
    ORDER BY s.family_name, s.given_name
    """)
    params = {"D": d, "role_code": role, "loc_code": location}
    with engine.connect() as c:
        return [dict(r) for r in c.execute(sql, params).mappings().all()]

@app.get("/staff/search", dependencies=[Depends(require_admin)])
def search_staff(q: str) -> List[Dict]:
    q_like = f"%{q.strip()}%"
    sql = sa.text("""
      SELECT id, given_name, family_name, display_name, mobile, email
      FROM staff
      WHERE mobile ILIKE :q
         OR given_name ILIKE :q
         OR family_name ILIKE :q
         OR display_name ILIKE :q
      ORDER BY family_name, given_name
      LIMIT 25
    """)
    with engine.connect() as c:
        return [dict(r) for r in c.execute(sql, {"q": q_like}).mappings().all()]

@app.get("/staff/{staff_id}/roles", dependencies=[Depends(require_admin)])
def list_staff_roles(staff_id: str) -> List[Dict]:
    sql = sa.text("""
    SELECT
      a.id,
      r.code AS role_code,
      r.label AS role_label,
      l.code AS location_code,
      a.effective_start,
      a.effective_end,
      a.priority
    FROM staff_role_assignment a
    JOIN role r ON r.id = a.role_id
    LEFT JOIN location l ON l.id = a.location_id
    WHERE a.staff_id = :sid
    ORDER BY a.effective_start DESC, r.code
    """)
    with engine.connect() as c:
        rows = c.execute(sql, {"sid": staff_id}).mappings().all()
        return [dict(r) for r in rows]

@app.post("/staff", status_code=201, dependencies=[Depends(require_admin)], operation_id="create_staff_v1")
def create_staff(payload: StaffCreate):
    display_name = f"{payload.given_name} {payload.family_name}".strip()
    with engine.begin() as c:
        existing = c.execute(sa.text("""
            SELECT id, given_name, family_name, mobile, email, start_date, end_date
            FROM staff WHERE mobile = :m
        """), {"m": payload.mobile}).mappings().first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail={"message": "Staff with that mobile already exists.", "existing": dict(existing)},
            )

        staff_id = c.execute(sa.text("""
            INSERT INTO staff (given_name, family_name, display_name, mobile, email, start_date)
            VALUES (:gn, :fn, :dn, :mobile, :email, :sd)
            RETURNING id
        """), {
            "gn": payload.given_name, "fn": payload.family_name, "dn": display_name,
            "mobile": payload.mobile, "email": payload.email, "sd": payload.start_date
        }).scalar_one()

        if payload.primary_role_code and payload.location_code:
            role = c.execute(sa.text("SELECT id FROM role WHERE code = :c"), {"c": payload.primary_role_code}).first()
            if not role:
                raise HTTPException(status_code=400, detail="Unknown role code.")
            loc  = c.execute(sa.text("SELECT id FROM location WHERE code = :c"), {"c": payload.location_code}).first()
            if not loc:
                raise HTTPException(status_code=400, detail="Unknown location code.")
            c.execute(sa.text("""
                INSERT INTO staff_role_assignment (staff_id, role_id, location_id, effective_start)
                VALUES (:sid, :rid, :lid, :sd)
            """), {"sid": staff_id, "rid": role.id, "lid": loc.id, "sd": payload.start_date})

    return {
        "id": str(staff_id),
        "given_name": payload.given_name,
        "family_name": payload.family_name,
        "display_name": display_name,
        "mobile": payload.mobile,
        "email": payload.email,
        "start_date": str(payload.start_date),
        "role_assigned": bool(payload.primary_role_code and payload.location_code),
    }

@app.post("/staff/{staff_id}/roles", status_code=201, dependencies=[Depends(require_admin)])
def add_role_assignment(staff_id: str, payload: RoleAssignCreate):
    with engine.begin() as c:
        exists = c.execute(sa.text("SELECT 1 FROM staff WHERE id = :sid"), {"sid": staff_id}).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Staff not found.")
        role = c.execute(sa.text("SELECT id FROM role WHERE code = :c"), {"c": payload.role_code}).first()
        if not role:
            raise HTTPException(status_code=400, detail="Unknown role code.")
        loc_id = None
        if payload.location_code:
            loc = c.execute(sa.text("SELECT id FROM location WHERE code = :c"), {"c": payload.location_code}).first()
            if not loc:
                raise HTTPException(status_code=400, detail="Unknown location code.")
            loc_id = loc.id

        start, end = payload.effective_start, payload.effective_end
        overlap = c.execute(sa.text("""
            SELECT a.id
              FROM staff_role_assignment a
             WHERE a.staff_id = :sid
               AND a.role_id  = :rid
               AND ((a.location_id IS NULL AND :lid IS NULL) OR a.location_id = :lid)
               AND a.effective_start <= COALESCE(:new_end, DATE '9999-12-31')
               AND COALESCE(a.effective_end, DATE '9999-12-31') >= :new_start
             LIMIT 1
        """), {"sid": staff_id, "rid": role.id, "lid": loc_id,
               "new_start": start, "new_end": end}).first()
        if overlap:
            raise HTTPException(status_code=409, detail=f"Overlapping assignment exists (id={overlap.id}).")

        new_id = c.execute(sa.text("""
            INSERT INTO staff_role_assignment (staff_id, role_id, location_id, effective_start, effective_end, priority)
            VALUES (:sid, :rid, :lid, :st, :en, :p)
            RETURNING id
        """), {"sid": staff_id, "rid": role.id, "lid": loc_id,
               "st": start, "en": end, "p": payload.priority}).scalar_one()

    return {
        "id": str(new_id),
        "staff_id": staff_id,
        "role_code": payload.role_code,
        "location_code": payload.location_code,
        "effective_start": str(start),
        "effective_end": str(end) if end else None,
        "priority": payload.priority,
    }

@app.post("/staff/{staff_id}/roles/{assignment_id}/end", dependencies=[Depends(require_admin)])
def end_role_assignment(
    staff_id: str,
    assignment_id: str,
    body: EndAssignment = Depends(lambda: EndAssignment(end_date=_date.today()))
):
    with engine.begin() as c:
        row = c.execute(sa.text("""
            SELECT effective_start
            FROM staff_role_assignment
            WHERE id = :aid AND staff_id = :sid
        """), {"aid": assignment_id, "sid": staff_id}).first()
        if not row:
            raise HTTPException(status_code=404, detail="Assignment not found for staff.")
        if body.end_date < row.effective_start:
            raise HTTPException(status_code=400, detail="End date cannot be before start date.")
        c.execute(sa.text("""
            UPDATE staff_role_assignment
               SET effective_end = :end_date
             WHERE id = :aid
        """), {"end_date": body.end_date, "aid": assignment_id})
    return {"ok": True, "assignment_id": assignment_id, "ended_on": str(body.end_date)}

@app.post("/staff/{staff_id}/end", dependencies=[Depends(require_admin)])
def end_staff(staff_id: str, body: StaffEnd):
    with engine.begin() as c:
        exists = c.execute(sa.text("SELECT 1 FROM staff WHERE id=:sid"), {"sid": staff_id}).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Staff not found.")
        c.execute(sa.text("UPDATE staff SET end_date=:d WHERE id=:sid"),
                  {"d": body.end_date, "sid": staff_id})
    return {"ok": True, "staff_id": staff_id, "ended_on": str(body.end_date)}

@app.post("/staff/{staff_id}/reactivate", dependencies=[Depends(require_admin)])
def reactivate_staff(staff_id: str):
    with engine.begin() as c:
        exists = c.execute(sa.text("SELECT 1 FROM staff WHERE id=:sid"), {"sid": staff_id}).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Staff not found.")
        c.execute(sa.text("UPDATE staff SET end_date=NULL WHERE id=:sid"), {"sid": staff_id})
    return {"ok": True, "staff_id": staff_id, "ended_on": None}

@app.post("/devices", status_code=201)
def register_device(payload: DeviceCreate):
    with engine.begin() as c:
        ok = c.execute(sa.text("SELECT 1 FROM staff WHERE id=:sid"), {"sid": payload.staff_id}).first()
        if not ok:
            raise HTTPException(status_code=404, detail="Staff not found.")
        c.execute(sa.text("""
            INSERT INTO device (staff_id, platform, token)
            VALUES (:sid, :pf, :tk)
            ON CONFLICT (token) DO UPDATE
                SET staff_id = EXCLUDED.staff_id,
                    platform  = EXCLUDED.platform,
                    last_seen_at = now()
        """), {"sid": payload.staff_id, "pf": payload.platform, "tk": payload.token})
    return {"ok": True}

# ----------------------- ADMIN WEB (APIRouter) -----------------------
admin = APIRouter(prefix="/admin", tags=["admin"])

def _admin_only(request: Request) -> bool:
    return bool(request.session.get("admin"))

def _fetch_assignments_for(conn, staff_id: str):
    return conn.execute(sa.text("""
      SELECT a.id, r.code AS role_code, r.label AS role_label,
             COALESCE(l.code,'—') AS location_code,
             a.effective_start, a.effective_end, a.priority
        FROM staff_role_assignment a
        JOIN role r ON r.id = a.role_id
        LEFT JOIN location l ON l.id = a.location_id
       WHERE a.staff_id = :sid
       ORDER BY a.effective_start DESC, r.code
    """), {"sid": staff_id}).mappings().all()

# Common list query (used by list, table partial, CSV)
def _fetch_staff_for_list(*, day: _date, role_code: Optional[str], loc_code: Optional[str],
                          status: Optional[str], q: Optional[str]) -> List[Dict]:
    """
    One row per staff, with the most-relevant assignment on `day`.
    is_active := base_active (start/end) AND has_active_assignment(on day).
    If status is '', we show everyone. If 'active' or 'inactive', we filter accordingly.
    """
    status_norm = (status or "").strip().lower()
    if status_norm not in ("active", "inactive"):
        status_norm = ""  # Any status

    role_code_s = (role_code or "").strip()
    loc_code_s  = (loc_code or "").strip()
    q_raw       = (q or "").strip()
    q_like      = f"%{q_raw}%" if q_raw else ""

    sql = sa.text("""
    WITH base AS (
      SELECT
        s.*,
        (s.start_date <= :D AND :D <= COALESCE(s.end_date, DATE '9999-12-31')) AS base_active
      FROM staff s
    )
    SELECT
      b.id, b.given_name, b.family_name, b.display_name, b.mobile, b.email,
      b.start_date, b.end_date,
      ar.role_code, ar.role_label, ar.location_code,
      (b.base_active AND ar.role_code IS NOT NULL) AS is_active
    FROM base b
    LEFT JOIN LATERAL (
      SELECT
        r.code  AS role_code,
        r.label AS role_label,
        l.code  AS location_code
      FROM staff_role_assignment a
      JOIN role r       ON r.id = a.role_id
      LEFT JOIN location l ON l.id = a.location_id
      WHERE a.staff_id = b.id
        AND a.effective_start <= :D
        AND (a.effective_end IS NULL OR :D <= a.effective_end)
      ORDER BY a.priority DESC, a.effective_start DESC, a.id DESC
      LIMIT 1
    ) ar ON TRUE
    WHERE
      (:status = '' OR
       (:status = 'active'   AND (b.base_active AND ar.role_code IS NOT NULL)) OR
       (:status = 'inactive' AND NOT (b.base_active AND ar.role_code IS NOT NULL)))
      AND (:role_code = '' OR ar.role_code     = :role_code)
      AND (:loc_code  = '' OR ar.location_code = :loc_code)
      AND (:q = '' OR
           b.mobile       ILIKE :q_like OR
           b.display_name ILIKE :q_like OR
           b.given_name   ILIKE :q_like OR
           b.family_name  ILIKE :q_like)
    ORDER BY b.family_name, b.given_name
    """)

    params = {
        "D": day,
        "status": status_norm,
        "role_code": role_code_s,
        "loc_code":  loc_code_s,
        "q": q_raw,
        "q_like": q_like,
    }

    with engine.connect() as c:
        return [dict(r) for r in c.execute(sql, params).mappings().all()]

@admin.get("/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@admin.post("/login")
def admin_login(request: Request, password: str = Form(...)):
    configured = (ADMIN_WEB_PASSWORD or "").strip()
    if not configured:
        if password.strip():
            request.session["admin"] = True
            return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)
        return HTMLResponse("<h3>Login blocked: set ADMIN_WEB_PASSWORD in .env</h3>", status_code=500)

    if password.strip() == configured:
        request.session["admin"] = True
        return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)

    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><h2>Wrong password</h2>"
        "<p><a href='/admin/login'>Back</a></p>", status_code=401
    )

@admin.get("/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

@admin.get("", response_class=HTMLResponse)
def admin_home_redirect():
    return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)

@admin.get("/staff", response_class=HTMLResponse)
def admin_staff_list(request: Request, d: Optional[str] = None, q: Optional[str] = None,
                     role: Optional[str] = None, location: Optional[str] = None,
                     status: Optional[str] = None):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    try:
        day = _date.fromisoformat(d) if d else _date.today()
    except Exception:
        day = _date.today()

    with engine.connect() as c:
        roles = c.execute(sa.text("SELECT code,label FROM role ORDER BY code")).mappings().all()
        locs  = c.execute(sa.text("SELECT code,name FROM location ORDER BY code")).mappings().all()

    staff_rows = _fetch_staff_for_list(day=day, role_code=role, loc_code=location, status=status, q=q)

    return templates.TemplateResponse("admin_staff_list.html", {
        "request": request,
        "day": day.isoformat(),
        "staff": staff_rows,
        "roles": roles,
        "locations": locs,
        "active_role": role or "",
        "active_loc": location or "",
        "status": (status or ""),
        "q": q or ""
    })

@admin.get("/staff/table", response_class=HTMLResponse)
def admin_staff_table(request: Request, d: Optional[str] = None, q: Optional[str] = None,
                      role: Optional[str] = None, location: Optional[str] = None,
                      status: Optional[str] = None):
    if not _admin_only(request):
        return HTMLResponse("", status_code=401)
    try:
        day = _date.fromisoformat(d) if d else _date.today()
    except Exception:
        day = _date.today()

    staff_rows = _fetch_staff_for_list(day=day, role_code=role, loc_code=location, status=status, q=q)
    return templates.TemplateResponse("partials/staff_table_rows.html", {
        "request": request, "staff": staff_rows
    })

@admin.get("/staff/new", response_class=HTMLResponse)
def admin_staff_new(request: Request):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    with engine.connect() as c:
        roles = c.execute(sa.text("SELECT code,label FROM role ORDER BY code")).mappings().all()
        locs  = c.execute(sa.text("SELECT code,name FROM location ORDER BY code")).mappings().all()
    return templates.TemplateResponse("admin_staff_new.html", {
        "request": request, "roles": roles, "locations": locs,
        "today": _date.today().isoformat(), "now": _date.today().isoformat()
    })

@admin.post("/staff/create")
def admin_staff_create(
    request: Request,
    given_name: str = Form(...),
    family_name: str = Form(...),
    mobile: str = Form(...),
    start_date: str = Form(...),
    email: Optional[str] = Form(None),
    primary_role_code: Optional[str] = Form(None),
    location_code: Optional[str] = Form(None),
):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    try:
        sd = _date.fromisoformat(start_date)
    except Exception:
        sd = _date.today()

    display_name = f"{given_name} {family_name}".strip()
    with engine.begin() as c:
        existing = c.execute(sa.text("SELECT id FROM staff WHERE mobile = :m"), {"m": mobile.strip()}).first()
        if existing:
            return RedirectResponse(f"/admin/staff/{existing.id}", status_code=http_status.HTTP_303_SEE_OTHER)

        staff_id = c.execute(sa.text("""
            INSERT INTO staff (given_name, family_name, display_name, mobile, email, start_date)
            VALUES (:gn,:fn,:dn,:m,:e,:sd) RETURNING id
        """), {"gn": given_name, "fn": family_name, "dn": display_name, "m": mobile.strip(), "e": email, "sd": sd}).scalar_one()

        if primary_role_code and location_code:
            role = c.execute(sa.text("SELECT id FROM role WHERE code=:c"), {"c": primary_role_code}).first()
            loc  = c.execute(sa.text("SELECT id FROM location WHERE code=:c"), {"c": location_code}).first()
            if role and loc:
                c.execute(sa.text("""
                    INSERT INTO staff_role_assignment (staff_id, role_id, location_id, effective_start)
                    VALUES (:sid,:rid,:lid,:sd)
                """), {"sid": staff_id, "rid": role.id, "lid": loc.id, "sd": sd})

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

# -------- CSV export (place BEFORE any /staff/{staff_id} routes) --------
@admin.get("/staff/export.csv")
def admin_staff_export_csv(request: Request,
                           d: Optional[str] = None, q: Optional[str] = None,
                           role: Optional[str] = None, location: Optional[str] = None,
                           status: Optional[str] = None):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    try:
        day = _date.fromisoformat(d) if d else _date.today()
    except Exception:
        day = _date.today()

    rows = _fetch_staff_for_list(day=day, role_code=role, loc_code=location, status=status, q=q)

    output = io.StringIO()
    writer = csv.writer(output)
    headers = [
        "id","given_name","family_name","display_name","mobile","email",
        "start_date","end_date","is_active",
        "role_code","role_label","location_code"
    ]
    writer.writerow(headers)
    for r in rows:
        writer.writerow([
            r.get("id"), r.get("given_name"), r.get("family_name"), r.get("display_name"),
            r.get("mobile"), r.get("email"),
            r.get("start_date"), r.get("end_date"),
            "TRUE" if r.get("is_active") else "FALSE",
            r.get("role_code"), r.get("role_label"), r.get("location_code"),
        ])
    output.seek(0)

    filename = f"staff_{day.isoformat()}.csv"
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@admin.get("/staff/{staff_id}", response_class=HTMLResponse)
def admin_staff_detail(request: Request, staff_id: str):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    with engine.connect() as c:
        s = c.execute(sa.text("""
          SELECT id, given_name, family_name, display_name, mobile, email, start_date, end_date
          FROM staff WHERE id=:sid
        """), {"sid": staff_id}).mappings().first()
        if not s:
            return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)

        assignments = _fetch_assignments_for(c, staff_id)
        roles = c.execute(sa.text("SELECT code,label FROM role ORDER BY code")).mappings().all()
        locs  = c.execute(sa.text("SELECT code,name FROM location ORDER BY code")).mappings().all()

    return templates.TemplateResponse("admin_staff_detail.html", {
        "request": request, "s": s, "assignments": assignments, "roles": roles, "locations": locs
    })

@admin.get("/staff/{staff_id}/assignments/table", response_class=HTMLResponse)
def admin_assignments_table(request: Request, staff_id: str):
    if not _admin_only(request):
        return HTMLResponse("", status_code=401)
    with engine.connect() as c:
        assignments = _fetch_assignments_for(c, staff_id)
    return templates.TemplateResponse(
        "partials/assignments_table.html",
        {"request": request, "s_id": staff_id, "assignments": assignments}
    )

@admin.post("/staff/{staff_id}/assign")
def admin_add_assignment(
    request: Request, staff_id: str,
    role_code: str = Form(...),
    location_code: Optional[str] = Form(None),
    effective_start: str = Form(...),
    effective_end: Optional[str] = Form(None),
):
    """
    Business rules:
      - Staff can hold ONE assignment at a time (one role + one location).
      - Adding a new assignment automatically ends any active assignment
        at (new_start - 1 day). If the existing active assignment is identical,
        we no-op.
      - 'Priority' is ignored/removed.
    """
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    start = _date.fromisoformat(effective_start)
    end = _date.fromisoformat(effective_end) if (effective_end or "").strip() else None

    with engine.begin() as c:
        role = c.execute(sa.text("SELECT id FROM role WHERE code=:c"), {"c": role_code}).first()
        if not role:
            if request.headers.get("hx-request"):
                assignments = _fetch_assignments_for(c, staff_id)
                return templates.TemplateResponse(
                    "partials/assignments_table.html",
                    {"request": request, "s_id": staff_id, "assignments": assignments},
                    status_code=400
                )
            return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

        loc_code = (location_code or "").strip()
        if loc_code in ("", "—"):
            loc_id = None
        else:
            loc = c.execute(sa.text("SELECT id FROM location WHERE code=:c"), {"c": loc_code}).first()
            loc_id = loc.id if loc else None

        current = c.execute(sa.text("""
            SELECT id, role_id, location_id, effective_start, effective_end
              FROM staff_role_assignment
             WHERE staff_id = :sid
               AND effective_start <= :st
               AND (effective_end IS NULL OR effective_end >= :st)
             ORDER BY effective_start DESC
             LIMIT 1
        """), {"sid": staff_id, "st": start}).mappings().first()

        if current and current.role_id == role.id and (
            (current.location_id is None and loc_id is None) or current.location_id == loc_id
        ):
            if end and (current.effective_end is None or current.effective_end != end):
                c.execute(sa.text("""
                    UPDATE staff_role_assignment
                       SET effective_end = :en
                     WHERE id = :aid
                """), {"en": end, "aid": current.id})
        else:
            if current:
                new_prev_end = start - timedelta(days=1)
                c.execute(sa.text("""
                    UPDATE staff_role_assignment
                       SET effective_end = :en
                     WHERE id = :aid
                       AND (effective_end IS NULL OR effective_end > :en)
                """), {"en": new_prev_end, "aid": current.id})

            c.execute(sa.text("""
                INSERT INTO staff_role_assignment
                    (staff_id, role_id, location_id, effective_start, effective_end)
                VALUES (:sid, :rid, :lid, :st, :en)
            """), {"sid": staff_id, "rid": role.id, "lid": loc_id, "st": start, "en": end})

    if request.headers.get("hx-request"):
        with engine.connect() as c2:
            assignments = _fetch_assignments_for(c2, staff_id)
        return templates.TemplateResponse(
            "partials/assignments_table.html",
            {"request": request, "s_id": staff_id, "assignments": assignments}
        )

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

@admin.post("/staff/{staff_id}/assign/{assignment_id}/end")
def admin_end_assignment(
    request: Request, staff_id: str, assignment_id: str,
    end_date: Optional[str] = Form(None)
):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    d = _date.fromisoformat(end_date) if end_date else _date.today()
    with engine.begin() as c:
        row = c.execute(sa.text("""
            SELECT effective_start
              FROM staff_role_assignment
             WHERE id=:aid AND staff_id=:sid
        """), {"aid": assignment_id, "sid": staff_id}).first()
        if row and d >= row.effective_start:
            c.execute(sa.text("""
                UPDATE staff_role_assignment SET effective_end=:d WHERE id=:aid
            """), {"d": d, "aid": assignment_id})

    if request.headers.get("hx-request"):
        with engine.connect() as c2:
            assignments = _fetch_assignments_for(c2, staff_id)
        return templates.TemplateResponse(
            "partials/assignments_table.html",
            {"request": request, "s_id": staff_id, "assignments": assignments}
        )

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

@admin.post("/staff/{staff_id}/end")
def admin_end_staff(request: Request, staff_id: str, end_date: str = Form("")):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    d = _date.fromisoformat(end_date) if end_date else _date.today()

    with engine.begin() as c:
        c.execute(sa.text("UPDATE staff SET end_date=:d WHERE id=:sid"), {"d": d, "sid": staff_id})
        c.execute(sa.text("""
            UPDATE staff_role_assignment
               SET effective_end = :d
             WHERE staff_id = :sid
               AND effective_start <= :d
               AND (effective_end IS NULL OR :d < effective_end)
        """), {"sid": staff_id, "d": d})

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

@admin.post("/staff/{staff_id}/reactivate")
def admin_reactivate_staff(request: Request, staff_id: str):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    with engine.begin() as c:
        c.execute(sa.text("UPDATE staff SET end_date=NULL WHERE id=:sid"), {"sid": staff_id})
    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

@admin.get("/staff/{staff_id}/edit", response_class=HTMLResponse)
def admin_staff_edit(request: Request, staff_id: str):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    with engine.connect() as c:
        s = c.execute(sa.text("""
          SELECT id, given_name, family_name, display_name, mobile, email, start_date, end_date
          FROM staff WHERE id=:sid
        """), {"sid": staff_id}).mappings().first()
        if not s:
            return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("admin_staff_edit.html", {"request": request, "s": s})

@admin.post("/staff/{staff_id}/update")
def admin_staff_update(
    request: Request, staff_id: str,
    given_name: str = Form(...),
    family_name: str = Form(...),
    mobile: str = Form(...),
    email: Optional[str] = Form(None),
    start_date: str = Form(...),
    end_date: Optional[str] = Form(None),
):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    try:
        sd = _date.fromisoformat(start_date)
    except Exception:
        sd = _date.today()
    ed = None
    if end_date:
        try:
            ed = _date.fromisoformat(end_date)
        except Exception:
            ed = None

    display_name = f"{given_name} {family_name}".strip()
    with engine.begin() as c:
        dup = c.execute(sa.text("""
            SELECT id FROM staff WHERE mobile=:m AND id<>:sid LIMIT 1
        """), {"m": mobile.strip(), "sid": staff_id}).first()
        if dup:
            return HTMLResponse(
                "<p style='color:crimson'>That mobile is used by another staff member.</p>"
                f"<p><a href='/admin/staff/{staff_id}/edit'>Back to edit</a></p>",
                status_code=400
            )

        c.execute(sa.text("""
            UPDATE staff
               SET given_name=:gn,
                   family_name=:fn,
                   display_name=:dn,
                   mobile=:m,
                   email=:e,
                   start_date=:sd,
                   end_date=:ed
             WHERE id=:sid
        """), {
            "gn": given_name.strip(), "fn": family_name.strip(), "dn": display_name,
            "m": mobile.strip(), "e": (email or None), "sd": sd, "ed": ed, "sid": staff_id
        })

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

# Register router
app.include_router(admin)

# Root redirect
@app.get("/")
def root(_: Request):
    return RedirectResponse("/admin/login")
