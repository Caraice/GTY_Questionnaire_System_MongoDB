from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import bearer, current_user
from app.db import get_db
from app.models import Credentials
from app.services.auth import AuthService, public_user, token_digest
from app.repositories.mongo import UserRepository

router = APIRouter(prefix="/api/auth", tags=["账号"])


@router.post("/register", status_code=201)
def register(data: Credentials, db=Depends(get_db)):
    return AuthService(db).register(data)


@router.post("/login")
def login(data: Credentials, request: Request, db=Depends(get_db)):
    return AuthService(db, request.app.state.settings.session_hours).login(data)


@router.get("/me")
def me(user=Depends(current_user)):
    return public_user(user)


@router.post("/logout", status_code=204)
def logout(user=Depends(current_user), credentials=Depends(bearer), db=Depends(get_db)):
    UserRepository(db).revoke(token_digest(credentials.credentials))
    return Response(status_code=204)

