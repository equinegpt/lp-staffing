from __future__ import annotations
from datetime import date as _date, timedelta
from typing import Optional, List, Dict
import sqlalchemy as sa
from app.core.db import engine

def fetch_assignments_for(conn, staff_id: str):
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

def fetch_staff_for_list(*, day: _date, role_code: Optional[str], loc_code: Optional[str],
                         status: Optional[str], q: Optional[str]) -> List[Dict]:
    status_norm = (status or "").strip().lower()
    if status_norm not in ("active", "inactive"):
        status_norm = ""
    role_code_s = (role_code or "").strip()
    loc_code_s  = (loc_code  or "").strip()
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
    params = {"D": day, "status": status_norm, "role_code": role_code_s,
              "loc_code": loc_code_s, "q": q_raw, "q_like": q_like}
    with engine.connect() as c:
        return [dict(r) for r in c.execute(sql, params).mappings().all()]
