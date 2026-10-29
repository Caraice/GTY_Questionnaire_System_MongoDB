"""Question editing acceptance tests against real MongoDB, including competing writes."""
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.repositories.mongo import SurveyRepository

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.getenv("RUN_MONGO_TESTS") != "1", reason="Set RUN_MONGO_TESTS=1 with real MongoDB running")]


@pytest.fixture
def context():
    settings = Settings(os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27019"), "survey_test_" + uuid4().hex)
    with TestClient(create_app(settings)) as client:
        headers = {}
        for username in ["Editor", "Other"]:
            data = {"username": username, "password": "Test-pass-123"}
            assert client.post('/api/auth/register', json=data).status_code == 201
            login = client.post('/api/auth/login', json=data).json()
            headers[username] = {"Authorization": "Bearer " + login["access_token"]}
        survey = client.post('/api/surveys', json={"title": "题目编辑验收"}, headers=headers["Editor"]).json()
        yield client, headers, survey, settings
    print(f"\nQuestion test database retained: {settings.mongodb_db}")


@pytest.fixture
def questions():
    return [
        {"id":"q1", "type":"single", "title":" 单选 ", "required": True,
         "options":[{"id":"o1", "label":" A "}, {"id":"o2", "label":"B"}]},
        {"id":"q2", "type":"multiple", "title":"多选", "options":[{"id":"o3", "label":"C"}, {"id":"o4", "label":"D"}],
         "validation":{"exact_choices":2}},
        {"id":"q3", "type":"text", "title":"文本", "validation":{"min_length":2,"max_length":50}},
        {"id":"q4", "type":"number", "title":"年龄", "validation":{"minimum":0,"maximum":120,"integer_only":True}},
    ]


def endpoint(survey):
    return f'/api/surveys/{survey["id"]}/questions'


def save(client, headers, survey, questions, version=1):
    return client.put(endpoint(survey), headers=headers["Editor"], json={"expected_version": version, "questions":questions})


def test_save_reorder_delete_and_reload(context, questions):
    client, headers, survey, settings = context
    result = save(client, headers, survey, questions)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved['version'] == 2
    assert saved['questions'][0]['title'] == '单选'
    assert saved['questions'][0]['options'][0]['label'] == 'A'
    stored = client.app.state.db.surveys.find_one({"_id":survey['id']})
    assert stored['questions'] == saved['questions']
    assert stored['updated_at'] >= stored['created_at']
    assert stored['share_id'] == survey['share_id'] and stored['rules'] == []
    edited = deepcopy(saved['questions'])
    edited[0]['title'] = '改写题干'
    edited[0]['options'][0]['label'] = '改写选项'
    edited[0]['options'].reverse()
    edited = [edited[3], edited[0], edited[1]]  # reorder and delete text
    result = save(client, headers, survey, edited, version=2)
    assert result.status_code == 200, result.text
    assert result.json()['version'] == 3
    assert [q['id'] for q in result.json()['questions']] == ['q4','q1','q2']
    assert [o['id'] for o in result.json()['questions'][1]['options']] == ['o2','o1']
    # Independent application lifespan reads the same persisted document.
    with TestClient(create_app(settings)) as restarted:
        reread = restarted.get('/api/surveys/' + survey['id'], headers=headers['Editor'])
        assert reread.json()['questions'] == result.json()['questions']
        assert reread.json()['version'] == 3
    cleared = save(client, headers, survey, [], version=3)
    assert cleared.status_code == 200 and cleared.json()['questions'] == []
    assert cleared.json()['version'] == 4


def test_stale_version_and_ownership(context, questions):
    client, headers, survey, _ = context
    assert save(client, headers, survey, questions).status_code == 200
    original = client.app.state.db.surveys.find_one({"_id":survey['id']})
    stale = save(client, headers, survey, [], version=1)
    assert stale.status_code == 409
    assert client.put(endpoint(survey), json={"questions":[],"expected_version":2}).status_code == 401
    assert client.put(endpoint(survey), headers=headers['Other'], json={"questions":[],"expected_version":2}).status_code == 404
    assert client.put('/api/surveys/missing/questions', headers=headers['Editor'], json={"questions":[],"expected_version":1}).status_code == 404
    assert client.app.state.db.surveys.find_one({"_id":survey['id']}) == original


@pytest.mark.parametrize('status', ['published', 'closed'])
def test_frozen_status(context, questions, status):
    client, headers, survey, _ = context
    db = client.app.state.db
    db.surveys.update_one({"_id":survey['id']}, {'$set': {'status':status}})
    assert save(client, headers, survey, questions).status_code == 409
    assert db.surveys.find_one({"_id":survey['id']})['questions'] == []
    assert db.surveys.find_one({"_id":survey['id']})['version'] == 1


@pytest.mark.parametrize('kind', ['blank_title','blank_option','duplicate_question','duplicate_option','bad_bounds','non_integer_version','missing_version'])
def test_invalid_payload_does_not_write(context, questions, kind):
    client, headers, survey, _ = context
    payload = {'expected_version':1,'questions':questions}
    if kind == 'blank_title': questions[2]['title'] = ' \t\n\u3000'
    elif kind == 'blank_option': questions[0]['options'][0]['label'] = '\n\u3000 '
    elif kind == 'duplicate_question': questions[1]['id'] = 'q1'
    elif kind == 'duplicate_option': questions[0]['options'][1]['id'] = 'o1'
    elif kind == 'bad_bounds': questions[3]['validation']['maximum'] = -1
    elif kind == 'non_integer_version': payload['expected_version'] = True
    elif kind == 'missing_version': del payload['expected_version']
    before = client.app.state.db.surveys.find_one({"_id":survey['id']})
    result = client.put(endpoint(survey), headers=headers['Editor'], json=payload)
    assert result.status_code == 422, result.text
    assert client.app.state.db.surveys.find_one({"_id":survey['id']}) == before


def test_competing_writes_only_one_succeeds(context, questions, monkeypatch):
    client, headers, survey, _ = context
    barrier = Barrier(2)
    real_update = SurveyRepository.replace_questions
    def synchronized_update(self, *args, **kwargs):
        barrier.wait(timeout=10)  # both pass preflight before either MongoDB write
        return real_update(self, *args, **kwargs)
    monkeypatch.setattr(SurveyRepository, 'replace_questions', synchronized_update)
    alternatives = [deepcopy(questions), deepcopy(questions)]
    alternatives[0][0]['title'], alternatives[1][0]['title'] = '窗口 A', '窗口 B'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, client, headers, survey, value) for value in alternatives]
        results = [future.result() for future in futures]
    assert sorted(result.status_code for result in results) == [200,409]
    winner = next(result.json() for result in results if result.status_code == 200)
    stored = client.app.state.db.surveys.find_one({'_id':survey['id']})
    assert stored['version'] == 2
    assert stored['questions'] == winner['questions']


def test_existing_rules_are_preserved_and_protected(context, questions):
    client, headers, survey, _ = context
    assert save(client, headers, survey, questions).status_code == 200
    rules = [{'source_question_id':'q1','operator':'eq','value':'o1','target_question_id':'q4'}]
    client.app.state.db.surveys.update_one({'_id':survey['id']}, {'$set': {'rules':rules}})
    questions[0]['title'] = '保留已有规则'
    result = save(client, headers, survey, questions, version=2)
    assert result.status_code == 200 and result.json()['rules'] == rules
    invalid = [q for q in questions if q['id'] != 'q4']
    assert save(client, headers, survey, invalid, version=3).status_code == 422
    assert save(client, headers, survey, list(reversed(questions)), version=3).status_code == 422
    altered = deepcopy(questions); altered[0]['options'][0]['id'] = 'replacement'
    assert save(client, headers, survey, altered, version=3).status_code == 422
    stored = client.app.state.db.surveys.find_one({'_id':survey['id']})
    assert stored['version'] == 3 and stored['rules'] == rules
