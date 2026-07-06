from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import or_,func
from typing import List
from ..database import get_db
from .. import models,schemas
from ..auth import require_school_user
router=APIRouter(prefix='/students',tags=['Students'])
def clean(v): return v.strip() if v and v.strip() else None
def validate(data,db,sid,student_id=None):
 if not data.name.strip(): raise HTTPException(400,'Invalid student data')
 cfg=db.query(models.SchoolConfig).filter(models.SchoolConfig.school_id==sid).first()
 if cfg:
  import json
  allowed=json.loads(cfg.classes_json or '[]')
  if allowed and data.class_name not in allowed: raise HTTPException(400,'Class is not configured for this school')
 g=data.gender.strip().title();
 if g not in ('Boy','Girl'): raise HTTPException(400,'Gender must be Boy or Girl')
 adm,pen=clean(data.admission_no),clean(data.pen_number)
 for col,val,msg in [(models.Student.admission_no,adm,'Admission number already exists'),(models.Student.pen_number,pen,'PEN number already exists')]:
  if val:
   q=db.query(models.Student).filter(models.Student.school_id==sid,col==val)
   if student_id:q=q.filter(models.Student.id!=student_id)
   if q.first():raise HTTPException(400,msg)
 return g,adm,pen
@router.get('',response_model=List[schemas.StudentOut])
def list_students(class_name:str|None=None,active_only:bool=True,search:str|None=None,u=Depends(require_school_user),db:Session=Depends(get_db)):
 q=db.query(models.Student).filter(models.Student.school_id==u.school_id)
 if class_name:q=q.filter(models.Student.class_name==class_name)
 if active_only:q=q.filter(models.Student.is_active==True)
 if search and search.strip():
  t=f'%{search.strip()}%';q=q.filter(or_(models.Student.name.ilike(t),models.Student.admission_no.ilike(t),models.Student.pen_number.ilike(t),models.Student.category.ilike(t),models.Student.father_name.ilike(t),models.Student.mother_name.ilike(t)))
 order={'UKG/KG2/PP1':0,**{str(i):i for i in range(1,13)}};gender_order={'Girl':0,'Boy':1};return sorted(q.all(),key=lambda s:(order.get(s.class_name,99),gender_order.get(s.gender,2),s.name.lower()))
@router.post('',response_model=schemas.StudentOut)
def add(data:schemas.StudentCreate,u=Depends(require_school_user),db:Session=Depends(get_db)):
 g,a,p=validate(data,db,u.school_id);s=models.Student(school_id=u.school_id,name=data.name.strip(),class_name=data.class_name,section=data.section,stream=data.stream,gender=g,admission_no=a,pen_number=p,father_name=clean(data.father_name),mother_name=clean(data.mother_name),date_of_birth=data.date_of_birth,category=clean(data.category),admission_date=data.admission_date,is_active=True);db.add(s);db.commit();db.refresh(s);return s
@router.put('/{student_id}',response_model=schemas.StudentOut)
def update(student_id:int,data:schemas.StudentUpdate,u=Depends(require_school_user),db:Session=Depends(get_db)):
 s=db.query(models.Student).filter(models.Student.id==student_id,models.Student.school_id==u.school_id).first()
 if not s:raise HTTPException(404,'Student not found')
 g,a,p=validate(data,db,u.school_id,student_id);s.name=data.name.strip();s.class_name=data.class_name;s.section=data.section;s.stream=data.stream;s.gender=g;s.admission_no=a;s.pen_number=p;s.father_name=clean(data.father_name);s.mother_name=clean(data.mother_name);s.date_of_birth=data.date_of_birth;s.category=clean(data.category);s.admission_date=data.admission_date;s.is_active=data.is_active;db.commit();db.refresh(s);return s
@router.delete('/{student_id}')
def delete(student_id:int,u=Depends(require_school_user),db:Session=Depends(get_db)):
 s=db.query(models.Student).filter(models.Student.id==student_id,models.Student.school_id==u.school_id).first()
 if not s:raise HTTPException(404,'Student not found')
 s.is_active=False;db.commit();return {'message':'Student deactivated'}
