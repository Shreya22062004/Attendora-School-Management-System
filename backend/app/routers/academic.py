import json
from fastapi import APIRouter,Depends,HTTPException
from datetime import timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from ..database import get_db
from ..auth import require_school_user
from .. import models,schemas
router=APIRouter(prefix='/academic',tags=['Academic Years'])
def audit(db,u,action,etype,eid=None,old=None,new=None): db.add(models.AuditLog(school_id=u.school_id,user_id=u.id,action=action,entity_type=etype,entity_id=str(eid) if eid else None,old_value=json.dumps(old,default=str) if old is not None else None,new_value=json.dumps(new,default=str) if new is not None else None))
@router.get('/years')
def years(u=Depends(require_school_user),db:Session=Depends(get_db)): return db.query(models.AcademicYear).filter_by(school_id=u.school_id).order_by(models.AcademicYear.start_date.desc()).all()
@router.post('/years')
def add_year(data:schemas.AcademicYearCreate,u=Depends(require_school_user),db:Session=Depends(get_db)):
 if u.role!='school_admin': raise HTTPException(403,'School admin required')
 if data.end_date<=data.start_date: raise HTTPException(400,'End date must be after start date')
 if data.is_active: db.query(models.AcademicYear).filter_by(school_id=u.school_id).update({'is_active':False})
 y=models.AcademicYear(school_id=u.school_id,**data.model_dump());db.add(y);db.flush();audit(db,u,'CREATE','AcademicYear',y.id,new=data.model_dump());db.commit();return {'message':'Academic year created','id':y.id}
@router.put('/years/{year_id}/activate')
def activate(year_id:int,u=Depends(require_school_user),db:Session=Depends(get_db)):
 if u.role!='school_admin': raise HTTPException(403,'School admin required')
 y=db.query(models.AcademicYear).filter_by(id=year_id,school_id=u.school_id).first()
 if not y: raise HTTPException(404,'Academic year not found')
 db.query(models.AcademicYear).filter_by(school_id=u.school_id).update({'is_active':False});y.is_active=True;audit(db,u,'ACTIVATE','AcademicYear',y.id);db.commit();return {'message':'Academic year activated'}
@router.post('/promote')
def promote(data:schemas.PromotionRequest,u=Depends(require_school_user),db:Session=Depends(get_db)):
 if u.role!='school_admin': raise HTTPException(403,'School admin required')
 source=db.query(models.AcademicYear).filter_by(id=data.from_academic_year_id,school_id=u.school_id).first()
 target=db.query(models.AcademicYear).filter_by(id=data.to_academic_year_id,school_id=u.school_id).first()
 if not source or not target: raise HTTPException(400,'Source or target academic year invalid')
 cfg=db.query(models.SchoolConfig).filter_by(school_id=u.school_id).first()
 classes=json.loads(cfg.classes_json or '[]') if cfg else []
 if not classes: raise HTTPException(400,'Configure school classes before promotion')
 promoted=graduated=skipped=0
 # Server decides the next class from this school's configured order. Client cannot invent Class 11.
 for p in data.promotions:
  sid=int(p.get('student_id')); s=db.query(models.Student).filter_by(id=sid,school_id=u.school_id).first()
  if not s or not s.is_active: skipped+=1; continue
  current=s.class_name
  if current not in classes: skipped+=1; continue
  idx=classes.index(current)
  old={'class_name':s.class_name,'section':s.section,'stream':s.stream}
  if idx==len(classes)-1:
   s.is_active=False; s.exit_status='Completed'; s.exit_date=source.end_date; s.exit_reason=f'Completed highest configured class {current}'
   audit(db,u,'GRADUATE','Student',s.id,old,{'status':'Completed','academic_year':source.name}); graduated+=1; continue
  new_class=classes[idx+1]
  section=p.get('section',s.section); stream=p.get('stream',s.stream)
  s.class_name=new_class; s.section=section; s.stream=stream
  enr=db.query(models.StudentEnrollment).filter_by(student_id=s.id,academic_year_id=target.id).first()
  if not enr: db.add(models.StudentEnrollment(student_id=s.id,academic_year_id=target.id,class_name=new_class,section=section,stream=stream,status='Active'))
  else: enr.class_name=new_class; enr.section=section; enr.stream=stream; enr.status='Active'
  audit(db,u,'PROMOTE','Student',s.id,old,{'class_name':new_class,'academic_year':target.name}); promoted+=1
 db.commit();return {'message':f'{promoted} promoted, {graduated} completed school, {skipped} skipped','promoted':promoted,'graduated':graduated,'skipped':skipped}

