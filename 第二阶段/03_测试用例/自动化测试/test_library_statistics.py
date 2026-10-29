"""Real Mongo aggregation: hand-counted cross-survey instances, privacy and merge safety."""
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
from sys import float_info
from uuid import uuid4

from bson import BSON
from pymongo.collection import Collection
import pytest

from app.domain.question_content import content_of, content_hash
from test_library_api import ctx, question, create_survey, imported, pytestmark
from test_library_versions import append, share, revoke


def new_question(ctx, kind='number', unit=None):
    c,h,_=ctx;q=question(kind)
    if unit: q['unit']=unit
    r=c.post('/api/library/questions',headers=h,json={'question':q})
    assert r.status_code==201,r.text
    return r.json()


def publish(ctx,survey):
    c,h,_=ctx;r=c.post('/api/surveys/'+survey['id']+'/publish',headers=h,json={'expected_version':survey['version']})
    assert r.status_code==200,r.text
    return r.json()


def submit(ctx,survey,answers):
    c,h,_=ctx;r=c.post('/api/fill/'+survey['share_id']+'/responses',headers=h,json={
        'survey_version':survey['published_version'],'submission_key':uuid4().hex,'answers':answers,
        'anonymous':survey['allow_anonymous']})
    assert r.status_code==201,r.text


def summary(ctx,item):
    c,h,_=ctx;r=c.get(f'/api/library/questions/{item["id"]}/statistics',headers=h)
    assert r.status_code==200,r.text
    return r.json()


def merged(ctx,items,*,mappings=None,units=None,status=200):
    c,h,_=ctx;payload={'version_ids':[item['latest']['id'] for item in items],
        'base_version_id':items[0]['latest']['id'],'option_mappings':mappings or {},'unit_confirmations':units or {}}
    r=c.post(f'/api/library/questions/{items[0]["id"]}/statistics/merge',headers=h,json=payload)
    assert r.status_code==status,r.text
    return r.json()


def by_version(result):
    return {v['id']:v for v in result['versions']}


def raw_response(ctx,survey,answers,path,*,version=None):
    c,_,_=ctx;key=uuid4().hex
    c.app.state.db.responses.insert_one({'_id':key,'schema_version':1,'survey_id':survey['id'],
        'survey_version':version or survey['published_version'],'respondent_key':'same-anonymous-marker',
        'attempt_key':key,'submission_key':key,'anonymous':True,'answers':answers,'path':path,
        'submitted_at':datetime.now(timezone.utc)})


def hand_fixture(ctx):
    c,h,other=ctx;v1=new_question(ctx,unit='kg')
    v2=append(ctx,v1,{**question(),'title':'重量新版','unit':'kg','validation':{'minimum':0,'maximum':20,'integer_only':True}})
    v3=append(ctx,v2,{**question(),'title':'以克计量','unit':'g'})
    a=imported(ctx,create_survey(ctx),v1);a=imported(ctx,a,v1);a=imported(ctx,a,v2)
    qa,qb,qc=[q['id'] for q in a['questions']]
    a=c.put('/api/surveys/'+a['id']+'/rules',headers=h,json={'expected_version':a['version'],'rules':[
        {'source_question_id':qa,'operator':'eq','value':4,'target_question_id':'END'}]}).json()
    a=publish(ctx,a);submit(ctx,a,{qa:0,qb:2,qc:15});submit(ctx,a,{qa:4})
    b=publish(ctx,imported(ctx,create_survey(ctx),v1));submit(ctx,b,{b['questions'][0]['id']:6})
    c.post('/api/surveys/'+b['id']+'/close',headers=h,json={'expected_version':b['version']})
    empty=publish(ctx,imported(ctx,create_survey(ctx),v1))
    draft=imported(ctx,create_survey(ctx),v1)
    changed=imported(ctx,create_survey(ctx),v1);changed['questions'][0]['title']='本地修改'
    changed=c.put('/api/surveys/'+changed['id']+'/questions',headers=h,json={
        'expected_version':changed['version'],'questions':changed['questions']}).json()
    changed=publish(ctx,changed);submit(ctx,changed,{changed['questions'][0]['id']:8})
    # Never trust a stored modified flag in place of checking the actual frozen definition.
    c.app.state.db.surveys.update_one({'_id':changed['id']},{'$set':{'question_sources.0.modified':False}})
    grant=share(ctx,v1);recipient=(c,other,h)
    foreign=publish(recipient,imported(recipient,create_survey(recipient),v1))
    submit(recipient,foreign,{foreign['questions'][0]['id']:9})
    return v1,v2,v3,a,b,empty,draft,changed,foreign,grant


