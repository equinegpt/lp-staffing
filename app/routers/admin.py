# app/routers/admin.py
from __future__ import annotations
import io, csv
from datetime import date as _date, timedelta
from typing import Optional, Any

import sqlalchemy as sa
from fastapi import APIRouter, Request, Form, status as http_status
from starlette.responses import RedirectResponse, HTMLResponse, StreamingResponse

from app.core.db import engine
from app.core.constants import ROLE_WHERE, LOC_WHERE
from app.core.config import ADMIN_WEB_PASSWORD
from app.core.templates import render_any
from app.services.staff import fetch_assignments_for, fetch_staff_for_list

router = APIRouter(prefix="/admin", tags=["admin"])

# ------------------------------- helpers ---------------------------------- #

def _admin_only(request: Request) -> bool:
    return bool(request.session.get("admin"))

def _as_bool(v: Any, default: bool = True) -> bool:
    if v is None: return default
    if isinstance(v, bool): return v
    if isinstance(v, int): return bool(v)
    if isinstance(v, str): return v.strip().lower() in {"1","true","t","yes","y"}
    return default

def staff_to_api(row):
    return {
        "id": str(row.get("id") or ""),
        "first_name": row.get("first_name") or row.get("given_name"),
        "last_name":  row.get("last_name")  or row.get("family_name"),
        "role":       row.get("role") or row.get("role_label") or row.get("primary_role_label"),
        "location":   row.get("location") or row.get("location_label")
                       or row.get("primary_location_label") or row.get("location_code"),
        "phone":      row.get("phone") or row.get("mobile"),
        "email":      row.get("email"),
        "is_active":  bool(row.get("is_active", True)),
        "notes":      row.get("notes"),
    }

def _normalize_loc_code(code: str | None) -> str | None:
    if code is None:
        return None
    c = code.strip()
    return None if c in ("", "—") else c

def _upsert_current_assignment(conn, staff_id: str, role_code: str | None, loc_code: str | None, start: _date) -> None:
    """Ensure there is a current assignment effective on 'start'.
       If different from existing, end the current at 'start' and insert a new one."""
    if not role_code:
        return

    role = conn.execute(sa.text("SELECT id FROM role WHERE code=:c"), {"c": role_code}).first()
    if not role:
        return
    loc_id = None
    loc_code = _normalize_loc_code(loc_code)
    if loc_code:
        loc = conn.execute(sa.text("SELECT id FROM location WHERE code=:c"), {"c": loc_code}).first()
        loc_id = loc.id if loc else None

    current = conn.execute(sa.text("""
        SELECT id, role_id, location_id, effective_start, effective_end
          FROM staff_role_assignment
         WHERE staff_id = :sid
           AND effective_start <= :st
           AND (effective_end IS NULL OR effective_end > :st)
         ORDER BY effective_start DESC
         LIMIT 1
    """), {"sid": staff_id, "st": start}).mappings().first()

    if current and current.role_id == role.id and current.location_id == loc_id:
        return

    if current:
        conn.execute(
            sa.text("""UPDATE staff_role_assignment
                          SET effective_end=:en
                        WHERE id=:aid AND (effective_end IS NULL OR effective_end > :en)"""),
            {"en": start, "aid": current.id}
        )

    conn.execute(sa.text("""
        INSERT INTO staff_role_assignment (staff_id, role_id, location_id, effective_start)
        VALUES (:sid, :rid, :lid, :st)
    """), {"sid": staff_id, "rid": role.id, "lid": loc_id, "st": start})

def _select_staff_api_row(conn, staff_id: str):
    return conn.execute(sa.text("""
        SELECT s.id,
               s.given_name, s.family_name, s.mobile, s.email,
               (s.end_date IS NULL) AS is_active,
               r.code  AS role_code,
               r.label AS role_label,
               l.code  AS location_code,
               l.name  AS location_label
          FROM staff s
          LEFT JOIN LATERAL (
              SELECT a.role_id, a.location_id
                FROM staff_role_assignment a
               WHERE a.staff_id = s.id
                 AND a.effective_start <= :today
                 AND (a.effective_end IS NULL OR a.effective_end > :today)
               ORDER BY a.effective_start DESC
               LIMIT 1
          ) cur ON true
          LEFT JOIN role     r ON r.id = cur.role_id
          LEFT JOIN location l ON l.id = cur.location_id
         WHERE s.id = :sid
    """), {"sid": staff_id, "today": _date.today()}).mappings().first()

