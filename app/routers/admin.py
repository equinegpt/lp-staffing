from __future__ import annotations
import io, csv
from datetime import date as _date, timedelta
from typing import Optional, List, Dict
from uuid import UUID as _UUID

import sqlalchemy as sa
from fastapi import APIRouter, Request, Form, Depends
from fastapi import status as http_status
from starlette.responses import RedirectResponse, HTMLResponse, StreamingResponse

from app.core.db import engine
from app.core.constants import ROLE_WHERE, LOC_WHERE
from app.core.config import ADMIN_API_KEY, ADMIN_WEB_PASSWORD
from app.core.templates import templates
from app.services.staff import fetch_assignments_for, fetch_staff_for_list

router = APIRouter(prefix="/admin", tags=["admin"])

def _admin_only(request: Request) -> bool:
    return bool(request.session.get("admin"))

def staff_to_dict(r: Dict) -> Dict:
    return {
        "id": str(r.get("id", "")),
        "first_name": r.get("given_name"),
        "last_name": r.get("family_name"),
        "role": r.get("role_label") or r.get("role_code"),
        "phone": r.get("mobile"),
        "email": r.get("email"),
        "is_active": bool(r.get("is_active", True)),
        "notes": None,
    }

# ---------- Login / Logout ----------
@router.get("/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

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

    return templates.TemplateResponse("login.html", {"request": request, "error": "Wrong password"}, status_code=401)

@router.get("/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)

@router.get("", response_class=HTMLResponse)
def admin_home_redirect():
    return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)

# ---------- Staff List (HTML + JSON) ----------
@router.get("/staff", response_class=HTMLResponse)
def admin_staff_list(request: Request,
                     d: Optional[str] = None, q: Optional[str] = None,
                     role: Optional[str] = None, location: Optional[str] = None,
                     status: Optional[str] = None):
    # JSON branch for apps
    if "application/json" in (request.headers.get("accept") or ""):
        day = _date.today()
        rows = fetch_staff_for_list(day=day, role_code=role, loc_code=location, status=status, q=q)
        return [staff_to_dict(r) for r in rows]

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

# ---------- CSV (must be BEFORE /staff/{id}) ----------
@router.get("/staff/export.csv")
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
                             headers={"Content-Disposition": f'attachment; filename="staff_{(_date.today().isoformat())}.csv"'} )

