from datetime import date
from calendar import monthrange
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, case, or_

from ..database import get_db
from ..auth import require_school_user
from ..models import (
    Student,
    Attendance,
    AttendanceSession,
    SchoolCalendar,
    SchoolConfig,
)


router = APIRouter(prefix="/reports", tags=["Reports"])


CLASS_ORDER = {
    "UKG/KG2/PP1": 0,
    **{str(i): i for i in range(1, 13)},
}

GENDER_ORDER = {
    "Girl": 0,
    "Boy": 1,
}


def student_sort_key(r):
    return (
        CLASS_ORDER.get(str(r["class_name"]), 99),
        GENDER_ORDER.get(r.get("gender"), 2),
        (r.get("student_name") or "").lower(),
    )


def holiday_dates(db, sid, start, end):
    calendar_rows = (
        db.query(
            SchoolCalendar.calendar_date,
            SchoolCalendar.day_type,
        )
        .filter(
            SchoolCalendar.school_id == sid,
            SchoolCalendar.calendar_date.between(start, end),
        )
        .all()
    )

    calendar_map = {
        row.calendar_date: row.day_type
        for row in calendar_rows
    }

    holidays = {
        row.calendar_date
        for row in calendar_rows
        if row.day_type != "Working Day"
    }

    for ordinal in range(start.toordinal(), end.toordinal() + 1):
        current_date = date.fromordinal(ordinal)

        if (
            current_date.weekday() == 6
            and calendar_map.get(current_date) != "Working Day"
        ):
            holidays.add(current_date)

    return holidays


def get_strength_map(db, sid):
    rows = (
        db.query(
            Student.class_name.label("class_name"),
            func.sum(
                case(
                    (Student.gender == "Boy", 1),
                    else_=0,
                )
            ).label("boys_total"),
            func.sum(
                case(
                    (Student.gender == "Girl", 1),
                    else_=0,
                )
            ).label("girls_total"),
            func.count(Student.id).label("total_students"),
        )
        .filter(
            Student.school_id == sid,
            Student.is_active == True,
        )
        .group_by(Student.class_name)
        .all()
    )

    return {
        row.class_name: {
            "boys_total": row.boys_total or 0,
            "girls_total": row.girls_total or 0,
            "total_students": row.total_students or 0,
        }
        for row in rows
    }


def summary_query(db, sid, start, end, holidays=None, strength_map=None):
    if holidays is None:
        holidays = holiday_dates(db, sid, start, end)

    if strength_map is None:
        strength_map = get_strength_map(db, sid)

    filters = [
        Student.school_id == sid,
        Attendance.attendance_date.between(start, end),
    ]

    if holidays:
        filters.append(
            ~Attendance.attendance_date.in_(holidays)
        )

    rows = (
        db.query(
            Student.class_name.label("class_name"),

            func.sum(
                case(
                    (
                        (Student.gender == "Boy")
                        & (Attendance.status == "Present"),
                        1,
                    ),
                    else_=0,
                )
            ).label("boys_present"),

            func.sum(
                case(
                    (
                        (Student.gender == "Girl")
                        & (Attendance.status == "Present"),
                        1,
                    ),
                    else_=0,
                )
            ).label("girls_present"),

            func.sum(
                case(
                    (Attendance.status == "Present", 1),
                    else_=0,
                )
            ).label("total_present"),

            func.sum(
                case(
                    (Attendance.status == "Absent", 1),
                    else_=0,
                )
            ).label("total_absent"),

            func.count(Attendance.id).label("total_marked"),
        )
        .join(
            Attendance,
            Attendance.student_id == Student.id,
        )
        .filter(*filters)
        .group_by(Student.class_name)
        .all()
    )

    out = []

    for row in rows:
        data = dict(row._mapping)

        data.update(
            strength_map.get(
                data["class_name"],
                {
                    "boys_total": 0,
                    "girls_total": 0,
                    "total_students": 0,
                },
            )
        )

        out.append(data)

    return sorted(
        out,
        key=lambda r: CLASS_ORDER.get(str(r["class_name"]), 99),
    )


