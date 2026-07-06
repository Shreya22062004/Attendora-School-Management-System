import os
from datetime import datetime,timedelta
from fastapi import Depends,HTTPException
from fastapi.security import HTTPBearer,HTTPAuthorizationCredentials
from jose import jwt,JWTError
from sqlalchemy.orm import Session
from .database import get_db
from .models import User
SECRET_KEY=os.getenv('SECRET_KEY','change-this-secret-before-production');ALGORITHM='HS256';security=HTTPBearer()
def create_token(user): return jwt.encode({'sub':user.username,'user_id':user.id,'school_id':user.school_id,'role':user.role,'exp':datetime.utcnow()+timedelta(hours=12)},SECRET_KEY,algorithm=ALGORITHM)
def get_current_user(credentials:HTTPAuthorizationCredentials=Depends(security),db:Session=Depends(get_db)):
 try:
  payload=jwt.decode(credentials.credentials,SECRET_KEY,algorithms=[ALGORITHM]); uid=payload.get('user_id'); user=db.get(User,uid) if uid else None
  if not user: raise ValueError()
  return user
 except (JWTError,ValueError): raise HTTPException(401,'Invalid or expired login. Please sign in again.')
def require_school_user(user:User=Depends(get_current_user)):
 if not user.school_id: raise HTTPException(403,'This account is not assigned to a school')
 return user
