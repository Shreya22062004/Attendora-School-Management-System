"""Safely migrate legacy BYTEA ID-card media to Cloudinary."""

import argparse

from dotenv import load_dotenv

load_dotenv("../.env", override=True)

from app import models
from app.database import SessionLocal
from app.media_storage import MediaStorageError, put_bytes


def key(school_id, category, owner_id):
    if category == "students":
        return f"attendora/students/{owner_id}"
    return f"attendora/signatures/{school_id}_headmaster"


def migrate_student(db, student, apply):
    if student.photo_storage_key:
        return "already_migrated"

    if not student.photo_data:
        return "skipped_no_media"

    storage_key = key(student.school_id, "students", student.id)

    if not apply:
        print(f"DRY RUN student {student.id} -> {storage_key}")
        return "ready"

    try:
        public_id, secure_url = put_bytes(
            storage_key,
            student.photo_data,
            student.photo_mime_type or "image/jpeg",
        )

        student.photo_storage_key = public_id
        student.photo_storage_url = secure_url

        db.commit()

        print(f"UPLOADED student {student.id} -> {public_id}")
        return "uploaded"

    except Exception as error:
        db.rollback()
        print(f"FAILED student {student.id}: {error}")
        return "failed"


def migrate_signature(db, school, apply):
    if school.headmaster_signature_key:
        return "already_migrated"

    if not school.headmaster_signature_data:
        return "skipped_no_media"

    storage_key = key(school.id, "signatures", school.id)

    if not apply:
        print(f"DRY RUN school signature {school.id} -> {storage_key}")
        return "ready"

    try:
        public_id, secure_url = put_bytes(
            storage_key,
            school.headmaster_signature_data,
            school.headmaster_signature_mime_type or "image/png",
        )

        school.headmaster_signature_key = public_id
        school.headmaster_signature_url = secure_url

        db.commit()

        print(f"UPLOADED school signature {school.id} -> {public_id}")
        return "uploaded"

    except Exception as error:
        db.rollback()
        print(f"FAILED school signature {school.id}: {error}")
        return "failed"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually upload media to Cloudinary",
    )
    args = parser.parse_args()

    db = SessionLocal()

    summary = {
        "total": 0,
        "already_migrated": 0,
        "uploaded": 0,
        "failed": 0,
        "skipped_no_media": 0,
    }

    try:
        # IMPORTANT:
        # Process one record at a time instead of .all().
        student_query = (
            db.query(models.Student)
            .filter(models.Student.photo_data.isnot(None))
            .order_by(models.Student.id)
            .yield_per(1)
        )

        for student in student_query:
            summary["total"] += 1

            result = migrate_student(
                db,
                student,
                args.apply,
            )

            if result in summary:
                summary[result] += 1

        # School signatures
        school_query = (
            db.query(models.School)
            .filter(models.School.headmaster_signature_data.isnot(None))
            .order_by(models.School.id)
            .yield_per(1)
        )

        for school in school_query:
            summary["total"] += 1

            result = migrate_signature(
                db,
                school,
                args.apply,
            )

            if result in summary:
                summary[result] += 1

        print(
            "complete: "
            + ", ".join(
                f"{name}={value}"
                for name, value in summary.items()
            )
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()