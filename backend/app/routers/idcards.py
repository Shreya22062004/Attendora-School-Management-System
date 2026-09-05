"""School-scoped 54 x 85 mm portrait ID cards for students and personnel."""
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from PIL import Image, ImageOps
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, defer

from .. import models
from ..auth import require_school_user
from ..database import get_db
from ..media_storage import MediaStorageError, delete as delete_media, get_bytes as get_media, optimize_student_photo, put_bytes as put_media

router = APIRouter(prefix="/idcards", tags=["ID Cards"])
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads" / "idcards"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _admin(user):
    if user.role != "school_admin":
        raise HTTPException(403, "School administrator access required")


def _image_type(data: bytes):
    if data.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if data.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    return None


async def _read_image(upload: UploadFile):
    data = await upload.read(MAX_IMAGE_BYTES + 1)
    mime = _image_type(data)
    if not data or len(data) > MAX_IMAGE_BYTES or not mime:
        raise HTTPException(400, "Upload a valid JPG, JPEG, or PNG image no larger than 5 MB")
    return data, mime


def _remove_white_background_bytes(data: bytes) -> tuple[bytes, str]:
    """Turn a white paper background into transparency while preserving ink/seal."""
    try:
        image = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGBA")

        # Work on a grayscale copy instead of looping through every pixel in
        # Python. This keeps signature uploads fast even for large camera scans.
        gray = image.convert("L")

        def alpha_for_white_background(value):
            if value >= 242:
                return 0
            if value <= 185:
                return 255
            return int(255 * (242 - value) / (242 - 185))

        alpha = gray.point(alpha_for_white_background)
        image.putalpha(alpha)

        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue(), "image/png"
    except Exception as error:
        raise HTTPException(400, "Could not process the signature image") from error


def _file_path(relative_path):
    if not relative_path: return None
    candidate = (UPLOAD_DIR / relative_path).resolve()
    return candidate if UPLOAD_DIR.resolve() in candidate.parents and candidate.is_file() else None


def _legacy_image(path):
    image_path = _file_path(path)
    if not image_path: return None
    data = image_path.read_bytes(); mime = _image_type(data)
    return (data, mime) if mime else None


@lru_cache(maxsize=512)
def _cached_remote_photo(public_id: str, secure_url: str):
    """Fetch a small Cloudinary derivative once per warm server instance."""
    try:
        data, mime = get_media(
            public_id,
            secure_url,
            transformation=[
                {"width": 360, "height": 460, "crop": "fill", "quality": "auto"},
            ],
        )
        if mime != "image/jpeg":
            image = ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
            image.thumbnail((360, 460), Image.Resampling.LANCZOS)
            out = BytesIO()
            image.save(out, format="JPEG", quality=82, optimize=True)
            data, mime = out.getvalue(), "image/jpeg"
        return data, mime
    except MediaStorageError:
        return None


def _student_photo(student):
    # Local DB bytes are the fastest/reliable primary copy. Cloudinary is only
    # a fallback for older records that do not have the local binary copy.
    if student.photo_data:
        return student.photo_data, student.photo_mime_type or "image/jpeg"
    if student.photo_storage_key or student.photo_storage_url:
        return _cached_remote_photo(
            student.photo_storage_key or "",
            student.photo_storage_url or "",
        )
    return _legacy_image(student.photo)


def _staff_photo(staff):
    if not staff.photo_storage_key:
        return None
    return _cached_remote_photo(
        staff.photo_storage_key,
        staff.photo_storage_url or "",
    )


def _school_signature(school):
    # Use the transparent DB copy first. This avoids a Cloudinary round trip for
    # every PDF request and keeps the signature available if Cloudinary is slow.
    if school and school.headmaster_signature_data:
        return school.headmaster_signature_data, school.headmaster_signature_mime_type or "image/png"
    if school and (school.headmaster_signature_key or school.headmaster_signature_url):
        try: return get_media(school.headmaster_signature_key, school.headmaster_signature_url)
        except MediaStorageError: pass
    return _legacy_image(school.headmaster_signature if school else None)


def _student_query(db, school_id, class_name=None, search=None):
    query = db.query(models.Student).options(defer(models.Student.photo_data)).filter(models.Student.school_id == school_id, models.Student.is_active == True)
    if class_name: query = query.filter(models.Student.class_name == class_name)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(models.Student.name.ilike(term), models.Student.pen_number.ilike(term), models.Student.contact_number.ilike(term)))
    class_order = case({"UKG/KG2/PP1": 0, **{str(i): i for i in range(1, 13)}}, value=models.Student.class_name, else_=99)
    return query.order_by(class_order, func.lower(models.Student.name))


def _student(db, student_id, school_id, include_photo=False):
    query = db.query(models.Student)
    if not include_photo: query = query.options(defer(models.Student.photo_data))
    item = query.filter(models.Student.id == student_id, models.Student.school_id == school_id).first()
    if not item: raise HTTPException(404, "Student not found")
    return item


def _school(db, school_id, include_signature=False):
    query = db.query(models.School)
    if not include_signature: query = query.options(defer(models.School.headmaster_signature_data))
    item = query.filter(models.School.id == school_id).first()
    if not item: raise HTTPException(404, "School not found")
    return item


