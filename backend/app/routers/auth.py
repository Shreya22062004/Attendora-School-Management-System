import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    User,
    School,
    SchoolConfig,
    TeacherAssignment,
    AuditLog,
)
from ..schemas import (
    LoginRequest,
    PasswordChangeRequest,
    UserCreateRequest,
    SchoolCreateConfiguredRequest,
    SchoolUpdateConfiguredRequest,
    ResetPasswordRequest,
)
from ..auth import create_token, get_current_user


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


def hp(v):
    return hashlib.sha256(v.encode()).hexdigest()


DEFAULT_FIELDS = {
    "name": {
        "visible": True,
        "required": True,
    },
    "class_name": {
        "visible": True,
        "required": True,
    },
    "gender": {
        "visible": True,
        "required": True,
    },
    "admission_no": {
        "visible": True,
        "required": False,
    },
    "pen_number": {
        "visible": True,
        "required": False,
    },
    "father_name": {
        "visible": True,
        "required": False,
    },
    "mother_name": {
        "visible": True,
        "required": False,
    },
    "date_of_birth": {
        "visible": True,
        "required": False,
    },
    "category": {
        "visible": True,
        "required": False,
    },
    "admission_date": {
        "visible": True,
        "required": False,
    },
}


def clean_classes(values):
    out = []

    for x in values:
        x = x.strip()

        if x and x not in out:
            out.append(x)

    return out


def school_payload(s, db):
    cfg = (
        db.query(SchoolConfig)
        .filter(SchoolConfig.school_id == s.id)
        .first()
    )

    admin = (
        db.query(User)
        .filter(
            User.school_id == s.id,
            User.role == "school_admin",
        )
        .first()
    )

    return {
        "id": s.id,
        "school_name": s.school_name,
        "address": s.address,
        "udise_code": s.udise_code,
        "is_active": s.is_active,
        "admin_username": admin.username if admin else None,
        "classes": (
            json.loads(cfg.classes_json)
            if cfg
            else []
        ),
        "fields": (
            json.loads(cfg.fields_json)
            if cfg
            else DEFAULT_FIELDS
        ),
        "dashboard_groups": (
            json.loads(cfg.dashboard_groups_json or "[]")
            if cfg
            else []
        ),
    }


@router.post("/login")
def login(
    data: LoginRequest,
    db: Session = Depends(get_db),
):
    u = (
        db.query(User)
        .filter(User.username == data.username.strip())
        .first()
    )

    if not u or hp(data.password) != u.password_hash:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
        )

    s = (
        db.get(School, u.school_id)
        if u.school_id
        else None
    )

    return {
        "access_token": create_token(u),
        "token_type": "bearer",
        "username": u.username,
        "role": u.role,
        "school_id": u.school_id,
        "school_name": (
            s.school_name
            if s
            else None
        ),
    }


@router.get("/me")
def me(
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = (
        db.get(School, u.school_id)
        if u.school_id
        else None
    )

    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "school_id": u.school_id,
        "school_name": (
            s.school_name
            if s
            else None
        ),
    }


