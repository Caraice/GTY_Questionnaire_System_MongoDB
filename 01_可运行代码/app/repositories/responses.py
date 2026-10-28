"""Response persistence, durable pseudonyms and the final atomic admission write."""
import secrets

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class ResponseRepository:
    def __init__(self, db):
        self.collection = db.responses
        self.settings = db.app_settings
        self.surveys = db.surveys

    def pseudonym_secret(self):
        # $setOnInsert keeps the same secret across workers and application restarts.
        try:
            self.settings.update_one({"_id": "response_hmac"}, {"$setOnInsert": {
                "schema_version": 1, "secret": secrets.token_hex(32)}}, upsert=True)
        except DuplicateKeyError:
            pass  # Another worker initialized this _id first.
        return self.settings.find_one({"_id": "response_hmac"})["secret"]

    def by_submission(self, survey_id, respondent_key, submission_key):
        return self.collection.find_one({"survey_id": survey_id, "respondent_key": respondent_key,
                                         "submission_key": submission_key})

    def admit(self, survey_id, survey_version, now):
        # This conditional write orders acceptance against close/deadline changes
        # on the same survey document. It is NOT a multi-collection transaction.
        return self.surveys.find_one_and_update({
            "_id": survey_id, "status": "published",
            "$or": [{"deadline": None}, {"deadline": {"$gt": now}}],
            "$expr": {"$and": [
                {"$eq": [{"$ifNull": ["$published_version", "$version"]}, survey_version]},
                {"$or": [{"$eq": ["$deadline", None]}, {"$gt": ["$deadline", "$$NOW"]}]},
            ]},
        }, {"$inc": {"submission_sequence": 1}}, return_document=ReturnDocument.AFTER)

    def insert(self, document):
        self.collection.insert_one(document)

    def by_id(self, response_id):
        return self.collection.find_one({"_id": response_id})

    def history(self, identities, limit, offset):
        if not identities:
            return [], 0
        query = {"$or": identities}
        projection = {"_id": 1, "survey_id": 1, "survey_version": 1,
                      "submitted_at": 1, "anonymous": 1}
        rows = list(self.collection.find(query, projection)
                    .sort([("submitted_at", -1), ("_id", -1)]).skip(offset).limit(limit))
        return rows, self.collection.count_documents(query)