def _staff(db, staff_id, school_id):
    item = db.query(models.Staff).filter(models.Staff.id == staff_id, models.Staff.school_id == school_id).first()
    if not item: raise HTTPException(404, "Staff member not found")
    return item


def _settings_payload(db, school):
    has_legacy = db.query(models.School.id).filter(models.School.id == school.id, models.School.headmaster_signature_data.isnot(None)).first()
    return {"school_name": school.school_name, "established_year": school.established_year or "", "address": school.address or "", "udise_code": school.udise_code or "", "headmaster_signature_url": school.headmaster_signature_url or "", "has_headmaster_signature": bool(school.headmaster_signature_key or school.headmaster_signature or has_legacy)}


@router.get("/settings")
def get_settings(user=Depends(require_school_user), db: Session = Depends(get_db)):
    return _settings_payload(db, _school(db, user.school_id))


@router.put("/settings")
def update_settings(payload: dict, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user); school = _school(db, user.school_id)
    year = str(payload.get("established_year") or "").strip()
    if year and (not year.isdigit() or len(year) != 4): raise HTTPException(400, "Established year must be a four-digit year")
    school.established_year = year or None
    db.add(models.AuditLog(school_id=user.school_id, user_id=user.id, action="UPDATE_ID_CARD_SETTINGS", entity_type="School", entity_id=str(school.id)))
    db.commit(); return {"message": "ID card settings saved", "established_year": school.established_year or ""}


@router.post("/settings/signature")
async def upload_signature(file: UploadFile = File(...), user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user)
    school = _school(db, user.school_id, include_signature=True)
    raw, _ = await _read_image(file)
    data, mime = _remove_white_background_bytes(raw)

    previous = school.headmaster_signature_key
    public_id = None
    secure_url = ""
    try:
        # Cloudinary is still useful for external/media backup, but the
        # transparent PNG is also stored in the database so PDF generation
        # does not depend on a remote image download.
        public_id, secure_url = put_media(
            f"attendora/signatures/{school.id}_headmaster", data, mime
        )
    except MediaStorageError:
        # Do not fail the upload just because the remote media service is slow.
        # The DB copy is sufficient for the ID-card preview and PDF.
        public_id = None
        secure_url = ""

    school.headmaster_signature_data = data
    school.headmaster_signature_mime_type = mime
    school.headmaster_signature_key = public_id
    school.headmaster_signature_url = secure_url

    try:
        db.commit()
    except Exception as error:
        db.rollback()
        if public_id and not previous:
            try: delete_media(public_id)
            except MediaStorageError: pass
        raise HTTPException(500, "Could not save signature") from error

    return {
        "message": "Headmaster signature saved with transparent background",
        "photo_url": secure_url,
    }


@router.get("/settings/signature")
def get_signature(user=Depends(require_school_user), db: Session = Depends(get_db)):
    signature = _school_signature(_school(db, user.school_id, include_signature=True))
    if not signature: raise HTTPException(404, "Headmaster signature not uploaded")
    return Response(signature[0], media_type=signature[1], headers={"Cache-Control": "private, max-age=86400"})


