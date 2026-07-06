import json
from datetime import date
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models,schemas
from ..auth import require_school_user
router=APIRouter(prefix='/attendance',tags=['Attendance'])
def allowed_classes(db,u):
 cfg=db.query(models.SchoolConfig).filter_by(school_id=u.school_id).first(); classes=json.loads(cfg.classes_json or '[]') if cfg else []
 if u.role=='teacher':
  assigned={x.class_name for x in db.query(models.TeacherAssignment).filter_by(user_id=u.id,school_id=u.school_id).all()}; classes=[c for c in classes if c in assigned]
 return classes
@router.get('/classes')
def classes(u=Depends(require_school_user),db:Session=Depends(get_db)): return {'classes':allowed_classes(db,u)}
def students_for(db,sid,cls,section=None):
 q=db.query(models.Student).filter(models.Student.school_id==sid,models.Student.class_name==cls,models.Student.is_active==True)
 if section:q=q.filter(models.Student.section==section)
 return q.all()
def validate(data,db,u):
 if data.class_name not in allowed_classes(db,u): raise HTTPException(400,'Invalid class or class not assigned to this teacher')
 students=students_for(db,u.school_id,data.class_name,data.section);valid={s.id for s in students};got={r.student_id for r in data.records}
 if not valid: raise HTTPException(400,'No active students found in this class')
 if valid!=got: raise HTTPException(400,'Attendance must include every active student in selected class')
 if any(r.status not in ('Present','Absent') for r in data.records): raise HTTPException(400,'Invalid status')

def ensure_working_day(db,sid,d):
 m=db.query(models.SchoolCalendar).filter_by(school_id=sid,calendar_date=d).first()
 if m and m.day_type!='Working Day': raise HTTPException(400,f'Attendance cannot be marked: {m.day_type}' + (f' - {m.description}' if m.description else ''))
 if d.weekday()==6 and not (m and m.day_type=='Working Day'): raise HTTPException(400,'Attendance cannot be marked on Sunday')

def active_year(db,sid,d): return db.query(models.AcademicYear).filter(models.AcademicYear.school_id==sid,models.AcademicYear.start_date<=d,models.AcademicYear.end_date>=d).order_by(models.AcademicYear.is_active.desc()).first()
@router.get('/sheet')
def sheet(class_name:str,attendance_date:date,section:str|None=None,u=Depends(require_school_user),db:Session=Depends(get_db)):
 if class_name not in allowed_classes(db,u): raise HTTPException(400,'Invalid class or class not assigned to this teacher')
 students=sorted(students_for(db,u.school_id,class_name,section),key=lambda x:(0 if x.gender=='Girl' else 1 if x.gender=='Boy' else 2,x.name.lower()));q=db.query(models.AttendanceSession).filter_by(school_id=u.school_id,class_name=class_name,attendance_date=attendance_date)
 if section:q=q.filter(models.AttendanceSession.section==section)
 sess=q.first();rows=[]
 for s in students:
  a=db.query(models.Attendance).filter_by(student_id=s.id,attendance_date=attendance_date).first();rows.append({'student_id':s.id,'name':s.name,'gender':s.gender,'section':s.section,'status':a.status if a else 'Present'})
 return {'submitted':bool(sess),'edit_count':sess.edit_count if sess else 0,'edits_remaining':max(0,6-sess.edit_count) if sess else 6,'is_locked':sess.is_locked if sess else False,'records':rows}
@router.post('/submit')
def submit(data:schemas.AttendanceSheet,u=Depends(require_school_user),db:Session=Depends(get_db)):
 ensure_working_day(db,u.school_id,data.attendance_date);validate(data,db,u);y=active_year(db,u.school_id,data.attendance_date);sess=db.query(models.AttendanceSession).filter_by(school_id=u.school_id,class_name=data.class_name,section=data.section,attendance_date=data.attendance_date).first()
 if sess: raise HTTPException(409,'Attendance already submitted. Use edit.')
 sess=models.AttendanceSession(school_id=u.school_id,academic_year_id=y.id if y else None,class_name=data.class_name,section=data.section,attendance_date=data.attendance_date,created_by=u.id);db.add(sess)
 for r in data.records: db.add(models.Attendance(student_id=r.student_id,academic_year_id=y.id if y else None,attendance_date=data.attendance_date,status=r.status))
 db.add(models.AuditLog(school_id=u.school_id,user_id=u.id,action='SUBMIT',entity_type='AttendanceSession',new_value=json.dumps({'class':data.class_name,'section':data.section,'date':str(data.attendance_date),'records':len(data.records)})));db.commit();return {'message':'Attendance submitted','edits_remaining':6}
@router.put('/edit')
def edit(data:schemas.AttendanceSheet,u=Depends(require_school_user),db:Session=Depends(get_db)):
 ensure_working_day(db,u.school_id,data.attendance_date);validate(data,db,u);sess=db.query(models.AttendanceSession).filter_by(school_id=u.school_id,class_name=data.class_name,section=data.section,attendance_date=data.attendance_date).first()
 if not sess: raise HTTPException(404,'Submit attendance first')
 if sess.is_locked or sess.edit_count>=6: raise HTTPException(403,'Attendance is locked')
 changed=0
 for r in data.records:
  a=db.query(models.Attendance).filter_by(student_id=r.student_id,attendance_date=data.attendance_date).first()
  if a and a.status!=r.status: db.add(models.AttendanceHistory(attendance_id=a.id,student_id=r.student_id,attendance_date=data.attendance_date,old_status=a.status,new_status=r.status,edited_by=u.id));a.status=r.status;changed+=1
 if not changed: raise HTTPException(400,'No attendance status was changed')
 sess.edit_count+=1;sess.is_locked=sess.edit_count>=6;db.add(models.AuditLog(school_id=u.school_id,user_id=u.id,action='EDIT',entity_type='AttendanceSession',entity_id=str(sess.id),new_value=json.dumps({'changed_records':changed})));db.commit();return {'message':'Attendance updated','changed_records':changed,'edits_remaining':max(0,6-sess.edit_count),'is_locked':sess.is_locked}
