from datetime import timedelta
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import extract
from ..database import get_db
from ..auth import require_school_user
from .. import models,schemas
router=APIRouter(prefix='/calendar',tags=['Calendar'])
@router.get('')
def days(year:int,month:int,u=Depends(require_school_user),db:Session=Depends(get_db)):
 return db.query(models.SchoolCalendar).filter(models.SchoolCalendar.school_id==u.school_id,extract('year',models.SchoolCalendar.calendar_date)==year,extract('month',models.SchoolCalendar.calendar_date)==month).all()
@router.post('')
def save(data:schemas.CalendarDayCreate,u=Depends(require_school_user),db:Session=Depends(get_db)):
 if u.role!='school_admin': raise HTTPException(403,'School admin required')
 allowed={'Declared Holiday','Local Holiday','Examination','Summer Vacation','Emergency Closure','Working Day','Sunday'}
 if data.day_type not in allowed: raise HTTPException(400,'Invalid day type')
 end=data.end_date or data.start_date
 if end<data.start_date: raise HTTPException(400,'End date cannot be before start date')
 if (end-data.start_date).days>370: raise HTTPException(400,'Calendar range cannot exceed 370 days')
 d=data.start_date; count=0
 while d<=end:
  r=db.query(models.SchoolCalendar).filter_by(school_id=u.school_id,calendar_date=d).first()
  if r: r.day_type=data.day_type; r.description=data.description
  else: db.add(models.SchoolCalendar(school_id=u.school_id,calendar_date=d,day_type=data.day_type,description=data.description))
  count+=1; d+=timedelta(days=1)
 db.add(models.AuditLog(school_id=u.school_id,user_id=u.id,action='CALENDAR_RANGE',entity_type='SchoolCalendar',new_value=f'{data.start_date} to {end}: {data.day_type}'))
 db.commit();return {'message':f'Calendar saved for {count} day(s)','days_saved':count}