@router.get('/stats/categories')
def stats(u=Depends(require_school_user),db:Session=Depends(get_db)):
 rows=db.query(models.Student.category,func.count(models.Student.id)).filter(models.Student.school_id==u.school_id,models.Student.is_active==True).group_by(models.Student.category).all(); merged={}
 for c,n in rows: label=(c or 'Unspecified').strip().upper() or 'Unspecified';merged[label]=merged.get(label,0)+n
 cats=[{'category':k,'count':v} for k,v in sorted(merged.items())];return {'categories':cats,'grand_total':sum(x['count'] for x in cats)}

from fastapi import UploadFile,File
import pandas as pd, io, json
@router.post('/import')
async def import_students(mode:str='merge',file:UploadFile=File(...),u=Depends(require_school_user),db:Session=Depends(get_db)):
 if mode not in ('merge','replace'): raise HTTPException(400,'mode must be merge or replace')
 if u.role!='school_admin': raise HTTPException(403,'School admin required for replacement import')
 name=(file.filename or '').lower()
 if not name.endswith(('.xlsx','.xls','.csv')): raise HTTPException(400,'Only .xlsx, .xls and .csv files are supported')
 raw=await file.read()
 try:
  if name.endswith('.csv'): df=pd.read_csv(io.BytesIO(raw))
  else:
   probe=pd.read_excel(io.BytesIO(raw),header=None)
   header_idx=0
   for i in range(min(30,len(probe))):
    vals=[str(x).strip().lower() for x in probe.iloc[i].tolist() if not pd.isna(x)]
    joined=' | '.join(vals)
    if ('name' in joined or 'student' in joined) and ('gender' in joined or 'sex' in joined): header_idx=i; break
   df=pd.read_excel(io.BytesIO(raw),header=header_idx)
 except Exception as e: raise HTTPException(400,f'Could not read file: {e}')
 def norm(c): return ' '.join(str(c).strip().lower().replace('_',' ').replace('.',' ').split())
 aliases={'student name':'name','name':'name','name of the student':'name','student':'name','class':'class_name','class name':'class_name','sl no':'class_name','slno':'class_name','gender':'gender','sex':'gender','section':'section','stream':'stream','admission no':'admission_no','admission number':'admission_no','pen number':'pen_number','pen':'pen_number','student pen':'pen_number','father name':'father_name','father s name':'father_name',"father's name":'father_name','mother name':'mother_name','mother s name':'mother_name',"mother's name":'mother_name','date of birth':'date_of_birth','dob':'date_of_birth','birth date':'date_of_birth','birthdate':'date_of_birth','social category':'category','category':'category','admission date':'admission_date'}
 df.columns=[aliases.get(norm(c),norm(c).replace(' ','_')) for c in df.columns]
 if 'class_name' not in df.columns and len(df.columns)>=3 and 'name' in df.columns and 'gender' in df.columns: df=df.rename(columns={df.columns[0]:'class_name'})
 missing=[x for x in ('name','class_name','gender') if x not in df.columns]
 if missing: raise HTTPException(400,'Missing required columns: '+', '.join(missing)+'. Detected columns: '+', '.join(map(str,df.columns)))
 roman={'I':'1','II':'2','III':'3','IV':'4','V':'5','VI':'6','VII':'7','VIII':'8','IX':'9','X':'10','XI':'11','XII':'12'}
 gender_map={'male':'Boy','m':'Boy','boy':'Boy','female':'Girl','f':'Girl','girl':'Girl'}
 prepared=[];errors=[]
 for idx,row in df.iterrows():
  try:
   def val(k):
    v=row.get(k,None);return None if pd.isna(v) else str(v).strip()
   cls=val('class_name') or ''; cls=roman.get(cls.upper(),cls)
   gen=gender_map.get((val('gender') or '').lower(),(val('gender') or '').title())
   cat=val('category')
   if cat and '-' in cat and cat.split('-',1)[0].strip().isdigit(): cat=cat.split('-',1)[1].strip()
   data=schemas.StudentCreate(name=val('name') or '',class_name=cls,gender=gen,section=val('section'),stream=val('stream'),admission_no=val('admission_no'),pen_number=val('pen_number'),father_name=val('father_name'),mother_name=val('mother_name'),date_of_birth=pd.to_datetime(row.get('date_of_birth')).date() if 'date_of_birth' in df.columns and not pd.isna(row.get('date_of_birth')) else None,category=cat,admission_date=pd.to_datetime(row.get('admission_date')).date() if 'admission_date' in df.columns and not pd.isna(row.get('admission_date')) else None)
   g,a,p=validate(data,db,u.school_id); prepared.append((data,g,a,p))
  except Exception as e: errors.append({'row':int(idx)+2,'error':str(e.detail if isinstance(e,HTTPException) else e)})
 if errors: raise HTTPException(400,{'message':'Import cancelled. Fix the invalid rows; existing directory was not changed.','errors':errors[:50]})
 # Two safe modes:
 # merge   = add new admissions to the current promoted directory; never deactivate students missing from the file.
 # replace = uploaded file becomes the active directory, but historical student rows are reused where safely matched.
 if mode=='replace':
  db.query(models.Student).filter_by(school_id=u.school_id,is_active=True).update({'is_active':False},synchronize_session=False)
 added=updated=unchanged=0
 consumed_ids=set()
 for data,g,a,pn in prepared:
  q=db.query(models.Student).filter(models.Student.school_id==u.school_id)
  existing=None
  if a: existing=q.filter(models.Student.admission_no==a).first()
  if not existing and pn: existing=q.filter(models.Student.pen_number==pn).first()
  # Without a unique identifier, match one row at a time by name + gender + class.
  # Consuming each match once preserves genuine same-name students instead of collapsing them.
  if not existing:
   candidates=q.filter(func.lower(models.Student.name)==data.name.strip().lower(),models.Student.gender==g,models.Student.class_name==data.class_name).order_by(models.Student.is_active.desc(),models.Student.id.asc()).all()
   existing=next((x for x in candidates if x.id not in consumed_ids),None)
  if existing and existing.id not in consumed_ids:
   consumed_ids.add(existing.id)
   existing.name=data.name.strip();existing.class_name=data.class_name;existing.section=data.section;existing.stream=data.stream;existing.gender=g;existing.admission_no=a;existing.pen_number=pn;existing.father_name=clean(data.father_name);existing.mother_name=clean(data.mother_name);existing.date_of_birth=data.date_of_birth;existing.category=clean(data.category);existing.admission_date=data.admission_date;existing.is_active=True;existing.exit_status=None;existing.exit_date=None;existing.exit_reason=None;updated+=1
  else:
   obj=models.Student(school_id=u.school_id,name=data.name.strip(),class_name=data.class_name,section=data.section,stream=data.stream,gender=g,admission_no=a,pen_number=pn,father_name=clean(data.father_name),mother_name=clean(data.mother_name),date_of_birth=data.date_of_birth,category=clean(data.category),admission_date=data.admission_date,is_active=True)
   db.add(obj);db.flush();consumed_ids.add(obj.id);added+=1
 action='MERGE_IMPORT' if mode=='merge' else 'REPLACE_DIRECTORY'
 db.add(models.AuditLog(school_id=u.school_id,user_id=u.id,action=action,entity_type='StudentDirectory',new_value=json.dumps({'file':file.filename,'mode':mode,'rows':len(prepared),'added':added,'updated':updated})))
 db.commit()
 verb='merged into' if mode=='merge' else 'replaced'
 return {'message':f'{len(prepared)} spreadsheet rows {verb} the student directory successfully ({added} new, {updated} matched/updated). Genuine same-name students are preserved.','mode':mode,'added':added,'updated':updated,'rows_processed':len(prepared)}
