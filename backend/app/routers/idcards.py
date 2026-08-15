"""School-scoped student ID card management and print-ready PDF generation."""
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageChops, ImageOps
from reportlab.pdfgen import canvas
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_school_user
from ..database import get_db

router = APIRouter(prefix="/idcards", tags=["ID Cards"])

# A3-size plastic ID-card format from the supplied reference:
# 92 mm wide x 115 mm high (portrait).
CARD_WIDTH = 92 * mm
CARD_HEIGHT = 115 * mm

# A4 print sheet: 2 columns x 2 rows, with a small cutting gap on all sides.
CUT_GAP = 5 * mm
PAGE_MARGIN_X = (A4[0] - (2 * CARD_WIDTH) - CUT_GAP) / 2
PAGE_MARGIN_Y = (A4[1] - (2 * CARD_HEIGHT) - CUT_GAP) / 2

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


async def _read_image(upload: UploadFile) -> tuple[bytes, str]:
    data = await upload.read(MAX_IMAGE_BYTES + 1)
    image_info = _image_type(data)
    if not data or len(data) > MAX_IMAGE_BYTES or not image_info:
        raise HTTPException(400, "Upload a valid JPG, JPEG, or PNG image no larger than 5 MB")
    return data, image_info[1]


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


def _legacy_image(path: str | None) -> tuple[bytes, str] | None:
    """Read legacy filesystem media without making it the source of truth."""
    legacy_path = _file_path(path)
    if not legacy_path:
        return None
    data = legacy_path.read_bytes()
    image_info = _image_type(data)
    return (data, image_info[1]) if image_info else None


def _student_photo(student) -> tuple[bytes, str] | None:
    if student.photo_data:
        return student.photo_data, (student.photo_mime_type or "image/png")
    return _legacy_image(student.photo)


def _school_signature(school) -> tuple[bytes, str] | None:
    if school and school.headmaster_signature_data:
        return school.headmaster_signature_data, (school.headmaster_signature_mime_type or "image/png")
    return _legacy_image(school.headmaster_signature if school else None)


def _migrate_legacy_student_photo(student) -> bool:
    if student.photo_data:
        return False
    legacy = _legacy_image(student.photo)
    if not legacy:
        return False
    student.photo_data, student.photo_mime_type = legacy
    return True