@router.post('/promotion/revert')
def revert_promotion(
    data: schemas.PromotionRequest,
    u=Depends(require_school_user),
    db: Session = Depends(get_db)
):
    if u.role != 'school_admin':
        raise HTTPException(403, 'School admin required')

    source = db.query(models.AcademicYear).filter_by(
        id=data.from_academic_year_id,
        school_id=u.school_id
    ).first()

    target = db.query(models.AcademicYear).filter_by(
        id=data.to_academic_year_id,
        school_id=u.school_id
    ).first()

    if not source or not target:
        raise HTTPException(
            400,
            'Source or target academic year invalid'
        )

    reverted = 0
    restored = 0

    # Find promotion logs for this promotion operation.
    promotion_logs = db.query(models.AuditLog).filter(
        models.AuditLog.school_id == u.school_id,
        models.AuditLog.action == 'PROMOTE',
        models.AuditLog.entity_type == 'Student'
    ).order_by(
        models.AuditLog.id.desc()
    ).all()

    reverted_student_ids = set()

    for log in promotion_logs:
        if not log.entity_id:
            continue

        try:
            old_data = json.loads(log.old_value or '{}')
            new_data = json.loads(log.new_value or '{}')
        except Exception:
            continue

        # Only revert promotions made into selected target year
        if new_data.get('academic_year') != target.name:
            continue

        student_id = int(log.entity_id)

        if student_id in reverted_student_ids:
            continue

        student = db.query(models.Student).filter_by(
            id=student_id,
            school_id=u.school_id
        ).first()

        if not student:
            continue

        previous_class = old_data.get('class_name')

        if not previous_class:
            continue

        current_data = {
            'class_name': student.class_name,
            'section': student.section,
            'stream': student.stream
        }

        student.class_name = previous_class
        student.section = old_data.get('section')
        student.stream = old_data.get('stream')
        student.is_active = True
        student.exit_status = None
        student.exit_date = None
        student.exit_reason = None

        target_enrollment = db.query(
            models.StudentEnrollment
        ).filter_by(
            student_id=student.id,
            academic_year_id=target.id
        ).first()

        if target_enrollment:
            db.delete(target_enrollment)

        audit(
            db,
            u,
            'REVERT_PROMOTION',
            'Student',
            student.id,
            current_data,
            {
                'class_name': previous_class,
                'academic_year': source.name
            }
        )

        reverted_student_ids.add(student_id)
        reverted += 1

    # Restore students who were marked completed
    graduate_logs = db.query(models.AuditLog).filter(
        models.AuditLog.school_id == u.school_id,
        models.AuditLog.action == 'GRADUATE',
        models.AuditLog.entity_type == 'Student'
    ).order_by(
        models.AuditLog.id.desc()
    ).all()

    restored_student_ids = set()

    for log in graduate_logs:
        if not log.entity_id:
            continue

        try:
            old_data = json.loads(log.old_value or '{}')
            new_data = json.loads(log.new_value or '{}')
        except Exception:
            continue

        if new_data.get('academic_year') != source.name:
            continue

        student_id = int(log.entity_id)

        if student_id in restored_student_ids:
            continue

        student = db.query(models.Student).filter_by(
            id=student_id,
            school_id=u.school_id
        ).first()

        if not student:
            continue

        student.class_name = old_data.get(
            'class_name',
            student.class_name
        )

        student.section = old_data.get('section')
        student.stream = old_data.get('stream')

        student.is_active = True
        student.exit_status = None
        student.exit_date = None
        student.exit_reason = None

        restored_student_ids.add(student_id)
        restored += 1

    # Make previous year active again
    db.query(models.AcademicYear).filter_by(
        school_id=u.school_id
    ).update({
        'is_active': False
    })

    source.is_active = True
    target.is_active = False

    audit(
        db,
        u,
        'REVERT_PROMOTION',
        'AcademicYear',
        target.id,
        new={
            'from': target.name,
            'back_to': source.name,
            'students_reverted': reverted,
            'completed_students_restored': restored
        }
    )

    db.commit()

    return {
        'message': (
            f'Promotion reverted successfully. '
            f'{reverted} promoted students returned to {source.name}; '
            f'{restored} completed students restored. '
            f'{source.name} is now Active.'
        ),
        'reverted': reverted,
        'restored': restored,
        'active_year': source.name
    }
    
