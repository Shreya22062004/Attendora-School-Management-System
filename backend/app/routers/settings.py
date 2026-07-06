from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import School, SchoolConfig
from ..schemas import SchoolSettingUpdate
from ..auth import require_school_user


router = APIRouter(
    prefix="/settings",
    tags=["School Settings"]
)


# ============================================================
# GET CURRENT SCHOOL SETTINGS
# ============================================================

@router.get("/school")
def get_school_settings(
    u=Depends(require_school_user),
    db: Session = Depends(get_db)
):
    school = db.get(School, u.school_id)

    if not school:
        raise HTTPException(
            status_code=404,
            detail="School not found"
        )

    return school


# ============================================================
# UPDATE CURRENT SCHOOL SETTINGS
# ============================================================

@router.put("/school")
def update_school_settings(
    data: SchoolSettingUpdate,
    u=Depends(require_school_user),
    db: Session = Depends(get_db)
):
    # Only the administrator of the school can modify
    # the school's details.
    if u.role != "school_admin":
        raise HTTPException(
            status_code=403,
            detail="School administrator access required"
        )

    school = db.get(School, u.school_id)

    if not school:
        raise HTTPException(
            status_code=404,
            detail="School not found"
        )

    # --------------------------------------------------------
    # CLEAN INPUT VALUES
    # --------------------------------------------------------

    school_name = data.school_name.strip()
    address = data.address.strip()
    udise_code = data.udise_code.strip()

    # --------------------------------------------------------
    # VALIDATE REQUIRED FIELDS
    # --------------------------------------------------------

    if not school_name:
        raise HTTPException(
            status_code=400,
            detail="School name is required"
        )

    if not address:
        raise HTTPException(
            status_code=400,
            detail="School address is required"
        )

    if not udise_code:
        raise HTTPException(
            status_code=400,
            detail="UDISE code is required"
        )

    # --------------------------------------------------------
    # CHECK WHETHER ANOTHER SCHOOL ALREADY USES THIS UDISE CODE
    # --------------------------------------------------------

    existing_school = (
        db.query(School)
        .filter(
            School.udise_code == udise_code,
            School.id != school.id
        )
        .first()
    )

    if existing_school:
        raise HTTPException(
            status_code=409,
            detail="UDISE code already belongs to another school"
        )

    # --------------------------------------------------------
    # UPDATE SCHOOL
    # --------------------------------------------------------

    school.school_name = school_name
    school.address = address
    school.udise_code = udise_code

    db.commit()
    db.refresh(school)

    return school
@router.get("/config")
def get_school_config(u=Depends(require_school_user), db: Session=Depends(get_db)):
    import json
    cfg=db.query(SchoolConfig).filter(SchoolConfig.school_id==u.school_id).first()
    default_classes=["UKG/KG2/PP1","1","2","3","4","5","6","7","8"]
    default_fields={"name":{"visible":True,"required":True},"class_name":{"visible":True,"required":True},"gender":{"visible":True,"required":True},"admission_no":{"visible":True,"required":False},"pen_number":{"visible":True,"required":False},"father_name":{"visible":True,"required":False},"mother_name":{"visible":True,"required":False},"date_of_birth":{"visible":True,"required":False},"category":{"visible":True,"required":False},"admission_date":{"visible":True,"required":False}}
    return {"classes":json.loads(cfg.classes_json) if cfg else default_classes,"fields":json.loads(cfg.fields_json) if cfg else default_fields,"dashboard_groups":json.loads(cfg.dashboard_groups_json or '[]') if cfg else []}
