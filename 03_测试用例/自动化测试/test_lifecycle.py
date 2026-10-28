import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.repositories.mongo import SurveyRepository
from app.services import responses, surveys

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.getenv('RUN_MONGO_TESTS') != '1',reason='Requires real MongoDB')]


@pytest.fixture
def ctx():
    settings = Settings(os.getenv('MONGODB_URI','mongodb://127.0.0.1:27019'),'survey_test_'+uuid4().hex)
    with TestClient(create_app(settings)) as client:
        auth = {}
        for name in ['Owner','Reader']:
            credentials = {'username':name,'password':'Test-pass-123'}
            assert client.post('/api/auth/register',json=credentials).status_code == 201
            auth[name] = {'Authorization':'Bearer '+client.post('/api/auth/login',json=credentials).json()['access_token']}
        survey = client.post('/api/surveys',headers=auth['Owner'],json={'title':'生命周期测试','allow_multiple':True}).json()
        yield client,auth,survey
    print(f'\nRetained lifecycle test database: {settings.mongodb_db}')


def definitions():
    return [
        {'id':'q1','type':'single','title':'是否在校','required':True,'options':[{'id':'yes','label':'是'},{'id':'no','label':'否'}]},
        {'id':'q2','type':'number','title':'年龄','validation':{'minimum':0,'maximum':120}},
    ]


def setup_questions(ctx):
    client,auth,survey = ctx
    response = client.put(f'/api/surveys/{survey["id"]}/questions',headers=auth['Owner'],
                          json={'expected_version':1,'questions':definitions()})
    assert response.status_code == 200, response.text
    return response.json()


def manage(ctx, action, version, **fields):
    client,auth,survey = ctx
    url = f'/api/surveys/{survey["id"]}/{action}'
    method = client.patch if action == 'settings' else client.post
    return method(url,headers=auth['Owner'],json={'expected_version':version,**fields})


def gate(ctx, survey_version):
    client,auth,survey = ctx
    url = '/api/fill/'+survey['share_id']
    return (client.get(url,headers=auth['Reader']),client.post(url+'/responses',headers=auth['Reader'],json={
        'survey_version':survey_version,'submission_key':uuid4().hex,'answers':{'q1':'yes','q2':0}}))


def test_publish_freeze_deadline_and_close(ctx):
    client,auth,survey = ctx
    assert manage(ctx,'publish',1).status_code == 422  # empty survey
    assert [response.status_code for response in gate(ctx,1)] == [409,409]
    assert client.get('/api/fill/'+survey['share_id']).status_code == 401
    setup_questions(ctx)
    rules = [{'source_question_id':'q1','operator':'eq','value':'no','target_question_id':'END'}]
    assert client.put(f'/api/surveys/{survey["id"]}/rules',headers=auth['Owner'],json={'expected_version':2,'rules':rules}).status_code == 200
    published = manage(ctx,'publish',3)
    assert published.status_code == 200, published.text
    published = published.json()
    assert published['status'] == 'published' and published['availability'] == 'open'
    assert published['version'] == published['published_version'] == 4
    loaded, submitted = gate(ctx,4)
    assert loaded.status_code == 200 and submitted.status_code == 201
    assert 'owner_id' not in loaded.json() and loaded.json()['survey_version'] == 4
    for resource,payload in [('questions',{'questions':[]}),('rules',{'rules':[]})]:
        result = client.put(f'/api/surveys/{survey["id"]}/{resource}',headers=auth['Owner'],json={'expected_version':4,**payload})
        assert result.status_code == 409
    deadline = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    updated = manage(ctx,'settings',4,deadline=deadline).json()
    assert updated['version'] == 5 and updated['published_version'] == 4
    assert gate(ctx,4)[1].status_code == 201  # deadline does not invalidate frozen content version
    assert gate(ctx,5)[1].status_code == 409
    before_close = client.app.state.db.responses.count_documents({})
    closed = manage(ctx,'close',5)
    assert closed.status_code == 200 and closed.json()['availability'] == 'closed'
    assert [response.status_code for response in gate(ctx,4)] == [410,410]
    assert manage(ctx,'settings',6,deadline=None).status_code == 409
    assert manage(ctx,'publish',6).status_code == 409
    assert manage(ctx,'close',6).status_code == 409
    stored = client.app.state.db.surveys.find_one({'_id':survey['id']})
    assert stored['rules'] == rules and stored['questions'] == published['questions']
    assert client.app.state.db.responses.count_documents({}) == before_close == 2


