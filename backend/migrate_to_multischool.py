"""Run ONCE against an existing single-school PostgreSQL database BEFORE starting the new backend."""
import os
from sqlalchemy import create_engine,text
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parents[1]/'.env');url=os.getenv('DATABASE_URL')
if not url or not url.startswith('postgresql'):raise SystemExit('DATABASE_URL must point to PostgreSQL')
engine=create_engine(url)
statements=[
"CREATE TABLE IF NOT EXISTS schools (id SERIAL PRIMARY KEY, school_name VARCHAR NOT NULL, address VARCHAR NOT NULL, udise_code VARCHAR NOT NULL UNIQUE, is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
"INSERT INTO schools(school_name,address,udise_code) SELECT school_name,address,udise_code FROM school_settings WHERE NOT EXISTS (SELECT 1 FROM schools)",
"ALTER TABLE users ADD COLUMN IF NOT EXISTS school_id INTEGER", "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR DEFAULT 'teacher'", "ALTER TABLE students ADD COLUMN IF NOT EXISTS school_id INTEGER", "ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS school_id INTEGER", "ALTER TABLE school_calendar ADD COLUMN IF NOT EXISTS school_id INTEGER",
"UPDATE users SET school_id=(SELECT id FROM schools ORDER BY id LIMIT 1) WHERE school_id IS NULL", "UPDATE students SET school_id=(SELECT id FROM schools ORDER BY id LIMIT 1) WHERE school_id IS NULL", "UPDATE attendance_sessions SET school_id=(SELECT id FROM schools ORDER BY id LIMIT 1) WHERE school_id IS NULL", "UPDATE school_calendar SET school_id=(SELECT id FROM schools ORDER BY id LIMIT 1) WHERE school_id IS NULL", "UPDATE users SET role='super_admin' WHERE username='admin'",
"ALTER TABLE students DROP CONSTRAINT IF EXISTS students_admission_no_key", "ALTER TABLE students DROP CONSTRAINT IF EXISTS students_pen_number_key", "DROP INDEX IF EXISTS ix_students_pen_number", "ALTER TABLE attendance_sessions DROP CONSTRAINT IF EXISTS uq_class_date_session", "ALTER TABLE school_calendar DROP CONSTRAINT IF EXISTS school_calendar_calendar_date_key",
"CREATE UNIQUE INDEX IF NOT EXISTS uq_student_school_admission ON students(school_id,admission_no) WHERE admission_no IS NOT NULL", "CREATE UNIQUE INDEX IF NOT EXISTS uq_student_school_pen ON students(school_id,pen_number) WHERE pen_number IS NOT NULL", "CREATE UNIQUE INDEX IF NOT EXISTS uq_school_class_date_session_idx ON attendance_sessions(school_id,class_name,attendance_date)", "CREATE UNIQUE INDEX IF NOT EXISTS uq_school_calendar_date_idx ON school_calendar(school_id,calendar_date)"
]
with engine.begin() as c:
 for s in statements:c.execute(text(s))
print('MULTI-SCHOOL MIGRATION COMPLETE')
