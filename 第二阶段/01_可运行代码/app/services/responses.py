"""Authoritative path validation and one durable response per accepted submission."""
import hashlib
import hmac
import json
from uuid import uuid4

from bson import BSON
from bson.errors import InvalidDocument
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.domain.availability import availability, utc_now
from app.repositories.mongo import SurveyRepository
from app.repositories.responses import ResponseRepository
from app.domain.routing import PathValidationError, validate_path


class ResponseService:
    def __init__(self, db):
        self.surveys = SurveyRepository(db)
        self.responses = ResponseRepository(db)

    def require_available(self, share_id):
        survey = self.surveys.get_by_share_id(share_id)
        if survey is None:
            raise HTTPException(404, "问卷不存在")
        now = utc_now()  # Each GET/POST reads MongoDB and then checks the clock anew.
        state = availability(survey, now)
        if state == "draft":
            raise HTTPException(409, "问卷尚未发布")
        if state == "closed":
            raise HTTPException(410, "问卷已关闭，无法填写或提交")
        if state == "expired":
            raise HTTPException(410, "问卷已截止，无法填写或提交")
        return survey, now

    def load(self, share_id):
        survey, now = self.require_available(share_id)
        fields = ("share_id", "title", "description", "deadline", "allow_anonymous", "allow_multiple", "questions", "rules")
        return {**{key: survey[key] for key in fields}, "status": "published", "availability": "open",
                "survey_version": survey.get("published_version", survey["version"]), "server_time": now}

    def submit(self, share_id, user, submission):
        survey = self.surveys.get_by_share_id(share_id)
        if survey is None:
            raise HTTPException(404, "问卷不存在")
        secret = self.responses.pseudonym_secret()
        respondent = hmac.new(secret.encode(), f'{survey["_id"]}\0{user["_id"]}'.encode(), hashlib.sha256).hexdigest()
        fingerprint = hashlib.sha256(json.dumps(submission.model_dump(), sort_keys=True, ensure_ascii=True,
                                               separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        existing = self.responses.by_submission(survey["_id"], respondent, submission.submission_key)
        if existing is not None:
            return self._replay(existing, fingerprint)
        survey, _ = self.require_available(share_id)
        if submission.survey_version != survey.get("published_version", survey["version"]):
            raise HTTPException(409, "问卷内容版本不匹配，请重新加载")
        if submission.anonymous and not survey["allow_anonymous"]:
            raise HTTPException(422, "此问卷不允许匿名填写")
        try:
            path, answers = validate_path(survey["questions"], survey["rules"], submission.answers)
        except PathValidationError as error:
            raise HTTPException(422, {"question_id": error.question_id, "message": str(error)}) from None
        except ValueError:
            raise HTTPException(422, "问卷定义或规则不合法，请联系创建者") from None
        now = utc_now()
        document = {
            "_id": str(uuid4()), "schema_version": 1,
            "survey_id": survey["_id"], "survey_version": submission.survey_version,
            "respondent_key": respondent, "respondent_id": None if submission.anonymous else user["_id"],
            "attempt_key": submission.submission_key if survey["allow_multiple"] else "once",
            "submission_key": submission.submission_key, "request_hash": fingerprint,
            "anonymous": submission.anonymous, "answers": answers, "path": path,
            "submitted_at": now.replace(microsecond=now.microsecond // 1000 * 1000),
        }
        try:
            encoded = BSON.encode(document)
        except (OverflowError, InvalidDocument, UnicodeError):
            raise HTTPException(422, "答案超出 MongoDB 可保存的数值或文本范围") from None
        if len(encoded) > 16 * 1024 * 1024:
            raise HTTPException(422, "答卷内容过大，请缩短文本")
        if self.responses.admit(survey["_id"], submission.survey_version, now) is None:
            self.require_available(share_id)  # Explain a concurrent close or deadline update.
            raise HTTPException(409, "问卷内容版本或状态已变化，请重新加载")
        try:
            self.responses.insert(document)
        except DuplicateKeyError:
            existing = self.responses.by_submission(survey["_id"], respondent, submission.submission_key)
            if existing is not None:
                return self._replay(existing, fingerprint)
            raise HTTPException(409, "此问卷不允许同一账号重复填写") from None
        return self._receipt(document, False)

    @staticmethod
    def _receipt(document, replayed):
        return {"id": document["_id"], "survey_id": document["survey_id"],
                "survey_version": document["survey_version"], "submitted_at": document["submitted_at"],
                "path": document["path"], "replayed": replayed}

    def _replay(self, document, fingerprint):
        if document.get("request_hash") != fingerprint:
            raise HTTPException(409, "同一 submission_key 不能用于不同提交内容")
        return self._receipt(document, True)

