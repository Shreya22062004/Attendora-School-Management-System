"""School-scoped 54 x 86 mm portrait ID cards for students and personnel."""
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from PIL import Image, ImageChops, ImageOps
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, defer

from .. import models
from ..auth import require_school_user
from ..database import get_db
from ..media_storage import MediaStorageError, delete as delete_media, get_bytes as get_media, optimize_student_photo, put_bytes as put_media

router = APIRouter(prefix="/idcards", tags=["ID Cards"])
CARD_WIDTH = 54 * mm
CARD_HEIGHT = 86 * mm
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
    if student.photo_storage_key:
        try: return get_media(student.photo_storage_key, student.photo_storage_url)
        except MediaStorageError: pass
    if student.photo_data: return student.photo_data, student.photo_mime_type or "image/jpeg"
    return _legacy_image(student.photo)


def _staff_photo(staff):
    if not staff.photo_storage_key: return None
    try: return get_media(staff.photo_storage_key, staff.photo_storage_url)
    except MediaStorageError: return None


def _school_signature(school):
    if school and school.headmaster_signature_key:
        try: return get_media(school.headmaster_signature_key, school.headmaster_signature_url)
        except MediaStorageError: pass
    if school and school.headmaster_signature_data:
        return school.headmaster_signature_data, school.headmaster_signature_mime_type or "image/png"
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
    _admin(user); school = _school(db, user.school_id); data, mime = await _read_image(file)
    previous = school.headmaster_signature_key
    try: public_id, secure_url = put_media(f"attendora/signatures/{school.id}_headmaster", data, mime)
    except MediaStorageError as error: raise HTTPException(503, str(error)) from error
    school.headmaster_signature_key = public_id; school.headmaster_signature_url = secure_url; school.headmaster_signature_mime_type = mime
    try: db.commit()
    except Exception as error:
        db.rollback()
        if not previous:
            try: delete_media(public_id)
            except MediaStorageError: pass
        raise HTTPException(500, "Could not save signature reference") from error
    return {"message": "Headmaster signature saved"}


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
    _admin(user); item = _student(db, student_id, user.school_id); raw, _ = await _read_image(file); previous = item.photo_storage_key
    try:
        data, mime = optimize_student_photo(raw); public_id, secure_url = put_media(f"attendora/students/{item.id}", data, mime)
    except MediaStorageError as error: raise HTTPException(503, str(error)) from error
    item.photo_storage_key = public_id; item.photo_storage_url = secure_url; item.photo_mime_type = mime
    try: db.commit()
    except Exception as error:
        db.rollback()
        if not previous:
            try: delete_media(public_id)
            except MediaStorageError: pass
        raise HTTPException(500, "Could not save photo reference") from error
    return {"message": "Student photo saved"}


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
            white = Image.new("RGBA", image.size, "white"); bbox = ImageChops.difference(image, white).convert("RGB").point(lambda value: 0 if value < 18 else 255).getbbox()
            if bbox: image = image.crop(bbox)
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
    pdf.setFont("Helvetica-Bold", 3.45)
    pdf.drawString(x, y, label)
    pdf.setFillColor(colors.HexColor("#252525"))
    pdf.setFont("Helvetica", 3.5)
    text = str(value or "")
    while text and pdf.stringWidth(text, "Helvetica", 3.5) > width:
        text = text[:-1]
    pdf.drawString(x + 11 * mm, y, text)