def test_deadline_is_checked_at_each_request_and_exact_boundary(ctx,monkeypatch):
    client,_,survey = ctx
    setup_questions(ctx)
    deadline = datetime.now(timezone.utc).replace(microsecond=0)+timedelta(hours=1)
    assert manage(ctx,'settings',2,deadline=deadline.isoformat()).status_code == 200
    published = manage(ctx,'publish',3).json()
    assert gate(ctx,4)[0].status_code == 200  # previous load must not authorize later POST
    for instant in [deadline,deadline+timedelta(seconds=1)]:
        monkeypatch.setattr(responses,'utc_now',lambda:instant)
        monkeypatch.setattr(surveys,'utc_now',lambda:instant)
        assert [response.status_code for response in gate(ctx,4)] == [410,410]
        listed = client.get('/api/surveys',headers=ctx[1]['Owner']).json()[0]
        assert listed['status'] == 'published' and listed['availability'] == 'expired'
    # Extending an expired published survey restores availability without editing definitions.
    updated = manage(ctx,'settings',4,deadline=(deadline+timedelta(days=1)).isoformat())
    assert updated.status_code == 200 and updated.json()['availability'] == 'open'
    assert updated.json()['published_version'] == published['published_version']
    assert gate(ctx,4)[0].status_code == 200
    assert manage(ctx,'settings',5,deadline=None).json()['deadline'] is None
    assert gate(ctx,4)[0].status_code == 200


@pytest.mark.parametrize('payload', [
    {'deadline':'2000-01-01T00:00:00Z'}, {'deadline':'2030-01-01T00:00:00'}, {}, {'deadline':None,'title':'forged'},
])
def test_invalid_deadline_never_writes(ctx,payload):
    client,auth,survey = ctx
    before = client.app.state.db.surveys.find_one({'_id':survey['id']})
    result = client.patch(f'/api/surveys/{survey["id"]}/settings',headers=auth['Owner'],json={'expected_version':1,**payload})
    assert result.status_code == 422
    assert client.app.state.db.surveys.find_one({'_id':survey['id']}) == before


@pytest.mark.parametrize('action', ['publish','close','settings'])
def test_lifecycle_permissions_and_stale_versions(ctx,action):
    client,auth,survey = ctx
    setup_questions(ctx)
    payload = {'expected_version':2,**({'deadline':None} if action == 'settings' else {})}
    method = client.patch if action == 'settings' else client.post
    url = f'/api/surveys/{survey["id"]}/{action}'
    assert method(url,headers=auth['Reader'],json=payload).status_code == 404
    assert method(url,json=payload).status_code == 401
    payload['expected_version'] = 1
    assert method(url,headers=auth['Owner'],json=payload).status_code == 409


@pytest.mark.parametrize('corruption', ['blank_title','duplicate_id','no_options','missing_target','back_jump','numeric_bool','expired'])
def test_publish_revalidates_stored_definition_and_rules(ctx,corruption):
    client,_,survey = ctx
    setup_questions(ctx)
    questions = definitions()
    fields = {}
    if corruption == 'blank_title': questions[0]['title'] = '  '
    elif corruption == 'duplicate_id': questions[1]['id'] = 'q1'
    elif corruption == 'no_options': questions[0]['options'] = []
    elif corruption == 'expired': fields['deadline'] = datetime(2000,1,1,tzinfo=timezone.utc)
    else:
        fields['rules'] = [{'source_question_id':'q1','operator':'eq','value':'yes','target_question_id':'missing'}]
        if corruption == 'back_jump': fields['rules'][0]['target_question_id'] = 'q1'
        if corruption == 'numeric_bool': fields['rules'] = [{'source_question_id':'q2','operator':'gte','value':True,'target_question_id':'END'}]
    fields['questions'] = questions
    db = client.app.state.db
    db.surveys.update_one({'_id':survey['id']},{'$set':fields})
    before = db.surveys.find_one({'_id':survey['id']})
    assert manage(ctx,'publish',2).status_code == 422
    assert db.surveys.find_one({'_id':survey['id']}) == before