# ---------- HTML Create/New ----------
@router.get("/staff/new", response_class=HTMLResponse)
def admin_staff_new(request: Request):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    with engine.connect() as c:
        roles = c.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
        locs  = c.execute(sa.text(f"SELECT code,name  FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()
    return templates.TemplateResponse("admin_staff_new.html", {
        "request": request, "roles": roles, "locations": locs,
        "today": _date.today().isoformat(), "now": _date.today().isoformat()
    })

@router.post("/staff/create")
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

# ---------- JSON Create/Update/Delete (Accept: application/json) ----------
@router.post("/staff")
async def admin_staff_create_json(request: Request):
    if "application/json" not in (request.headers.get("accept") or ""):
        return RedirectResponse("/admin/staff", status_code=http_status.HTTP_303_SEE_OTHER)

    data = await request.json()
    gn = data.get("first_name") or data.get("firstName") or data.get("given_name")
    fn = data.get("last_name")  or data.get("lastName")  or data.get("family_name")
    phone = data.get("phone") or data.get("mobile")
    email = data.get("email")
    is_active = data.get("is_active", data.get("isActive", True))
    if not (gn and fn and phone):
        return HTMLResponse("first_name, last_name, phone required", status_code=400)

    display_name = f"{gn} {fn}".strip()
    today = _date.today()

    with engine.begin() as c:
        dup = c.execute(sa.text("SELECT id FROM staff WHERE mobile=:m"), {"m": phone.strip()}).first()
        if dup:
            return HTMLResponse("Mobile already exists for another staff", status_code=409)

        staff_id = c.execute(sa.text("""
            INSERT INTO staff (given_name, family_name, display_name, mobile, email, start_date, end_date)
            VALUES (:gn,:fn,:dn,:m,:e,:sd,:ed)
            RETURNING id
        """), {"gn": gn, "fn": fn, "dn": display_name, "m": phone.strip(),
               "e": email, "sd": today, "ed": (None if is_active else today)}).scalar_one()

    row = {"id": str(staff_id), "given_name": gn, "family_name": fn,
           "role_label": None, "role_code": None, "mobile": phone, "email": email,
           "is_active": bool(is_active)}
    return staff_to_dict(row)

@router.put("/staff/{staff_id}")
async def admin_staff_update_json(staff_id: str, request: Request):
    data = await request.json()
    gn = data.get("first_name") or data.get("firstName")
    fn = data.get("last_name")  or data.get("lastName")
    phone = data.get("phone")
    email = data.get("email")
    is_active = data.get("is_active") if "is_active" in data else data.get("isActive")

    with engine.begin() as c:
        exists = c.execute(sa.text("SELECT id FROM staff WHERE id=:sid"), {"sid": staff_id}).first()
        if not exists:
            return HTMLResponse("Staff not found", status_code=404)

        sets, params = [], {"sid": staff_id}
        if gn is not None: sets += ["given_name=:gn"]; params["gn"] = gn
        if fn is not None: sets += ["family_name=:fn"]; params["fn"] = fn
        if gn is not None or fn is not None:
            params["dn"] = f"{gn or ''} {fn or ''}".strip(); sets += ["display_name=:dn"]
        if phone is not None: params["m"] = phone; sets += ["mobile=:m"]
        if email is not None: params["e"] = email; sets += ["email=:e"]
        if is_active is not None:
            if bool(is_active): sets += ["end_date=NULL"]
            else: params["ed"] = _date.today(); sets += ["end_date=:ed"]

        if sets:
            c.execute(sa.text(f"UPDATE staff SET {', '.join(sets)} WHERE id=:sid"), params)

        row = c.execute(sa.text("""
            SELECT id, given_name, family_name, mobile, email, (end_date IS NULL) AS is_active
              FROM staff WHERE id=:sid
        """), {"sid": staff_id}).mappings().first()

    if not row:
        return HTMLResponse("Staff not found", status_code=404)

    d = dict(row); d["role_label"] = None; d["role_code"] = None
    return staff_to_dict(d)

@router.delete("/staff/{staff_id}")
def admin_staff_delete_json(staff_id: str):
    with engine.begin() as c:
        row = c.execute(sa.text("DELETE FROM staff WHERE id=:sid RETURNING id"), {"sid": staff_id}).first()
    if not row:
        return HTMLResponse("Staff not found", status_code=404)
    return {"ok": True}

# ---------- HTML partials / detail / edit ----------
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
    return templates.TemplateResponse("partials/staff_table_rows.html", {"request": request, "staff": staff_rows})

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

    return templates.TemplateResponse("admin_staff_detail.html", {"request": request, "s": s, "assignments": assignments, "roles": roles, "locations": locs})

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
    return templates.TemplateResponse("admin_staff_edit.html", {"request": request, "s": s})

# ---------- Assignments ----------
@router.get("/staff/{staff_id}/assignments/table", response_class=HTMLResponse)
def admin_assignments_table(request: Request, staff_id: str):
    if not _admin_only(request):
        return HTMLResponse("", status_code=401)
    with engine.connect() as c:
        assignments = fetch_assignments_for(c, staff_id)
        roles = c.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
        locations = c.execute(sa.text(f"SELECT code,name FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()
    return templates.TemplateResponse("partials/assignments_table.html", {"request": request, "s_id": staff_id, "assignments": assignments, "roles": roles, "locations": locations})

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
                return templates.TemplateResponse("partials/assignments_table.html",
                    {"request": request, "s_id": staff_id, "assignments": assignments, "roles": roles, "locations": locations},
                    status_code=400)
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

        if current and current.role_id == role.id and ((current.location_id is None and loc_id is None) or current.location_id == loc_id):
            if end and (current.effective_end is None or current.effective_end != end):
                c.execute(sa.text("UPDATE staff_role_assignment SET effective_end=:en WHERE id=:aid"),
                          {"en": end, "aid": current.id})
        else:
            if current:
                new_prev_end = start - timedelta(days=1)
                c.execute(sa.text("UPDATE staff_role_assignment SET effective_end=:en WHERE id=:aid AND (effective_end IS NULL OR effective_end > :en)"),
                          {"en": new_prev_end, "aid": current.id})
            c.execute(sa.text("""
                INSERT INTO staff_role_assignment (staff_id, role_id, location_id, effective_start, effective_end)
                VALUES (:sid, :rid, :lid, :st, :en)
            """), {"sid": staff_id, "rid": role.id, "lid": loc_id, "st": start, "en": end})

    if request.headers.get("hx-request"):
        with engine.connect() as c2:
            assignments = fetch_assignments_for(c2, staff_id)
            roles = c2.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
            locations = c2.execute(sa.text(f"SELECT code,name FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()
        return templates.TemplateResponse("partials/assignments_table.html",
            {"request": request, "s_id": staff_id, "assignments": assignments, "roles": roles, "locations": locations})

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

@router.post("/staff/{staff_id}/assign/{assignment_id}/end")
def admin_end_assignment(
    request: Request, staff_id: str, assignment_id: str,
    end_date: Optional[str] = Form(None)
):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    d = _date.fromisoformat(end_date) if end_date else _date.today()
    with engine.begin() as c:
        row = c.execute(sa.text("SELECT effective_start FROM staff_role_assignment WHERE id=:aid AND staff_id=:sid"),
                        {"aid": assignment_id, "sid": staff_id}).first()
        if row and d >= row.effective_start:
            c.execute(sa.text("UPDATE staff_role_assignment SET effective_end=:d WHERE id=:aid"),
                      {"d": d, "aid": assignment_id})

    if request.headers.get("hx-request"):
        with engine.connect() as c2:
            assignments = fetch_assignments_for(c2, staff_id)
            roles = c2.execute(sa.text(f"SELECT code,label FROM role WHERE {ROLE_WHERE} ORDER BY label")).mappings().all()
            locations = c2.execute(sa.text(f"SELECT code,name FROM location WHERE {LOC_WHERE} ORDER BY name")).mappings().all()
        return templates.TemplateResponse("partials/assignments_table.html",
            {"request": request, "s_id": staff_id, "assignments": assignments, "roles": roles, "locations": locations})

    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)

@router.post("/staff/{staff_id}/end")
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

@router.post("/staff/{staff_id}/reactivate")
def admin_reactivate_staff(request: Request, staff_id: str):
    if not _admin_only(request):
        return RedirectResponse("/admin/login", status_code=http_status.HTTP_303_SEE_OTHER)
    with engine.begin() as c:
        c.execute(sa.text("UPDATE staff SET end_date=NULL WHERE id=:sid"), {"sid": staff_id})
    return RedirectResponse(f"/admin/staff/{staff_id}", status_code=http_status.HTTP_303_SEE_OTHER)