def test_per_version_hand_counts_distinguish_responses_instances_skip_and_zero(ctx,monkeypatch):
    v1,v2,v3,a,b,empty,draft,changed,foreign,grant=hand_fixture(ctx)
    original_find, original_aggregate=Collection.find,Collection.aggregate;pipelines=[]
    def guarded_find(collection,*args,**kwargs):
        assert collection.name!='responses','Do not fetch answer documents into Python'
        return original_find(collection,*args,**kwargs)
    def recorded(collection,pipeline,*args,**kwargs):
        if collection.name=='surveys':pipelines.append(pipeline)
        return original_aggregate(collection,pipeline,*args,**kwargs)
    monkeypatch.setattr(Collection,'find',guarded_find);monkeypatch.setattr(Collection,'aggregate',recorded)
    result=summary(ctx,v1);versions=by_version(result)
    one=versions[v1['latest']['id']]['metrics'];two=versions[v2['latest']['id']]['metrics']
    assert (one['survey_count'],one['response_count'],one['answer_count'],one['average'])==(3,3,4,3)
    assert sorted(one['values'])==[0,2,4,6]
    assert one['excluded_answer_counts']==[{'reason':'实际路径跳过','count':1}]
    assert (two['survey_count'],two['response_count'],two['answer_count'],two['average'])==(1,2,1,15)
    assert versions[v3['latest']['id']]['metrics']['survey_count']==0
    assert foreign['id'] not in str(result) and foreign['questions'][0]['id'] not in str(result)
    excluded={r['survey_id']:'；'.join(r['reasons']) for r in result['excluded_instances']}
    assert '草稿' in excluded[draft['id']] and '本地修改' in excluded[changed['id']]
    assert pipelines and all(p[0]['$match']['owner_id'] for p in pipelines)
    assert any('$lookup' in str(p) and '$getField' in str(p) and '$facet' in str(p) for p in pipelines)


def test_merge_weighted_average_uses_original_limits_and_deduplicates_responses_only(ctx):
    v1,v2,v3,*_=hand_fixture(ctx)
    result=merged(ctx,[v1,v2,v3]);m=result['metrics']
    assert result['performed'] and (m['survey_count'],m['response_count'],m['answer_count'])==(3,3,5)
    assert m['average']==5.4 and sorted(m['values'])==[0,2,4,6,15]
    assert [v['id'] for v in result['included_versions']]==[v1['latest']['id'],v2['latest']['id']]
    assert result['excluded_versions'][0]['id']==v3['latest']['id']
    assert '计量单位不一致' in str(result['excluded_versions'])


def test_shared_question_never_opens_foreign_results_and_revoked_owner_snapshots_still_statistic(ctx):
    c,h,other=ctx;v1,v2,v3,a,b,empty,draft,changed,foreign,grant=hand_fixture(ctx)
    recipient=(c,other,h);r=summary(recipient,v1)
    assert len(r['versions'])==1
    m=r['versions'][0]['metrics'];assert (m['survey_count'],m['response_count'],m['answer_count'],m['average'])==(1,1,1,9)
    assert a['id'] not in str(r)
    merged(recipient,[v1,v2],status=404)
    assert revoke(ctx,v1,grant).status_code==200
    assert summary(recipient,v1)['versions'][0]['metrics']==m
    assert summary(recipient,v1)['source_history_available'] is False
    assert c.get('/api/surveys/'+a['id']+'/statistics',headers=other).status_code==404


def test_grant_without_owned_survey_shows_zero_and_unauthorized_requests_disclose_nothing(ctx):
    c,h,other=ctx;v1=new_question(ctx)
    s=publish(ctx,imported(ctx,create_survey(ctx),v1));submit(ctx,s,{s['questions'][0]['id']:7})
    url=f'/api/library/questions/{v1["id"]}/statistics'
    assert c.get(url).status_code==401 and c.get(url,headers=other).status_code==404
    share(ctx,v1);result=summary((c,other,h),v1)
    assert result['versions'][0]['metrics']['response_count']==0
    assert s['id'] not in str(result) and result['excluded_instances']==[]


