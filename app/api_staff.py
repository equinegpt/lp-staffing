from fastapi import APIRouter, HTTPException, Depends, Request, status as http_status
from fastapi.responses import JSONResponse
import sqlalchemy as sa
from sqlalchemy.orm import Session

# import your get_session and Staff model from wherever they live
from app.main import get_session, Staff  # adjust if your paths differ

router = APIRouter(prefix="/api", tags=["api"])

def staff_to_dict(r):
    return {
        "id": str(getattr(r, "id", "")),
        "first_name": getattr(r, "first_name", None),
        "last_name": getattr(r, "last_name", None),
        "role": getattr(r, "role", None),
        "phone": getattr(r, "phone", None),
        "email": getattr(r, "email", None),
        "is_active": bool(getattr(r, "is_active", True)),
        "notes": getattr(r, "notes", None),
    }

@router.get("/health")
def api_health():
    return {"ok": True}

@router.get("/staff")
def api_staff_list(db: Session = Depends(get_session)):
    rows = db.scalars(sa.select(Staff).order_by(Staff.last_name, Staff.first_name)).all()
    return [staff_to_dict(r) for r in rows]

@router.post("/staff")
async def api_staff_create(request: Request, db: Session = Depends(get_session)):
    data = await request.json()
    obj = Staff(
        first_name=data.get("first_name") or data.get("firstName"),
        last_name=data.get("last_name") or data.get("lastName"),
        role=data.get("role"),
        phone=data.get("phone"),
        email=data.get("email"),
        is_active=(data.get("is_active") if "is_active" in data else data.get("isActive", True)),
        notes=data.get("notes"),
    )
    db.add(obj); db.commit(); db.refresh(obj)
    return JSONResponse(staff_to_dict(obj), status_code=http_status.HTTP_201_CREATED)

@router.put("/staff/{staff_id}")
async def api_staff_update(staff_id: str, request: Request, db: Session = Depends(get_session)):
    data = await request.json()
    obj = db.get(Staff, staff_id) or db.scalar(sa.select(Staff).where(Staff.id == staff_id))
    if not obj:
        raise HTTPException(status_code=404, detail="Staff not found")
    mapping = {
        "first_name": "first_name", "firstName": "first_name",
        "last_name": "last_name",   "lastName": "last_name",
        "role": "role", "phone": "phone", "email": "email",
        "is_active": "is_active", "isActive": "is_active",
        "notes": "notes",
    }
    for k_api, k_model in mapping.items():
        if k_api in data: setattr(obj, k_model, data[k_api])
    db.add(obj); db.commit(); db.refresh(obj)
    return staff_to_dict(obj)

@router.delete("/staff/{staff_id}")
def api_staff_delete(staff_id: str, db: Session = Depends(get_session)):
    obj = db.get(Staff, staff_id) or db.scalar(sa.select(Staff).where(Staff.id == staff_id))
    if not obj:
        raise HTTPException(status_code=404, detail="Staff not found")
    db.delete(obj); db.commit()
    return {"ok": True}