def _migrate_legacy_signature(school) -> bool:
    if not school or school.headmaster_signature_data:
        return False
    legacy = _legacy_image(school.headmaster_signature)
    if not legacy:
        return False
    school.headmaster_signature_data, school.headmaster_signature_mime_type = legacy
    return True


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
    if _migrate_legacy_signature(school):
        db.commit()
    return {
        "school_name": school.school_name,
        "established_year": school.established_year or "",
        "address": school.address or "",
        "udise_code": school.udise_code or "",
        "has_headmaster_signature": bool(_school_signature(school)),
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
    if _migrate_legacy_signature(school):
        db.commit()
    school.established_year = year or None
    db.add(models.AuditLog(
        school_id=user.school_id,
        user_id=user.id,
        action="UPDATE_ID_CARD_SETTINGS",
        entity_type="School",
        entity_id=str(school.id),
    ))
    db.commit()
    return {"message": "ID card settings saved", "established_year": school.established_year or ""}


@router.post("/settings/signature")
async def upload_signature(file: UploadFile = File(...), user=Depends(require_school_user), db: Session = Depends(get_db)):
    _require_school_admin(user)
    school = db.get(models.School, user.school_id)
    if not school:
        raise HTTPException(404, "School not found")
    data, mime_type = await _read_image(file)
    school.headmaster_signature_data = data
    school.headmaster_signature_mime_type = mime_type
    db.add(models.AuditLog(
        school_id=user.school_id,
        user_id=user.id,
        action="UPLOAD_HEADMASTER_SIGNATURE",
        entity_type="School",
        entity_id=str(school.id),
    ))
    db.commit()
    return {"message": "Headmaster signature saved"}


@router.get("/settings/signature")
def get_signature(user=Depends(require_school_user), db: Session = Depends(get_db)):
    school = db.get(models.School, user.school_id)
    if _migrate_legacy_signature(school):
        db.commit()
    signature = _school_signature(school)
    if not signature:
        raise HTTPException(404, "Headmaster signature not uploaded")
    data, mime_type = signature
    return Response(data, media_type=mime_type)


@router.get("/students")
def list_students(class_name: str | None = None, search: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    students = _student_query(db, user.school_id, class_name, search).all()
    if any([_migrate_legacy_student_photo(student) for student in students]):
        db.commit()
    uploaded = sum(bool(_student_photo(student)) for student in students)
    return {
        "students": [{
            "id": student.id,
            "name": student.name,
            "class_name": student.class_name,
            "father_name": student.father_name,
            "mother_name": student.mother_name,
            "contact_number": student.contact_number,
            "blood_group": student.blood_group,
            "pen_number": student.pen_number,
            "date_of_birth": student.date_of_birth.isoformat() if student.date_of_birth else None,
            "photo_uploaded": bool(_student_photo(student)),
        } for student in students],
        "summary": {
            "students": len(students),
            "photos_uploaded": uploaded,
            "photos_pending": len(students) - uploaded,
        },
    }


def _get_student(db: Session, student_id: int, school_id: int):
    student = db.query(models.Student).filter(
        models.Student.id == student_id,
        models.Student.school_id == school_id,
    ).first()
    if not student:
        raise HTTPException(404, "Student not found")
    return student


@router.post("/students/{student_id}/photo")
async def upload_photo(student_id: int, file: UploadFile = File(...), user=Depends(require_school_user), db: Session = Depends(get_db)):
    _require_school_admin(user)
    student = _get_student(db, student_id, user.school_id)
    data, mime_type = await _read_image(file)
    student.photo_data = data
    student.photo_mime_type = mime_type
    db.add(models.AuditLog(
        school_id=user.school_id,
        user_id=user.id,
        action="UPLOAD_STUDENT_ID_PHOTO",
        entity_type="Student",
        entity_id=str(student.id),
    ))
    db.commit()
    return {"message": "Student photo saved"}


@router.delete("/students/{student_id}/photo")
def delete_photo(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _require_school_admin(user)
    student = _get_student(db, student_id, user.school_id)
    previous = student.photo
    student.photo = None
    student.photo_data = None
    student.photo_mime_type = None
    db.commit()
    _remove_file(previous)
    return {"message": "Student photo removed"}


@router.get("/students/{student_id}/photo")
def get_photo(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    student = _get_student(db, student_id, user.school_id)
    if _migrate_legacy_student_photo(student):
        db.commit()
    photo = _student_photo(student)
    if not photo:
        raise HTTPException(404, "Student photo not uploaded")
    data, mime_type = photo
    return Response(data, media_type=mime_type)


def _draw_image_or_placeholder(
    pdf,
    image_source: bytes | Path | None,
    x: float,
    y: float,
    width: float,
    height: float,
    label: str,
    cover: bool = False,
    frame: bool = True,
):
    if frame:
        pdf.setStrokeColor(colors.HexColor("#7d9bc0"))
        pdf.setFillColor(colors.HexColor("#f7faff"))
        pdf.roundRect(x, y, width, height, 1.2 * mm, fill=1, stroke=1)

    if image_source:
        try:
            # Signature uploads often contain large white margins. Trim those margins
            # before placing the signature so the actual ink remains clearly visible.
            if not frame and not label:
                pil_image = Image.open(BytesIO(image_source) if isinstance(image_source, bytes) else image_source).convert("RGBA")
                white = Image.new("RGBA", pil_image.size, (255, 255, 255, 255))
                diff = ImageChops.difference(pil_image, white)
                bbox = diff.convert("RGB").point(lambda p: 0 if p < 18 else 255).getbbox()
                if bbox:
                    pad = max(3, int(min(pil_image.size) * 0.03))
                    bbox = (
                        max(0, bbox[0] - pad),
                        max(0, bbox[1] - pad),
                        min(pil_image.width, bbox[2] + pad),
                        min(pil_image.height, bbox[3] + pad),
                    )
                    pil_image = pil_image.crop(bbox)
                image = ImageReader(pil_image)
            else:
                # Camera JPEGs may carry EXIF orientation rather than rotated pixel
                # data. Normalize only the in-memory PDF image, leaving the stored
                # original untouched for the browser preview and future downloads.
                pil_image = ImageOps.exif_transpose(
                    Image.open(BytesIO(image_source) if isinstance(image_source, bytes) else image_source)
                )
                image = ImageReader(pil_image)
            image_width, image_height = image.getSize()
            scale = (max if cover else min)(width / image_width, height / image_height)
            draw_width, draw_height = image_width * scale, image_height * scale
            pdf.saveState()
            if cover:
                clip = pdf.beginPath()
                clip.roundRect(x, y, width, height, 1.2 * mm)
                pdf.clipPath(clip, stroke=0, fill=0)
            pdf.drawImage(
                image,
                x + (width - draw_width) / 2,
                y + (height - draw_height) / 2,
                draw_width,
                draw_height,
                mask="auto",
            )
            pdf.restoreState()
            if frame:
                pdf.setStrokeColor(colors.HexColor("#537ba8"))
                pdf.roundRect(x, y, width, height, 1.2 * mm, fill=0, stroke=1)
            return
        except Exception:
            pass

    if frame:
        pdf.setFillColor(colors.HexColor("#cbd5e1"))
        pdf.setFont("Helvetica-Bold", 6.2)
        pdf.drawCentredString(x + width / 2, y + height / 2 - 2, label)


def _draw_card(pdf, student, school, x: float, y: float):
    # A3 portrait reference-style design: compact school header, separated identity title,
    # large photo, prominent student name, clean detail rows, and signature above HEADMASTER.
    navy = colors.HexColor("#174f82")
    blue = colors.HexColor("#3a82b8")
    light_blue = colors.HexColor("#eaf4fb")
    line_blue = colors.HexColor("#5b9dcc")
    ink = colors.HexColor("#24415e")
    muted = colors.HexColor("#60758a")

    # Outer card and inner print-safe border.
    pdf.setFillColor(colors.white)
    pdf.setStrokeColor(navy)
    pdf.setLineWidth(1.0)
    pdf.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, 2.2 * mm, fill=1, stroke=1)
    pdf.setStrokeColor(colors.HexColor("#6b8db6"))
    pdf.setLineWidth(0.45)
    pdf.roundRect(x + 1.4 * mm, y + 1.4 * mm, CARD_WIDTH - 2.8 * mm, CARD_HEIGHT - 2.8 * mm, 1.3 * mm, fill=0, stroke=1)

    left = x + 2.2 * mm
    right = x + CARD_WIDTH - 2.2 * mm
    center = x + CARD_WIDTH / 2

    # Header: strong navy block like the user's sample, but cleaner and more compact.
    header_h = 25.5 * mm
    header_y = y + CARD_HEIGHT - header_h
    pdf.setFillColor(navy)
    pdf.roundRect(left, header_y, right - left, header_h, 1.5 * mm, fill=1, stroke=0)

    school_name = (school.school_name or "SCHOOL NAME").upper()
    # Wrap long school names to two lines without overflowing the card.
    words = school_name.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdf.stringWidth(candidate, "Helvetica-Bold", 8.6) <= 82 * mm:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = lines[:2]

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 8.6)
    name_y = y + CARD_HEIGHT - 7.0 * mm
    for line in lines:
        pdf.drawCentredString(center, name_y, line)
        name_y -= 4.2 * mm

    if school.established_year:
        pdf.setFont("Helvetica-Bold", 6.4)
        pdf.drawCentredString(center, name_y, f"ESTD - {school.established_year}")
        name_y -= 4.0 * mm

    if school.address:
        address = str(school.address).upper()
        # Keep the reference-style address compact; split once if needed.
        if len(address) > 48:
            split = address.rfind(" ", 0, 48)
            split = split if split > 20 else 48
            address_lines = [address[:split], address[split:].strip()]
        else:
            address_lines = [address]
        pdf.setFont("Helvetica-Bold", 4.65)
        for line in address_lines[:2]:
            pdf.drawCentredString(center, name_y, line[:62])
            name_y -= 3.0 * mm

    # UDISE pill.
    pill_w, pill_h = 47 * mm, 5.2 * mm
    pill_x = center - pill_w / 2
    pill_y = header_y + 0.9 * mm
    pdf.setFillColor(colors.white)
    pdf.roundRect(pill_x, pill_y, pill_w, pill_h, 2.4 * mm, fill=1, stroke=0)
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 5.6)
    udise_text = f"UDISE CODE - {school.udise_code}" if school.udise_code else "UDISE CODE"
    pdf.drawCentredString(center, pill_y + 1.75 * mm, udise_text)

    # Curved light-blue/blue transition under the header.
    wave_y = header_y
    pdf.setFillColor(light_blue)
    wave = pdf.beginPath()
    wave.moveTo(left, wave_y + 1 * mm)
    wave.curveTo(x + 25 * mm, wave_y - 5 * mm, x + 57 * mm, wave_y + 5 * mm, right, wave_y - 1 * mm)
    wave.lineTo(right, wave_y - 6.5 * mm)
    wave.curveTo(x + 59 * mm, wave_y - 1 * mm, x + 27 * mm, wave_y - 9 * mm, left, wave_y - 3 * mm)
    wave.close()
    pdf.drawPath(wave, fill=1, stroke=0)
    pdf.setFillColor(blue)
    accent = pdf.beginPath()
    accent.moveTo(left, wave_y + 0.5 * mm)
    accent.curveTo(x + 22 * mm, wave_y - 2.5 * mm, x + 48 * mm, wave_y + 3.5 * mm, x + 64 * mm, wave_y - 1.5 * mm)
    accent.lineTo(x + 64 * mm, wave_y - 3.2 * mm)
    accent.curveTo(x + 44 * mm, wave_y + 1.5 * mm, x + 21 * mm, wave_y - 4.5 * mm, left, wave_y - 1.5 * mm)
    accent.close()
    pdf.drawPath(accent, fill=1, stroke=0)

    # Large photo, deliberately integrated into the upper-middle area.
    photo_w, photo_h = 50 * mm, 50 * mm
    photo_x = center - photo_w / 2
    # Keep the photo bottom higher so the extra height grows upward, leaving a
    # deliberate gap between the photo and the student name.
    photo_y = y + CARD_HEIGHT - 76.0 * mm
    pdf.setFillColor(colors.white)
    pdf.setStrokeColor(line_blue)
    pdf.setLineWidth(0.8)
    pdf.roundRect(photo_x - 1.2 * mm, photo_y - 1.2 * mm, photo_w + 2.4 * mm, photo_h + 2.4 * mm, 1.8 * mm, fill=1, stroke=1)
    photo = _student_photo(student)
    _draw_image_or_placeholder(
        pdf,
        photo[0] if photo else None,
        photo_x,
        photo_y,
        photo_w,
        photo_h,
        "PHOTO PENDING",
        # Match the shared browser card: preserve the complete uploaded portrait
        # inside the fixed frame instead of cropping it to fill the box.
        cover=False,
        frame=True,
    )

    # Student name: prominent, centered, like a finished plastic ID card.
    name_text = str(student.name or "").upper()
    name_y = photo_y - 5.5 * mm
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 8.4)
    if len(name_text) > 27:
        pdf.setFont("Helvetica-Bold", 7.4)
    pdf.drawCentredString(center, name_y, name_text[:34])

    # Details use clean label/value rows with no horizontal rules.
    rows = [
        ("FATHER NAME", student.father_name),
        ("MOTHER NAME", student.mother_name),
    ]
    if student.pen_number:
        rows.append(("PEN NUMBER", student.pen_number))
    if student.date_of_birth:
        rows.append(("DATE OF BIRTH", student.date_of_birth.strftime("%d/%m/%Y")))
    if student.contact_number:
        rows.append(("CONTACT NO", student.contact_number))
    if student.blood_group:
        rows.append(("BLOOD GROUP", student.blood_group))

    detail_left = x + 10 * mm
    detail_value_x = x + 43 * mm
    row_y = name_y - 6.0 * mm
    row_h = 4.35 * mm

    for label, value in rows:
        pdf.setFillColor(navy)
        pdf.setFont("Helvetica-Bold", 5.45)
        pdf.drawString(detail_left, row_y, f"{label} :")
        pdf.setFillColor(ink)
        pdf.setFont("Helvetica", 5.65)
        value_text = str(value or "-")
        # Clip long values by shortening them, preserving the row geometry.
        while len(value_text) > 28 and pdf.stringWidth(value_text, "Helvetica", 5.65) > 39 * mm:
            value_text = value_text[:-1]
        pdf.drawString(detail_value_x, row_y, value_text)
        row_y -= row_h

    # Compact signature block at bottom-right. Keep it clearly separated from the
    # student details so the signature is visible without becoming oversized.
    panel_w, panel_h = 28 * mm, 12.2 * mm
    panel_x = right - panel_w - 1.5 * mm
    panel_y = y + 2.8 * mm
    pdf.setFillColor(colors.HexColor("#f4f9fd"))
    pdf.setStrokeColor(colors.HexColor("#c6ddec"))
    pdf.setLineWidth(0.45)
    pdf.roundRect(panel_x, panel_y, panel_w, panel_h, 1.4 * mm, fill=1, stroke=1)

    signature = _school_signature(school)
    signature_w, signature_h = 22 * mm, 5.8 * mm
    signature_x = panel_x + (panel_w - signature_w) / 2
    signature_y = panel_y + 4.6 * mm
    if signature:
        _draw_image_or_placeholder(
            pdf,
            signature[0],
            signature_x,
            signature_y,
            signature_w,
            signature_h,
            "",
            cover=False,
            frame=False,
        )

    pdf.setStrokeColor(colors.HexColor("#6fa6cb"))
    pdf.setLineWidth(0.45)
    pdf.line(panel_x + 3.0 * mm, panel_y + 3.6 * mm, panel_x + panel_w - 3.0 * mm, panel_y + 3.6 * mm)
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 5.4)
    pdf.drawCentredString(panel_x + panel_w / 2, panel_y + 1.25 * mm, "HEADMASTER")


def _pdf_response(students, school, filename: str):
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4, pageCompression=1)

    for index, student in enumerate(students):
        slot = index % 4
        if slot == 0 and index:
            pdf.showPage()

        column, row = slot % 2, slot // 2
        x = PAGE_MARGIN_X + column * (CARD_WIDTH + CUT_GAP)
        y = A4[1] - PAGE_MARGIN_Y - CARD_HEIGHT - row * (CARD_HEIGHT + CUT_GAP)
        _draw_card(pdf, student, school, x, y)

    if not students:
        pdf.setFont("Helvetica", 12)
        pdf.drawCentredString(A4[0] / 2, A4[1] / 2, "No active students found for this selection.")

    pdf.save()
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
