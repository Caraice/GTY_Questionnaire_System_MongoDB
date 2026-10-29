from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from pymongo.collection import Collection
from pymongo.errors import AutoReconnect

from test_library_api import ctx, create_question, create_survey, imported, question, pytestmark
from test_library_versions import append
from test_library_statistics import publish, submit
from app.repositories.library import LibraryRepository


def test_question_bank_and_history_pages_preserve_boundaries(ctx):
    c,h,other=ctx
    for i in range(3):
        c.post('/api/question-banks',headers=h,json={'name':str(i)})
        create_question(ctx)
    path='/api/library/questions?page_size=2&page='
    a,b=[c.get(path+str(p),headers=h).json() for p in [1,2]]
    assert len(a['items'])==2 and a['has_more'] and len(b['items'])==1 and not b['has_more']
    assert not {x['id'] for x in a['items']} & {x['id'] for x in b['items']}
    assert not c.get(path+'1',headers=other).json()['items']
    assert len(c.get('/api/question-banks?page=2&page_size=2',headers=h).json()['items'])==1
    item=create_question(ctx)
    for i in range(3): item=append(ctx,item,{**question(),'title':str(i)})
    url=f'/api/library/questions/{item["id"]}/history-page?page_size=2'
    first=c.get(url,headers=h).json();second=c.get(url+'&before='+str(first['next_before']),headers=h).json()
    assert [v['number'] for v in first['versions']]==[4,3]
    assert [v['number'] for v in second['versions']]==[2,1]
    assert second['next_before'] is None and first['edit_version']==4
    assert c.get(url,headers=other).status_code==404


def test_reference_pages_keep_duplicate_instances_and_owner_scope(ctx):
    c,h,other=ctx;item=create_question(ctx);s=create_survey(ctx)
    for _ in range(3): s=imported(ctx,s,item)
    url=f'/api/library/questions/{item["id"]}/versions/{item["latest"]["id"]}/usages?page_size=2'
    a=c.get(url,headers=h).json();b=c.get(url+'&page=2',headers=h).json()
    assert len(a['items'])==2 and a['has_more'] and len(b['items'])==1 and not b['has_more']
    assert sorted(x['question_number'] for x in a['items']+b['items'])==[1,2,3]
    assert all(not x['modified'] for x in a['items']+b['items'])
    assert c.get(url,headers=other).status_code==404


def test_statistics_values_second_page_keeps_whole_aggregate(ctx):
    c,h,_=ctx;item=create_question(ctx,'text');s=publish(ctx,imported(ctx,create_survey(ctx),item))
    # Persisted API responses; zero duplicates in stable ordering across pages.
    for i in range(101): submit(ctx,s,{s['questions'][0]['id']:str(i)})
    url=f'/api/library/questions/{item["id"]}/statistics'
    a=c.get(url,headers=h).json()['versions'][0]['metrics']
    b=c.get(url+'?value_page=2',headers=h).json()['versions'][0]['metrics']
    assert len(a['values'])==100 and len(b['values'])==1
    assert a['answer_count']==b['answer_count']==101
    assert not set(a['values']) & set(b['values'])
    assert a['values_has_more'] and not b['values_has_more']


@pytest.mark.parametrize('url', ['/api/question-banks?page=0','/api/library/questions?page_size=101&page=1',
    '/api/library/questions/unknown/history-page?before=0','/api/library/questions/unknown/statistics?value_page=0'])
def test_bad_pagination_422(ctx,url):
    c,h,_=ctx;assert c.get(url,headers=h).status_code==422


@pytest.mark.parametrize('after_write',[False,True])
def test_pointer_interruption_or_lost_ack_recovers_without_second_number(ctx,monkeypatch,after_write):
    c,h,_=ctx;item=create_question(ctx);original=Collection.update_one;fired=False
    def fail(self,criteria,update,*args,**kwargs):
        nonlocal fired
        if self.name=='question_templates' and '$unset' in update and not fired:
            fired=True
            if after_write: original(self,criteria,update,*args,**kwargs)
            raise AutoReconnect('injected pointer failure or lost acknowledgement')
        return original(self,criteria,update,*args,**kwargs)
    monkeypatch.setattr(Collection,'update_one',fail)
    append(ctx,item,{**question(),'title':'恢复只追加一次'},expected_status=503)
    for _ in range(2):
        d=c.get(f'/api/library/questions/{item["id"]}',headers=h).json()
        assert d['edit_version']==2 and [v['number'] for v in d['versions']]==[2,1]
    append(ctx,item,question(),expected_status=409)


def test_reserved_immutable_collision_returns_409_without_rewrite(ctx,monkeypatch):
    c,h,_=ctx;item=create_question(ctx);original=LibraryRepository.append_version
    def collision(self,*args,**kwargs): raise ValueError('immutable mismatch')
    monkeypatch.setattr(LibraryRepository,'append_version',collision)
    append(ctx,item,question(),expected_status=409)
    monkeypatch.setattr(LibraryRepository,'append_version',original)
    assert len(c.get(f'/api/library/questions/{item["id"]}/versions',headers=h).json())==1


def test_edit_token_is_not_content_number(ctx):
    c,h,_=ctx;item=create_question(ctx)
    c.app.state.db.question_templates.update_one({'_id':item['id']},{'$set':{'edit_version':8}})
    append(ctx,item,question(),expected_status=409)
    current={**item,'edit_version':8}
    result=append(ctx,current,question())
    assert result['edit_version']==9 and result['latest']['number']==2


def test_lost_reservation_ack_is_recovered_once(ctx,monkeypatch):
    c,h,_=ctx;item=create_question(ctx);original=Collection.find_one_and_update;fired=False
    def fail(self,criteria,update,*args,**kwargs):
        nonlocal fired
        result=original(self,criteria,update,*args,**kwargs)
        if self.name=='question_templates' and 'pending_version' in update.get('$set',{}) and not fired:
            fired=True;raise AutoReconnect('lost reservation acknowledgement')
        return result
    monkeypatch.setattr(Collection,'find_one_and_update',fail)
    append(ctx,item,question(),expected_status=503)
    d=c.get(f'/api/library/questions/{item["id"]}',headers=h).json()
    assert d['edit_version']==2 and [v['number'] for v in d['versions']]==[2,1]


def test_later_writer_cannot_lend_its_edit_token_to_older_content(ctx,monkeypatch):
    c,h,_=ctx;item=create_question(ctx);original=Collection.update_one;fired=False
    def intervening(self,criteria,update,*args,**kwargs):
        nonlocal fired
        result=original(self,criteria,update,*args,**kwargs)
        if self.name=='question_templates' and '$unset' in update and not fired:
            fired=True
            # Another metadata edit after the commit but before its response.
            original(self,{'_id':item['id']},{'$inc':{'edit_version':1}})
        return result
    monkeypatch.setattr(Collection,'update_one',intervening)
    r=append(ctx,item,question())
    assert r['edit_version']==2 and r['latest']['number']==2
    append(ctx,r,question(),expected_status=409)
