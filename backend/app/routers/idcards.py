"""School-scoped 54 x 85 mm portrait ID cards for students and personnel."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from PIL import Image, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, defer

from .. import models
from ..auth import require_school_user
from ..database import get_db
from ..media_storage import MediaStorageError, delete as delete_media, get_bytes as get_media, optimize_student_photo, put_bytes as put_media

router = APIRouter(prefix="/idcards", tags=["ID Cards"])
CARD_WIDTH = 54 * mm
CARD_HEIGHT = 85 * mm
CUT_GAP = 4 * mm
COLS, ROWS = 3, 3
PAGE_MARGIN_X = (A4[0] - COLS * CARD_WIDTH - (COLS - 1) * CUT_GAP) / 2
PAGE_MARGIN_Y = (A4[1] - ROWS * CARD_HEIGHT - (ROWS - 1) * CUT_GAP) / 2
UPLOAD_DIR = Path(__file__).resolve().parents[2] / "uploads" / "idcards"
LOGO_PATH = Path(__file__).resolve().parents[1] / "odisha-govt-logo.png"
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


def _student_photo(student):
    # Local DB bytes are the fast/reliable primary copy. Cloudinary remains a
    # fallback for older records that were uploaded before this optimization.
    if student.photo_data:
        return student.photo_data, student.photo_mime_type or "image/jpeg"
    if student.photo_storage_key or student.photo_storage_url:
        try: return get_media(student.photo_storage_key, student.photo_storage_url)
        except MediaStorageError: pass
    return _legacy_image(student.photo)


def _staff_photo(staff):
    if not staff.photo_storage_key: return None
    try: return get_media(staff.photo_storage_key, staff.photo_storage_url)
    except MediaStorageError: return None


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
        public_id, secure_url = put_media(
            f"attendora/students/{item.id}", data, mime
        )
    except MediaStorageError:
        # Keep the DB copy even if Cloudinary is temporarily unavailable.
        public_id = None
        secure_url = ""

    item.photo_data = data
    item.photo_mime_type = mime
    item.photo_storage_key = public_id
    item.photo_storage_url = secure_url

    try:
        db.commit()
    except Exception as error:
        db.rollback()
        if public_id and not previous:
            try: delete_media(public_id)
            except MediaStorageError: pass
        raise HTTPException(500, "Could not save photo") from error

    return {"message": "Student photo saved", "photo_url": secure_url}


@router.delete("/students/{student_id}/photo")
def delete_student_photo(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    _admin(user); item = _student(db, student_id, user.school_id, include_photo=True); key = item.photo_storage_key
    item.photo_storage_key = None; item.photo_storage_url = None; item.photo_data = None; item.photo_mime_type = None; item.photo = None; db.commit()
    if key:
        try: delete_media(key)
        except MediaStorageError: pass
    return {"message": "Student photo removed"}


@router.get("/students/{student_id}/photo")
def get_student_photo(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    photo = _student_photo(_student(db, student_id, user.school_id, include_photo=True))
    if not photo: raise HTTPException(404, "Student photo not uploaded")
    return Response(photo[0], media_type=photo[1], headers={"Cache-Control": "private, max-age=86400"})


def _draw_image(pdf, source, x, y, width, height, placeholder="", signature=False):
    if not source:
        pdf.setFillColor(colors.HexColor("#fff7ee")); pdf.rect(x, y, width, height, fill=1, stroke=0); return
    try:
        image = ImageOps.exif_transpose(Image.open(BytesIO(source) if isinstance(source, bytes) else source)).convert("RGBA")
        if signature:
            # Also clean older signatures that were uploaded before the
            # transparent-background fix.
            png_bytes, _ = _remove_white_background_bytes(
                source if isinstance(source, bytes) else Path(source).read_bytes()
            )
            image = Image.open(BytesIO(png_bytes)).convert("RGBA")
            bbox = image.getbbox()
            if bbox:
                image = image.crop(bbox)
        iw, ih = image.size; scale = min(width / iw, height / ih); dw, dh = iw * scale, ih * scale
        pdf.drawImage(ImageReader(image), x + (width - dw) / 2, y + (height - dh) / 2, dw, dh, mask="auto")
    except Exception:
        _draw_image(pdf, None, x, y, width, height, placeholder, signature)


def _drop(pdf, x, y, value):
    path = pdf.beginPath(); path.moveTo(x, y + 7 * mm); path.curveTo(x - 4.5 * mm, y + 1.5 * mm, x - 4.2 * mm, y - 3 * mm, x, y - 4.5 * mm); path.curveTo(x + 4.2 * mm, y - 3 * mm, x + 4.5 * mm, y + 1.5 * mm, x, y + 7 * mm); path.close()
    pdf.setFillColor(colors.HexColor("#c6292f")); pdf.drawPath(path, fill=1, stroke=0)
    if value:
        pdf.setFillColor(colors.white); pdf.setFont("Helvetica-Bold", 6.6); pdf.drawCentredString(x, y, str(value)[:4])


def _line(pdf, x, y, label, value, width=18 * mm):
    pdf.setFillColor(colors.HexColor("#7b3e11"))
    pdf.setFont("Helvetica-Bold", 5.55)
    pdf.drawString(x, y, label)
    pdf.setFillColor(colors.HexColor("#252525"))
    pdf.setFont("Helvetica-Bold", 5.65)
    text = str(value or "")
    while text and pdf.stringWidth(text, "Helvetica-Bold", 5.65) > width:
        text = text[:-1]
    pdf.drawString(x + 11 * mm, y, text)


def _wrap_words(text, font_name, font_size, max_width):
    """Wrap text by words so long school/address strings stay inside the header."""
    words = str(text or "").split()
    if not words:
        return []
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or pdf_string_width(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def pdf_string_width(text, font_name, font_size):
    return stringWidth(text, font_name, font_size)


def _draw_centered_lines(pdf, lines, center_x, first_y, font_name, font_size, leading):
    pdf.setFont(font_name, font_size)
    for index, line in enumerate(lines):
        pdf.drawCentredString(center_x, first_y - index * leading, line)


def _draw_card(pdf, person, school, x, y, photo_data=None, signature_data=None):
    """Draw the 54 x 85 mm portrait card to match the React preview."""
    orange = colors.HexColor("#ec6414")
    dark = colors.HexColor("#42230f")
    cream = colors.HexColor("#fff7ee")

    # Base card.
    pdf.setFillColor(cream)
    pdf.setStrokeColor(dark)
    pdf.setLineWidth(.35 * mm)
    pdf.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, 1.1 * mm, fill=1, stroke=1)

    # ============================================================
    # HEADER — 18 mm high, matching the reference composition.
    # ============================================================
    header_h = 18 * mm
    header_y = y + CARD_HEIGHT - header_h
    pdf.setFillColor(orange)
    pdf.rect(x + .35 * mm, header_y, CARD_WIDTH - .7 * mm, header_h, fill=1, stroke=0)

    if LOGO_PATH.is_file():
        _draw_image(
            pdf,
            LOGO_PATH,
            x + 2.25 * mm,
            header_y + 2.45 * mm,
            11.8 * mm,
            12.7 * mm,
            "",
            False,
        )

    text_x = x + 14.7 * mm
    text_w = CARD_WIDTH - 16.0 * mm
    text_center_x = text_x + text_w / 2
    school_name = (school.school_name or "").upper()
    address = (school.address or "").upper()
    udise_code = (school.udise_code or "").upper()

    pdf.setFillColor(colors.white)

    # School name: two centered lines, large and bold.
    name_size = 7.45
    name_lines = _wrap_words(school_name, "Helvetica-Bold", name_size, text_w)
    while len(name_lines) > 2 and name_size > 6.0:
        name_size -= .15
        name_lines = _wrap_words(school_name, "Helvetica-Bold", name_size, text_w)
    name_lines = name_lines[:2]
    if name_lines:
        _draw_centered_lines(
            pdf,
            name_lines,
            text_center_x,
            header_y + 13.7 * mm,
            "Helvetica-Bold",
            name_size,
            3.25 * mm,
        )

    # Address: two centered lines, large and bold.
    address_size = 4.25
    address_lines = _wrap_words(address, "Helvetica-Bold", address_size, text_w)
    while len(address_lines) > 2 and address_size > 3.2:
        address_size -= .12
        address_lines = _wrap_words(address, "Helvetica-Bold", address_size, text_w)
    address_lines = address_lines[:2]
    if address_lines:
        _draw_centered_lines(
            pdf,
            address_lines,
            text_center_x,
            header_y + 6.9 * mm,
            "Helvetica-Bold",
            address_size,
            3.0 * mm,
        )

    # UDISE is pushed down and enlarged.
    udise_text = f"UDISE CODE : {udise_code}" if udise_code else ""
    udise_size = 5.05
    while udise_text and udise_size > 4.0 and pdf.stringWidth(udise_text, "Helvetica-Bold", udise_size) > text_w:
        udise_size -= .1
    if udise_text:
        pdf.setFont("Helvetica-Bold", udise_size)
        pdf.drawCentredString(text_center_x, header_y + 1.9 * mm, udise_text)

    # ============================================================
    # IDENTITY CARD heading.
    # ============================================================
    pdf.setFillColor(colors.HexColor("#253b9b"))
    pdf.setFont("Helvetica-Bold", 9.1)
    pdf.drawCentredString(x + CARD_WIDTH / 2, y + CARD_HEIGHT - 21.2 * mm, "IDENTITY CARD")

    # ============================================================
    # PHOTO + BLOOD GROUP.
    # ============================================================
    photo_w, photo_h = 22.2 * mm, 27.2 * mm
    photo_x = x + (CARD_WIDTH - photo_w) / 2
    photo_y = y + CARD_HEIGHT - 50.5 * mm

    pdf.setFillColor(orange)
    pdf.setStrokeColor(orange)
    pdf.setLineWidth(.28 * mm)
    pdf.roundRect(
        photo_x - .3 * mm,
        photo_y - .3 * mm,
        photo_w + .6 * mm,
        photo_h + .6 * mm,
        .35 * mm,
        fill=1,
        stroke=1,
    )

    photo = photo_data if photo_data is not None else (_student_photo(person) if isinstance(person, models.Student) else _staff_photo(person))
    _draw_image(pdf, photo[0] if photo else None, photo_x, photo_y, photo_w, photo_h)

    _drop(pdf, x + 46.9 * mm, photo_y + 8.9 * mm, getattr(person, "blood_group", None))

    # ============================================================
    # NAME + DETAILS.
    # ============================================================
    name = str(person.name or "").upper()
    name_size = 9.0
    while name_size > 7.0 and pdf.stringWidth(name, "Helvetica-Bold", name_size) > CARD_WIDTH - 6 * mm:
        name_size -= .15
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", name_size)
    pdf.drawCentredString(x + CARD_WIDTH / 2, photo_y - 4.0 * mm, name)

    if isinstance(person, models.Student):
        rows = [
            ("FATHER'S NAME", person.father_name),
            ("MOTHER'S NAME", person.mother_name),
            ("CONTACT NO", person.contact_number),
            ("PEN NUMBER", person.pen_number),
            ("DATE OF BIRTH", person.date_of_birth.strftime("%d/%m/%Y") if person.date_of_birth else None),
        ]
    else:
        rows = [
            ("DESIGNATION", person.designation),
            ("FATHER/HUSBAND", person.father_husband_name),
            ("LEVEL", person.level),
            ("MOBILE NO", person.mobile_number),
            ("DATE OF BIRTH", person.date_of_birth.strftime("%d/%m/%Y") if person.date_of_birth else None),
        ]

    # Larger detail rows with the same left/value alignment as the reference.
    row_y = photo_y - 9.4 * mm
    label_x = x + 4.0 * mm
    value_w = CARD_WIDTH - 27.0 * mm
    for label, value in rows:
        _line(pdf, label_x, row_y, f"{label}:", value, value_w)
        row_y -= 3.7 * mm

    # ============================================================
    # BOTTOM ORANGE WAVE + DEPARTMENT + SIGNATURE.
    # ============================================================
    bottom_y = y

    path = pdf.beginPath()
    left = x + .35 * mm
    right = x + 39.5 * mm
    base = bottom_y + .35 * mm
    top = bottom_y + 9.7 * mm
    path.moveTo(left, base)
    path.lineTo(right, base)
    path.curveTo(
        right - 2.0 * mm,
        base + 4.4 * mm,
        right - 8.5 * mm,
        top,
        right - 17.0 * mm,
        top,
    )
    path.lineTo(left, top)
    path.close()
    pdf.setFillColor(orange)
    pdf.drawPath(path, fill=1, stroke=0)

    # Full-width lower orange strip.
    pdf.setFillColor(orange)
    pdf.rect(x + .35 * mm, bottom_y + .35 * mm, CARD_WIDTH - .7 * mm, 5.15 * mm, fill=1, stroke=0)

    # Reference uses an ampersand and the text fills the orange strip.
    department = "SCHOOL & MASS EDUCATION DEPARTMENT"
    department_size = 6.15
    department_max_width = CARD_WIDTH - 4.5 * mm
    while department_size > 4.8 and pdf.stringWidth(department, "Helvetica-Bold", department_size) > department_max_width:
        department_size -= .1
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", department_size)
    pdf.drawCentredString(x + 26.5 * mm, bottom_y + 1.65 * mm, department)

    # Signature above HEADMASTER, both on the cream area.
    signature = signature_data if signature_data is not None else _school_signature(school)
    # Larger signature area: the transparent signature/seal is intentionally
    # given more room while staying above the HEADMASTER label and inside the
    # cream portion of the card.
    sig_x = x + 35.8 * mm
    sig_w, sig_h = 17.0 * mm, 5.8 * mm
    if signature:
        _draw_image(pdf, signature[0], sig_x, bottom_y + 5.65 * mm, sig_w, sig_h, "", True)

    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 6.0)
    pdf.drawCentredString(sig_x + sig_w / 2, bottom_y + 4.45 * mm, "HEADMASTER")


def _person_photo(person):
    try:
        return _student_photo(person) if isinstance(person, models.Student) else _staff_photo(person)
    except Exception:
        return None


def _pdf_response(people, school, filename):
    # Fetch remote Cloudinary media concurrently once per PDF request. The old
    # browser-print flow fetched every protected image in React and then waited
    # for the print renderer; this keeps the PDF generation server-side and
    # avoids repeated signature downloads.
    people = list(people)
    signature = _school_signature(school)
    photos = {}
    if people:
        with ThreadPoolExecutor(max_workers=min(16, len(people))) as executor:
            futures = {executor.submit(_person_photo, person): index for index, person in enumerate(people)}
            for future in as_completed(futures):
                photos[futures[future]] = future.result()

    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
    for index, person in enumerate(people):
        slot = index % (COLS * ROWS)
        if slot == 0 and index:
            pdf.showPage()
        column, row = slot % COLS, slot // COLS
        x = PAGE_MARGIN_X + column * (CARD_WIDTH + CUT_GAP)
        y = A4[1] - PAGE_MARGIN_Y - CARD_HEIGHT - row * (CARD_HEIGHT + CUT_GAP)
        _draw_card(pdf, person, school, x, y, photo_data=photos.get(index), signature_data=signature)
    pdf.save()
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


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


@router.post("/print.pdf")
def print_selected_cards(payload: dict, user=Depends(require_school_user), db: Session = Depends(get_db)):
    """Generate a real A4 PDF for exactly the cards selected in the ID-card table."""
    student_ids = [int(value) for value in (payload.get("student_ids") or [])]
    staff_ids = [int(value) for value in (payload.get("staff_ids") or [])]
    if not student_ids and not staff_ids:
        raise HTTPException(400, "Select at least one person to generate the PDF")

    students = {
        item.id: item
        for item in db.query(models.Student).filter(
            models.Student.school_id == user.school_id,
            models.Student.is_active == True,
            models.Student.id.in_(student_ids),
        ).all()
    } if student_ids else {}
    staff = {
        item.id: item
        for item in db.query(models.Staff).filter(
            models.Staff.school_id == user.school_id,
            models.Staff.is_active == True,
            models.Staff.id.in_(staff_ids),
        ).all()
    } if staff_ids else {}

    missing_students = [value for value in student_ids if value not in students]
    missing_staff = [value for value in staff_ids if value not in staff]
    if missing_students or missing_staff:
        raise HTTPException(404, "One or more selected people could not be found")

    # Preserve the exact order shown/selected by the frontend: students first
    # in the request, followed by staff. Duplicate IDs are removed.
    people = []
    seen = set()
    for value in student_ids:
        if value not in seen:
            people.append(students[value])
            seen.add(value)
    for value in staff_ids:
        key = ("staff", value)
        if key not in seen:
            people.append(staff[value])
            seen.add(key)

    return _pdf_response(people, _school(db, user.school_id, include_signature=True), "id-cards.pdf")


@router.get("/staff/bulk.pdf")
def bulk_staff_cards(staff_type: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    query = db.query(models.Staff).filter(models.Staff.school_id == user.school_id, models.Staff.is_active == True)
    if staff_type: query = query.filter(models.Staff.staff_type == staff_type.upper())
    return _pdf_response(query.order_by(models.Staff.name).all(), _school(db, user.school_id, include_signature=True), "staff-id-cards.pdf")


@router.get("/staff/bulk.docx")
def bulk_staff_docx(staff_type: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    query = db.query(models.Staff).filter(models.Staff.school_id == user.school_id, models.Staff.is_active == True)
    if staff_type: query = query.filter(models.Staff.staff_type == staff_type.upper())
    return _docx_response(query.order_by(models.Staff.name).all(), _school(db, user.school_id, include_signature=True), "staff-id-cards.docx")


@router.get("/staff/{staff_id}.pdf")
def staff_card(staff_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    return _pdf_response([_staff(db, staff_id, user.school_id)], _school(db, user.school_id, include_signature=True), f"staff-id-card-{staff_id}.pdf")


@router.get("/bulk.pdf")
def bulk_student_cards(class_name: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    people = _student_query(db, user.school_id, class_name).all()
    return _pdf_response(people, _school(db, user.school_id, include_signature=True), "student-id-cards.pdf")


@router.get("/bulk.docx")
def bulk_student_docx(class_name: str | None = None, user=Depends(require_school_user), db: Session = Depends(get_db)):
    return _docx_response(_student_query(db, user.school_id, class_name).all(), _school(db, user.school_id, include_signature=True), "student-id-cards.docx")


@router.get("/{student_id}.pdf")
def student_card(student_id: int, user=Depends(require_school_user), db: Session = Depends(get_db)):
    return _pdf_response([_student(db, student_id, user.school_id, include_photo=True)], _school(db, user.school_id, include_signature=True), f"student-id-card-{student_id}.pdf")
