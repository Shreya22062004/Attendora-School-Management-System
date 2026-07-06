from datetime import date
from calendar import monthrange
from fastapi import APIRouter,Depends
from sqlalchemy.orm import Session
from sqlalchemy import func,case,or_
from ..database import get_db
from ..auth import require_school_user
from ..models import Student,Attendance,AttendanceSession,SchoolCalendar,SchoolConfig
router=APIRouter(prefix='/reports',tags=['Reports'])
CLASS_ORDER={'UKG/KG2/PP1':0, **{str(i):i for i in range(1,13)}}
GENDER_ORDER={'Girl':0,'Boy':1}
def student_sort_key(r):
 return (CLASS_ORDER.get(str(r['class_name']),99), GENDER_ORDER.get(r.get('gender'),2), (r.get('student_name') or '').lower())
def class_strength(db,sid,cls):
 r=db.query(func.sum(case((Student.gender=='Boy',1),else_=0)).label('boys_total'),func.sum(case((Student.gender=='Girl',1),else_=0)).label('girls_total'),func.count(Student.id).label('total_students')).filter(Student.school_id==sid,Student.is_active==True,Student.class_name==cls).first();return {'boys_total':r.boys_total or 0,'girls_total':r.girls_total or 0,'total_students':r.total_students or 0}
def holiday_dates(db,sid,start,end):
 return {r[0] for r in db.query(SchoolCalendar.calendar_date).filter(SchoolCalendar.school_id==sid,SchoolCalendar.calendar_date.between(start,end),SchoolCalendar.day_type!='Working Day').all()} | {start.fromordinal(n) for n in range(start.toordinal(),end.toordinal()+1) if start.fromordinal(n).weekday()==6 and not db.query(SchoolCalendar).filter_by(school_id=sid,calendar_date=start.fromordinal(n),day_type='Working Day').first()}
def summary_query(db,sid,start,end):
 holidays=holiday_dates(db,sid,start,end)
 rows=db.query(Student.class_name.label('class_name'),func.sum(case(((Student.gender=='Boy')&(Attendance.status=='Present'),1),else_=0)).label('boys_present'),func.sum(case(((Student.gender=='Girl')&(Attendance.status=='Present'),1),else_=0)).label('girls_present'),func.sum(case((Attendance.status=='Present',1),else_=0)).label('total_present'),func.sum(case((Attendance.status=='Absent',1),else_=0)).label('total_absent'),func.count(Attendance.id).label('total_marked')).join(Attendance,Attendance.student_id==Student.id).filter(Student.school_id==sid,Attendance.attendance_date.between(start,end),~Attendance.attendance_date.in_(holidays) if holidays else True).group_by(Student.class_name).all();out=[]
 for x in rows:d=dict(x._mapping);d.update(class_strength(db,sid,d['class_name']));out.append(d)
 return sorted(out,key=lambda r:CLASS_ORDER.get(r['class_name'],99))
def studentwise_query(db,sid,start,end,search=None,class_name=None):
 holidays=holiday_dates(db,sid,start,end); join_cond=(Attendance.student_id==Student.id)&Attendance.attendance_date.between(start,end); join_cond=join_cond & (~Attendance.attendance_date.in_(holidays) if holidays else True); q=db.query(Student.id.label('student_id'),Student.class_name.label('class_name'),Student.name.label('student_name'),Student.gender.label('gender'),func.sum(case((Attendance.status=='Present',1),else_=0)).label('present'),func.sum(case((Attendance.status=='Absent',1),else_=0)).label('absent'),func.count(Attendance.id).label('total_marked_days')).outerjoin(Attendance,join_cond).filter(Student.school_id==sid,Student.is_active==True)
 if class_name:q=q.filter(Student.class_name==class_name)
 if search and search.strip():
  t=f'%{search.strip()}%';q=q.filter(or_(Student.name.ilike(t),Student.admission_no.ilike(t),Student.pen_number.ilike(t)))
 out=[]
 for x in q.group_by(Student.id,Student.class_name,Student.name,Student.gender).all():
  d=dict(x._mapping);d['present']=d['present'] or 0;d['absent']=d['absent'] or 0;total=d['total_marked_days'] or 0;d['percentage']=round(d['present']*100/total,2) if total else 0;out.append(d)
 return sorted(out,key=student_sort_key)
def total(rows):
 keys=['boys_present','girls_present','total_present','total_absent','total_marked'];return {k:sum(r.get(k,0) or 0 for r in rows) for k in keys}
def strength_total(db,sid,classes):
 r=db.query(func.sum(case((Student.gender=='Boy',1),else_=0)).label('boys_total'),func.sum(case((Student.gender=='Girl',1),else_=0)).label('girls_total'),func.count(Student.id).label('total_students')).filter(Student.school_id==sid,Student.is_active==True,Student.class_name.in_(classes)).first();return {'boys_total':r.boys_total or 0,'girls_total':r.girls_total or 0,'total_students':r.total_students or 0}
def group(db,sid,rows,classes):
 x=total([r for r in rows if r['class_name'] in classes]);x.update(strength_total(db,sid,classes));return x