def studentwise_query(
    db,
    sid,
    start,
    end,
    search=None,
    class_name=None,
    holidays=None,
):
    if holidays is None:
        holidays = holiday_dates(db, sid, start, end)

    join_cond = (
        (Attendance.student_id == Student.id)
        & Attendance.attendance_date.between(start, end)
    )

    if holidays:
        join_cond = join_cond & (
            ~Attendance.attendance_date.in_(holidays)
        )

    q = (
        db.query(
            Student.id.label("student_id"),
            Student.class_name.label("class_name"),
            Student.name.label("student_name"),
            Student.gender.label("gender"),

            func.sum(
                case(
                    (Attendance.status == "Present", 1),
                    else_=0,
                )
            ).label("present"),

            func.sum(
                case(
                    (Attendance.status == "Absent", 1),
                    else_=0,
                )
            ).label("absent"),

            func.count(
                Attendance.id
            ).label("total_marked_days"),
        )
        .outerjoin(Attendance, join_cond)
        .filter(
            Student.school_id == sid,
            Student.is_active == True,
        )
    )

    if class_name:
        q = q.filter(
            Student.class_name == class_name
        )

    if search and search.strip():
        term = f"%{search.strip()}%"

        q = q.filter(
            or_(
                Student.name.ilike(term),
                Student.admission_no.ilike(term),
                Student.pen_number.ilike(term),
            )
        )

    rows = (
        q.group_by(
            Student.id,
            Student.class_name,
            Student.name,
            Student.gender,
        )
        .all()
    )

    out = []

    for row in rows:
        data = dict(row._mapping)

        data["present"] = data["present"] or 0
        data["absent"] = data["absent"] or 0

        total_marked = data["total_marked_days"] or 0

        data["percentage"] = (
            round(
                data["present"] * 100 / total_marked,
                2,
            )
            if total_marked
            else 0
        )

        out.append(data)

    return sorted(out, key=student_sort_key)


def total(rows):
    keys = [
        "boys_present",
        "girls_present",
        "total_present",
        "total_absent",
        "total_marked",
    ]

    return {
        key: sum(
            row.get(key, 0) or 0
            for row in rows
        )
        for key in keys
    }


def strength_for_classes(strength_map, classes):
    boys_total = 0
    girls_total = 0
    total_students = 0

    for class_name in classes:
        strength = strength_map.get(class_name, {})

        boys_total += strength.get("boys_total", 0) or 0
        girls_total += strength.get("girls_total", 0) or 0
        total_students += strength.get("total_students", 0) or 0

    return {
        "boys_total": boys_total,
        "girls_total": girls_total,
        "total_students": total_students,
    }


def group(rows, classes, strength_map):
    result = total(
        [
            row
            for row in rows
            if row["class_name"] in classes
        ]
    )

    result.update(
        strength_for_classes(
            strength_map,
            classes,
        )
    )

    return result


def day_status(db, sid, d):
    calendar_row = (
        db.query(SchoolCalendar)
        .filter_by(
            school_id=sid,
            calendar_date=d,
        )
        .first()
    )

    if (
        calendar_row
        and calendar_row.day_type != "Working Day"
    ):
        return {
            "day_status": "Declared Holiday",
            "reason": (
                calendar_row.description
                or calendar_row.day_type
            ),
        }

    if d.weekday() == 6 and not calendar_row:
        return {
            "day_status": "Declared Holiday",
            "reason": "Sunday",
        }

    session_exists = (
        db.query(AttendanceSession.id)
        .filter_by(
            school_id=sid,
            attendance_date=d,
        )
        .first()
    )

    if not session_exists:
        return {
            "day_status": "Not Marked",
            "reason": "Attendance not submitted",
        }

    present_exists = (
        db.query(Attendance.id)
        .join(
            Student,
            Attendance.student_id == Student.id,
        )
        .filter(
            Student.school_id == sid,
            Attendance.attendance_date == d,
            Attendance.status == "Present",
        )
        .first()
    )

    if present_exists:
        return {
            "day_status": "Working Day",
            "reason": "Attendance recorded",
        }

    return {
        "day_status": "Auto-detected Holiday",
        "reason": "Attendance submitted with no student marked present",
    }


