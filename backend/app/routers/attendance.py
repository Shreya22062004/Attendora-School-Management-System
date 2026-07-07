import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..auth import require_school_user

router = APIRouter(prefix="/attendance", tags=["Attendance"])


def allowed_classes(db: Session, u):
    cfg = db.query(models.SchoolConfig.classes_json).filter(
        models.SchoolConfig.school_id == u.school_id
    ).scalar()
    classes = json.loads(cfg or "[]")

    if u.role == "teacher":
        assigned = {
            row[0]
            for row in db.query(models.TeacherAssignment.class_name).filter(
                models.TeacherAssignment.user_id == u.id,
                models.TeacherAssignment.school_id == u.school_id,
            ).all()
        }
        classes = [c for c in classes if c in assigned]
    return classes


def ensure_class_access(db: Session, u, class_name: str):
    # Admin/school users may access any class containing active students.
    # Teachers must still pass an assignment check. This avoids loading and
    # decoding SchoolConfig.classes_json on every sheet/submit/edit request.
    if u.role == "teacher":
        assigned = db.query(models.TeacherAssignment.id).filter(
            models.TeacherAssignment.user_id == u.id,
            models.TeacherAssignment.school_id == u.school_id,
            models.TeacherAssignment.class_name == class_name,
        ).first()
        if not assigned:
            raise HTTPException(400, "Invalid class or class not assigned to this teacher")


@router.get("/classes")
def classes(u=Depends(require_school_user), db: Session = Depends(get_db)):
    return {"classes": allowed_classes(db, u)}


def class_student_rows(db: Session, sid: int, class_name: str, section=None):
    q = db.query(
        models.Student.id,
        models.Student.name,
        models.Student.gender,
        models.Student.section,
    ).filter(
        models.Student.school_id == sid,
        models.Student.class_name == class_name,
        models.Student.is_active.is_(True),
    )
    if section:
        q = q.filter(models.Student.section == section)
    return q.all()


def validate_and_get_ids(data, db: Session, u):
    ensure_class_access(db, u, data.class_name)

    q = db.query(models.Student.id).filter(
        models.Student.school_id == u.school_id,
        models.Student.class_name == data.class_name,
        models.Student.is_active.is_(True),
    )
    if data.section:
        q = q.filter(models.Student.section == data.section)

    valid_ids = {row[0] for row in q.all()}
    received_ids = {r.student_id for r in data.records}

    if not valid_ids:
        raise HTTPException(400, "No active students found in this class")
    if valid_ids != received_ids or len(received_ids) != len(data.records):
        raise HTTPException(400, "Attendance must include every active student in selected class exactly once")
    if any(r.status not in ("Present", "Absent") for r in data.records):
        raise HTTPException(400, "Invalid status")

    return valid_ids


def ensure_working_day(db: Session, sid: int, d: date):
    day = db.query(
        models.SchoolCalendar.day_type,
        models.SchoolCalendar.description,
    ).filter(
        models.SchoolCalendar.school_id == sid,
        models.SchoolCalendar.calendar_date == d,
    ).first()

    if day and day.day_type != "Working Day":
        detail = f"Attendance cannot be marked: {day.day_type}"
        if day.description:
            detail += f" - {day.description}"
        raise HTTPException(400, detail)

    if d.weekday() == 6 and not (day and day.day_type == "Working Day"):
        raise HTTPException(400, "Attendance cannot be marked on Sunday")


def active_year_id(db: Session, sid: int, d: date):
    return db.query(models.AcademicYear.id).filter(
        models.AcademicYear.school_id == sid,
        models.AcademicYear.start_date <= d,
        models.AcademicYear.end_date >= d,
    ).order_by(models.AcademicYear.is_active.desc()).scalar()


def session_query(db: Session, sid: int, class_name: str, attendance_date: date, section=None):
    q = db.query(models.AttendanceSession).filter(
        models.AttendanceSession.school_id == sid,
        models.AttendanceSession.class_name == class_name,
        models.AttendanceSession.attendance_date == attendance_date,
    )
    if section:
        q = q.filter(models.AttendanceSession.section == section)
    else:
        q = q.filter(models.AttendanceSession.section.is_(None))
    return q


