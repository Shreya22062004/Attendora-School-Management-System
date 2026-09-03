"""School-scoped personnel records and Cloudinary-backed staff photos."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import require_school_user
from ..database import get_db
from ..media_storage import MediaStorageError, delete as delete_media, get_bytes, optimize_student_photo, put_bytes

router = APIRouter(prefix="/staff", tags=["Staff"])
VALID_TYPES = {"TEACHER", "STAFF", "SMC_MEMBER"}


def _admin(user):
    if user.role != "school_admin":
        raise HTTPException(403, "School administrator access required")


def _clean(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _staff(db, staff_id, school_id):
    item = db.query(models.Staff).filter(models.Staff.id == staff_id, models.Staff.school_id == school_id).first()
    if not item:
        raise HTTPException(404, "Staff member not found")
    return item


def _payload(item):
    return {
        "id": item.id, "name": item.name, "designation": item.designation or "",
        "father_husband_name": item.father_husband_name or "", "level": item.level or "",
        "date_of_birth": item.date_of_birth.isoformat() if item.date_of_birth else None,
        "mobile_number": item.mobile_number or "", "blood_group": item.blood_group or "",
        "staff_type": item.staff_type, "employee_id": item.employee_id or "", "gender": item.gender or "",
        "user_id": item.user_id, "is_active": item.is_active,
        "photo_uploaded": bool(item.photo_storage_key), "photo_url": item.photo_storage_url or "",
    }


def _apply(item, data, db, school_id):
    staff_type = (data.staff_type or "STAFF").upper()
    if staff_type not in VALID_TYPES:
        raise HTTPException(400, "Staff type must be Teacher, Staff, or SMC Member")
    if not data.name.strip():
        raise HTTPException(400, "Staff name is required")
    linked_user_id = data.user_id
    if linked_user_id is not None:
        linked = db.query(models.User).filter(
            models.User.id == linked_user_id,
            models.User.school_id == school_id,
            models.User.role == "teacher",
        ).first()
        if not linked:
            raise HTTPException(400, "Selected login account is not a teacher login in this school")
        if staff_type != "TEACHER":
            raise HTTPException(400, "A login account can only be linked to a Teacher staff record")
        duplicate = db.query(models.Staff).filter(
            models.Staff.user_id == linked_user_id,
            models.Staff.school_id == school_id,
            models.Staff.id != (item.id or -1),
            models.Staff.is_active == True,
        ).first()
        if duplicate:
            raise HTTPException(409, "This teacher login is already linked to another staff record")

    item.name = data.name.strip(); item.designation = _clean(data.designation)
    item.father_husband_name = _clean(data.father_husband_name); item.level = _clean(data.level)
    item.date_of_birth = data.date_of_birth; item.mobile_number = _clean(data.mobile_number)
    item.blood_group = _clean(data.blood_group); item.staff_type = staff_type
    item.employee_id = _clean(data.employee_id); item.gender = _clean(data.gender); item.user_id = linked_user_id


@router.get("")
def list_staff(staff_type: str | None = None, search: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    query = db.query(models.Staff).filter(models.Staff.school_id == user.school_id, models.Staff.is_active == True)
    if staff_type and staff_type.upper() in VALID_TYPES:
        query = query.filter(models.Staff.staff_type == staff_type.upper())
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(models.Staff.name.ilike(term), models.Staff.mobile_number.ilike(term), models.Staff.designation.ilike(term)))
    return [_payload(item) for item in query.order_by(models.Staff.staff_type, models.Staff.name).all()]


@router.get("/login-users")
def list_login_users(user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user)
    users = db.query(models.User).filter(
        models.User.school_id == user.school_id,
        models.User.role == "teacher",
    ).order_by(models.User.username).all()
    return [{"id": item.id, "username": item.username} for item in users]


@router.post("")
def create_staff(data: schemas.StaffCreate, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user)
    item = models.Staff(school_id=user.school_id)
    _apply(item, data, db, user.school_id)
    db.add(item); db.flush()
    db.add(models.AuditLog(school_id=user.school_id, user_id=user.id, action="CREATE", entity_type="Staff", entity_id=str(item.id)))
    db.commit(); db.refresh(item)
    return _payload(item)


@router.put("/{staff_id}")
def update_staff(staff_id: int, data: schemas.StaffUpdate, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user); item = _staff(db, staff_id, user.school_id); _apply(item, data, db, user.school_id); item.is_active = data.is_active
    db.add(models.AuditLog(school_id=user.school_id, user_id=user.id, action="UPDATE", entity_type="Staff", entity_id=str(item.id)))
    db.commit(); db.refresh(item); return _payload(item)


@router.delete("/{staff_id}")
def deactivate_staff(staff_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user); item = _staff(db, staff_id, user.school_id); item.is_active = False
    db.add(models.AuditLog(school_id=user.school_id, user_id=user.id, action="DEACTIVATE", entity_type="Staff", entity_id=str(item.id)))
    db.commit(); return {"message": "Staff member deactivated"}


@router.post("/{staff_id}/photo")
async def upload_photo(staff_id: int, file: UploadFile = File(...), user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user); item = _staff(db, staff_id, user.school_id)
    raw = await file.read(5 * 1024 * 1024 + 1)
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "Upload an image no larger than 5 MB")
    try:
        data, mime_type = optimize_student_photo(raw)
        public_id, secure_url = put_bytes(f"attendora/staff/{item.id}", data, mime_type)
    except MediaStorageError as error:
        raise HTTPException(503, str(error)) from error
    previous_key = item.photo_storage_key; item.photo_storage_key = public_id; item.photo_storage_url = secure_url; item.photo_mime_type = mime_type
    try: db.commit()
    except Exception as error:
        db.rollback()
        if not previous_key:
            try: delete_media(public_id)
            except MediaStorageError: pass
        raise HTTPException(500, "Could not save staff photo reference") from error
    return {"message": "Staff photo saved", "photo_url": secure_url}


@router.delete("/{staff_id}/photo")
def delete_photo(staff_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user)
    item = _staff(db, staff_id, user.school_id)
    key = item.photo_storage_key
    item.photo_storage_key = None
    item.photo_storage_url = None
    item.photo_mime_type = None
    item.photo_data = None
    item.photo = None
    db.commit()
    if key:
        try:
            delete_media(key)
        except MediaStorageError:
            pass
    return {"message": "Staff photo removed"}


@router.get("/{staff_id}/photo")
def get_photo(staff_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    item = _staff(db, staff_id, user.school_id)
    if not item.photo_storage_key:
        raise HTTPException(404, "Staff photo not uploaded")
    try: data, mime_type = get_bytes(item.photo_storage_key, item.photo_storage_url)
    except MediaStorageError as error: raise HTTPException(502, "Could not retrieve staff photo") from error
    return Response(data, media_type=mime_type, headers={"Cache-Control": "private, max-age=86400"})
