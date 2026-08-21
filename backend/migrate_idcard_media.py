"""Safely copy legacy Neon BYTEA ID-card media to Cloudinary.

Default mode is a dry run. With --apply, each Cloudinary upload must return a
public ID and secure URL before its Neon metadata is committed. BYTEA values
are always retained as a fallback by this tool.
"""
import argparse

from app import models
from app.database import SessionLocal
from app.media_storage import MediaStorageError, put_bytes


def key(school_id, category, owner_id):
    return f"attendora/students/{owner_id}" if category == "students" else f"attendora/signatures/{school_id}_headmaster"


def migrate_student(db, student, apply):
    if student.photo_storage_key:
        return "already_migrated"
    storage_key = key(student.school_id, "students", student.id)
    if not apply:
        print(f"DRY RUN student {student.id} -> {storage_key}")
        return "ready"
    try:
        public_id, secure_url = put_bytes(storage_key, student.photo_data, student.photo_mime_type or "image/jpeg")
        student.photo_storage_key = public_id
        student.photo_storage_url = secure_url
        db.commit()
        return "uploaded"
    except (MediaStorageError, Exception) as error:
        db.rollback()
        print(f"FAILED student {student.id}: {error}")
        return "failed"


def migrate_signature(db, school, apply):
    if school.headmaster_signature_key:
        return "already_migrated"
    storage_key = key(school.id, "signatures", school.id)
    if not apply:
        print(f"DRY RUN school signature {school.id} -> {storage_key}")
        return "ready"
    try:
        public_id, secure_url = put_bytes(storage_key, school.headmaster_signature_data, school.headmaster_signature_mime_type or "image/png")
        school.headmaster_signature_key = public_id
        school.headmaster_signature_url = secure_url
        db.commit()
        return "uploaded"
    except (MediaStorageError, Exception) as error:
        db.rollback()
        print(f"FAILED school signature {school.id}: {error}")
        return "failed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="upload objects and save verified storage keys")
    args = parser.parse_args()
    db = SessionLocal()
    summary = {"total": 0, "already_migrated": 0, "uploaded": 0, "failed": 0, "skipped_no_media": 0}
    try:
        # Stream one BYTEA-bearing record at a time; metadata is committed only
        # after Cloudinary has returned a verified public ID and secure URL.
        for student in db.query(models.Student).filter(models.Student.photo_data.isnot(None)).yield_per(1):
            summary["total"] += 1
            result = migrate_student(db, student, args.apply)
            if result != "ready":
                summary[result] += 1
        for school in db.query(models.School).filter(models.School.headmaster_signature_data.isnot(None)).yield_per(1):
            summary["total"] += 1
            result = migrate_signature(db, school, args.apply)
            if result != "ready":
                summary[result] += 1
        summary["skipped_no_media"] = (
            db.query(models.Student.id).filter(models.Student.photo_storage_key.is_(None), models.Student.photo_data.is_(None)).count()
            + db.query(models.School.id).filter(models.School.headmaster_signature_key.is_(None), models.School.headmaster_signature_data.is_(None)).count()
        )
        print("complete: " + ", ".join(f"{name}={value}" for name, value in summary.items()))
    finally:
        db.close()


if __name__ == "__main__":
    main()
