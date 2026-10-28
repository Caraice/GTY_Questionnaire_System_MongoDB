from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import get_db
from app.services.auth import AuthService

bearer = HTTPBearer(auto_error=False)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db=Depends(get_db)):
    if credentials is None:
        raise HTTPException(401, "请先登录")
    return AuthService(db).current_user(credentials.credentials)

