import os,hashlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import Base,engine,SessionLocal
from .models import User,School,SchoolConfig
from .routers import students,attendance,reports,calendar,auth,settings,exports,backup,academic
Base.metadata.create_all(bind=engine)
# Lightweight additive migration for existing PostgreSQL databases.
# Production deployments should replace this with Alembic migrations.
from sqlalchemy import text
def additive_migrate():
 with engine.begin() as c:
  statements=[
   'ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE',
   'ALTER TABLE students ADD COLUMN IF NOT EXISTS section VARCHAR',
   'ALTER TABLE students ADD COLUMN IF NOT EXISTS stream VARCHAR',
   'ALTER TABLE students ADD COLUMN IF NOT EXISTS father_name VARCHAR',
   'ALTER TABLE students ADD COLUMN IF NOT EXISTS mother_name VARCHAR',
   'ALTER TABLE students ADD COLUMN IF NOT EXISTS date_of_birth DATE',
   'ALTER TABLE students ADD COLUMN IF NOT EXISTS exit_status VARCHAR',
   'ALTER TABLE students ADD COLUMN IF NOT EXISTS exit_date DATE',
   'ALTER TABLE students ADD COLUMN IF NOT EXISTS exit_reason VARCHAR',
   'ALTER TABLE attendance ADD COLUMN IF NOT EXISTS academic_year_id INTEGER REFERENCES academic_years(id)',
   'ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS academic_year_id INTEGER REFERENCES academic_years(id)',
   'ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS section VARCHAR',
   'ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS created_by INTEGER REFERENCES users(id)',
   'ALTER TABLE attendance_history ADD COLUMN IF NOT EXISTS edited_by INTEGER REFERENCES users(id)',
   'ALTER TABLE school_calendar ADD COLUMN IF NOT EXISTS academic_year_id INTEGER REFERENCES academic_years(id)',
   "ALTER TABLE school_configs ADD COLUMN IF NOT EXISTS dashboard_groups_json TEXT DEFAULT '[]'"
  ]
  for q in statements: c.execute(text(q))
additive_migrate()
def seed():
 db=SessionLocal()
 try:
  school=db.query(School).first()
  if not school:
   school=School(school_name='GOVERNMENT NODAL UPS KANTABANJI',address='BLOCK_TUREKELA DIST_BALANGIR ODISHA PIN_767039',udise_code='21241600701');db.add(school);db.flush()
  admin=db.query(User).filter(User.username=='admin').first()
  if not admin: db.add(User(username='admin',password_hash=hashlib.sha256('admin123'.encode()).hexdigest(),role='super_admin',school_id=None))
  else: admin.role='super_admin';admin.school_id=None
  nodal=db.query(User).filter(User.username=='nodal').first()
  if not nodal: db.add(User(username='nodal',password_hash=hashlib.sha256('nodal123'.encode()).hexdigest(),role='school_admin',school_id=school.id))
  cfg=db.query(SchoolConfig).filter(SchoolConfig.school_id==school.id).first()
  if not cfg:
   import json
   db.add(SchoolConfig(school_id=school.id,classes_json=json.dumps(['UKG/KG2/PP1','1','2','3','4','5','6','7','8']),fields_json=json.dumps({'name':{'visible':True,'required':True},'class_name':{'visible':True,'required':True},'gender':{'visible':True,'required':True},'admission_no':{'visible':True,'required':False},'pen_number':{'visible':True,'required':False},'father_name':{'visible':True,'required':False},'mother_name':{'visible':True,'required':False},'date_of_birth':{'visible':True,'required':False},'category':{'visible':True,'required':False},'admission_date':{'visible':True,'required':False}})))
  db.commit()
 finally:db.close()
# seed();
app=FastAPI(title='Multi-School Attendance System');origins=['http://localhost:5173','http://127.0.0.1:5173'];origins += [x.strip() for x in os.getenv('CORS_ORIGINS','').split(',') if x.strip()];app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=True,allow_methods=['*'],allow_headers=['*'])
for r in [auth.router,settings.router,students.router,attendance.router,reports.router,calendar.router,exports.router,backup.router,academic.router]:app.include_router(r)
@app.get('/')
def root():return {'message':'Multi-School Attendance API is running'}
@app.get('/health')
def health():return {'status':'ok','database':'connected'}