@pytest.mark.parametrize('kind',['single','multiple'])
def test_equal_labels_different_ids_require_explicit_mapping(ctx,kind):
    c,h,_=ctx;v1=new_question(ctx,kind)
    q={'id':'edit',**deepcopy(v1['latest']['content'])};q['options'][0]['id']='x';q['options'][1]['id']='y'
    if kind=='multiple':q['validation']={'min_choices':1,'max_choices':2}
    v2=append(ctx,v1,q)
    s=imported(ctx,create_survey(ctx),v1);s=publish(ctx,imported(ctx,s,v2));a,b=[q['id'] for q in s['questions']]
    submit(ctx,s,{a:'a' if kind=='single' else ['a'],b:'x' if kind=='single' else ['x','y']})
    unmerged=merged(ctx,[v1,v2]);assert not unmerged['performed'] and unmerged['metrics'] is None
    assert '选项ID集合不同' in str(unmerged['excluded_versions'])
    # Mapping is deliberately independent of labels; x is mapped to b for single choice.
    mapping={'x':'b','y':'a'} if kind=='single' else {'x':'a','y':'b'}
    result=merged(ctx,[v1,v2],mappings={v2['latest']['id']:mapping});m=result['metrics']
    assert result['performed'] and (m['survey_count'],m['response_count'],m['answer_count'])==(1,1,2)
    counts={o['id']:o['count'] for o in m['options']}
    assert counts==({'a':1,'b':1} if kind=='single' else {'a':2,'b':1})
    if kind=='multiple':assert m['selection_count']==3


@pytest.mark.parametrize('mapping',[{'x':'a'},{'x':'a','y':'a'},{'x':'a','z':'b'},{'x':'a','y':'unknown'}])
def test_invalid_mapping_excluded_without_fallback_to_option_text(ctx,mapping):
    v1=new_question(ctx,'single');q={'id':'edit',**deepcopy(v1['latest']['content'])}
    q['options']=[{'id':'x','label':'甲'},{'id':'y','label':'乙'}];v2=append(ctx,v1,q)
    result=merged(ctx,[v1,v2],mappings={v2['latest']['id']:mapping})
    assert not result['performed'] and '一一对应' in str(result['excluded_versions'])


def test_stable_ids_merge_even_when_labels_changed_and_show_warning(ctx):
    v1=new_question(ctx,'single');q={'id':'edit',**deepcopy(v1['latest']['content'])}
    q['options'].reverse();q['options'][1]['label']='甲的新名称';v2=append(ctx,v1,q)
    s=imported(ctx,create_survey(ctx),v1);s=publish(ctx,imported(ctx,s,v2));a,b=[q['id'] for q in s['questions']]
    submit(ctx,s,{a:'a',b:'a'});result=merged(ctx,[v1,v2])
    assert result['performed'] and result['metrics']['options'][0]=={'id':'a','label':'甲','count':2}
    assert '文字有变化' in str(result['included_versions'])


def test_legacy_unknown_units_require_explicit_confirmation_without_rewriting_versions(ctx):
    c,h,_=ctx;v1=new_question(ctx);v2=append(ctx,v1,{**question(),'title':'另一个旧版'})
    s=imported(ctx,create_survey(ctx),v1);s=publish(ctx,imported(ctx,s,v2));a,b=[q['id'] for q in s['questions']]
    submit(ctx,s,{a:0,b:10})
    before=[BSON.encode(v) for v in c.app.state.db.question_versions.find({'template_id':v1['id']}).sort('number',1)]
    result=merged(ctx,[v1,v2]);assert not result['performed'] and '计量单位未声明' in str(result)
    result=merged(ctx,[v1,v2],units={v1['latest']['id']:' kg ',v2['latest']['id']:'kg'})
    assert result['performed'] and result['metrics']['average']==5
    assert all(v['unit_source']=='本次明确确认' for v in result['included_versions'])
    assert [BSON.encode(v) for v in c.app.state.db.question_versions.find({'template_id':v1['id']}).sort('number',1)]==before
    assert 'unit' not in content_of({'id':'old',**v1['latest']['content']})


