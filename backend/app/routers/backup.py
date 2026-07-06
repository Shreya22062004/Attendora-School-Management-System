import os,subprocess,datetime
from fastapi import APIRouter,Depends,HTTPException
from ..auth import get_current_user
router=APIRouter(prefix='/system',tags=['System'])
@router.get('/status')
def status(u=Depends(get_current_user)):
 if u.role!='super_admin': raise HTTPException(403,'Super admin required')
 return {'status':'healthy','time':datetime.datetime.utcnow().isoformat(),'backup_strategy':'Use scheduled pg_dump in production; never store backups inside the web root.'}
@router.post('/backup')
def backup(u=Depends(get_current_user)):
 if u.role!='super_admin': raise HTTPException(403,'Super admin required')
 os.makedirs('backups',exist_ok=True);stamp=datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S');path=f'backups/school_attendance_{stamp}.dump';url=os.getenv('DATABASE_URL','').replace('postgresql+psycopg://','postgresql://')
 if not url: raise HTTPException(500,'DATABASE_URL not configured')
 try: subprocess.run(['pg_dump','--format=custom','--file',path,url],check=True,capture_output=True,text=True)
 except FileNotFoundError: raise HTTPException(500,'pg_dump not found. Install PostgreSQL client tools and add bin folder to PATH.')
 except subprocess.CalledProcessError as e: raise HTTPException(500,e.stderr[-500:])
 return {'message':'Backup created','file':path}