def test_publish_competes_with_question_edit_atomically(ctx,monkeypatch):
    client,auth,survey = ctx
    setup_questions(ctx)
    barrier = Barrier(2)
    update,replace = SurveyRepository.update_owned,SurveyRepository.replace_questions
    def synchronized_update(self,*args,**kwargs):
        barrier.wait(timeout=10); return update(self,*args,**kwargs)
    def synchronized_replace(self,*args,**kwargs):
        barrier.wait(timeout=10); return replace(self,*args,**kwargs)
    monkeypatch.setattr(SurveyRepository,'update_owned',synchronized_update)
    monkeypatch.setattr(SurveyRepository,'replace_questions',synchronized_replace)
    edited = definitions(); edited[0]['title'] = '另一窗口修改'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manage,ctx,'publish',2)
        second = pool.submit(client.put,f'/api/surveys/{survey["id"]}/questions',headers=auth['Owner'],json={'expected_version':2,'questions':edited})
        results = [first.result(),second.result()]
    assert sorted(response.status_code for response in results) == [200,409]
    stored = client.app.state.db.surveys.find_one({'_id':survey['id']})
    assert stored['version'] == 3
    assert stored['questions'][0]['title'] == ('是否在校' if stored['status'] == 'published' else '另一窗口修改')


def test_close_competes_with_deadline_update_atomically(ctx,monkeypatch):
    setup_questions(ctx); assert manage(ctx,'publish',2).status_code == 200
    barrier = Barrier(2)
    update = SurveyRepository.update_owned
    def synchronized_update(self,*args,**kwargs):
        barrier.wait(timeout=10); return update(self,*args,**kwargs)
    monkeypatch.setattr(SurveyRepository,'update_owned',synchronized_update)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(manage,ctx,'close',3),pool.submit(manage,ctx,'settings',3,deadline=None)]
        results = [future.result() for future in futures]
    assert sorted(response.status_code for response in results) == [200,409]
    assert ctx[0].app.state.db.surveys.find_one({'_id':ctx[2]['id']})['version'] == 4


def test_legacy_published_document_retains_content_version_on_deadline_update(ctx):
    client,_,survey = ctx
    setup_questions(ctx)
    client.app.state.db.surveys.update_one({'_id':survey['id']},{'$set':{'status':'published'}})
    assert gate(ctx,2)[0].json()['survey_version'] == 2
    updated = manage(ctx,'settings',2,deadline=None)
    assert updated.status_code == 200
    assert updated.json()['version'] == 3 and updated.json()['published_version'] == 2
    assert gate(ctx,2)[1].status_code == 201


@pytest.mark.parametrize('definition',[
    {'id':'q','title':'不可能的必答','type':'multiple','required':True,'options':[{'id':'a','label':'A'},{'id':'b','label':'B'}],'validation':{'max_choices':0}},
    {'id':'q','title':'不可能的必答','type':'text','required':True,'validation':{'max_length':0}},
    {'id':'q','title':'不可能的必答','type':'number','required':True,'validation':{'minimum':0.2,'maximum':0.8,'integer_only':True}},
])
def test_publish_rejects_required_questions_without_possible_answers(ctx,definition):
    client,auth,survey = ctx
    saved = client.put(f'/api/surveys/{survey["id"]}/questions',headers=auth['Owner'],json={'expected_version':1,'questions':[definition]})
    assert saved.status_code == 200
    assert manage(ctx,'publish',2).status_code == 422