def test_declared_units_cannot_be_overridden_and_type_mismatch_is_explained(ctx):
    v1=new_question(ctx,unit='kg');v2=append(ctx,v1,{**question(),'unit':'g'})
    result=merged(ctx,[v1,v2],units={v2['latest']['id']:'kg'})
    assert not result['performed'] and '不能覆盖' in str(result['excluded_versions'])
    v3=append(ctx,v2,question('text'));result=merged(ctx,[v1,v3])
    assert not result['performed'] and '题型' in str(result['excluded_versions'])


@pytest.mark.parametrize('bad',[True,float('nan'),float('inf'),-float('inf'),'0',None,1.5,11])
def test_malformed_historical_numbers_never_count_as_zero_or_valid_answers(ctx,bad):
    item=new_question(ctx);s=publish(ctx,imported(ctx,create_survey(ctx),item));qid=s['questions'][0]['id']
    raw_response(ctx,s,{qid:bad},[qid]);raw_response(ctx,s,{qid:0},[qid]);raw_response(ctx,s,{qid:9},[])
    m=summary(ctx,item)['versions'][0]['metrics']
    assert (m['response_count'],m['answer_count'],m['average'])==(3,1,0)


def test_text_versions_normalize_whitespace_and_use_their_own_limits(ctx):
    v1=new_question(ctx,'text');q=question('text');q['validation']['max_length']=20;v2=append(ctx,v1,q)
    s=publish(ctx,imported(ctx,create_survey(ctx),v1));a=s['questions'][0]['id']
    t=publish(ctx,imported(ctx,create_survey(ctx),v2));b=t['questions'][0]['id']
    raw_response(ctx,s,{a:' \u3000甲\t '},[a]);raw_response(ctx,s,{a:'  '},[a]);raw_response(ctx,s,{a:False},[a])
    raw_response(ctx,t,{b:'123456789012345'},[b]);result=merged(ctx,[v1,v2])
    assert result['performed'] and result['metrics']['response_count']==4 and result['metrics']['answer_count']==2
    assert sorted(result['metrics']['values'])==['123456789012345','甲']


def test_values_preview_is_bounded_but_counts_and_zero_average_use_all_answers(ctx):
    item=new_question(ctx);s=publish(ctx,imported(ctx,create_survey(ctx),item));qid=s['questions'][0]['id']
    for _ in range(101):raw_response(ctx,s,{qid:0},[qid])
    raw_response(ctx,s,{qid:10},[qid],version=999)
    m=summary(ctx,item)['versions'][0]['metrics']
    assert (m['response_count'],m['answer_count'],m['average'])==(101,101,0)
    assert len(m['values'])==100 and m['values_truncated']


def test_m1_migrated_references_and_missing_published_version_remain_compatible(ctx):
    c,h,_=ctx;s=create_survey(ctx)
    s=c.put('/api/surveys/'+s['id']+'/questions',headers=h,json={'expected_version':1,'questions':[question()]}).json()
    s=publish(ctx,s);submit(ctx,s,{'original_local_id':0});db=c.app.state.db
    db.surveys.update_one({'_id':s['id']},{'$unset':{'published_version':''}})
    before=BSON.encode(db.surveys.find_one({'_id':s['id']}))
    migration=import_module('database.migrations.002_question_library');migration.migrate(db,apply=True)
    item=c.get('/api/library/questions',headers=h).json()[0];m=summary(ctx,item)['versions'][0]['metrics']
    assert (m['survey_count'],m['response_count'],m['answer_count'],m['average'])==(1,1,1,0)
    assert BSON.encode(db.surveys.find_one({'_id':s['id']}))==before


def test_bad_source_hash_and_missing_instance_have_visible_owned_exclusion_reasons(ctx):
    c,h,_=ctx;item=new_question(ctx);s=publish(ctx,imported(ctx,create_survey(ctx),item));db=c.app.state.db
    db.surveys.update_one({'_id':s['id']},{'$set':{'question_sources.0.content_hash':'0'*64}})
    result=summary(ctx,item);assert result['versions'][0]['metrics']['survey_count']==0
    assert '摘要校验失败' in str(result['excluded_instances'])
    db.surveys.update_one({'_id':s['id']},{'$set':{'questions':[]}})
    assert '实例已从问卷移除' in str(summary(ctx,item)['excluded_instances'])


