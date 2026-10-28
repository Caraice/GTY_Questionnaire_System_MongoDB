"""Real persisted history, including old anonymous documents and account isolation."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.getenv('RUN_MONGO_TESTS') != '1', reason='Requires real MongoDB')]


@pytest.fixture
def ctx():
    settings = Settings(os.getenv('MONGODB_URI', 'mongodb://127.0.0.1:27019'), 'survey_test_'+uuid4().hex)
    with TestClient(create_app(settings)) as client:
        auth = {}
        for name in ['Owner', 'Alice', 'Bob']:
            credentials = {'username': name, 'password': 'Test-pass-123'}
            assert client.post('/api/auth/register', json=credentials).status_code == 201
            auth[name] = {'Authorization': 'Bearer '+client.post('/api/auth/login', json=credentials).json()['access_token']}
        yield client, auth, settings
    print('Retained history test database: '+settings.mongodb_db)


def publish(ctx, title='历史样本'):
    client, auth, _ = ctx
    survey = client.post('/api/surveys', headers=auth['Owner'], json={
        'title': title, 'allow_multiple': True, 'allow_anonymous': True}).json()
    questions = [
        {'id': 'route', 'type': 'single', 'title': '路线', 'required': True,
         'options': [{'id': 'full', 'label': '完整'}, {'id': 'skip', 'label': '跳过'}]},
        {'id': 'text', 'type': 'text', 'title': '建议'},
        {'id': 'number', 'type': 'number', 'title': '数量', 'required': True},
    ]
    base = '/api/surveys/'+survey['id']
    assert client.put(base+'/questions', headers=auth['Owner'], json={'expected_version': 1, 'questions': questions}).status_code == 200
    rules = [{'source_question_id': 'route', 'operator': 'eq', 'value': 'skip', 'target_question_id': 'number'}]
    assert client.put(base+'/rules', headers=auth['Owner'], json={'expected_version': 2, 'rules': rules}).status_code == 200
    response = client.post(base+'/publish', headers=auth['Owner'], json={'expected_version': 3})
    assert response.status_code == 200
    return response.json()


def submit(ctx, survey, anonymous=False, user='Alice', full=False):
    answers = {'route': 'full' if full else 'skip', 'number': 0}
    if full:
        answers['text'] = ' 我的建议 '
    response = ctx[0].post('/api/fill/'+survey['share_id']+'/responses', headers=ctx[1][user], json={
        'survey_version': survey['published_version'], 'submission_key': uuid4().hex,
        'anonymous': anonymous, 'answers': answers})
    assert response.status_code == 201, response.text
    return response.json()


def test_empty_history_and_page_routes(ctx):
    client, auth, _ = ctx
    assert client.get('/api/me/responses', headers=auth['Alice']).json() == {'items': [], 'total': 0, 'limit': 20, 'offset': 0}
    assert client.get('/api/me/responses').status_code == 401
    assert client.get('/api/me/responses/missing').status_code == 401
    assert client.get('/api/me/responses/missing', headers=auth['Alice']).status_code == 404
    assert client.get('/statistics/example').status_code == 200
    assert client.get('/response/example').status_code == 200


@pytest.mark.parametrize('anonymous', [False, True])
def test_history_reads_existing_responses_without_identity_migration(ctx, anonymous):
    client, auth, _ = ctx
    survey = publish(ctx)
    receipt = submit(ctx, survey, anonymous=anonymous)
    before = client.app.state.db.responses.find_one({'_id': receipt['id']})
    if anonymous:
        assert before['respondent_id'] is None
    result = client.get('/api/me/responses', headers=auth['Alice']).json()
    assert result['total'] == 1 and result['items'][0]['id'] == receipt['id']
    assert result['items'][0]['anonymous'] is anonymous
    detail = client.get('/api/me/responses/'+receipt['id'], headers=auth['Alice']).json()
    assert detail['path'] == ['route', 'number']
    assert [q['answer'] for q in detail['questions']] == ['skip', 0]
    assert detail['questions'][0]['options'][1]['label'] == '跳过'
    assert 'text' not in [q['id'] for q in detail['questions']]
    for private in ['respondent_key', 'respondent_id', 'attempt_key', 'submission_key', 'request_hash', 'secret']:
        assert private not in str(result) and private not in str(detail)
    assert client.app.state.db.responses.find_one({'_id': receipt['id']}) == before


@pytest.mark.parametrize('other', ['Owner', 'Bob'])
def test_owner_and_other_respondents_cannot_read_private_answers(ctx, other):
    survey = publish(ctx)
    receipt = submit(ctx, survey, anonymous=True)
    client, auth, _ = ctx
    assert client.get('/api/me/responses', headers=auth[other]).json()['total'] == 0
    assert client.get('/api/me/responses/'+receipt['id'], headers=auth[other]).status_code == 404
    # Survey owner sees aggregates, never individual identities in that endpoint.
    assert client.get('/api/surveys/'+survey['id']+'/statistics', headers=auth['Owner']).json()['total_submissions'] == 1


def test_list_contains_each_attempt_and_other_surveys_with_stable_pagination(ctx):
    client, auth, _ = ctx
    first, second = publish(ctx, '问卷一'), publish(ctx, '问卷二')
    receipts = [submit(ctx, first), submit(ctx, first, anonymous=True), submit(ctx, second, full=True)]
    now = datetime.now(timezone.utc)
    for index, receipt in enumerate(receipts):
        client.app.state.db.responses.update_one({'_id': receipt['id']}, {'$set': {'submitted_at': now+timedelta(seconds=index)}})
    one = client.get('/api/me/responses?limit=2&offset=0', headers=auth['Alice']).json()
    two = client.get('/api/me/responses?limit=2&offset=2', headers=auth['Alice']).json()
    assert one['total'] == two['total'] == 3
    assert [item['id'] for item in one['items']+two['items']] == [r['id'] for r in reversed(receipts)]
    assert one['items'][0]['title'] == '问卷二'
    detail = client.get('/api/me/responses/'+receipts[-1]['id'], headers=auth['Alice']).json()
    assert detail['questions'][1]['answer'] == '我的建议'


@pytest.mark.parametrize('state', ['closed', 'expired'])
def test_history_remains_readable_after_collection_ends(ctx, state):
    client, auth, _ = ctx
    survey = publish(ctx)
    receipt = submit(ctx, survey, anonymous=True)
    fields = {'status': 'closed'} if state == 'closed' else {'deadline': datetime.now(timezone.utc)-timedelta(seconds=1)}
    client.app.state.db.surveys.update_one({'_id': survey['id']}, {'$set': fields})
    assert client.get('/api/me/responses', headers=auth['Alice']).json()['total'] == 1
    assert client.get('/api/me/responses/'+receipt['id'], headers=auth['Alice']).status_code == 200
    assert client.get('/api/fill/'+survey['share_id'], headers=auth['Alice']).status_code == 410


def test_restart_keeps_anonymous_history(ctx):
    survey = publish(ctx)
    receipt = submit(ctx, survey, anonymous=True)
    with TestClient(create_app(ctx[2])) as restarted:
        assert restarted.get('/api/me/responses', headers=ctx[1]['Alice']).json()['total'] == 1
        assert restarted.get('/api/me/responses/'+receipt['id'], headers=ctx[1]['Alice']).status_code == 200


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'offset=-1', 'offset=10001'])
def test_history_paging_bounds(ctx, query):
    assert ctx[0].get('/api/me/responses?'+query, headers=ctx[1]['Alice']).status_code == 422


def test_history_rejects_mismatched_definition_instead_of_mislabeling_answers(ctx):
    survey = publish(ctx)
    receipt = submit(ctx, survey)
    ctx[0].app.state.db.surveys.update_one({'_id': survey['id']}, {'$set': {'published_version': 999}})
    assert ctx[0].get('/api/me/responses/'+receipt['id'], headers=ctx[1]['Alice']).status_code == 409