@router.get("/sheet")
def sheet(
    class_name: str,
    attendance_date: date,
    section: str | None = None,
    u=Depends(require_school_user),
    db: Session = Depends(get_db),
):
    ensure_class_access(db, u, class_name)

    students = class_student_rows(db, u.school_id, class_name, section)
    students = sorted(
        students,
        key=lambda x: (
            0 if x.gender == "Girl" else 1 if x.gender == "Boy" else 2,
            x.name.lower(),
        ),
    )

    sess = session_query(
        db, u.school_id, class_name, attendance_date, section
    ).first()

    student_ids = [s.id for s in students]
    status_map = {}
    if student_ids:
        status_map = dict(
            db.query(models.Attendance.student_id, models.Attendance.status).filter(
                models.Attendance.attendance_date == attendance_date,
                models.Attendance.student_id.in_(student_ids),
            ).all()
        )

    rows = [
        {
            "student_id": s.id,
            "name": s.name,
            "gender": s.gender,
            "section": s.section,
            "status": status_map.get(s.id, "Present"),
        }
        for s in students
    ]

    return {
        "submitted": bool(sess),
        "edit_count": sess.edit_count if sess else 0,
        "edits_remaining": max(0, 6 - sess.edit_count) if sess else 6,
        "is_locked": sess.is_locked if sess else False,
        "records": rows,
    }


@router.post("/submit")
def submit(
    data: schemas.AttendanceSheet,
    u=Depends(require_school_user),
    db: Session = Depends(get_db),
):
    ensure_working_day(db, u.school_id, data.attendance_date)
    validate_and_get_ids(data, db, u)

    existing_session_id = session_query(
        db, u.school_id, data.class_name, data.attendance_date, data.section
    ).with_entities(models.AttendanceSession.id).scalar()

    if existing_session_id:
        raise HTTPException(409, "Attendance already submitted. Reload the sheet and use edit.")

    year_id = active_year_id(db, u.school_id, data.attendance_date)

    try:
        db.add(
            models.AttendanceSession(
                school_id=u.school_id,
                academic_year_id=year_id,
                class_name=data.class_name,
                section=data.section,
                attendance_date=data.attendance_date,
                created_by=u.id,
            )
        )

        db.bulk_insert_mappings(
            models.Attendance,
            [
                {
                    "student_id": r.student_id,
                    "academic_year_id": year_id,
                    "attendance_date": data.attendance_date,
                    "status": r.status,
                }
                for r in data.records
            ],
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Attendance submitted",
        "edits_remaining": 6,
        "is_locked": False,
    }


@router.put("/edit")
def edit(
    data: schemas.AttendanceSheet,
    u=Depends(require_school_user),
    db: Session = Depends(get_db),
):
    ensure_working_day(db, u.school_id, data.attendance_date)
    ensure_class_access(db, u, data.class_name)

    # One joined query replaces the old separate validation query and attendance query.
    q = db.query(
        models.Student.id.label("student_id"),
        models.Attendance.id.label("attendance_id"),
        models.Attendance.status.label("old_status"),
    ).outerjoin(
        models.Attendance,
        and_(
            models.Attendance.student_id == models.Student.id,
            models.Attendance.attendance_date == data.attendance_date,
        ),
    ).filter(
        models.Student.school_id == u.school_id,
        models.Student.class_name == data.class_name,
        models.Student.is_active.is_(True),
    )
    if data.section:
        q = q.filter(models.Student.section == data.section)

    rows = q.all()
    valid_ids = {row.student_id for row in rows}
    received_ids = {r.student_id for r in data.records}

    if not valid_ids:
        raise HTTPException(400, "No active students found in this class")
    if valid_ids != received_ids or len(received_ids) != len(data.records):
        raise HTTPException(
            400,
            "Attendance must include every active student in selected class exactly once",
        )
    if any(r.status not in ("Present", "Absent") for r in data.records):
        raise HTTPException(400, "Invalid status")

    sess = session_query(
        db, u.school_id, data.class_name, data.attendance_date, data.section
    ).first()

    if not sess:
        raise HTTPException(404, "Submit attendance first")
    if sess.is_locked or sess.edit_count >= 6:
        raise HTTPException(403, "Attendance is locked")

    existing = {row.student_id: row for row in rows if row.attendance_id is not None}
    updates = []

    for r in data.records:
        old = existing.get(r.student_id)
        if old and old.old_status != r.status:
            updates.append({"id": old.attendance_id, "status": r.status})

    changed = len(updates)
    if not changed:
        raise HTTPException(400, "No attendance status was changed")

    try:
        # One batched UPDATE. AttendanceHistory and AuditLog writes were intentionally
        # removed because they added remote database round trips to the save path.
        db.bulk_update_mappings(models.Attendance, updates)

        sess.edit_count += 1
        sess.is_locked = sess.edit_count >= 6
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Attendance updated",
        "changed_records": changed,
        "edits_remaining": max(0, 6 - sess.edit_count),
        "is_locked": sess.is_locked,
    }