def monthly_matrix(
    db,
    sid,
    year,
    month,
    holidays=None,
):
    last_day = monthrange(year, month)[1]

    start = date(year, month, 1)
    end = date(year, month, last_day)

    if holidays is None:
        holidays = holiday_dates(
            db,
            sid,
            start,
            end,
        )

    students = (
        db.query(Student)
        .filter(
            Student.school_id == sid,
            Student.is_active == True,
        )
        .all()
    )

    attendance_rows = (
        db.query(
            Attendance.student_id,
            Attendance.attendance_date,
            Attendance.status,
        )
        .join(
            Student,
            Attendance.student_id == Student.id,
        )
        .filter(
            Student.school_id == sid,
            Attendance.attendance_date.between(
                start,
                end,
            ),
        )
        .all()
    )

    attendance_map = {
        (
            row.student_id,
            row.attendance_date.day,
        ): (
            "P"
            if row.status == "Present"
            else "A"
        )
        for row in attendance_rows
        if row.attendance_date not in holidays
    }

    out = []

    for student in students:
        days = []

        for day_number in range(1, last_day + 1):
            current_date = date(
                year,
                month,
                day_number,
            )

            if current_date in holidays:
                days.append("H")
            else:
                days.append(
                    attendance_map.get(
                        (student.id, day_number),
                        "-",
                    )
                )

        present = days.count("P")
        absent = days.count("A")
        marked = present + absent

        out.append(
            {
                "student_id": student.id,
                "class_name": student.class_name,
                "student_name": student.name,
                "gender": student.gender,
                "days": days,
                "present": present,
                "absent": absent,
                "working_days": marked,
                "percentage": (
                    round(
                        present * 100 / marked,
                        2,
                    )
                    if marked
                    else 0
                ),
            }
        )

    return sorted(out, key=student_sort_key)


def yearly_matrix(
    db,
    sid,
    year,
    holidays=None,
):
    # Academic year: April of `year` through March of `year + 1`
    start = date(year, 4, 1)
    end = date(year + 1, 3, 31)
    academic_months = [(year, m) for m in range(4, 13)] + [(year + 1, m) for m in range(1, 4)]

    if holidays is None:
        holidays = holiday_dates(
            db,
            sid,
            start,
            end,
        )

    students = (
        db.query(Student)
        .filter(
            Student.school_id == sid,
            Student.is_active == True,
        )
        .all()
    )

    attendance_rows = (
        db.query(
            Attendance.student_id,
            Attendance.attendance_date,
            Attendance.status,
        )
        .join(
            Student,
            Attendance.student_id == Student.id,
        )
        .filter(
            Student.school_id == sid,
            Attendance.attendance_date.between(
                start,
                end,
            ),
        )
        .all()
    )

    monthly_stats = defaultdict(
        lambda: defaultdict(
            lambda: {
                "present": 0,
                "absent": 0,
            }
        )
    )

    for row in attendance_rows:
        if row.attendance_date in holidays:
            continue

        month_key = (row.attendance_date.year, row.attendance_date.month)

        if row.status == "Present":
            monthly_stats[row.student_id][month_key][
                "present"
            ] += 1

        elif row.status == "Absent":
            monthly_stats[row.student_id][month_key][
                "absent"
            ] += 1

    out = []

    for student in students:
        months = []
        yearly_present = 0
        yearly_absent = 0

        student_stats = monthly_stats.get(
            student.id,
            {},
        )

        for month_year, month_number in academic_months:
            month_stats = student_stats.get(
                (month_year, month_number),
                {
                    "present": 0,
                    "absent": 0,
                },
            )

            present = month_stats["present"]
            absent = month_stats["absent"]
            marked = present + absent

            months.append(
                f"{present}/{marked}"
                if marked
                else "-"
            )

            yearly_present += present
            yearly_absent += absent

        total_marked = (
            yearly_present
            + yearly_absent
        )

        out.append(
            {
                "student_id": student.id,
                "class_name": student.class_name,
                "student_name": student.name,
                "gender": student.gender,
                "months": months,
                "present": yearly_present,
                "absent": yearly_absent,
                "working_days": total_marked,
                "percentage": (
                    round(
                        yearly_present
                        * 100
                        / total_marked,
                        2,
                    )
                    if total_marked
                    else 0
                ),
            }
        )

    return sorted(out, key=student_sort_key)


