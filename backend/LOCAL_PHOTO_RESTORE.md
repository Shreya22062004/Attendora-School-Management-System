# Local student-photo restoration

`migrate_local_student_photos.py` is deliberately dry-run by default. It does
not upload, delete, rename, or modify local files without `--apply`.

## Matching rules

- `123.jpg` is matched only to student database ID `123`.
- A non-numeric file name is matched only when it exactly and uniquely matches
  an admission number or PEN number after normalizing punctuation and case.
- Ambiguous or unmatched files are reported, never assigned by name guessing.
- Records that already have a Cloudinary metadata reference are skipped unless
  `--restore-existing-reference` is explicitly supplied after reviewing the
  dry-run report.

## Dry run

From the project root in PowerShell:

```powershell
cd backend
python migrate_local_student_photos.py --photos-dir "C:\Users\KIIT0001\Desktop\ID CARD PHOTOS" --report ..\tmp\photo-restore-dry-run.json
```

Review the JSON report before any upload. The script does not read or clear
legacy `students.photo_data` image bytes.

## Small approved test batch

After checking the dry-run report and configuring Cloudinary environment
variables on the machine running the script:

```powershell
python migrate_local_student_photos.py --photos-dir "C:\Users\KIIT0001\Desktop\ID CARD PHOTOS" --apply --limit 3 --report ..\tmp\photo-restore-test.json
```

Use `--restore-existing-reference` only when a reviewed report shows that the
database contains a stale Cloudinary reference for a deleted asset and you
explicitly want to restore that exact student's file. No mode deletes local
files, database BYTEA values, or Cloudinary assets.
