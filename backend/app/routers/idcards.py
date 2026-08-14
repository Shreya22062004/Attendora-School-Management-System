"""School-scoped student ID card management and print-ready PDF generation."""
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_school_user
from ..database import get_db

router = APIRouter(prefix="/idcards", tags=["ID Cards"])

# Portrait orientation preserves the required 126 mm x 95 mm physical size while
# allowing exactly two cards across and two cards down on an A4 page.
CARD_WIDTH = 95 * mm
CARD_HEIGHT = 126 * mm
PAGE_MARGIN_X = 10 * mm
PAGE_MARGIN_Y = (A4[1] - (2 * CARD_HEIGHT)) / 2
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads" / "idcards"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _require_school_admin(user):
    if user.role != "school_admin":
        raise HTTPException(403, "School administrator access required")


def _image_type(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ("png", "image/png")
    if data.startswith(b"\xff\xd8\xff"):
        return ("jpg", "image/jpeg")
    return None


async def _save_image(upload: UploadFile, school_id: int, category: str) -> str:
    data = await upload.read(MAX_IMAGE_BYTES + 1)
    image_info = _image_type(data)
    if not data or len(data) > MAX_IMAGE_BYTES or not image_info:
        raise HTTPException(400, "Upload a valid JPG, JPEG, or PNG image no larger than 5 MB")
    extension, _ = image_info
    relative = Path(str(school_id)) / category / f"{uuid4().hex}.{extension}"
    target = UPLOAD_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative.as_posix()


def _file_path(relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    candidate = (UPLOAD_DIR / relative_path).resolve()
    if UPLOAD_DIR.resolve() not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _remove_file(relative_path: str | None):
    path = _file_path(relative_path)
    if path:
        path.unlink(missing_ok=True)


def _student_query(db: Session, school_id: int, class_name: str | None, search: str | None):
    query = db.query(models.Student).filter(
        models.Student.school_id == school_id,
        models.Student.is_active == True,
    )
    if class_name:
        query = query.filter(models.Student.class_name == class_name)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(
            models.Student.name.ilike(term),
            models.Student.pen_number.ilike(term),
            models.Student.father_name.ilike(term),
            models.Student.mother_name.ilike(term),
        ))
    class_order = case({"UKG/KG2/PP1": 0, **{str(i): i for i in range(1, 13)}}, value=models.Student.class_name, else_=99)
    gender_order = case({"Girl": 0, "Boy": 1}, value=models.Student.gender, else_=2)
    return query.order_by(class_order, gender_order, func.lower(models.Student.name))


@router.get("/settings")
def get_settings(user=Depends(require_school_user), db: Session = Depends(get_db)):
    school = db.get(models.School, user.school_id)
    if not school:
        raise HTTPException(404, "School not found")
    return {
        "school_name": school.school_name,
        "established_year": school.established_year or "",
        "address": school.address or "",
        "udise_code": school.udise_code or "",
        "has_headmaster_signature": bool(_file_path(school.headmaster_signature)),
    }


@router.put("/settings")
def update_settings(payload: dict, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _require_school_admin(user)
    year = str(payload.get("established_year") or "").strip()
    if year and (not year.isdigit() or len(year) != 4):
        raise HTTPException(400, "Established year must be a four-digit year")
    school = db.get(models.School, user.school_id)
    if not school:
        raise HTTPException(404, "School not found")
    school.established_year = year or None
    db.add(models.AuditLog(school_id=user.school_id, user_id=user.id, action="UPDATE_ID_CARD_SETTINGS", entity_type="School", entity_id=str(school.id)))
    db.commit()
    return {"message": "ID card settings saved", "established_year": school.established_year or ""}


@router.post("/settings/signature")
async def upload_signature(file: UploadFile = File(...), user=Depends(require_school_user), db: Session = Depends(get_db)):
    _require_school_admin(user)
    school = db.get(models.School, user.school_id)
    if not school:
        raise HTTPException(404, "School not found")
    previous = school.headmaster_signature
    school.headmaster_signature = await _save_image(file, user.school_id, "signature")
    db.add(models.AuditLog(school_id=user.school_id, user_id=user.id, action="UPLOAD_HEADMASTER_SIGNATURE", entity_type="School", entity_id=str(school.id)))
    db.commit()
    _remove_file(previous)
    return {"message": "Headmaster signature saved"}


@router.get("/settings/signature")
def get_signature(user=Depends(require_school_user), db: Session = Depends(get_db)):
    school = db.get(models.School, user.school_id)
    path = _file_path(school.headmaster_signature if school else None)
    if not path:
        raise HTTPException(404, "Headmaster signature not uploaded")
    return Response(path.read_bytes(), media_type="image/png" if path.suffix == ".png" else "image/jpeg")


@router.get("/students")
def list_students(class_name: str | None = None, search: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    students = _student_query(db, user.school_id, class_name, search).all()
    uploaded = sum(bool(_file_path(student.photo)) for student in students)
    return {
        "students": [{
            "id": student.id, "name": student.name, "class_name": student.class_name,
            "father_name": student.father_name, "mother_name": student.mother_name,
            "pen_number": student.pen_number,
            "date_of_birth": student.date_of_birth.isoformat() if student.date_of_birth else None,
            "photo_uploaded": bool(_file_path(student.photo)),
        } for student in students],
        "summary": {"students": len(students), "photos_uploaded": uploaded, "photos_pending": len(students) - uploaded},
    }


def _get_student(db: Session, student_id: int, school_id: int):
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.school_id == school_id).first()
    if not student:
        raise HTTPException(404, "Student not found")
    return student


@router.post("/students/{student_id}/photo")
async def upload_photo(student_id: int, file: UploadFile = File(...), user=Depends(require_school_user), db: Session = Depends(get_db)):
    _require_school_admin(user)
    student = _get_student(db, student_id, user.school_id)
    previous = student.photo
    student.photo = await _save_image(file, user.school_id, "students")
    db.add(models.AuditLog(school_id=user.school_id, user_id=user.id, action="UPLOAD_STUDENT_ID_PHOTO", entity_type="Student", entity_id=str(student.id)))
    db.commit()
    _remove_file(previous)
    return {"message": "Student photo saved"}


@router.delete("/students/{student_id}/photo")
def delete_photo(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _require_school_admin(user)
    student = _get_student(db, student_id, user.school_id)
    previous = student.photo
    student.photo = None
    db.commit()
    _remove_file(previous)
    return {"message": "Student photo removed"}


@router.get("/students/{student_id}/photo")
def get_photo(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    student = _get_student(db, student_id, user.school_id)
    path = _file_path(student.photo)
    if not path:
        raise HTTPException(404, "Student photo not uploaded")
    return Response(path.read_bytes(), media_type="image/png" if path.suffix == ".png" else "image/jpeg")


def _draw_image_or_placeholder(pdf, path: Path | None, x: float, y: float, width: float, height: float, label: str, cover: bool = False, frame: bool = True):
    if frame:
        pdf.setStrokeColor(colors.HexColor("#94a3b8"))
        pdf.setFillColor(colors.HexColor("#f8fafc"))
        pdf.rect(x, y, width, height, fill=1, stroke=1)
    if path:
        try:
            image = ImageReader(str(path))
            image_width, image_height = image.getSize()
            scale = (max if cover else min)(width / image_width, height / image_height)
            draw_width, draw_height = image_width * scale, image_height * scale
            pdf.saveState()
            if cover:
                clip = pdf.beginPath()
                clip.rect(x, y, width, height)
                pdf.clipPath(clip, stroke=0, fill=0)
            pdf.drawImage(image, x + (width - draw_width) / 2, y + (height - draw_height) / 2, draw_width, draw_height, mask="auto")
            pdf.restoreState()
            if frame:
                pdf.setStrokeColor(colors.HexColor("#94a3b8"))
                pdf.rect(x, y, width, height, fill=0, stroke=1)
            return
        except Exception:
            pass
    if not frame:
        return
    pdf.setFillColor(colors.HexColor("#64748b"))
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawCentredString(x + width / 2, y + height / 2 - 3, label)


def _draw_card(pdf, student, school, x: float, y: float):
    navy = colors.HexColor("#0d315a")
    blue = colors.HexColor("#1269a7")
    sky = colors.HexColor("#dceef9")
    ink = colors.HexColor("#14253d")
    paper = colors.HexColor("#f7fbff")
    pdf.setStrokeColor(colors.HexColor("#12375b"))
    pdf.setLineWidth(1.1)
    pdf.setFillColor(colors.white)
    pdf.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, 3 * mm, fill=1, stroke=1)

    # Layered header and wave accents deliberately remain inside the card frame.
    inner_x, inner_y = x + 1.5 * mm, y + 1.5 * mm
    inner_w, inner_h = CARD_WIDTH - 3 * mm, CARD_HEIGHT - 3 * mm
    pdf.setFillColor(navy)
    pdf.roundRect(inner_x, y + CARD_HEIGHT - 35 * mm, inner_w, 33.5 * mm, 2 * mm, fill=1, stroke=0)
    pdf.setFillColor(blue)
    accent = pdf.beginPath()
    accent.moveTo(inner_x, y + CARD_HEIGHT - 27 * mm)
    accent.curveTo(x + 23 * mm, y + CARD_HEIGHT - 20 * mm, x + 59 * mm, y + CARD_HEIGHT - 39 * mm, x + CARD_WIDTH - 1.5 * mm, y + CARD_HEIGHT - 24 * mm)
    accent.lineTo(x + CARD_WIDTH - 1.5 * mm, y + CARD_HEIGHT - 33 * mm)
    accent.curveTo(x + 62 * mm, y + CARD_HEIGHT - 46 * mm, x + 27 * mm, y + CARD_HEIGHT - 28 * mm, inner_x, y + CARD_HEIGHT - 35 * mm)
    accent.close()
    pdf.drawPath(accent, fill=1, stroke=0)
    pdf.setFillColor(sky)
    wave = pdf.beginPath()
    wave.moveTo(inner_x, y + CARD_HEIGHT - 34 * mm)
    wave.curveTo(x + 24 * mm, y + CARD_HEIGHT - 28 * mm, x + 58 * mm, y + CARD_HEIGHT - 43 * mm, x + CARD_WIDTH - 1.5 * mm, y + CARD_HEIGHT - 31 * mm)
    wave.lineTo(x + CARD_WIDTH - 1.5 * mm, y + CARD_HEIGHT - 35 * mm)
    wave.curveTo(x + 61 * mm, y + CARD_HEIGHT - 47 * mm, x + 26 * mm, y + CARD_HEIGHT - 33 * mm, inner_x, y + CARD_HEIGHT - 38 * mm)
    wave.close()
    pdf.drawPath(wave, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 8.5)
    school_name = (school.school_name or "SCHOOL").upper()
    if len(school_name) > 32:
        split_at = school_name.rfind(" ", 0, 32)
        split_at = split_at if split_at > 12 else 32
        pdf.drawCentredString(x + CARD_WIDTH / 2, y + CARD_HEIGHT - 8 * mm, school_name[:split_at])
        pdf.drawCentredString(x + CARD_WIDTH / 2, y + CARD_HEIGHT - 12 * mm, school_name[split_at:].strip()[:32])
        estd_y = y + CARD_HEIGHT - 17 * mm
    else:
        pdf.drawCentredString(x + CARD_WIDTH / 2, y + CARD_HEIGHT - 10 * mm, school_name)
        estd_y = y + CARD_HEIGHT - 15 * mm
    header_detail_y = estd_y
    if school.established_year:
        pdf.setFont("Helvetica-Bold", 6.8)
        pdf.drawCentredString(x + CARD_WIDTH / 2, estd_y, f"ESTD - {school.established_year}")
        header_detail_y -= 4 * mm
    if school.address:
        pdf.setFont("Helvetica", 5.2)
        pdf.drawCentredString(x + CARD_WIDTH / 2, header_detail_y, str(school.address)[:76])
        header_detail_y -= 3.3 * mm
    if school.udise_code:
        pdf.setFont("Helvetica-Bold", 5.5)
        pdf.drawCentredString(x + CARD_WIDTH / 2, header_detail_y, f"UDISE CODE: {school.udise_code}")

    photo_w, photo_h = 29 * mm, 34 * mm
    photo_x = x + (CARD_WIDTH - photo_w) / 2
    photo_y = y + CARD_HEIGHT - 67 * mm
    pdf.setFillColor(colors.white)
    pdf.setStrokeColor(colors.HexColor("#9fc6df"))
    pdf.roundRect(photo_x - 1.7 * mm, photo_y - 1.7 * mm, photo_w + 3.4 * mm, photo_h + 3.4 * mm, 1.5 * mm, fill=1, stroke=1)
    _draw_image_or_placeholder(pdf, _file_path(student.photo), photo_x, photo_y, photo_w, photo_h, "PHOTO PENDING", cover=True)

    rows = [("Student Name", student.name), ("Father Name", student.father_name), ("Mother Name", student.mother_name)]
    if student.pen_number:
        rows.append(("PEN No.", student.pen_number))
    if student.date_of_birth:
        rows.append(("Date of Birth", student.date_of_birth.strftime("%d/%m/%Y")))
    row_h = 5.05 * mm
    info_h = len(rows) * row_h + 5 * mm
    info_x, info_y = x + 5.5 * mm, photo_y - info_h - 5.5 * mm
    pdf.setFillColor(paper)
    pdf.setStrokeColor(colors.HexColor("#c8dfef"))
    pdf.roundRect(info_x, info_y, CARD_WIDTH - 11 * mm, info_h, 1.8 * mm, fill=1, stroke=1)
    pdf.setFillColor(blue)
    pdf.roundRect(info_x, info_y, 1.8 * mm, info_h, 1.2 * mm, fill=1, stroke=0)
    text_x, text_y = info_x + 4.5 * mm, info_y + info_h - 4.1 * mm
    pdf.setFillColor(ink)
    for label, value in rows:
        pdf.setFont("Helvetica-Bold", 7.1)
        pdf.drawString(text_x, text_y, f"{label} :")
        pdf.setFont("Helvetica", 7.1)
        pdf.drawString(text_x + 25 * mm, text_y, str(value or "-")[:33])
        text_y -= row_h

    # A restrained footer wave anchors the signature without adding extra data.
    pdf.setFillColor(sky)
    footer = pdf.beginPath()
    footer.moveTo(inner_x, y + 1.5 * mm)
    footer.lineTo(inner_x, y + 14 * mm)
    footer.curveTo(x + 26 * mm, y + 23 * mm, x + 51 * mm, y + 4 * mm, x + CARD_WIDTH - 1.5 * mm, y + 15 * mm)
    footer.lineTo(x + CARD_WIDTH - 1.5 * mm, y + 1.5 * mm)
    footer.close()
    pdf.drawPath(footer, fill=1, stroke=0)

    signature_w, signature_h = 25 * mm, 7.5 * mm
    signature_x, signature_y = x + CARD_WIDTH - signature_w - 6.5 * mm, y + 9.2 * mm
    signature_path = _file_path(school.headmaster_signature)
    if signature_path:
        _draw_image_or_placeholder(pdf, signature_path, signature_x, signature_y, signature_w, signature_h, "", frame=False)
    pdf.setFont("Helvetica-Bold", 7)
    pdf.setFillColor(navy)
    pdf.drawCentredString(signature_x + signature_w / 2, y + 5.2 * mm, "HEADMASTER")


def _pdf_response(students, school, filename: str):
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
    for index, student in enumerate(students):
        slot = index % 4
        if slot == 0 and index:
            pdf.showPage()
        column, row = slot % 2, slot // 2
        x = PAGE_MARGIN_X + column * CARD_WIDTH
        y = A4[1] - PAGE_MARGIN_Y - CARD_HEIGHT if row == 0 else PAGE_MARGIN_Y
        _draw_card(pdf, student, school, x, y)
    if not students:
        pdf.setFont("Helvetica", 12)
        pdf.drawCentredString(A4[0] / 2, A4[1] / 2, "No active students found for this selection.")
    pdf.save()
    stream.seek(0)
    return StreamingResponse(stream, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/bulk.pdf")
def bulk_cards(class_name: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    students = _student_query(db, user.school_id, class_name, None).all()
    school = db.get(models.School, user.school_id)
    suffix = f"-class-{class_name}" if class_name else ""
    return _pdf_response(students, school, f"id-cards{suffix}.pdf")


@router.get("/{student_id}.pdf")
def individual_card(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    student = _get_student(db, student_id, user.school_id)
    school = db.get(models.School, user.school_id)
    return _pdf_response([student], school, f"id-card-{student.id}.pdf")