def _draw_card(pdf, person, school, x, y):
    """Draw the same 54 x 86 mm portrait composition used by the React preview."""
    orange = colors.HexColor("#ec6414")
    dark = colors.HexColor("#42230f")
    cream = colors.HexColor("#fff7ee")

    pdf.setFillColor(cream)
    pdf.setStrokeColor(dark)
    pdf.setLineWidth(.35 * mm)
    pdf.roundRect(x, y, CARD_WIDTH, CARD_HEIGHT, 1.1 * mm, fill=1, stroke=1)

    # Header: logo + school name on the same horizontal line, with
    # address and UDISE safely centered underneath.  Use fixed safe
    # baselines rather than vertical percentages so the lines cannot
    # disappear above the orange header in either preview or PDF.
    header_h = 16.5 * mm
    header_y = y + CARD_HEIGHT - header_h
    pdf.setFillColor(orange)
    pdf.roundRect(x + .35 * mm, header_y, CARD_WIDTH - .7 * mm, header_h, .8 * mm, fill=1, stroke=0)

    if LOGO_PATH.is_file():
        _draw_image(pdf, LOGO_PATH, x + 2.2 * mm, header_y + 3.0 * mm, 8.5 * mm, 9.5 * mm, "", False)

    text_x = x + 11.5 * mm
    text_w = CARD_WIDTH - 13.3 * mm
    text_center_x = text_x + text_w / 2
    school_name = (school.school_name or "").upper()
    address = (school.address or "").upper()
    udise_code = (school.udise_code or "").upper()

    pdf.setFillColor(colors.white)

    # School name: deliberately smaller so KANTABANJI and other long
    # school names remain completely visible.
    name_size = 4.45
    while name_size > 3.35 and pdf.stringWidth(school_name, "Helvetica-Bold", name_size) > text_w:
        name_size -= .08
    pdf.setFont("Helvetica-Bold", name_size)
    if school_name:
        pdf.drawCentredString(text_center_x, header_y + 11.1 * mm, school_name)

    # Address and UDISE each have their own safe baseline inside the header.
    address_size = 2.45
    while address_size > 1.65 and pdf.stringWidth(address, "Helvetica-Bold", address_size) > text_w:
        address_size -= .06
    pdf.setFont("Helvetica-Bold", address_size)
    if address:
        pdf.drawCentredString(text_center_x, header_y + 7.35 * mm, address)

    udise_text = f"UDISE CODE: {udise_code}" if udise_code else ""
    udise_size = 3.05
    while udise_size > 2.0 and pdf.stringWidth(udise_text, "Helvetica-Bold", udise_size) > text_w:
        udise_size -= .06
    pdf.setFont("Helvetica-Bold", udise_size)
    if udise_text:
        pdf.drawCentredString(text_center_x, header_y + 4.0 * mm, udise_text)

    # Central heading.
    pdf.setFillColor(colors.HexColor("#253b9b"))
    pdf.setFont("Helvetica-Bold", 6.3)
    pdf.drawCentredString(x + CARD_WIDTH / 2, y + CARD_HEIGHT - 20.5 * mm, "IDENTITY CARD")

    # Photo centered, blood droplet to its right.
    # Stamp-size photograph: 20 x 25 mm.
    photo_w, photo_h = 20 * mm, 25 * mm
    photo_x = x + (CARD_WIDTH - photo_w) / 2
    photo_y = y + CARD_HEIGHT - 45.8 * mm
    pdf.setFillColor(orange)
    pdf.setStrokeColor(orange)
    pdf.setLineWidth(.3 * mm)
    pdf.roundRect(photo_x - .45 * mm, photo_y - .45 * mm, photo_w + .9 * mm, photo_h + .9 * mm, .55 * mm, fill=1, stroke=1)
    photo = _student_photo(person) if isinstance(person, models.Student) else _staff_photo(person)
    _draw_image(pdf, photo[0] if photo else None, photo_x, photo_y, photo_w, photo_h)
    _drop(pdf, x + 43.2 * mm, photo_y + 15.2 * mm, getattr(person, "blood_group", None))

    # Name below the photograph.
    name = str(person.name or "").upper()
    name_size = 5.0
    while name_size > 3.6 and pdf.stringWidth(name, "Helvetica-Bold", name_size) > CARD_WIDTH - 7 * mm:
        name_size -= .15
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", name_size)
    pdf.drawCentredString(x + CARD_WIDTH / 2, photo_y - 3.5 * mm, name)
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

    row_y = photo_y - 9.7 * mm
    label_x = x + 4.2 * mm
    value_x = x + 20.5 * mm
    value_w = CARD_WIDTH - 24.5 * mm
    for label, value in rows:
        _line(pdf, label_x, row_y, f"{label}:", value, value_w)
        row_y -= 4.2 * mm

    # Sample-style bottom: cream body remains visible; orange is a curved left fill.
    bottom_h = 13.0 * mm
    bottom_y = y
    pdf.setFillColor(colors.HexColor("#f2ddc2"))
    pdf.rect(x + .7 * mm, bottom_y + bottom_h - .35 * mm, CARD_WIDTH - 1.4 * mm, .35 * mm, fill=1, stroke=0)

    # Curved orange panel on the lower-left, matching the reference card.
    path = pdf.beginPath()
    left = x + .35 * mm
    right = x + 36.0 * mm
    base = bottom_y + .35 * mm
    top = bottom_y + 9.8 * mm
    path.moveTo(left, base)
    path.lineTo(right, base)
    path.curveTo(right - 1.5 * mm, base + 4.8 * mm, right - 7.5 * mm, top, right - 15.5 * mm, top)
    path.lineTo(left, top)
    path.close()
    pdf.setFillColor(orange)
    pdf.drawPath(path, fill=1, stroke=0)

    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 2.35)
    pdf.drawString(x + 2.2 * mm, bottom_y + 4.25 * mm, "SCHOOL AND MASS EDUCATION DEPARTMENT")

    # Signature sits clearly ABOVE the HEADMASTER label and both remain on
    # the cream area, away from the orange bottom strip/curve.
    signature = _school_signature(school)
    sig_x = x + 38.0 * mm
    sig_w, sig_h = 14.0 * mm, 5.7 * mm
    if signature:
        _draw_image(pdf, signature[0], sig_x, bottom_y + 6.1 * mm, sig_w, sig_h, "", True)
    pdf.setStrokeColor(dark)
    pdf.setLineWidth(.18 * mm)
    pdf.line(sig_x, bottom_y + 5.65 * mm, sig_x + sig_w, bottom_y + 5.65 * mm)
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 2.55)
    pdf.drawCentredString(sig_x + sig_w / 2, bottom_y + 3.25 * mm, "HEADMASTER")

def _pdf_response(people, school, filename):
    stream = BytesIO(); pdf = canvas.Canvas(stream, pagesize=A4, pageCompression=1)
    for index, person in enumerate(people):
        slot = index % (COLS * ROWS)
        if slot == 0 and index: pdf.showPage()
        column, row = slot % COLS, slot // COLS
        x = PAGE_MARGIN_X + column * (CARD_WIDTH + CUT_GAP); y = A4[1] - PAGE_MARGIN_Y - CARD_HEIGHT - row * (CARD_HEIGHT + CUT_GAP)
        _draw_card(pdf, person, school, x, y)
    pdf.save(); stream.seek(0)
    return StreamingResponse(stream, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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
        header.text = f"{school.school_name or ""}\n{school.address or ""}"
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