# -------------------------------- login ----------------------------------- #

@router.get("/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    # Try your saved "login" (no extension), then "admin/login", then ".html" variants
    return render_any("login", {"request": request, "error": None}, "admin/login", "login.html")

@router.post("/login")
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

    return render_any("login", {"request": request, "error": "Wrong password"}, "admin/login", "login.html")

@router.get("/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

@router.get("", response_class=HTMLResponse)
def admin_home_redirect():
    return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)

# --------------------------- staff list (HTML + JSON) --------------------- #

@router.get("/staff", response_class=HTMLResponse)
@router.get("/staff/", response_class=HTMLResponse)
def admin_staff_list(
    request: Request,
    d: Optional[str] = None,
    q: Optional[str] = None,
    role: Optional[str] = None,
    location: Optional[str] = None,
    status: Optional[str] = None,
):
    # JSON branch: no login, used by iOS client
    if "application/json" in (request.headers.get("accept") or ""):
        day = _date.today()
        rows = fetch_staff_for_list(day=day, role_code=role, loc_code=location, status=status, q=q)
        return [staff_to_api(r) for r in rows]

    # HTML branch
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    try:
        day = _date.fromisoformat(d) if d else _date.today()
    except Exception:
        day = _date.today()

    with engine.connect() as c:
        roles = c.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
        locs  = c.execute(sa.text(f"SELECT code,name  FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()

    staff_rows = fetch_staff_for_list(day=day, role_code=role, loc_code=location, status=status, q=q)
    return render_any(
        "admin/staff_list",
        {
            "request": request,
            "day": day.isoformat(),
            "staff": staff_rows,
            "roles": roles,
            "locations": locs,
            "active_role": role or "",
            "active_loc": location or "",
            "status": (status or ""),
            "q": q or ""
        },
        "admin_staff_list",
    )

# ------------------------------- CSV export -------------------------------- #

@router.get("/staff/export.csv")
def admin_staff_export_csv(
    request: Request,
    d: Optional[str] = None, q: Optional[str] = None,
    role: Optional[str] = None, location: Optional[str] = None,
    status: Optional[str] = None
):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    try:
        day = _date.fromisoformat(d) if d else _date.today()
    except Exception:
        day = _date.today()

    rows = fetch_staff_for_list(day=day, role_code=role, loc_code=location, status=status, q=q)
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["id","given_name","family_name","display_name","mobile","email","start_date","end_date","is_active","role_code","role_label","location_code"])
    for r in rows:
        w.writerow([
            r.get("id"), r.get("given_name"), r.get("family_name"), r.get("display_name"),
            r.get("mobile"), r.get("email"), r.get("start_date"), r.get("end_date"),
            "TRUE" if r.get("is_active") else "FALSE",
            r.get("role_code"), r.get("role_label"), r.get("location_code"),
        ])
    out.seek(0)
    return StreamingResponse(out, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="staff_{(_date.today().isoformat())}.csv"'}
    )

# ------------------------------ HTML create -------------------------------- #

@router.get("/staff/new", response_class=HTMLResponse)
def admin_staff_new(request: Request):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    with engine.connect() as c:
        roles = c.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
        locs  = c.execute(sa.text(f"SELECT code,name  FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()
    return render_any(
        "admin/staff_new",
        {"request": request, "roles": roles, "locations": locs,
         "today": _date.today().isoformat(), "now": _date.today().isoformat()},
        "admin_staff_new",
    )

# ------------------------- JSON CRUD for apps (no login) ------------------- #

@router.post("/staff")
async def admin_staff_create_json(request: Request):
    # Only JSON callers
    if "application/json" not in (request.headers.get("accept") or "").lower():
        return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)

    data = await request.json()
    gn = data.get("first_name") or data.get("firstName") or data.get("given_name")
    fn = data.get("last_name")  or data.get("lastName")  or data.get("family_name")
    phone = data.get("phone") or data.get("mobile")
    email = data.get("email")
    is_active = _as_bool(data.get("is_active", data.get("isActive", True)), True)
    role_code = data.get("role") or data.get("role_code")
    loc_code  = data.get("location") or data.get("location_code")

    if not (gn and fn and phone):
        return HTMLResponse("first_name, last_name, phone required", status_code=400)

    start = _date.today()
    end_for_insert = None if is_active else start  # exclusive end: not active on 'start'
    display_name = f"{gn} {fn}".strip()

    with engine.begin() as c:
        dup = c.execute(sa.text("SELECT id FROM staff WHERE mobile=:m"), {"m": phone.strip()}).first()
        if dup:
            row = _select_staff_api_row(c, dup.id)
            return staff_to_api(row)

        staff_id = c.execute(sa.text("""
            INSERT INTO staff (given_name, family_name, display_name, mobile, email, start_date, end_date, status)
            VALUES (:gn,:fn,:dn,:m,:e,:sd,:ed,:st)
            RETURNING id
        """), {
            "gn": gn, "fn": fn, "dn": display_name, "m": phone.strip(),
            "e": email, "sd": start,
            "ed": end_for_insert,
            "st": ("ACTIVE" if is_active else "INACTIVE"),
        }).scalar_one()

        _upsert_current_assignment(c, staff_id, role_code, loc_code, start)
        row = _select_staff_api_row(c, staff_id)

    return staff_to_api(row)

@router.put("/staff/{staff_id}")
async def admin_staff_update_json(staff_id: str, request: Request):
    data = await request.json()

    gn = data.get("first_name") or data.get("firstName")
    fn = data.get("last_name")  or data.get("lastName")
    phone = data.get("phone")
    email = data.get("email")
    is_active = data.get("is_active") if "is_active" in data else data.get("isActive")
    role_code = data.get("role") or data.get("role_code")
    loc_code  = data.get("location") or data.get("location_code")

    today = _date.today()

    with engine.begin() as c:
        exists = c.execute(sa.text("SELECT id FROM staff WHERE id=:sid"), {"sid": staff_id}).first()
        if not exists:
            return HTMLResponse("Staff not found", status_code=404)

        sets, params = [], {"sid": staff_id}
        if gn is not None:
            sets += ["given_name=:gn"]; params["gn"] = gn
        if fn is not None:
            sets += ["family_name=:fn"]; params["fn"] = fn
        if gn is not None or fn is not None:
            params["dn"] = f"{gn or ''} {fn or ''}".strip(); sets += ["display_name=:dn"]
        if phone is not None:
            params["m"] = phone; sets += ["mobile=:m"]
        if email is not None:
            params["e"] = email; sets += ["email=:e"]

        if is_active is not None:
            if bool(is_active):
                sets += ["end_date=NULL", "status='ACTIVE'"]
            else:
                params["ed"] = today
                sets += ["end_date=:ed", "status='INACTIVE'"]

        if sets:
            c.execute(sa.text(f"UPDATE staff SET {', '.join(sets)} WHERE id=:sid"), params)

        if role_code is not None or loc_code is not None:
            _upsert_current_assignment(c, staff_id, role_code, loc_code, today)

        if is_active is not None and not bool(is_active):
            c.execute(sa.text("""
                UPDATE staff_role_assignment
                   SET effective_end = :d
                 WHERE staff_id = :sid
                   AND effective_start <= :d
                   AND (effective_end IS NULL OR :d < effective_end)
            """), {"sid": staff_id, "d": today})

        row = _select_staff_api_row(c, staff_id)

    return staff_to_api(row)

@router.delete("/staff/{staff_id}")
def admin_staff_delete_json(staff_id: str):
    with engine.begin() as c:
        row = c.execute(sa.text("DELETE FROM staff WHERE id=:sid RETURNING id"), {"sid": staff_id}).first()
    if not row:
        return HTMLResponse("Staff not found", status_code=404)
    return {"ok": True}

# ---------------------------- HTML detail/edit ----------------------------- #

@router.get("/staff/table", response_class=HTMLResponse)
def admin_staff_table(request: Request, d: Optional[str] = None, q: Optional[str] = None,
                      role: Optional[str] = None, location: Optional[str] = None,
                      status: Optional[str] = None):
    if not _admin_only(request):
        return HTMLResponse("", status_code=401)
    try:
        day = _date.fromisoformat(d) if d else _date.today()
    except Exception:
        day = _date.today()
    staff_rows = fetch_staff_for_list(day=day, role_code=role, loc_code=location, status=status, q=q)
    return render_any(
        "partials/staff_table_rows",
        {"request": request, "staff": staff_rows},
        "partials/staff_table_rows.html",
    )

@router.get("/staff/{staff_id}", response_class=HTMLResponse)
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

        assignments = fetch_assignments_for(c, staff_id)
        roles = c.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
        locs  = c.execute(sa.text(f"SELECT code,name  FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()

    return render_any(
        "admin/staff_detail",
        {"request": request, "s": s, "assignments": assignments, "roles": roles, "locations": locs},
        "admin_staff_detail",
    )

@router.get("/staff/{staff_id}/edit", response_class=HTMLResponse)
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
    return render_any("admin/staff_edit", {"request": request, "s": s}, "admin_staff_edit")

# ------------------------------- assignments ------------------------------- #

@router.get("/staff/{staff_id}/assignments/table", response_class=HTMLResponse)
def admin_assignments_table(request: Request, staff_id: str):
    if not _admin_only(request):
        return HTMLResponse("", status_code=401)
    with engine.connect() as c:
        assignments = fetch_assignments_for(c, staff_id)
        roles = c.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
        locations = c.execute(sa.text(f"SELECT code,name FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()
    return render_any(
        "partials/assignments_table",
        {"request": request, "s_id": staff_id, "assignments": assignments, "roles": roles, "locations": locations},
        "partials/assignments_table.html",
    )

@router.post("/staff/{staff_id}/assign")
def admin_add_assignment(
    request: Request, staff_id: str,
    role_code: str = Form(...),
    location_code: Optional[str] = Form(None),
    effective_start: str = Form(...),
    effective_end: Optional[str] = Form(None),
):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    start = _date.fromisoformat(effective_start)
    end = _date.fromisoformat(effective_end) if (effective_end or "").strip() else None

    with engine.begin() as c:
        role = c.execute(sa.text("SELECT id FROM role WHERE code=:c"), {"c": role_code}).first()
        if not role:
            if request.headers.get("hx-request"):
                assignments = fetch_assignments_for(c, staff_id)
                roles = c.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
                locations = c.execute(sa.text(f"SELECT code,name FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()
                return render_any(
                    "partials/assignments_table",
                    {"request": request, "s_id": staff_id, "assignments": assignments, "roles": roles, "locations": locations},
                    "partials/assignments_table.html",
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
               AND (effective_end IS NULL OR effective_end > :st)
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
                c.execute(sa.text("""
                    UPDATE staff_role_assignment
                       SET effective_end = :en
                     WHERE id = :aid AND (effective_end IS NULL OR effective_end > :en)
                """), {"en": start, "aid": current.id})
            c.execute(sa.text("""
                INSERT INTO staff_role_assignment
                    (staff_id, role_id, location_id, effective_start, effective_end)
                VALUES (:sid, :rid, :lid, :st, :en)
            """), {"sid": staff_id, "rid": role.id, "lid": loc_id, "st": start, "en": end})

    if request.headers.get("hx-request"):
        with engine.connect() as c2:
            assignments = fetch_assignments_for(c2, staff_id)
            roles = c2.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
            locations = c2.execute(sa.text(f"SELECT code,name FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()
        return render_any(
            "partials/assignments_table",
            {"request": request, "s_id": staff_id, "assignments": assignments, "roles": roles, "locations": locations},
            "partials/assignments_table.html",
        )

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

@router.post("/staff/{staff_id}/end")
def admin_end_staff(request: Request, staff_id: str, end_date: str = Form("")):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

    d = _date.fromisoformat(end_date) if end_date else _date.today()
    with engine.begin() as c:
        c.execute(sa.text("UPDATE staff SET end_date=:d, status='INACTIVE' WHERE id=:sid"),
                  {"d": d, "sid": staff_id})
        c.execute(sa.text("""
            UPDATE staff_role_assignment
               SET effective_end = :d
             WHERE staff_id = :sid
               AND effective_start <= :d
               AND (effective_end IS NULL OR :d < effective_end)
        """), {"sid": staff_id, "d": d})

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

@router.post("/staff/{staff_id}/reactivate")
def admin_reactivate_staff(request: Request, staff_id: str):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    with engine.begin() as c:
        c.execute(sa.text("UPDATE staff SET end_date=NULL, status='ACTIVE' WHERE id=:sid"),
                  {"sid": staff_id})
    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)
