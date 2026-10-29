"""Real submission -> BSON persistence -> aggregation, including concurrent retries."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.repositories.responses import ResponseRepository
from app.services import responses

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('RUN_MONGO_TESTS')!='1',reason='Requires real MongoDB')]


@pytest.fixture
def ctx():
    settings=Settings(os.getenv('MONGODB_URI','mongodb://127.0.0.1:27019'),'survey_test_'+uuid4().hex)
    with TestClient(create_app(settings)) as client:
        auth={}
        for name in ['Owner','Reader','Other']:
            credentials={'username':name,'password':'Test-pass-123'}
            assert client.post('/api/auth/register',json=credentials).status_code==201
            auth[name]={'Authorization':'Bearer '+client.post('/api/auth/login',json=credentials).json()['access_token']}
        yield client,auth,settings
    print('\nRetained response test database: '+settings.mongodb_db)


def publish(ctx, *, multiple=False, anonymous=False, rules=None, numeric_first=False, number_limits=None):
    client,auth,_=ctx
    survey=client.post('/api/surveys',headers=auth['Owner'],json={
        'title':'真实提交测试','allow_multiple':multiple,'allow_anonymous':anonymous}).json()
    base='/api/surveys/'+survey['id']
    options=[{'id':'a','label':'A'},{'id':'b','label':'B'}]
    definitions=[
        {'id':'s','type':'single','title':'入口','required':True,'options':options},
        {'id':'m','type':'multiple','title':'选择','options':options,'validation':{'max_choices':2}},
        {'id':'t','type':'text','title':'内容','required':True,'validation':{'min_length':2,'max_length':10}},
        {'id':'n','type':'number','title':'数字','validation':{'minimum':0,'maximum':120,'integer_only':True}},
    ]
    if number_limits is not None:
        definitions[3]['validation']=number_limits
    if numeric_first:
        definitions=[definitions[0],definitions[1],definitions[3],definitions[2]]
    result=client.put(base+'/questions',headers=auth['Owner'],json={'expected_version':1,'questions':definitions})
    assert result.status_code==200,result.text
    version=2
    if rules is not None:
        result=client.put(base+'/rules',headers=auth['Owner'],json={'expected_version':2,'rules':rules})
        assert result.status_code==200,result.text
        version=3
    result=client.post(base+'/publish',headers=auth['Owner'],json={'expected_version':version})
    assert result.status_code==200,result.text
    return result.json()


def post(ctx,survey,*,answers=None,key=None,anonymous=False,reader='Reader',version=None):
    return ctx[0].post('/api/fill/'+survey['share_id']+'/responses',headers=ctx[1][reader],json={
        'survey_version':survey['published_version'] if version is None else version,
        'submission_key':key or uuid4().hex,'anonymous':anonymous,
        'answers':{'s':'b','m':['a','b'],'t':' 完成 ','n':0} if answers is None else answers,
    })


def test_real_submit_saved_normalized_and_survives_restart(ctx):
    survey=publish(ctx)
    result=post(ctx,survey)
    assert result.status_code==201,result.text
    receipt=result.json()
    db=ctx[0].app.state.db
    stored=db.responses.find_one({'_id':receipt['id']})
    assert stored['answers']=={'s':'b','m':['a','b'],'t':'完成','n':0}
    assert type(stored['answers']['n']) is int
    assert stored['path']==receipt['path']==['s','m','t','n']
    assert stored['respondent_id']==db.users.find_one({'username':'reader'})['_id']
    assert len(stored['respondent_key'])==64 and receipt['replayed'] is False
    assert not {'respondent_id','respondent_key','request_hash','answers','submission_key'} & set(receipt)
    with TestClient(create_app(ctx[2])) as restarted:
        stats=restarted.get('/api/surveys/'+survey['id']+'/statistics',headers=ctx[1]['Owner']).json()
        assert stats['total_submissions']==1
        assert stats['questions'][3]['values']==[0] and stats['questions'][3]['average']==0
        assert restarted.get('/api/fill/'+survey['share_id'],headers=ctx[1]['Reader']).status_code==200
    assert post(ctx,survey).status_code==409  # secret and once constraint survive restart


def test_branch_skips_required_and_rejects_outside_unknown_answers(ctx):
    survey=publish(ctx,multiple=True,rules=[{'source_question_id':'s','operator':'eq','value':'a','target_question_id':'n'}])
    saved=post(ctx,survey,answers={'s':'a','n':0})
    assert saved.status_code==201 and saved.json()['path']==['s','n']
    for answers in [{'s':'a','t':'完成','n':0},{'s':'a','t':None,'n':0},{'s':'a','unknown':1}]:
        assert post(ctx,survey,answers=answers).status_code==422
    assert ctx[0].app.state.db.responses.count_documents({})==1
    survey2=publish(ctx,rules=[{'source_question_id':'s','operator':'eq','value':'a','target_question_id':'END'}])
    result=post(ctx,survey2,answers={'s':'a'})
    assert result.status_code==201 and result.json()['path']==['s']


@pytest.mark.parametrize('field,value',[
    ('s',True),('s','missing'),('m',['a','a']),('m',['unknown']),('t',' '),('t','a'),
    ('t','a'*11),('n',True),('n','0'),('n',-1),('n',121),('n',1.5),('n',2**63),
])
def test_invalid_answers_never_save(ctx,field,value):
    survey=publish(ctx)
    answers={'s':'b','t':'完成','n':0}; answers[field]=value
    result=post(ctx,survey,answers=answers)
    assert result.status_code==422,result.text
    assert ctx[0].app.state.db.responses.count_documents({})==0


def test_idempotency_exact_payload_and_closed_receipt(ctx):
    survey=publish(ctx)
    key=uuid4().hex
    first=post(ctx,survey,key=key)
    again=post(ctx,survey,key=key)
    assert first.status_code==201 and again.status_code==200
    assert again.json()=={**first.json(),'replayed':True}
    for change in [{'s':'b','t':'改动','n':0},{'s':'b','m':['a','b'],'t':' 完成 ','n':0.0}]:
        assert post(ctx,survey,key=key,answers=change).status_code==409
    assert post(ctx,survey,key=key,anonymous=True).status_code==409
    assert post(ctx,survey,key=key,version=999).status_code==409
    assert ctx[0].post('/api/surveys/'+survey['id']+'/close',headers=ctx[1]['Owner'],json={'expected_version':survey['version']}).status_code==200
    assert post(ctx,survey,key=key).status_code==200  # replay receipt, not a new admission
    assert post(ctx,survey).status_code==410
    assert ctx[0].app.state.db.responses.count_documents({})==1


def test_anonymous_settings_repeat_policy_and_owner_privacy(ctx):
    survey=publish(ctx)
    assert post(ctx,survey,anonymous=True).status_code==422
    survey=publish(ctx,anonymous=True)
    result=post(ctx,survey,anonymous=True)
    assert result.status_code==201
    stored=ctx[0].app.state.db.responses.find_one({'_id':result.json()['id']})
    assert stored['respondent_id'] is None and stored['anonymous'] is True
    assert 'reader' not in str(stored).lower()
    assert post(ctx,survey,anonymous=False).status_code==409  # switching anonymity cannot bypass once
    assert post(ctx,survey,reader='Other',anonymous=True).status_code==201
    stats=ctx[0].get('/api/surveys/'+survey['id']+'/statistics',headers=ctx[1]['Owner']).json()
    assert stats['total_submissions']==2
    assert 'respondent' not in str(stats)
    another=publish(ctx,anonymous=True)
    result=post(ctx,another,anonymous=True)
    second=ctx[0].app.state.db.responses.find_one({'_id':result.json()['id']})
    assert second['respondent_key']!=stored['respondent_key']  # per-survey pseudonyms
    multiple=publish(ctx,multiple=True)
    assert post(ctx,multiple).status_code==201
    assert post(ctx,multiple).status_code==201


@pytest.mark.parametrize('mode',['same_key','different_keys_once','different_users','allowed_multiple'])
def test_concurrent_submissions_and_retries(ctx,monkeypatch,mode):
    survey=publish(ctx,multiple=mode=='allowed_multiple')
    barrier=Barrier(2); original=ResponseRepository.insert
    def synchronized(self,document):
        barrier.wait(timeout=10)
        return original(self,document)
    monkeypatch.setattr(ResponseRepository,'insert',synchronized)
    key=uuid4().hex
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(post,ctx,survey,key=key),pool.submit(post,ctx,survey,
            key=key if mode=='same_key' else uuid4().hex,reader='Other' if mode=='different_users' else 'Reader')]
        results=[job.result() for job in jobs]
    expected=[200,201] if mode=='same_key' else [201,409] if mode=='different_keys_once' else [201,201]
    assert sorted(result.status_code for result in results)==expected,[r.text for r in results]
    assert ctx[0].app.state.db.responses.count_documents({})==(1 if mode in ['same_key','different_keys_once'] else 2)


def test_final_admission_orders_against_close_and_expiry(ctx,monkeypatch):
    survey=publish(ctx,multiple=True)
    original=ResponseRepository.admit
    def close_before(self,survey_id,version,now):
        ctx[0].app.state.db.surveys.update_one({'_id':survey_id},{'$set':{'status':'closed'}})
        return original(self,survey_id,version,now)
    monkeypatch.setattr(ResponseRepository,'admit',close_before)
    assert post(ctx,survey).status_code==410
    assert ctx[0].app.state.db.responses.count_documents({})==0
    monkeypatch.setattr(ResponseRepository,'admit',original)
    survey=publish(ctx)
    def expire_before(self,survey_id,version,now):
        ctx[0].app.state.db.surveys.update_one({'_id':survey_id},{'$set':{'deadline':now-timedelta(seconds=1)}})
        return original(self,survey_id,version,now)
    monkeypatch.setattr(ResponseRepository,'admit',expire_before)
    assert post(ctx,survey).status_code==410
    assert ctx[0].app.state.db.responses.count_documents({})==0


def test_already_admitted_response_may_finish_after_close(ctx,monkeypatch):
    survey=publish(ctx)
    original=ResponseRepository.insert
    def close_after(self,document):
        ctx[0].app.state.db.surveys.update_one({'_id':survey['id']},{'$set':{'status':'closed'}})
        return original(self,document)
    monkeypatch.setattr(ResponseRepository,'insert',close_after)
    assert post(ctx,survey).status_code==201
    assert post(ctx,survey).status_code==410
    assert ctx[0].app.state.db.responses.count_documents({})==1


def test_unavailable_wrong_version_and_auth_never_save(ctx):
    survey=publish(ctx)
    client,auth,_=ctx
    url='/api/fill/'+survey['share_id']+'/responses'
    assert client.post(url,json={'survey_version':3,'submission_key':uuid4().hex,'answers':{}}).status_code==401
    assert post(ctx,survey,version=999).status_code==409
    client.app.state.db.surveys.update_one({'_id':survey['id']},{'$set':{'deadline':datetime.now(timezone.utc)-timedelta(seconds=1)}})
    assert post(ctx,survey).status_code==410
    assert client.app.state.db.responses.count_documents({})==0


def test_real_submissions_match_manual_statistics(ctx):
    survey=publish(ctx,multiple=True)
    # Six complete API submissions; one branch skips required text and other answers.
    client,auth,_=ctx
    # A second survey with optional questions makes the empty/omitted cases valid.
    draft=client.post('/api/surveys',headers=auth['Owner'],json={'title':'整段手算','allow_multiple':True}).json()
    questions=survey['questions']
    for question in questions: question['required']=False
    assert client.put('/api/surveys/'+draft['id']+'/questions',headers=auth['Owner'],json={'expected_version':1,'questions':questions}).status_code==200
    survey=client.post('/api/surveys/'+draft['id']+'/publish',headers=auth['Owner'],json={'expected_version':2}).json()
    for answers in [
        {'s':'a','m':['a','b'],'t':'优秀','n':0},{'s':'a','m':['a'],'t':'待改进','n':10},
        {'s':'b','m':['b'],'n':20},{'m':[],'t':'零值','n':0},{'s':None,'t':' \t','n':None},{},
    ]:
        result=post(ctx,survey,answers=answers)
        assert result.status_code==201,result.text
    result=client.get('/api/surveys/'+survey['id']+'/statistics',headers=auth['Owner']).json()
    assert result['total_submissions']==6
    assert [q['answered_count'] for q in result['questions']]==[3,3,3,4]
    assert result['questions'][1]['selection_count']==4
    assert sorted(result['questions'][3]['values'])==[0,0,10,20]
    assert result['questions'][3]['average']==7.5


def test_storage_error_never_returns_fake_success_and_lost_ack_replays(ctx,monkeypatch):
    from pymongo.errors import AutoReconnect
    survey=publish(ctx)
    original=ResponseRepository.insert
    def unavailable(self,document):
        raise AutoReconnect('simulated storage failure')
    monkeypatch.setattr(ResponseRepository,'insert',unavailable)
    key=uuid4().hex
    assert post(ctx,survey,key=key).status_code==503
    assert ctx[0].app.state.db.responses.count_documents({})==0
    def lost_ack(self,document):
        original(self,document)
        raise AutoReconnect('simulated acknowledgement loss')
    monkeypatch.setattr(ResponseRepository,'insert',lost_ack)
    assert post(ctx,survey,key=key).status_code==503
    monkeypatch.setattr(ResponseRepository,'insert',original)
    replay=post(ctx,survey,key=key)
    assert replay.status_code==200 and replay.json()['replayed'] is True
    assert ctx[0].app.state.db.responses.count_documents({})==1


@pytest.mark.parametrize('source,operator,value,answers,path',[
    ('m','contains_any',['b'],{'s':'b','m':['a','b'],'n':0},['s','m','n']),
    ('m','contains_all',['a','b'],{'s':'b','m':['a','b'],'n':0},['s','m','n']),
    ('n','between',[0,0],{'s':'b','n':0},['s','m','n']),
])
def test_real_multiple_and_numeric_branches_skip_required_text(ctx,source,operator,value,answers,path):
    survey=publish(ctx,numeric_first=source=='n',rules=[{
        'source_question_id':source,'operator':operator,'value':value,
        'target_question_id':'END' if source=='n' else 'n'}])
    saved=post(ctx,survey,answers=answers)
    assert saved.status_code==201,saved.text
    assert saved.json()['path']==path
    stored=ctx[0].app.state.db.responses.find_one({'_id':saved.json()['id']})
    assert 't' not in stored['answers'] and stored['answers']['n']==0


def test_bson_int64_limit_is_checked_without_numeric_coercion(ctx):
    survey=publish(ctx,multiple=True,number_limits={})
    key=uuid4().hex
    invalid=post(ctx,survey,key=key,answers={'s':'b','t':'完成','n':2**63})
    assert invalid.status_code==422 and 'MongoDB' in invalid.json()['detail']
    assert ctx[0].app.state.db.responses.count_documents({})==0
    valid=post(ctx,survey,key=key,answers={'s':'b','t':'完成','n':2**63-1})
    assert valid.status_code==201,valid.text
    assert ctx[0].app.state.db.responses.find_one({'_id':valid.json()['id']})['answers']['n']==2**63-1
