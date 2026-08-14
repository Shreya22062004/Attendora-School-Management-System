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
from PIL import Image, ImageChops
from reportlab.pdfgen import canvas
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_school_user
from ..database import get_db

router = APIRouter(prefix="/idcards", tags=["ID Cards"])

# Final school ID-card format: 85 mm wide x 110 mm high (portrait).
CARD_WIDTH = 85 * mm
CARD_HEIGHT = 110 * mm

# A4 portrait print sheet: 2 columns x 2 rows. A small, consistent cutting gap
# is maintained between cards and around the outside edges.
CARDS_PER_ROW = 2
CARDS_PER_COLUMN = 2
CARDS_PER_SHEET = CARDS_PER_ROW * CARDS_PER_COLUMN
CUT_GAP = 4 * mm
PAGE_MARGIN_X = (A4[0] - (CARDS_PER_ROW * CARD_WIDTH) - ((CARDS_PER_ROW - 1) * CUT_GAP)) / 2
PAGE_MARGIN_Y = (A4[1] - (CARDS_PER_COLUMN * CARD_HEIGHT) - ((CARDS_PER_COLUMN - 1) * CUT_GAP)) / 2

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
    previous = school.headmaster_signature
    school.headmaster_signature = await _save_image(file, user.school_id, "signature")
    db.add(models.AuditLog(
        school_id=user.school_id,
        user_id=user.id,
        action="UPLOAD_HEADMASTER_SIGNATURE",
        entity_type="School",
        entity_id=str(school.id),
    ))
    db.commit()
    _remove_file(previous)
    return {"message": "Headmaster signature saved"}


def _trim_image_bytes(path: Path) -> bytes:
    """Return a tightly cropped PNG for signatures, removing surrounding white margins."""
    image = Image.open(path).convert("RGBA")
    rgb = image.convert("RGB")
    # Treat near-white pixels as background so the actual signature/stamp remains.
    mask = rgb.point(lambda p: 0 if p >= 245 else 255)
    bbox = mask.getbbox()
    if bbox:
        pad = max(4, int(min(image.size) * 0.025))
        bbox = (
            max(0, bbox[0] - pad),
            max(0, bbox[1] - pad),
            min(image.width, bbox[2] + pad),
            min(image.height, bbox[3] + pad),
        )
        image = image.crop(bbox)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


@router.get("/settings/signature")
def get_signature(user=Depends(require_school_user), db: Session = Depends(get_db)):
    school = db.get(models.School, user.school_id)
    path = _file_path(school.headmaster_signature if school else None)
    if not path:
        raise HTTPException(404, "Headmaster signature not uploaded")
    try:
        # Serve the same tightly cropped signature used by the PDF renderer so the
        # browser preview does not show the large blank margins from the upload.
        return Response(_trim_image_bytes(path), media_type="image/png")
    except Exception:
        return Response(path.read_bytes(), media_type="image/png" if path.suffix == ".png" else "image/jpeg")


