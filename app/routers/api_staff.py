# app/routers/api_staff.py
from __future__ import annotations
from typing import Any, Mapping, List
from datetime import date as _date

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request, status as http_status

from app.core.db import engine

router = APIRouter(prefix="/api", tags=["api"])

def _as_bool(v: Any, default: bool = True) -> bool:
    if v is None: return default
    if isinstance(v, bool): return v
    if isinstance(v, int): return bool(v)
    if isinstance(v, str): return v.strip().lower() in {"1","true","t","yes","y"}
    return default

def _to_api(row: Mapping[str, Any]) -> dict:
    return {
        "id": str(row.get("id") or ""),
        "first_name": row.get("first_name") or row.get("given_name"),
        "last_name":  row.get("last_name")  or row.get("family_name"),
        "role":       row.get("role") or row.get("role_label") or row.get("primary_role_label"),
        "phone":      row.get("phone") or row.get("mobile"),
        "email":      row.get("email"),
        "is_active":  _as_bool(row.get("is_active", True), True),
        "notes":      row.get("notes"),
    }

@router.get("/health")
def api_health():
    return {"ok": True}

@router.get("/staff")
def api_staff_list() -> List[dict]:
    with engine.connect() as c:
        rows = c.execute(sa.text("""
            SELECT id, given_name, family_name, mobile, email,
                   (end_date IS NULL) AS is_active
              FROM staff
             ORDER BY family_name, given_name
        """)).mappings().all()
    return [_to_api(r) for r in rows]

@router.post("/staff", status_code=http_status.HTTP_201_CREATED)
async def api_staff_create(request: Request):
    data = await request.json()
    gn = data.get("first_name") or data.get("firstName") or data.get("given_name")
    fn = data.get("last_name")  or data.get("lastName")  or data.get("family_name")
    phone = data.get("phone") or data.get("mobile")
    email = data.get("email")
    is_active = _as_bool(data.get("is_active", data.get("isActive", True)), True)
    if not (gn and fn and phone):
        raise HTTPException(status_code=400, detail="first_name, last_name, phone required")

    today = _date.today()
    display_name = f"{gn} {fn}".strip()

    with engine.begin() as c:
        dup = c.execute(sa.text("SELECT id FROM staff WHERE mobile=:m"), {"m": phone.strip()}).first()
        if dup:
            raise HTTPException(status_code=409, detail="Mobile already exists for another staff")

        row = c.execute(sa.text("""
            INSERT INTO staff (given_name, family_name, display_name, mobile, email, start_date, end_date)
            VALUES (:gn,:fn,:dn,:m,:e,:sd,:ed)
            RETURNING id, given_name, family_name, mobile, email, (end_date IS NULL) AS is_active
        """), {"gn": gn, "fn": fn, "dn": display_name, "m": phone.strip(),
               "e": email, "sd": today, "ed": (None if is_active else today)}).mappings().first()

    return _to_api(row)

@router.put("/staff/{staff_id}")
async def api_staff_update(staff_id: str, request: Request):
    data = await request.json()
    gn = data.get("first_name") or data.get("firstName")
    fn = data.get("last_name")  or data.get("LastName") or data.get("lastName")
    phone = data.get("phone")
    email = data.get("email")
    is_active = data.get("is_active") if "is_active" in data else data.get("isActive")

    with engine.begin() as c:
        exists = c.execute(sa.text("SELECT id FROM staff WHERE id=:sid"), {"sid": staff_id}).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Staff not found")

        sets, params = [], {"sid": staff_id}
        if gn is not None: sets += ["given_name=:gn"]; params["gn"] = gn
        if fn is not None: sets += ["family_name=:fn"]; params["fn"] = fn
        if gn is not None or fn is not None:
            params["dn"] = f"{gn or ''} {fn or ''}".strip(); sets += ["display_name=:dn"]
        if phone is not None: params["m"] = phone; sets += ["mobile=:m"]
        if email is not None: params["e"] = email; sets += ["email=:e"]
        if is_active is not None:
            if _as_bool(is_active, True):
                sets += ["end_date=NULL"]
            else:
                params["ed"] = _date.today(); sets += ["end_date=:ed"]

        if sets:
            c.execute(sa.text(f"UPDATE staff SET {', '.join(sets)} WHERE id=:sid"), params)

        row = c.execute(sa.text("""
            SELECT id, given_name, family_name, mobile, email, (end_date IS NULL) AS is_active
              FROM staff WHERE id=:sid
        """), {"sid": staff_id}).mappings().first()

    return _to_api(row) if row else (_ for _ in ()).throw(HTTPException(status_code=404, detail="Staff not found"))

@router.delete("/staff/{staff_id}")
def api_staff_delete(staff_id: str):
    with engine.begin() as c:
        row = c.execute(sa.text("DELETE FROM staff WHERE id=:sid RETURNING id"), {"sid": staff_id}).first()
    if not row:
        raise HTTPException(status_code=404, detail="Staff not found")
    return {"ok": True}
