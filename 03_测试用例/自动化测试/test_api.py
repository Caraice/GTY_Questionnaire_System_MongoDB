"""Real MongoDB tests; each run uses a new test database and retains evidence."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.getenv("RUN_MONGO_TESTS") != "1", reason="Set RUN_MONGO_TESTS=1 with real MongoDB running")]


@pytest.fixture
def client():
    settings = Settings(os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27019"), "survey_test_" + uuid4().hex)
    with TestClient(create_app(settings)) as test_client:
        yield test_client
    # No drop_database: tests never delete an existing database; see test report for name.
    print(f"\nRetained isolated MongoDB test database: {settings.mongodb_db}")


def account(client, username):
    credentials = {"username": username, "password": "Test-pass-123"}
    result = client.post("/api/auth/register", json=credentials)
    assert result.status_code == 201, result.text
    result = client.post("/api/auth/login", json=credentials)
    assert result.status_code == 200, result.text
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def test_health_and_pages(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/").status_code == 200
    assert "正在检查问卷状态" in client.get("/survey/test-link").text
    assert client.get("/openapi.json").status_code == 200


def test_auth_and_isolation(client):
    alice, bob = account(client, "Alice"), account(client, "Bob")
    assert client.post("/api/auth/register", json={"username": "ALICE", "password": "Test-pass-123"}).status_code == 409
    assert client.post("/api/auth/login", json={"username": "Alice", "password": "Wrong-pass-123"}).status_code == 401
    assert client.get("/api/auth/me", headers=alice).json()["username"] == "alice"
    assert client.get("/api/surveys").status_code == 401
    created = client.post("/api/surveys", headers=alice, json={"title": "真实 MongoDB 测试", "allow_anonymous": True})
    assert created.status_code == 201, created.text
    survey = created.json()
    assert survey["status"] == "draft" and survey["questions"] == []
    assert len(client.get("/api/surveys", headers=alice).json()) == 1
    assert client.get("/api/surveys", headers=bob).json() == []
    endpoint = "/api/surveys/" + survey["id"]
    assert client.get(endpoint, headers=alice).status_code == 200
    assert client.get(endpoint, headers=bob).status_code == 404
    assert client.post(endpoint + "/publish", headers=alice, json={"expected_version":1}).status_code == 422
    assert client.post(endpoint + "/publish", headers=bob, json={"expected_version":1}).status_code == 404
    assert client.get(endpoint, headers=alice).json()["status"] == "draft"
    assert client.post("/api/auth/logout", headers=alice).status_code == 204
    assert client.get("/api/auth/me", headers=alice).status_code == 401


def test_input_and_session_expiry(client):
    auth = account(client, "Boundary")
    for payload in [{"title": "  "}, {"title": "T", "owner_id": "fake"}, {"title": "T", "deadline": "2027-01-01T00:00:00"}, {"title": "T", "deadline": "2020-01-01T00:00:00Z"}]:
        assert client.post("/api/surveys", headers=auth, json=payload).status_code == 422
    db = client.app.state.db
    assert db.surveys.count_documents({}) == 0
    user = db.users.find_one({"username": "boundary"})
    db.sessions.update_one({"user_id": user["_id"]}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    assert client.get("/api/auth/me", headers=auth).status_code == 401


def test_schema_and_unique_indexes(client):
    from pymongo.errors import DuplicateKeyError, WriteError
    from database.schema import initialize
    db = client.app.state.db
    initialize(db)  # repeated startup does not erase records
    assert db.surveys.count_documents({}) == 0
    account(client, "Alice")
    with pytest.raises(WriteError):
        db.surveys.insert_one({"_id": "invalid"})
    user = db.users.find_one({"username": "alice"})
    user["_id"] = str(uuid4())
    with pytest.raises(DuplicateKeyError):
        db.users.insert_one(user)
    assert "session_ttl" in db.sessions.index_information()
    assert db.responses.index_information()["attempt_unique"]["unique"] is True