@router.post("/change-password")
def change(
    data: PasswordChangeRequest,
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if hp(data.current_password) != u.password_hash:
        raise HTTPException(
            status_code=400,
            detail="Current password is incorrect",
        )

    if len(data.new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 8 characters",
        )

    u.password_hash = hp(data.new_password)

    db.commit()

    return {
        "message": "Password changed successfully. Please sign in again."
    }


@router.post("/users")
def create_user(
    data: UserCreateRequest,
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if u.role not in ("super_admin", "school_admin"):
        raise HTTPException(
            status_code=403,
            detail="Administrator access required",
        )

    existing = (
        db.query(User)
        .filter(User.username == data.username.strip())
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Username already exists",
        )

    sid = (
        data.school_id
        if u.role == "super_admin"
        else u.school_id
    )

    if not sid or not db.get(School, sid):
        raise HTTPException(
            status_code=400,
            detail="Valid school required",
        )

    role = (
        data.role
        if u.role == "super_admin"
        else "teacher"
    )

    new_user = User(
        username=data.username.strip(),
        password_hash=hp(data.password),
        role=role,
        school_id=sid,
        must_change_password=True,
    )

    db.add(new_user)
    db.flush()

    for cls in data.classes:
        db.add(
            TeacherAssignment(
                user_id=new_user.id,
                school_id=sid,
                class_name=cls,
                section=data.section,
            )
        )

    db.add(
        AuditLog(
            school_id=sid,
            user_id=u.id,
            action="CREATE",
            entity_type="User",
            entity_id=str(new_user.id),
            new_value=json.dumps(
                {
                    "username": new_user.username,
                    "role": role,
                    "classes": data.classes,
                }
            ),
        )
    )

    db.commit()

    return {
        "message": "Account created with class assignments"
    }


@router.post("/schools")
def create_school(
    data: SchoolCreateConfiguredRequest,
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if u.role != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Super admin access required",
        )

    existing_school = (
        db.query(School)
        .filter(
            School.udise_code
            == data.udise_code.strip()
        )
        .first()
    )

    if existing_school:
        raise HTTPException(
            status_code=409,
            detail=(
                "UDISE code already exists. "
                "Use Edit on the existing school."
            ),
        )

    existing_admin = (
        db.query(User)
        .filter(
            User.username
            == data.admin_username.strip()
        )
        .first()
    )

    if existing_admin:
        raise HTTPException(
            status_code=409,
            detail="Admin username already exists",
        )

    s = School(
        school_name=data.school_name.strip(),
        address=data.address.strip(),
        udise_code=data.udise_code.strip(),
    )

    db.add(s)
    db.flush()

    db.add(
        User(
            username=data.admin_username.strip(),
            password_hash=hp(data.admin_password),
            role="school_admin",
            school_id=s.id,
        )
    )

    db.add(
        SchoolConfig(
            school_id=s.id,
            classes_json=json.dumps(
                clean_classes(data.classes)
            ),
            fields_json=json.dumps(
                data.fields or DEFAULT_FIELDS
            ),
            dashboard_groups_json=json.dumps(
                data.dashboard_groups or []
            ),
        )
    )

    db.commit()

    return {
        "message": (
            "School, configuration and school admin created"
        ),
        "school_id": s.id,
    }


@router.get("/schools")
def schools(
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if u.role != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Super admin access required",
        )

    all_schools = (
        db.query(School)
        .order_by(School.school_name)
        .all()
    )

    return [
        school_payload(s, db)
        for s in all_schools
    ]


@router.put("/schools/{school_id}")
def update_school(
    school_id: int,
    data: SchoolUpdateConfiguredRequest,
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if u.role != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Super admin access required",
        )

    s = db.get(School, school_id)

    if not s:
        raise HTTPException(
            status_code=404,
            detail="School not found",
        )

    duplicate = (
        db.query(School)
        .filter(
            School.udise_code
            == data.udise_code.strip(),
            School.id != school_id,
        )
        .first()
    )

    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="UDISE code already exists",
        )

    admin = (
        db.query(User)
        .filter(
            User.school_id == school_id,
            User.role == "school_admin",
        )
        .first()
    )

    other = (
        db.query(User)
        .filter(
            User.username
            == data.admin_username.strip(),
            User.id != (
                admin.id
                if admin
                else -1
            ),
        )
        .first()
    )

    if other:
        raise HTTPException(
            status_code=409,
            detail="Admin username already exists",
        )

    s.school_name = data.school_name.strip()
    s.address = data.address.strip()
    s.udise_code = data.udise_code.strip()

    if not admin:
        if not data.admin_password:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Password required when creating "
                    "missing school admin"
                ),
            )

        admin = User(
            username=data.admin_username.strip(),
            password_hash=hp(data.admin_password),
            role="school_admin",
            school_id=school_id,
        )

        db.add(admin)

    else:
        admin.username = data.admin_username.strip()

        if data.admin_password:
            if len(data.admin_password) < 8:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "New password must be at least "
                        "8 characters"
                    ),
                )

            admin.password_hash = hp(
                data.admin_password
            )

    cfg = (
        db.query(SchoolConfig)
        .filter(
            SchoolConfig.school_id == school_id
        )
        .first()
    )

    if not cfg:
        cfg = SchoolConfig(
            school_id=school_id
        )

        db.add(cfg)

    cfg.classes_json = json.dumps(
        clean_classes(data.classes)
    )

    cfg.fields_json = json.dumps(
        data.fields or DEFAULT_FIELDS
    )

    cfg.dashboard_groups_json = json.dumps(
        data.dashboard_groups or []
    )

    db.commit()

    return {
        "message": "School configuration updated"
    }


