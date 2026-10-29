import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.repositories.mongo import UserRepository


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(user):
    return {"id": user["_id"], "username": user["username"], "created_at": user["created_at"]}


class AuthService:
    def __init__(self, db, session_hours=24):
        self.repo = UserRepository(db)
        self.session_hours = session_hours

    def register(self, credentials):
        user = {
            "_id": str(uuid4()), "schema_version": 1,
            "username": credentials.username.lower(),
            "password_hash": hash_password(credentials.password),
            "created_at": datetime.now(timezone.utc),
        }
        try:
            self.repo.create(user)
        except DuplicateKeyError:
            raise HTTPException(409, "用户名已存在")
        return public_user(user)

    def login(self, credentials):
        user = self.repo.by_username(credentials.username.lower())
        if not user or not verify_password(credentials.password, user["password_hash"]):
            raise HTTPException(401, "用户名或密码错误")
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        self.repo.create_session({
            "_id": str(uuid4()), "schema_version": 1,
            "token_hash": token_digest(token), "user_id": user["_id"],
            "created_at": now, "expires_at": now + timedelta(hours=self.session_hours),
        })
        return {"access_token": token, "token_type": "bearer", "user": public_user(user)}

    def current_user(self, token):
        session = self.repo.session(token_digest(token), datetime.now(timezone.utc))
        user = self.repo.by_id(session["user_id"]) if session else None
        if user is None:
            raise HTTPException(401, "登录已过期或无效")
        return user

