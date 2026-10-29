"""Safely map and restore original local student photos to Cloudinary.

Dry-run is the default.  It never writes to PostgreSQL or Cloudinary.
Only exact student IDs (numeric file names) and unique exact admission/PEN
matches are eligible; ambiguous files are reported and never guessed.
"""

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)

from app.database import SessionLocal  # noqa: E402
from app.media_storage import MediaStorageError, optimize_student_photo, put_bytes  # noqa: E402
from app.models import Student  # noqa: E402


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def photo_candidates(photo_dir):
    return sorted(
        (
            path for path in photo_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )


def build_matchers(db):
    """Fetch only small matching metadata; never select legacy image BYTEA."""
    rows = db.query(
        Student.id,
        Student.school_id,
        Student.admission_no,
        Student.pen_number,
        Student.photo_storage_key,
        Student.photo_storage_url,
    ).all()

    by_id = {str(row.id): row for row in rows}
    by_identifier = {}
    for row in rows:
        for value in (row.admission_no, row.pen_number):
            key = normalized(value)
            if key:
                by_identifier.setdefault(key, []).append(row)
    return by_id, by_identifier


def classify(path, by_id, by_identifier):
    stem = path.stem.strip()
    if stem.isdigit():
        student = by_id.get(str(int(stem)))
        return ("matched" if student else "unmatched"), [student] if student else []

    candidates = by_identifier.get(normalized(stem), [])
    if len(candidates) == 1:
        return "matched", candidates
    if len(candidates) > 1:
        return "ambiguous", candidates
    return "unmatched", []


def report_item(path, status, candidates):
    # Keep the report operationally useful without exposing student names.
    return {
        "file": path.name,
        "status": status,
        "student_ids": [candidate.id for candidate in candidates],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photos-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="Upload only exact, safe matches")
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many matched files")
    parser.add_argument(
        "--restore-existing-reference",
        action="store_true",
        help="Allow an explicit restoration over an existing Cloudinary metadata reference",
    )
    parser.add_argument("--report", type=Path, help="Write a JSON report")
    args = parser.parse_args()

    photo_dir = args.photos_dir.expanduser().resolve()
    if not photo_dir.is_dir():
        raise SystemExit("Photo directory does not exist or is not a directory")
    if args.limit < 0:
        raise SystemExit("--limit must be zero or a positive integer")

    db = SessionLocal()
    results = []
    summary = Counter()
    processed = 0
    try:
        by_id, by_identifier = build_matchers(db)
        classified = [
            (path, *classify(path, by_id, by_identifier))
            for path in photo_candidates(photo_dir)
        ]
        matched_counts = Counter(
            candidates[0].id
            for _, status, candidates in classified
            if status == "matched"
        )

        for path, status, candidates in classified:
            if status != "matched":
                results.append(report_item(path, status, candidates))
                summary[status] += 1
                continue

            student = candidates[0]
            if matched_counts[student.id] > 1:
                # More than one source image for one student is a human-review
                # case. Do not let a later file silently overwrite the first.
                results.append(report_item(path, "ambiguous_multiple_files", candidates))
                summary["ambiguous_multiple_files"] += 1
                continue
            if args.limit and processed >= args.limit:
                results.append(report_item(path, "deferred_by_limit", candidates))
                summary["deferred_by_limit"] += 1
                continue
            processed += 1

            if student.photo_storage_key and not args.restore_existing_reference:
                results.append(report_item(path, "skipped_existing_reference", candidates))
                summary["skipped_existing_reference"] += 1
                continue

            if not args.apply:
                results.append(report_item(path, "ready", candidates))
                summary["ready"] += 1
                continue

            try:
                data, mime_type = optimize_student_photo(path.read_bytes())
                # Numeric student IDs are globally unique and provide a stable,
                # repeatable Cloudinary public ID. Existing references are only
                # replaced when the explicit restoration flag is present.
                public_id = student.photo_storage_key or f"attendora/students/{student.id}"
                last_error = None
                for attempt in range(3):
                    try:
                        cloudinary_id, secure_url = put_bytes(public_id, data, mime_type)
                        last_error = None
                        break
                    except MediaStorageError as error:
                        last_error = error
                        if attempt < 2:
                            time.sleep(2 ** attempt)
                if last_error:
                    raise last_error
                student.photo_storage_key = cloudinary_id
                student.photo_storage_url = secure_url
                student.photo_mime_type = mime_type
                db.commit()
                results.append(report_item(path, "uploaded", candidates))
                summary["uploaded"] += 1
            except Exception:
                db.rollback()
                results.append(report_item(path, "error", candidates))
                summary["error"] += 1

        payload = {
            "mode": "apply" if args.apply else "dry-run",
            "photos_dir": str(photo_dir),
            "summary": {
                "total_files": len(classified),
                "matched_candidates": sum(
                    1 for _, status, _ in classified if status == "matched"
                ),
                "statuses": dict(sorted(summary.items())),
            },
            "results": results,
        }
        print(json.dumps({"mode": payload["mode"], "summary": payload["summary"]}, indent=2))
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    finally:
        db.close()


if __name__ == "__main__":
    main()
