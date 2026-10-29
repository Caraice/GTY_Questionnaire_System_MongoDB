"""MongoDB validators and indexes. Run repeatedly without dropping data.

Validators apply to new collections. Existing validators require explicit,
reviewed migrations rather than silent replacement at application startup.
"""
from pymongo import ASCENDING, DESCENDING


def object_schema(required, properties):
    return {"$jsonSchema": {"bsonType": "object", "required": required, "properties": properties}}


SCHEMAS = {
    "app_settings": object_schema(
        ["_id", "schema_version", "secret"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "secret": {"bsonType": "string", "minLength": 64}},
    ),
    "users": object_schema(
        ["_id", "schema_version", "username", "password_hash", "created_at"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "username": {"bsonType": "string"}, "password_hash": {"bsonType": "string"},
         "created_at": {"bsonType": "date"}},
    ),
    "sessions": object_schema(
        ["_id", "schema_version", "user_id", "token_hash", "created_at", "expires_at"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "user_id": {"bsonType": "string"}, "token_hash": {"bsonType": "string"},
         "created_at": {"bsonType": "date"}, "expires_at": {"bsonType": "date"}},
    ),
    "surveys": object_schema(
        ["_id", "schema_version", "version", "owner_id", "share_id", "title", "description",
         "allow_anonymous", "allow_multiple", "deadline", "status", "questions", "rules", "created_at", "updated_at"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "version": {"bsonType": "int", "minimum": 1}, "owner_id": {"bsonType": "string"},
         "share_id": {"bsonType": "string"}, "title": {"bsonType": "string", "minLength": 1},
         "description": {"bsonType": "string"}, "allow_anonymous": {"bsonType": "bool"},
         "allow_multiple": {"bsonType": "bool"}, "deadline": {"bsonType": ["date", "null"]},
         "status": {"enum": ["draft", "published", "closed"]},
         "questions": {"bsonType": "array", "items": {"bsonType": "object"}},
         "rules": {"bsonType": "array", "items": {"bsonType": "object"}},
         "created_at": {"bsonType": "date"}, "updated_at": {"bsonType": "date"}},
    ),
    "responses": object_schema(
        ["_id", "schema_version", "survey_id", "survey_version", "respondent_key", "attempt_key",
         "submission_key", "anonymous", "answers", "path", "submitted_at"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "survey_id": {"bsonType": "string"}, "survey_version": {"bsonType": "int"},
         "respondent_key": {"bsonType": "string"}, "respondent_id": {"bsonType": ["string", "null"]},
         "attempt_key": {"bsonType": "string"}, "submission_key": {"bsonType": "string"},
         "anonymous": {"bsonType": "bool"}, "answers": {"bsonType": "object"},
         "path": {"bsonType": "array", "items": {"bsonType": "string"}}, "submitted_at": {"bsonType": "date"}},
    ),
}


def initialize(db):
    existing = set(db.list_collection_names())
    for name, validator in SCHEMAS.items():
        if name not in existing:
            db.create_collection(name, validator=validator, validationLevel="strict")
    db.users.create_index("username", unique=True, name="username_unique")
    db.sessions.create_index("token_hash", unique=True, name="token_unique")
    db.sessions.create_index("expires_at", expireAfterSeconds=0, name="session_ttl")
    db.surveys.create_index("share_id", unique=True, name="share_unique")
    db.surveys.create_index([("owner_id", ASCENDING), ("created_at", DESCENDING)], name="owner_list")
    db.responses.create_index([("survey_id", ASCENDING), ("submitted_at", DESCENDING)], name="response_stats")
    db.responses.create_index([("survey_id", ASCENDING), ("survey_version", ASCENDING),
                               ("submitted_at", ASCENDING), ("_id", ASCENDING)], name="versioned_statistics")
    db.responses.create_index([("survey_id", ASCENDING), ("respondent_key", ASCENDING), ("attempt_key", ASCENDING)],
                              unique=True, name="attempt_unique")
    db.responses.create_index([("survey_id", ASCENDING), ("respondent_key", ASCENDING), ("submission_key", ASCENDING)],
                              unique=True, name="submission_idempotency")

