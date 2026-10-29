"""Only this layer issues collection queries used by implemented services."""
from pymongo import ReturnDocument


class UserRepository:
    def __init__(self, db):
        self.users = db.users
        self.sessions = db.sessions

    def create(self, document):
        self.users.insert_one(document)

    def by_username(self, username):
        return self.users.find_one({"username": username})

    def by_id(self, user_id):
        return self.users.find_one({"_id": user_id})

    def create_session(self, document):
        self.sessions.insert_one(document)

    def session(self, token_hash, now):
        return self.sessions.find_one({"token_hash": token_hash, "expires_at": {"$gt": now}})

    def revoke(self, token_hash):
        self.sessions.delete_one({"token_hash": token_hash})


class SurveyRepository:
    def __init__(self, db):
        self.collection = db.surveys

    def create(self, document):
        self.collection.insert_one(document)

    def list_owned(self, owner_id):
        return list(self.collection.find({"owner_id": owner_id}).sort("created_at", -1))

    def get_owned(self, survey_id, owner_id):
        return self.collection.find_one({"_id": survey_id, "owner_id": owner_id})

    def get_by_share_id(self, share_id):
        return self.collection.find_one({"share_id": share_id})

    def get_by_id(self, survey_id):
        return self.collection.find_one({"_id": survey_id})

    def history_metadata(self):
        return list(self.collection.find({}, {"_id": 1, "title": 1, "share_id": 1}))

    def update_owned(self, survey_id, owner_id, version, status, fields, now):
        criteria = {"_id": survey_id, "owner_id": owner_id, "status": status, "version": version}
        if fields.get("status") == "published":
            # Recheck the deadline at the final conditional write, after validation.
            criteria["$or"] = [{"deadline": None}, {"deadline": {"$gt": now}}]
        return self.collection.find_one_and_update(
            criteria,
            {"$set": {**fields, "updated_at": now}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )

    def replace_questions(self, survey_id, owner_id, expected_version, questions, now, sources=None):
        # The version comparison and increment MUST be part of the same write.
        return self.collection.find_one_and_update(
            {"_id": survey_id, "owner_id": owner_id, "status": "draft", "version": expected_version},
            {"$set": {"questions": questions, "updated_at": now,
                      **({'question_sources': sources} if sources is not None else {})}, "$inc": {"version": 1}},
            return_document=ReturnDocument.AFTER,
        )