def day_status(db,sid,d):
 m=db.query(SchoolCalendar).filter_by(school_id=sid,calendar_date=d).first()
 if m and m.day_type!='Working Day':return {'day_status':'Declared Holiday','reason':m.description or m.day_type}
 if d.weekday()==6 and not m:return {'day_status':'Declared Holiday','reason':'Sunday'}
 sessions=db.query(AttendanceSession).filter_by(school_id=sid,attendance_date=d).count()
 if sessions==0:return {'day_status':'Not Marked','reason':'Attendance not submitted'}
 present=db.query(Attendance).join(Student).filter(Student.school_id==sid,Attendance.attendance_date==d,Attendance.status=='Present').count()
 return {'day_status':'Working Day','reason':'Attendance recorded'} if present else {'day_status':'Auto-detected Holiday','reason':'Attendance submitted with no student marked present'}
def monthly_matrix(db,sid,year,month):
 last=monthrange(year,month)[1];start=date(year,month,1);end=date(year,month,last);students=db.query(Student).filter(Student.school_id==sid,Student.is_active==True).all();ats=db.query(Attendance).join(Student).filter(Student.school_id==sid,Attendance.attendance_date.between(start,end)).all();holidays=holiday_dates(db,sid,start,end); mp={(a.student_id,a.attendance_date.day):('P' if a.status=='Present' else 'A') for a in ats if a.attendance_date not in holidays};out=[]
 for s in students:
  days=['H' if date(year,month,d) in holidays else mp.get((s.id,d),'-') for d in range(1,last+1)];pr=days.count('P');ab=days.count('A');mk=pr+ab;out.append({'student_id':s.id,'class_name':s.class_name,'student_name':s.name,'gender':s.gender,'days':days,'present':pr,'absent':ab,'working_days':mk,'percentage':round(pr*100/mk,2) if mk else 0})
 return sorted(out,key=student_sort_key)
def yearly_matrix(db,sid,year):
 start=date(year,1,1);end=date(year,12,31);holidays=holiday_dates(db,sid,start,end);students=db.query(Student).filter(Student.school_id==sid,Student.is_active==True).all();ats=db.query(Attendance).join(Student).filter(Student.school_id==sid,Attendance.attendance_date.between(start,end),~Attendance.attendance_date.in_(holidays) if holidays else True).all();out=[]
 for s in students:
  sr=[a for a in ats if a.student_id==s.id];months=[];yp=ya=0
  for m in range(1,13):
   mr=[a for a in sr if a.attendance_date.month==m];pr=sum(a.status=='Present' for a in mr);ab=sum(a.status=='Absent' for a in mr);mk=pr+ab;months.append(f'{pr}/{mk}' if mk else '-');yp+=pr;ya+=ab
  mk=yp+ya;out.append({'student_id':s.id,'class_name':s.class_name,'student_name':s.name,'gender':s.gender,'months':months,'present':yp,'absent':ya,'working_days':mk,'percentage':round(yp*100/mk,2) if mk else 0})
 return sorted(out,key=student_sort_key)
@router.get('/daily')
def daily(report_date:date,u=Depends(require_school_user),db:Session=Depends(get_db)):
 import json
 rows=summary_query(db,u.school_id,report_date,report_date);cfg=db.query(SchoolConfig).filter_by(school_id=u.school_id).first();classes=json.loads(cfg.classes_json or '[]') if cfg else [];groups=json.loads(cfg.dashboard_groups_json or '[]') if cfg else []
 if not groups or (len(groups)==1 and groups[0].get('name')=='All Classes'):
  groups=[{'name':'UKG/KG2/PP1','classes':[c for c in classes if c=='UKG/KG2/PP1']},{'name':'Classes 1 to 5','classes':[c for c in classes if c in ['1','2','3','4','5']]},{'name':'Classes 6 to 8','classes':[c for c in classes if c in ['6','7','8']]}]
  groups=[g for g in groups if g['classes']]
 cards=[]
 for g in groups: cards.append({'name':g.get('name','Group'),'classes':g.get('classes',[]),'summary':group(db,u.school_id,rows,g.get('classes',[]))})
 submitted={x[0] for x in db.query(AttendanceSession.class_name).filter_by(school_id=u.school_id,attendance_date=report_date).all()};completion={'submitted':len(submitted & set(classes)),'total':len(classes),'pending_classes':[c for c in classes if c not in submitted]}
 out={'date':report_date,'classwise':rows,'groups':cards,'grand_total':group(db,u.school_id,rows,classes),'completion':completion};out.update(day_status(db,u.school_id,report_date));return out
@router.get('/monthly')
def monthly(year:int,month:int,search:str|None=None,class_name:str|None=None,u=Depends(require_school_user),db:Session=Depends(get_db)):
 start=date(year,month,1);end=date(year,month,monthrange(year,month)[1]);return {'year':year,'month':month,'classwise':summary_query(db,u.school_id,start,end),'studentwise':studentwise_query(db,u.school_id,start,end,search,class_name),'matrix':monthly_matrix(db,u.school_id,year,month)}
@router.get('/yearly')
def yearly(year:int,search:str|None=None,class_name:str|None=None,u=Depends(require_school_user),db:Session=Depends(get_db)):
 start=date(year,1,1);end=date(year,12,31);return {'year':year,'classwise':summary_query(db,u.school_id,start,end),'studentwise':studentwise_query(db,u.school_id,start,end,search,class_name),'matrix':yearly_matrix(db,u.school_id,year)}