@router.get("/teachers")
def teachers(
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if u.role != "school_admin":
        raise HTTPException(
            status_code=403,
            detail="School admin required",
        )

    out = []

    teacher_users = (
        db.query(User)
        .filter(
            User.school_id == u.school_id,
            User.role == "teacher",
        )
        .all()
    )

    for t in teacher_users:
        ass = (
            db.query(TeacherAssignment)
            .filter_by(user_id=t.id)
            .all()
        )

        out.append(
            {
                "id": t.id,
                "username": t.username,
                "assignments": [
                    {
                        "class_name": a.class_name,
                        "section": a.section,
                    }
                    for a in ass
                ],
            }
        )

    return out


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    data: ResetPasswordRequest,
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if u.role not in (
        "super_admin",
        "school_admin",
    ):
        raise HTTPException(
            status_code=403,
            detail="Administrator required",
        )

    target = db.get(User, user_id)

    if not target or (
        u.role == "school_admin"
        and target.school_id != u.school_id
    ):
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if len(data.new_password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )

    target.password_hash = hp(data.new_password)
    target.must_change_password = True

    db.add(
        AuditLog(
            school_id=target.school_id,
            user_id=u.id,
            action="RESET_PASSWORD",
            entity_type="User",
            entity_id=str(target.id),
        )
    )

    db.commit()

    return {
        "message": (
            "Temporary password set. "
            "User should change it after login."
        )
    }


@router.put("/teachers/{user_id}/assignments")
def update_teacher_assignments(
    user_id: int,
    data: UserCreateRequest,
    u: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if u.role != "school_admin":
        raise HTTPException(
            status_code=403,
            detail="School admin required",
        )

    t = (
        db.query(User)
        .filter_by(
            id=user_id,
            school_id=u.school_id,
            role="teacher",
        )
        .first()
    )

    if not t:
        raise HTTPException(
            status_code=404,
            detail="Teacher not found",
        )

    cfg = (
        db.query(SchoolConfig)
        .filter_by(
            school_id=u.school_id
        )
        .first()
    )

    allowed = (
        set(json.loads(cfg.classes_json or "[]"))
        if cfg
        else set()
    )

    requested = clean_classes(data.classes)

    if any(
        c not in allowed
        for c in requested
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "One or more classes are not "
                "configured for this school"
            ),
        )

    (
        db.query(TeacherAssignment)
        .filter_by(
            user_id=t.id,
            school_id=u.school_id,
        )
        .delete()
    )

    for cls in requested:
        db.add(
            TeacherAssignment(
                user_id=t.id,
                school_id=u.school_id,
                class_name=cls,
                section=data.section,
            )
        )

    db.add(
        AuditLog(
            school_id=u.school_id,
            user_id=u.id,
            action="UPDATE_ASSIGNMENTS",
            entity_type="Teacher",
            entity_id=str(t.id),
            new_value=json.dumps(
                {
                    "username": t.username,
                    "classes": requested,
                }
            ),
        )
    )

    db.commit()

    return {
        "message": "Teacher class assignments updated"
    }