@router.get("/students")
def list_students(class_name: str | None = None, search: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    students = _student_query(db, user.school_id, class_name, search).all()
    uploaded = sum(bool(_file_path(student.photo)) for student in students)
    return {
        "students": [{
            "id": student.id,
            "name": student.name,
            "class_name": student.class_name,
            "father_name": student.father_name,
            "mother_name": student.mother_name,
            "pen_number": student.pen_number,
            "date_of_birth": student.date_of_birth.isoformat() if student.date_of_birth else None,
            "photo_uploaded": bool(_file_path(student.photo)),
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
    previous = student.photo
    student.photo = await _save_image(file, user.school_id, "students")
    db.add(models.AuditLog(
        school_id=user.school_id,
        user_id=user.id,
        action="UPLOAD_STUDENT_ID_PHOTO",
        entity_type="Student",
        entity_id=str(student.id),
    ))
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
    return Response(
        path.read_bytes(),
        media_type="image/png" if path.suffix == ".png" else "image/jpeg",
    )


def _draw_image_or_placeholder(
    pdf,
    path: Path | None,
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

    if path:
        try:
            # Signature uploads often contain large white margins. Trim those margins
            # before placing the signature so the actual ink remains clearly visible.
            if not frame and not label:
                pil_image = Image.open(path).convert("RGBA")
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
                image = ImageReader(str(path))
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
        pdf.setFont("Helvetica-Bold", 6.8)
        pdf.drawCentredString(x + width / 2, y + height / 2 - 2, label)


def _draw_card(pdf, student, school, x: float, y: float):
    """Draw an 85 x 110 mm portrait school ID card."""
    navy = colors.HexColor("#2d6f9f")
    blue = colors.HexColor("#4b93c4")
    light_blue = colors.HexColor("#e8f4fb")
    pale_blue = colors.HexColor("#f5faff")
    line_blue = colors.HexColor("#6ba7cf")
    ink = colors.HexColor("#28445d")

    # Card shell.
    pdf.setFillColor(colors.white)
    pdf.setStrokeColor(colors.HexColor("#2b6090"))
    pdf.setLineWidth(0.9)
    pdf.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, 2.3 * mm, fill=1, stroke=1)
    pdf.setStrokeColor(colors.HexColor("#8db9d8"))
    pdf.setLineWidth(0.35)
    pdf.roundRect(x + 1.3 * mm, y + 1.3 * mm, CARD_WIDTH - 2.6 * mm, CARD_HEIGHT - 2.6 * mm, 1.5 * mm, fill=0, stroke=1)

    left = x + 2.5 * mm
    right = x + CARD_WIDTH - 2.5 * mm
    center = x + CARD_WIDTH / 2

    # Header.
    header_h = 27 * mm
    header_y = y + CARD_HEIGHT - header_h
    pdf.setFillColor(navy)
    pdf.roundRect(left, header_y, right - left, header_h, 1.7 * mm, fill=1, stroke=0)

    school_name = (school.school_name or "SCHOOL NAME").upper()
    words = school_name.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdf.stringWidth(candidate, "Helvetica-Bold", 12.0) <= 76 * mm:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = lines[:2]

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 12.0)
    title_y = y + CARD_HEIGHT - 6.2 * mm
    for line in lines:
        pdf.drawCentredString(center, title_y, line)
        title_y -= 5.0 * mm

    if school.established_year:
        pdf.setFont("Helvetica-Bold", 8.0)
        pdf.drawCentredString(center, title_y, f"ESTD - {school.established_year}")
        title_y -= 4.3 * mm

    if school.address:
        address = str(school.address).upper()
        address_words = address.split()
        address_lines = []
        current = ""
        for word in address_words:
            candidate = f"{current} {word}".strip()
            if pdf.stringWidth(candidate, "Helvetica-Bold", 7.25) <= 76 * mm:
                current = candidate
            else:
                if current:
                    address_lines.append(current)
                current = word
        if current:
            address_lines.append(current)
        pdf.setFont("Helvetica-Bold", 7.15)
        for line in address_lines[:2]:
            pdf.drawCentredString(center, title_y, line)
            title_y -= 3.35 * mm

    # UDISE pill.
    udise_text = f"UDISE CODE - {school.udise_code}" if school.udise_code else "UDISE CODE"
    pill_w, pill_h = 45 * mm, 5.4 * mm
    pill_x = center - pill_w / 2
    pill_y = header_y + 1.1 * mm
    pdf.setFillColor(colors.white)
    pdf.roundRect(pill_x, pill_y, pill_w, pill_h, 2.2 * mm, fill=1, stroke=0)
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 6.8)
    pdf.drawCentredString(center, pill_y + 1.78 * mm, udise_text)

    # Soft wave separating header from body.
    wave_y = header_y
    pdf.setFillColor(light_blue)
    wave = pdf.beginPath()
    wave.moveTo(left, wave_y + 1 * mm)
    wave.curveTo(x + 24 * mm, wave_y - 3.8 * mm, x + 55 * mm, wave_y + 3.5 * mm, right, wave_y - 1 * mm)
    wave.lineTo(right, wave_y - 5.2 * mm)
    wave.curveTo(x + 55 * mm, wave_y - 0.8 * mm, x + 25 * mm, wave_y - 6.2 * mm, left, wave_y - 2 * mm)
    wave.close()
    pdf.drawPath(wave, fill=1, stroke=0)
    pdf.setFillColor(blue)
    accent = pdf.beginPath()
    accent.moveTo(left, wave_y + 0.3 * mm)
    accent.curveTo(x + 24 * mm, wave_y - 2.2 * mm, x + 50 * mm, wave_y + 2.7 * mm, right, wave_y - 0.9 * mm)
    accent.lineTo(right, wave_y - 2.5 * mm)
    accent.curveTo(x + 48 * mm, wave_y + 1.1 * mm, x + 23 * mm, wave_y - 3.8 * mm, left, wave_y - 1.2 * mm)
    accent.close()
    pdf.drawPath(accent, fill=1, stroke=0)

    # Portrait body: large photo, student name, then details.
    photo_w, photo_h = 52 * mm, 52 * mm
    photo_x = center - photo_w / 2
    photo_y = y + 45.0 * mm
    pdf.setFillColor(colors.white)
    pdf.setStrokeColor(line_blue)
    pdf.setLineWidth(0.8)
    pdf.roundRect(photo_x - 1.0 * mm, photo_y - 1.0 * mm, photo_w + 2 * mm, photo_h + 2 * mm, 1.8 * mm, fill=1, stroke=1)
    _draw_image_or_placeholder(
        pdf, _file_path(student.photo), photo_x, photo_y, photo_w, photo_h,
        "PHOTO PENDING", cover=True, frame=True,
    )

    name_text = str(student.name or "").upper()
    name_y = y + 40.7 * mm
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 11.4 if len(name_text) <= 22 else 9.8)
    pdf.drawCentredString(center, name_y, name_text[:30])

    # Student details below the name.
    detail_left = x + 7.0 * mm
    detail_right = x + 51.0 * mm
    detail_value_x = x + 29.0 * mm
    row_y = y + 35.0 * mm
    row_h = 5.8 * mm
    rows = [("FATHER NAME", student.father_name), ("MOTHER NAME", student.mother_name)]
    if student.pen_number:
        rows.append(("PEN NUMBER", student.pen_number))
    if student.date_of_birth:
        rows.append(("DATE OF BIRTH", student.date_of_birth.strftime("%d/%m/%Y")))

    box_h = max(24.0, len(rows) * row_h + 3.5) * mm
    box_y = y + 12.0 * mm
    pdf.setFillColor(pale_blue)
    pdf.roundRect(detail_left - 2.0 * mm, box_y, detail_right - detail_left + 4.0 * mm, box_h, 1.8 * mm, fill=1, stroke=0)
    pdf.setStrokeColor(line_blue)
    pdf.setLineWidth(0.8)
    pdf.line(detail_left - 0.8 * mm, box_y + 1.0 * mm, detail_left - 0.8 * mm, box_y + box_h - 1.0 * mm)

    for label, value in rows:
        pdf.setFillColor(navy)
        pdf.setFont("Helvetica-Bold", 6.8)
        pdf.drawString(detail_left, row_y, f"{label}:")
        pdf.setFillColor(ink)
        pdf.setFont("Helvetica", 7.0)
        value_text = str(value or "-")
        while len(value_text) > 24 and pdf.stringWidth(value_text, "Helvetica", 7.0) > 38 * mm:
            value_text = value_text[:-1]
        pdf.drawString(detail_value_x, row_y, value_text)
        row_y -= row_h

    # Clean signature, no surrounding box.
    signature_path = _file_path(school.headmaster_signature)
    signature_w, signature_h = 38 * mm, 16.0 * mm
    signature_x = x + CARD_WIDTH - signature_w - 1.0 * mm
    signature_y = y + 3.8 * mm
    if signature_path:
        _draw_image_or_placeholder(
            pdf, signature_path, signature_x, signature_y + 1.8 * mm,
            signature_w, signature_h, "", cover=False, frame=False,
        )
    pdf.setStrokeColor(colors.HexColor("#5f93b7"))
    pdf.setLineWidth(0.45)
    pdf.line(signature_x, signature_y + 1.0 * mm, signature_x + signature_w, signature_y + 1.0 * mm)
    pdf.setFillColor(navy)
    pdf.setFont("Helvetica-Bold", 6.8)
    pdf.drawCentredString(signature_x + signature_w / 2, y + 2.0 * mm, "HEADMASTER")

def _pdf_response(students, school, filename: str):
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
    pdf.setTitle("Attendora Student ID Cards - 85x110 mm Portrait")

    for index, student in enumerate(students):
        slot = index % CARDS_PER_SHEET
        if slot == 0 and index:
            pdf.showPage()

        column = slot % CARDS_PER_ROW
        row = slot // CARDS_PER_ROW
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