@router.get("/students")
def list_students(class_name: str | None = None, search: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    students = _student_query(db, user.school_id, class_name, search).all()
    binary_ids = {row[0] for row in db.query(models.Student.id).filter(models.Student.school_id == user.school_id, models.Student.photo_data.isnot(None)).all()}
    rows = [{"id": s.id, "name": s.name, "class_name": s.class_name, "section": s.section or "", "father_name": s.father_name, "mother_name": s.mother_name, "contact_number": s.contact_number, "blood_group": s.blood_group, "pen_number": s.pen_number, "date_of_birth": s.date_of_birth.isoformat() if s.date_of_birth else None, "photo_url": s.photo_storage_url or "", "photo_uploaded": bool(s.photo_storage_key or s.id in binary_ids or _file_path(s.photo))} for s in students]
    return {"students": rows, "summary": {"students": len(rows), "photos_uploaded": sum(row["photo_uploaded"] for row in rows), "photos_pending": sum(not row["photo_uploaded"] for row in rows)}}


@router.post("/students/{student_id}/photo")
async def upload_student_photo(student_id: int, file: UploadFile = File(...), user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user)
    item = _student(db, student_id, user.school_id, include_photo=True)
    raw, _ = await _read_image(file)
    previous = item.photo_storage_key

    try:
        data, mime = optimize_student_photo(raw)
    except MediaStorageError as error:
        raise HTTPException(400, str(error)) from error

    public_id = None
    secure_url = ""
    try:
        # Every upload gets its own Cloudinary asset. This preserves the old
        # photo when an administrator changes a student's photo.
        public_id, secure_url = put_media(
            f"attendora/students/{item.id}/{uuid4().hex}", data, mime
        )
    except MediaStorageError:
        # Keep the DB copy even if Cloudinary is temporarily unavailable.
        public_id = None
        secure_url = ""

    item.photo = None
    item.photo_data = data
    item.photo_mime_type = mime
    item.photo_storage_key = public_id
    item.photo_storage_url = secure_url

    try:
        db.commit()
    except Exception as error:
        db.rollback()
        # The new asset is unreferenced because the DB commit failed, so it is
        # safe to remove ONLY this newly uploaded asset. Never remove the
        # previous photo as part of replacing/changing a photo.
        if public_id:
            try: delete_media(public_id)
            except MediaStorageError: pass
        raise HTTPException(500, "Could not save photo") from error

    return {"message": "Student photo saved", "photo_url": secure_url}


@router.delete("/students/{student_id}/photo")
def delete_student_photo(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user)
    item = _student(db, student_id, user.school_id, include_photo=True)

    # "Remove Photo" only unlinks the photo from the student record. The
    # Cloudinary asset is intentionally retained so an accidental removal can
    # be recovered and so the old photo remains available as an archive.
    # Keep a non-deliverable recovery marker containing the old Cloudinary
    # public ID. The marker is not treated as a photo by the legacy file loader.
    item.photo = f"cloudinary-archive:{item.photo_storage_key}" if item.photo_storage_key else None
    item.photo_storage_key = None
    item.photo_storage_url = None
    item.photo_data = None
    item.photo_mime_type = None
    db.commit()

    return {"message": "Student photo removed; Cloudinary asset retained"}


@router.get("/students/{student_id}/photo")
def get_student_photo(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    photo = _student_photo(_student(db, student_id, user.school_id, include_photo=True))
    if not photo: raise HTTPException(404, "Student photo not uploaded")
    return Response(photo[0], media_type=photo[1], headers={"Cache-Control": "private, max-age=86400"})


# ID-card PDF generation is intentionally handled by the browser print engine.
# The live preview and print sheet both render PersonIDCard in React, ensuring
# there is one source of truth for the visual design.


def _docx_response(people, school, filename):
    """Editable portrait cards; text remains ordinary Word text and photos are embedded."""
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ROW_HEIGHT_RULE
    from docx.shared import Mm, Pt

    document = Document()
    section = document.sections[0]
    section.left_margin = section.right_margin = Mm(12)
    section.top_margin = section.bottom_margin = Mm(12)

    for index, person in enumerate(people):
        if index and index % 4 == 0:
            document.add_page_break()

        table = document.add_table(rows=3, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        table.columns[0].width = Mm(22)
        table.columns[1].width = Mm(32)

        for row, height in zip(table.rows, [Mm(17), Mm(58), Mm(11)]):
            row.height = height
            row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY

        header = table.cell(0, 0).merge(table.cell(0, 1))
        header.text = f"{school.school_name or ''}\n{school.address or ''}"
        for paragraph in header.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(7)
                run.bold = True

        photo_cell, text_cell = table.cell(1, 0), table.cell(1, 1)
        photo = _student_photo(person) if isinstance(person, models.Student) else _staff_photo(person)
        if photo:
            try:
                photo_cell.paragraphs[0].add_run().add_picture(BytesIO(photo[0]), width=Mm(20), height=Mm(27))
            except Exception:
                photo_cell.text = ""
        else:
            photo_cell.text = ""

        if isinstance(person, models.Student):
            details = [
                f"Father: {person.father_name or '-'}",
                f"Mother: {person.mother_name or '-'}",
                f"Mobile: {person.contact_number or '-'}",
                f"PEN: {person.pen_number or '-'}",
                f"DOB: {person.date_of_birth.strftime('%d/%m/%Y') if person.date_of_birth else ''}",
            ]
        else:
            details = [
                f"Designation: {person.designation or '-'}",
                f"Father/Husband: {person.father_husband_name or '-'}",
                f"Level: {person.level or '-'}",
                f"Mobile: {person.mobile_number or '-'}",
                f"DOB: {person.date_of_birth.strftime('%d/%m/%Y') if person.date_of_birth else ''}",
            ]
        text_cell.text = f"{person.name}\nBlood Group: {person.blood_group or '-'}\n" + "\n".join(details)
        for paragraph in text_cell.paragraphs:
            for run in paragraph.runs:
                run.font.size = Pt(6.5)

        footer = table.cell(2, 0).merge(table.cell(2, 1))
        footer.text = "SCHOOL AND MASS EDUCATION DEPARTMENT                                      HEADMASTER"

    stream = BytesIO()
    document.save(stream)
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


# No /print.pdf endpoint: the frontend prints the exact React preview DOM.

@router.get("/staff/bulk.docx")
def bulk_staff_docx(staff_type: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    query = db.query(models.Staff).filter(models.Staff.school_id == user.school_id, models.Staff.is_active == True)
    if staff_type: query = query.filter(models.Staff.staff_type == staff_type.upper())
    return _docx_response(query.order_by(models.Staff.name).all(), _school(db, user.school_id, include_signature=True), "staff-id-cards.docx")


@router.get("/bulk.docx")
def bulk_student_docx(class_name: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    return _docx_response(_student_query(db, user.school_id, class_name).all(), _school(db, user.school_id, include_signature=True), "student-id-cards.docx")

