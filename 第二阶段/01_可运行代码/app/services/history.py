"""Only the signed-in respondent may inspect their own saved responses.

Reuse the existing per-survey HMAC identity, including pre-existing anonymous
responses. No plaintext account ID is added to anonymous response documents.
"""
import hashlib
import hmac

from fastapi import HTTPException

from app.repositories.mongo import SurveyRepository
from app.repositories.responses import ResponseRepository


class HistoryService:
    def __init__(self, db):
        self.surveys = SurveyRepository(db)
        self.responses = ResponseRepository(db)

    @staticmethod
    def identity(secret, survey_id, user_id):
        return hmac.new(secret.encode(), f'{survey_id}\0{user_id}'.encode(), hashlib.sha256).hexdigest()

    def list_owned(self, user_id, limit=20, offset=0):
        metadata = {item['_id']: item for item in self.surveys.history_metadata()}
        if not metadata:
            return {'items': [], 'total': 0, 'limit': limit, 'offset': offset}
        secret = self.responses.pseudonym_secret()
        identities = [{'survey_id': survey_id, 'respondent_key': self.identity(secret, survey_id, user_id)}
                      for survey_id in metadata]
        rows, total = self.responses.history(identities, limit, offset)
        items = [{
            'id': row['_id'], 'survey_id': row['survey_id'], 'survey_version': row['survey_version'],
            'title': metadata[row['survey_id']]['title'], 'share_id': metadata[row['survey_id']]['share_id'],
            'submitted_at': row['submitted_at'], 'anonymous': row['anonymous'],
        } for row in rows]
        return {'items': items, 'total': total, 'limit': limit, 'offset': offset}

    def detail(self, response_id, user_id):
        row = self.responses.by_id(response_id)
        if row is None:
            raise HTTPException(404, '答卷不存在')
        secret = self.responses.pseudonym_secret()
        expected = self.identity(secret, row['survey_id'], user_id)
        if not hmac.compare_digest(row['respondent_key'], expected):
            raise HTTPException(404, '答卷不存在')
        survey = self.surveys.get_by_id(row['survey_id'])
        if survey is None:
            raise HTTPException(404, '问卷不存在')
        if row['survey_version'] != survey.get('published_version', survey['version']):
            raise HTTPException(409, '此答卷的历史题目版本暂不可读取')
        by_id = {question['id']: question for question in survey['questions']}
        if any(question_id not in by_id for question_id in row['path']):
            raise HTTPException(409, '此答卷的历史题目定义不完整')
        questions = [{**by_id[question_id], 'answer': row['answers'].get(question_id)}
                     for question_id in row['path']]
        return {'id': row['_id'], 'survey_id': row['survey_id'], 'title': survey['title'],
                'description': survey['description'], 'survey_version': row['survey_version'],
                'submitted_at': row['submitted_at'], 'anonymous': row['anonymous'],
                'path': row['path'], 'questions': questions}