@pytest.mark.parametrize('bad',['duplicate','unknown_base','extra_mapping','blank_unit','too_few'])
def test_merge_contract_rejects_ambiguous_or_unselected_configuration(ctx,bad):
    c,h,_=ctx;v1=new_question(ctx);v2=append(ctx,v1)
    ids=[v1['latest']['id'],v2['latest']['id']]
    payload={'version_ids':ids,'base_version_id':ids[0]}
    if bad=='duplicate':payload['version_ids']=[ids[0],ids[0]]
    if bad=='unknown_base':payload['base_version_id']='unknown'
    if bad=='extra_mapping':payload['option_mappings']={'unknown':{'a':'b'}}
    if bad=='blank_unit':payload['unit_confirmations']={ids[0]:'  '}
    if bad=='too_few':payload['version_ids']=[ids[0]]
    assert c.post(f'/api/library/questions/{v1["id"]}/statistics/merge',headers=h,json=payload).status_code==422


def test_unit_validation_and_old_normalized_hash_stability(ctx):
    c,h,_=ctx
    for unit in ['  ',True,'x'*65]:
        assert c.post('/api/library/questions',headers=h,json={'question':{**question(),'unit':unit}}).status_code==422
    q=question();old_content=content_of(q)
    assert content_of({**q,'unit':None})==old_content
    item=new_question(ctx,unit='kg');detail=c.get('/api/library/questions/'+item['id'],headers=h).json()
    v2=append(ctx,item,{**question(),'unit':'g'})
    detail=c.get('/api/library/questions/'+item['id'],headers=h).json()
    assert {'field':'unit','before':'kg','after':'g'} in detail['versions'][0]['changes']['other']


def test_anonymous_responses_are_not_deduplicated_by_same_marker_across_surveys(ctx):
    item=new_question(ctx)
    for value in [0,8]:
        s=publish(ctx,imported(ctx,create_survey(ctx),item));qid=s['questions'][0]['id']
        raw_response(ctx,s,{qid:value},[qid])
    m=summary(ctx,item)['versions'][0]['metrics']
    assert (m['survey_count'],m['response_count'],m['answer_count'],m['average'])==(2,2,2,4)


def test_multiple_invalid_historical_arrays_are_excluded_with_reasons(ctx):
    item=new_question(ctx,'multiple');s=publish(ctx,imported(ctx,create_survey(ctx),item));qid=s['questions'][0]['id']
    for bad in [True,'a',{'a':1},['a','a'],['unknown'],[],['a','b']]:raw_response(ctx,s,{qid:bad},[qid])
    raw_response(ctx,s,{qid:['a']},[qid]);m=summary(ctx,item)['versions'][0]['metrics']
    assert (m['response_count'],m['answer_count'],m['selection_count'])==(8,1,1)
    assert m['excluded_answer_counts']==[{'reason':'空白或不符合原版本题型/限制','count':7}]


def test_large_finite_numbers_use_decimal_accumulator_for_cross_survey_average(ctx):
    c,h,_=ctx;q={**question(),'validation':{}};item=c.post('/api/library/questions',headers=h,json={'question':q}).json()
    for value in [float_info.max,float_info.max,-float_info.max]:
        s=publish(ctx,imported(ctx,create_survey(ctx),item));raw_response(ctx,s,{s['questions'][0]['id']:value},[s['questions'][0]['id']])
    m=summary(ctx,item)['versions'][0]['metrics']
    assert m['answer_count']==3 and m['average']==pytest.approx(float_info.max/3)


def test_invalid_reference_definition_is_explained_instead_of_crashing_aggregation(ctx):
    c,h,_=ctx;v1=new_question(ctx,'single');s=publish(ctx,imported(ctx,create_survey(ctx),v1));db=c.app.state.db
    db.question_versions.update_one({'_id':v1['latest']['id']},{'$set':{'content.options':[]}})
    result=summary(ctx,v1)
    assert result['versions'][0]['metrics'] is None and result['versions'][0]['definition_errors']
    assert result['excluded_instances']