@router.get('/audit')
def logs(limit:int=200,u=Depends(require_school_user),db:Session=Depends(get_db)):
 if u.role!='school_admin': raise HTTPException(403,'School admin required')
 logs=db.query(models.AuditLog).filter_by(school_id=u.school_id).order_by(models.AuditLog.created_at.desc()).limit(min(limit,500)).all()
 out=[]
 for x in logs:
  actor=db.get(models.User,x.user_id) if x.user_id else None
  
  subject=x.entity_type
  if x.entity_type=='Student' and x.entity_id:
   st=db.get(models.Student,int(x.entity_id)); subject=st.name if st else f'Student #{x.entity_id}'
  labels={'CREATE':'Created','ACTIVATE':'Activated','PROMOTE':'Promoted','GRADUATE':'Completed school','SUBMIT':'Submitted attendance','EDIT':'Edited attendance','IMPORT':'Imported students','REPLACE_DIRECTORY':'Replaced student directory','CALENDAR_RANGE':'Updated school calendar','UPDATE_ASSIGNMENTS':'Updated teacher class assignments','REVERT_PROMOTION':'Reverted promotion'}
  description=f"{labels.get(x.action,x.action.replace('_',' ').title())}: {subject}"
  ist=ZoneInfo('Asia/Kolkata')
  dt=x.created_at.replace(tzinfo=timezone.utc).astimezone(ist) if x.created_at.tzinfo is None else x.created_at.astimezone(ist)
  out.append({'id':x.id,'created_at':dt.isoformat(),'action':x.action,'entity_type':x.entity_type,'entity_id':x.entity_id,'actor_name':actor.username if actor else 'System','actor_role':actor.role if actor else 'system','description':description})
 return out

@router.delete('/maintenance/clear-audit')
def clear_audit(confirm:str,u=Depends(require_school_user),db:Session=Depends(get_db)):
 if u.role!='school_admin': raise HTTPException(403,'School admin required')
 if confirm!='CLEAR AUDIT LOG': raise HTTPException(400,'Confirmation text must be CLEAR AUDIT LOG')
 n=db.query(models.AuditLog).filter_by(school_id=u.school_id).delete(synchronize_session=False);db.commit();return {'message':f'{n} audit log entries cleared'}

@router.delete('/maintenance/clear-attendance')
def clear_attendance(confirm:str,u=Depends(require_school_user),db:Session=Depends(get_db)):
 if u.role!='school_admin': raise HTTPException(403,'School admin required')
 if confirm!='CLEAR ATTENDANCE HISTORY': raise HTTPException(400,'Confirmation text must be CLEAR ATTENDANCE HISTORY')
 student_ids=[x[0] for x in db.query(models.Student.id).filter_by(school_id=u.school_id).all()]
 attendance_ids=[x[0] for x in db.query(models.Attendance.id).filter(models.Attendance.student_id.in_(student_ids)).all()] if student_ids else []
 if attendance_ids: db.query(models.AttendanceHistory).filter(models.AttendanceHistory.attendance_id.in_(attendance_ids)).delete(synchronize_session=False)
 if student_ids: db.query(models.Attendance).filter(models.Attendance.student_id.in_(student_ids)).delete(synchronize_session=False)
 db.query(models.AttendanceSession).filter_by(school_id=u.school_id).delete(synchronize_session=False)
 db.commit();return {'message':'Past attendance, attendance edit history, and submission sessions cleared'}