@router.get("/daily")
def daily(
    report_date: date,
    u=Depends(require_school_user),
    db: Session = Depends(get_db),
):
    import json

    sid = u.school_id

    holidays = holiday_dates(
        db,
        sid,
        report_date,
        report_date,
    )

    strength_map = get_strength_map(
        db,
        sid,
    )

    rows = summary_query(
        db,
        sid,
        report_date,
        report_date,
        holidays=holidays,
        strength_map=strength_map,
    )

    cfg = (
        db.query(SchoolConfig)
        .filter_by(school_id=sid)
        .first()
    )

    classes = (
        json.loads(cfg.classes_json or "[]")
        if cfg
        else []
    )

    groups = (
        json.loads(
            cfg.dashboard_groups_json or "[]"
        )
        if cfg
        else []
    )

    if (
        not groups
        or (
            len(groups) == 1
            and groups[0].get("name")
            == "All Classes"
        )
    ):
        groups = [
            {
                "name": "UKG/KG2/PP1",
                "classes": [
                    c
                    for c in classes
                    if c == "UKG/KG2/PP1"
                ],
            },
            {
                "name": "Classes 1 to 5",
                "classes": [
                    c
                    for c in classes
                    if c
                    in ["1", "2", "3", "4", "5"]
                ],
            },
            {
                "name": "Classes 6 to 8",
                "classes": [
                    c
                    for c in classes
                    if c
                    in ["6", "7", "8"]
                ],
            },
        ]

        groups = [
            g
            for g in groups
            if g["classes"]
        ]

    cards = []

    for dashboard_group in groups:
        group_classes = dashboard_group.get(
            "classes",
            [],
        )

        cards.append(
            {
                "name": dashboard_group.get(
                    "name",
                    "Group",
                ),
                "classes": group_classes,
                "summary": group(
                    rows,
                    group_classes,
                    strength_map,
                ),
            }
        )

    submitted = {
        row[0]
        for row in (
            db.query(
                AttendanceSession.class_name
            )
            .filter_by(
                school_id=sid,
                attendance_date=report_date,
            )
            .all()
        )
    }

    class_set = set(classes)

    completion = {
        "submitted": len(
            submitted & class_set
        ),
        "total": len(classes),
        "pending_classes": [
            c
            for c in classes
            if c not in submitted
        ],
    }

    out = {
        "date": report_date,
        "classwise": rows,
        "groups": cards,
        "grand_total": group(
            rows,
            classes,
            strength_map,
        ),
        "completion": completion,
    }

    out.update(
        day_status(
            db,
            sid,
            report_date,
        )
    )

    return out


@router.get("/monthly")
def monthly(
    year: int,
    month: int,
    search: str | None = None,
    class_name: str | None = None,
    u=Depends(require_school_user),
    db: Session = Depends(get_db),
):
    sid = u.school_id

    start = date(year, month, 1)
    end = date(
        year,
        month,
        monthrange(year, month)[1],
    )

    holidays = holiday_dates(
        db,
        sid,
        start,
        end,
    )

    strength_map = get_strength_map(
        db,
        sid,
    )

    return {
        "year": year,
        "month": month,
        "classwise": summary_query(
            db,
            sid,
            start,
            end,
            holidays=holidays,
            strength_map=strength_map,
        ),
        "studentwise": studentwise_query(
            db,
            sid,
            start,
            end,
            search,
            class_name,
            holidays=holidays,
        ),
        "matrix": monthly_matrix(
            db,
            sid,
            year,
            month,
            holidays=holidays,
        ),
    }


@router.get("/yearly")
def yearly(
    year: int,
    search: str | None = None,
    class_name: str | None = None,
    u=Depends(require_school_user),
    db: Session = Depends(get_db),
):
    sid = u.school_id

    # Academic year: April of `year` through March of `year + 1`
    start = date(year, 4, 1)
    end = date(year + 1, 3, 31)

    holidays = holiday_dates(
        db,
        sid,
        start,
        end,
    )

    strength_map = get_strength_map(
        db,
        sid,
    )

    return {
        "year": year,
        "classwise": summary_query(
            db,
            sid,
            start,
            end,
            holidays=holidays,
            strength_map=strength_map,
        ),
        "studentwise": studentwise_query(
            db,
            sid,
            start,
            end,
            search,
            class_name,
            holidays=holidays,
        ),
        "matrix": yearly_matrix(
            db,
            sid,
            year,
            holidays=holidays,
        ),
    }