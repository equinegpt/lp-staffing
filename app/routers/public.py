from __future__ import annotations
from typing import Optional, List, Dict
from datetime import date as _date
import sqlalchemy as sa
from fastapi import APIRouter, Query
from app.core.db import engine
from app.core.constants import ROLE_WHERE, LOC_WHERE

router = APIRouter()

@router.get("/healthz")
def healthz():
    with engine.connect() as c:
        c.exec_driver_sql("SELECT 1")
    return {"ok": True}

@router.get("/roles")
def get_roles() -> List[Dict]:
    sql = sa.text(f"SELECT code, label FROM role WHERE {ROLE_WHERE} ORDER BY label")
    with engine.connect() as c:
        return [dict(r) for r in c.execute(sql).mappings().all()]

@router.get("/locations")
def get_locations() -> List[Dict]:
    sql = sa.text(f"SELECT code, name, timezone FROM location WHERE {LOC_WHERE} ORDER BY name")
    with engine.connect() as c:
        return [dict(r) for r in c.execute(sql).mappings().all()]

@router.get("/staff")
def get_staff(
    d: _date = Query(..., description="Date (YYYY-MM-DD)"),
    role: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
) -> List[Dict]:
    sql = sa.text("""
    SELECT s.id, s.given_name, s.family_name, s.display_name, s.mobile, s.email,
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
