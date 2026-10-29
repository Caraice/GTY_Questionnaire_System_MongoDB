"""A creates/shares; B imports/publishes; immutable history survives evolution."""
from copy import deepcopy
from bson import BSON
import pytest

from test_library_api import ctx, create_question, question, pytestmark
from test_library_versions import append, share, revoke


@pytest.mark.parametrize('kind,answer',[('single','a'),('multiple',['a']),('text','旧回答'),('number',0)])
def test_shared_version_end_to_end_preserves_frozen_answers_and_statistics(ctx,kind,answer):
    c,a,b=ctx;db=c.app.state.db;v1=create_question(ctx,kind);g1=share(ctx,v1)
    def req(method,url,body=None,auth=b,status=200):
        r=c.request(method,url,headers=auth,**({'json':body} if body is not None else {}))
        assert r.status_code==status,r.text
        return r.json()
    def survey(title):return req('POST','/api/surveys',{'title':title,'allow_anonymous':True,'allow_multiple':True},status=201)
    def import_(s,v,status=200):return req('POST',f'/api/surveys/{s["id"]}/imports',{
        'expected_version':s['version'],'template_id':v['id'],'template_version_id':v['latest']['id']},status=status)
    old=import_(survey('B旧问卷'),v1);instance=old['questions'][0]['id']
    old=req('POST',f'/api/surveys/{old["id"]}/publish',{'expected_version':old['version']})
    req('POST',f'/api/fill/{old["share_id"]}/responses',{'survey_version':old['published_version'],
        'submission_key':'journey-original','anonymous':True,'answers':{instance:answer}},status=201)
    old_doc=BSON.encode(db.surveys.find_one({'_id':old['id']}))
    response_doc=BSON.encode(db.responses.find_one({'survey_id':old['id']}))
    version_doc=BSON.encode(db.question_versions.find_one({'_id':v1['latest']['id']}))
    statistics=req('GET',f'/api/surveys/{old["id"]}/statistics')
    draft=import_(survey('B可主动升级的草稿'),v1);draft_id=draft['questions'][0]['id']
    v2=append(ctx,v1,{'id':'edit',**deepcopy(v1['latest']['content']),'title':'新版题干'})
    assert req('GET',f'/api/surveys/{old["id"]}/statistics')==statistics
    # A owning the shared definition still does not gain B's response permission.
    req('GET',f'/api/surveys/{old["id"]}/statistics',auth=a,status=404)
    req('POST',f'/api/surveys/{draft["id"]}/instances/{draft_id}/version',{
        'expected_version':draft['version'],'template_version_id':v2['latest']['id']},status=404)
    g2=share(ctx,v2)
    updated=req('POST',f'/api/surveys/{draft["id"]}/instances/{draft_id}/version',{
        'expected_version':draft['version'],'template_version_id':v2['latest']['id']})
    assert updated['questions'][0]['id']==draft_id and updated['questions'][0]['title']=='新版题干'
    req('POST',f'/api/surveys/{old["id"]}/instances/{instance}/version',{
        'expected_version':old['version'],'template_version_id':v2['latest']['id']},status=409)
    restored=req('POST',f'/api/library/questions/{v1["id"]}/restore',{
        'expected_version':v2['edit_version'],'source_version_id':v1['latest']['id']},auth=a,status=201)
    assert restored['latest']['number']==3 and restored['latest']['content']==v1['latest']['content']
    assert restored['latest']['restored_from_version_id']==v1['latest']['id']
    assert revoke(ctx,v1,g1).status_code==200 and revoke(ctx,v2,g2).status_code==200
    new_draft=survey('撤销后禁止新导入')
    import_(new_draft,v1,status=404);import_(new_draft,v2,status=404)
    assert req('GET',f'/api/surveys/{old["id"]}/statistics')==statistics
    cross=req('GET',f'/api/library/questions/{v1["id"]}/statistics')
    assert next(v for v in cross['versions'] if v['id']==v1['latest']['id'])['metrics']['answer_count']==1
    if kind=='number':assert statistics['questions'][0]['average']==0
    assert BSON.encode(db.surveys.find_one({'_id':old['id']}))==old_doc
    assert BSON.encode(db.responses.find_one({'survey_id':old['id']}))==response_doc
    assert BSON.encode(db.question_versions.find_one({'_id':v1['latest']['id']}))==version_doc
    assert db.surveys.find_one({'_id':new_draft['id']})['questions']==[]
