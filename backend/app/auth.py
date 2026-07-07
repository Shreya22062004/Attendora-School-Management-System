import os
from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from .database import get_db
from .models import User

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret-before-production")
ALGORITHM = "HS256"
security = HTTPBearer()


def create_token(user):
    return jwt.encode(
        {
            "sub": user.username,
            "user_id": user.id,
            "school_id": user.school_id,
            "role": user.role,
            "exp": datetime.utcnow() + timedelta(hours=12),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


def _decode(credentials: HTTPAuthorizationCredentials):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        if not payload.get("user_id") or not payload.get("sub"):
            raise ValueError()
        return payload
    except (JWTError, ValueError):
        raise HTTPException(401, "Invalid or expired login. Please sign in again.")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    payload = _decode(credentials)
    user = db.get(User, payload["user_id"])
    if not user:
        raise HTTPException(401, "Invalid or expired login. Please sign in again.")
    return user


def require_school_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    # School routes only need the signed identity claims. Avoiding a User SELECT on
    # every request removes one remote DB round trip. Sensitive account-management
    # routes continue to use get_current_user(), which verifies the user in the DB.
    payload = _decode(credentials)
    school_id = payload.get("school_id")
    if not school_id:
        raise HTTPException(403, "This account is not assigned to a school")
    return SimpleNamespace(
        id=payload["user_id"],
        username=payload["sub"],
        school_id=school_id,
        role=payload.get("role"),
    )
