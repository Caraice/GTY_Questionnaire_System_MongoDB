"""Rule editor contracts over real MongoDB, including text and published locks."""
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.getenv('RUN_MONGO_TESTS') != '1',reason='Requires real MongoDB')]


@pytest.fixture
def ctx():
    settings=Settings(os.getenv('MONGODB_URI','mongodb://127.0.0.1:27019'),'survey_test_'+uuid4().hex)
    with TestClient(create_app(settings)) as client:
        auth={}
        for username in ['Owner','Reader']:
            data={'username':username,'password':'Test-pass-123'}
            assert client.post('/api/auth/register',json=data).status_code==201
            auth[username]={'Authorization':'Bearer '+client.post('/api/auth/login',json=data).json()['access_token']}
        survey=client.post('/api/surveys',headers=auth['Owner'],json={'title':'可配置跳转验收','allow_anonymous':True,'allow_multiple':True}).json()
        questions=[
            {'id':'s','type':'single','title':'入口','required':True,'options':[{'id':'a','label':'A'},{'id':'b','label':'B'}]},
            {'id':'m','type':'multiple','title':'兴趣','required':True,'options':[{'id':'a','label':'阅读'},{'id':'b','label':'运动'}]},
            {'id':'n','type':'number','title':'年龄','required':True,'validation':{'minimum':0,'maximum':120,'integer_only':True}},
            {'id':'t','type':'text','title':'城市','required':True,'validation':{'min_length':1,'max_length':100}},
            {'id':'last','type':'number','title':'最后一题','required':True},
        ]
        base='/api/surveys/'+survey['id']
        assert client.put(base+'/questions',headers=auth['Owner'],json={'expected_version':1,'questions':questions}).status_code==200
        yield client,auth,survey,base
    print('Retained rule editor test database: '+settings.mongodb_db)


@pytest.mark.parametrize('source,operator,value,answers,path',[
    ('s','eq','a',{'s':'a'},['s']),
    ('m','contains_any',['a'],{'s':'b','m':['a']},['s','m']),
    ('m','contains_all',['a','b'],{'s':'b','m':['a','b']},['s','m']),
    ('n','between',[0,18],{'s':'b','m':['b'],'n':0},['s','m','n']),
    ('t','eq',' 北京 ',{'s':'b','m':['b'],'n':20,'t':' 北京 '},['s','m','n','t']),
    ('t','contains','北京',{'s':'b','m':['b'],'n':20,'t':' 我在北京 '},['s','m','n','t']),
])
def test_configure_publish_submit_and_aggregate_branches(ctx,source,operator,value,answers,path):
    client,auth,survey,base=ctx
    rule={'source_question_id':source,'operator':operator,'value':value,'target_question_id':'END'}
    result=client.put(base+'/rules',headers=auth['Owner'],json={'expected_version':2,'rules':[rule]})
    assert result.status_code==200,result.text
    assert result.json()['rules']==[rule]
    assert client.post(base+'/publish',headers=auth['Owner'],json={'expected_version':3}).status_code==200
    submitted=client.post('/api/fill/'+survey['share_id']+'/responses',headers=auth['Reader'],json={
        'survey_version':4,'submission_key':uuid4().hex,'anonymous':True,'answers':answers})
    assert submitted.status_code==201,submitted.text
    assert submitted.json()['path']==path
    stored=client.app.state.db.responses.find_one({'_id':submitted.json()['id']})
    assert stored['path']==path and set(stored['answers'])==set(path)
    if source=='t': assert stored['answers']['t']==answers['t'].strip()
    stats=client.get(base+'/statistics',headers=auth['Owner']).json()
    assert stats['total_submissions']==1
    assert [q['answered_count'] for q in stats['questions']]==[int(q['id'] in path) for q in stats['questions']]


@pytest.mark.parametrize('operator,value,text', [('eq','北京','上海'),('contains','北京','上海')])
def test_text_nonmatch_continues_sequentially(ctx,operator,value,text):
    client,auth,survey,base=ctx
    rule={'source_question_id':'t','operator':operator,'value':value,'target_question_id':'END'}
    assert client.put(base+'/rules',headers=auth['Owner'],json={'expected_version':2,'rules':[rule]}).status_code==200
    assert client.post(base+'/publish',headers=auth['Owner'],json={'expected_version':3}).status_code==200
    result=client.post('/api/fill/'+survey['share_id']+'/responses',headers=auth['Reader'],json={
        'survey_version':4,'submission_key':uuid4().hex,'answers':{'s':'b','m':['b'],'n':20,'t':text,'last':0}})
    assert result.status_code==201 and result.json()['path']==['s','m','n','t','last']


def test_rules_editor_permissions_version_conflict_freeze_and_safe_page(ctx):
    client,auth,survey,base=ctx
    rule={'source_question_id':'s','operator':'eq','value':'a','target_question_id':'n'}
    request={'expected_version':2,'rules':[rule]}
    assert client.get('/rules/'+survey['id']).status_code==200
    assert client.put(base+'/rules',json=request).status_code==401
    assert client.put(base+'/rules',headers=auth['Reader'],json=request).status_code==404
    assert client.put(base+'/rules',headers=auth['Owner'],json=request).status_code==200
    assert client.put(base+'/rules',headers=auth['Owner'],json={'expected_version':2,'rules':[]}).status_code==409
    assert client.get(base,headers=auth['Owner']).json()['rules']==[rule]
    assert client.post(base+'/publish',headers=auth['Owner'],json={'expected_version':3}).status_code==200
    assert client.put(base+'/rules',headers=auth['Owner'],json={'expected_version':4,'rules':[]}).status_code==409
    assert client.get(base,headers=auth['Owner']).json()['rules']==[rule]
