"""Immutable history, recoverable CAS appends, version grants and explicit draft updates."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module
from threading import Barrier
from uuid import uuid4

from pymongo.collection import Collection
from pymongo.errors import AutoReconnect
import pytest

from app.repositories.library import LibraryRepository
from app.services.library import LibraryService
from test_library_api import ctx, question, create_question, create_survey, imported, pytestmark


def append(ctx, item, content=None, expected_status=201):
    c, h, _ = ctx
    q = content or {'id': 'edit', **deepcopy(item['latest']['content'])}
    r = c.post(f'/api/library/questions/{item["id"]}/versions', headers=h, json={
        'expected_version': item['edit_version'], 'base_version_id': item['latest']['id'], 'question': q})
    assert r.status_code == expected_status, r.text
    return r.json()


def share(ctx, item, version_id=None):
    c, h, _ = ctx
    r = c.post(f'/api/library/questions/{item["id"]}/versions/{version_id or item["latest"]["id"]}/shares',
               headers=h, json={'username': 'Other'})
    assert r.status_code == 200, r.text
    return r.json()


def revoke(ctx, item, grant):
    c, h, _ = ctx
    return c.request('DELETE', f'/api/library/questions/{item["id"]}/versions/{grant["version_id"]}/shares/{grant["grantee_id"]}',
                     headers=h, json={'expected_version': grant['revision']})


def update_instance(ctx, survey, instance_id, version_id, status=200, auth=None):
    c, h, _ = ctx
    r = c.post(f'/api/surveys/{survey["id"]}/instances/{instance_id}/version', headers=auth or h,
               json={'expected_version': survey['version'], 'template_version_id': version_id})
    assert r.status_code == status, r.text
    return r.json()


def test_history_reports_each_change_and_restore_v1_as_v4_without_mutating_old_versions(ctx):
    c, h, _ = ctx
    v1 = create_question(ctx, 'multiple')
    old = deepcopy(c.app.state.db.question_versions.find_one({'_id': v1['latest']['id']}))
    q = {'id': 'edit', **deepcopy(v1['latest']['content'])}
    q['title'] = '改过的题干'; q['options'][0]['label'] = '改过的选项'
    q['validation'] = {'min_choices': 1, 'max_choices': 2}
    v2 = append(ctx, v1, q)
    q['required'] = False; q['options'].reverse()
    v3 = append(ctx, v2, q)
    base = '/api/library/questions/' + v1['id']
    detail = c.get(base, headers=h).json()
    assert [v['number'] for v in detail['versions']] == [3, 2, 1]
    changes = detail['versions'][1]['changes']
    assert changes['title'][0] == {'field': 'title', 'before': '复用multiple', 'after': '改过的题干'}
    assert changes['options'][0]['before'] == '甲' and changes['options'][0]['after'] == '改过的选项'
    assert {x['field'] for x in changes['validation']} == {'exact_choices', 'min_choices', 'max_choices'}
    assert any(x['field'] == 'order' for x in detail['versions'][0]['changes']['options'])
    r = c.post(base + '/restore', headers=h, json={'expected_version': v3['edit_version'], 'source_version_id': v1['latest']['id']})
    assert r.status_code == 201, r.text
    v4 = r.json()['latest']
    assert v4['number'] == 4 and v4['content'] == v1['latest']['content']
    assert v4['restored_from_version_id'] == v1['latest']['id'] and v4['parent_version_id'] == v3['latest']['id']
    assert c.app.state.db.question_versions.find_one({'_id': v1['latest']['id']}) == old
    assert c.app.state.db.question_versions.count_documents({'template_id': v1['id']}) == 4
    assert c.post(base+'/restore',headers=h,json={'expected_version':v3['edit_version'],'source_version_id':v1['latest']['id']}).status_code == 409


def test_save_as_independent_question_keeps_derived_origin_and_original_bank_membership(ctx):
    c, h, _ = ctx
    item = create_question(ctx)
    bank = c.post('/api/question-banks', headers=h, json={'name': '原题库'}).json()
    c.put(f'/api/question-banks/{bank["id"]}/questions/{item["id"]}', headers=h)
    q = {**question(), 'title': '另存的新题'}
    r = c.post(f'/api/library/questions/{item["id"]}/fork', headers=h,
               json={'source_version_id': item['latest']['id'], 'question': q})
    assert r.status_code == 201, r.text
    fork = r.json()
    assert fork['id'] != item['id'] and fork['latest']['number'] == 1
    assert fork['latest']['derived_from_version_id'] == item['latest']['id']
    assert fork['latest']['parent_version_id'] is None and fork['origin']['template_id'] == item['id']
    assert c.app.state.db.question_bank_memberships.count_documents({'template_id': fork['id']}) == 0
    fork = append(ctx, fork, {**q, 'title': '只改变独立新题'})
    assert c.get(f'/api/library/questions/{item["id"]}/versions', headers=h).json()[0] == item['latest']
    assert c.app.state.db.question_bank_memberships.count_documents({'template_id': item['id']}) == 1


def test_shared_exact_version_is_read_only_and_fork_becomes_recipient_owned(ctx):
    c, h, other = ctx
    v1 = create_question(ctx)
    v2 = append(ctx, v1, {**question(), 'title': '只共享这个版本'})
    grant = share(ctx, v2)
    base = '/api/library/questions/' + v1['id']
    versions = c.get(base+'/versions',headers=other).json()
    assert [v['id'] for v in versions] == [v2['latest']['id']]
    detail = c.get(base,headers=other).json()
    assert detail['can_edit'] is False and detail['versions'][0]['changes'] is None
    assert '复用number' not in str(detail)
    assert c.get('/api/library/shared',headers=other).json()[0]['latest']['id'] == v2['latest']['id']
    assert c.post(base+'/versions',headers=other,json={'expected_version':v2['edit_version'],
        'base_version_id':v2['latest']['id'],'question':question()}).status_code == 404
    assert c.post(base+'/restore',headers=other,json={'expected_version':2,'source_version_id':v2['latest']['id']}).status_code == 404
    assert c.get(base+f'/versions/{v2["latest"]["id"]}/shares',headers=other).status_code == 404
    recipient_ctx = (c,other,h)
    recipient_s = imported(recipient_ctx, create_survey(recipient_ctx), v2)
    imported(recipient_ctx, recipient_s, v1, expected_status=404)
    r = c.post(base+'/fork',headers=other,json={'source_version_id':v2['latest']['id'],'question':{**question(),'title':'我的改编'}})
    assert r.status_code == 201, r.text
    fork = r.json()
    assert fork['owner_id'] == grant['grantee_id'] and fork['latest']['derived_from_version_id'] == v2['latest']['id']
    assert revoke(ctx,v2,grant).status_code == 200
    assert c.get(base,headers=other).status_code == 404
    assert c.get('/api/library/shared',headers=other).json() == []
    imported(recipient_ctx,recipient_s,v2,expected_status=404)
    assert c.post(base+'/fork',headers=other,json={'source_version_id':v2['latest']['id'],'question':question()}).status_code == 404
    assert c.get('/api/library/questions/'+fork['id'],headers=other).status_code == 200
    # Revoked imported snapshots still publish and accept answers from the saved definition.
    published = c.post('/api/surveys/'+recipient_s['id']+'/publish',headers=other,json={'expected_version':recipient_s['version']}).json()
    r = c.post('/api/fill/'+published['share_id']+'/responses',headers=other,json={
        'survey_version':published['published_version'],'submission_key':uuid4().hex,'answers':{published['questions'][0]['id']:0}})
    assert r.status_code == 201,r.text
    assert import_module('database.migrations.002_question_library').migrate(c.app.state.db,apply=True)['created']['question_templates'] == 0


def test_usage_lists_every_instance_status_and_never_other_users_private_surveys(ctx):
    c, h, other = ctx
    item = create_question(ctx)
    own = imported(ctx,create_survey(ctx),item); own=imported(ctx,own,item)
    published = imported(ctx,create_survey(ctx),item)
    published=c.post('/api/surveys/'+published['id']+'/publish',headers=h,json={'expected_version':published['version']}).json()
    closed = imported(ctx,create_survey(ctx),item)
    closed=c.post('/api/surveys/'+closed['id']+'/publish',headers=h,json={'expected_version':closed['version']}).json()
    c.post('/api/surveys/'+closed['id']+'/close',headers=h,json={'expected_version':closed['version']})
    grant=share(ctx,item)
    recipient=(c,other,h); foreign=imported(recipient,create_survey(recipient),item); foreign=imported(recipient,foreign,item)
    base='/api/library/questions/'+item['id']
    usages=c.get(base,headers=h).json()['versions'][0]['usages']
    assert len(usages)==4 and {u['status'] for u in usages}=={'draft','published','closed'}
    assert {u['instance_id'] for u in usages if u['survey_id']==own['id']}=={q['id'] for q in own['questions']}
    assert all(u['survey_id']!=foreign['id'] for u in usages)
    theirs=c.get(base,headers=other).json()['versions'][0]['usages']
    assert len(theirs)==2 and {u['survey_id'] for u in theirs}=={foreign['id']}
    assert c.get('/api/surveys/'+own['id'],headers=other).status_code==404


def test_draft_new_version_hint_explicit_update_keeps_instance_and_published_stays_pinned(ctx):
    c,h,_=ctx; item=create_question(ctx)
    s=imported(ctx,create_survey(ctx),item); s=imported(ctx,s,item)
    ids=[q['id'] for q in s['questions']]
    v2=append(ctx,item,{**question(),'title':'新题干'})
    fresh=c.get('/api/surveys/'+s['id'],headers=h).json()
    assert [q['title'] for q in fresh['questions']]==['复用number']*2
    assert all(x['available_update']['id']==v2['latest']['id'] for x in fresh['question_sources'])
    updated=update_instance(ctx,fresh,ids[0],v2['latest']['id'])
    assert [q['id'] for q in updated['questions']]==ids
    assert [q['title'] for q in updated['questions']]==['新题干','复用number']
    assert updated['question_sources'][1]['template_version_id']==item['latest']['id']
    update_instance(ctx,fresh,ids[0],v2['latest']['id'],status=409)
    s=c.post('/api/surveys/'+s['id']+'/publish',headers=h,json={'expected_version':updated['version']}).json()
    v3=append(ctx,v2,{**question(),'title':'版本三'})
    final=c.get('/api/surveys/'+s['id'],headers=h).json()
    assert all(x['available_update'] is None for x in final['question_sources'])
    update_instance(ctx,final,ids[0],v3['latest']['id'],status=409)
    assert c.get('/api/surveys/'+s['id'],headers=h).json()['questions']==s['questions']


@pytest.mark.parametrize('change',['remove_used','change_type','remove_unused'])
def test_draft_update_revalidates_option_and_rule_references_atomically(ctx,change):
    c,h,_=ctx
    q=question('single');q['options'].append({'id':'c','label':'丙'})
    r=c.post('/api/library/questions',headers=h,json={'question':q});item=r.json()
    s=imported(ctx,create_survey(ctx),item); instance=s['questions'][0]['id']
    s=c.put('/api/surveys/'+s['id']+'/rules',headers=h,json={'expected_version':s['version'],
        'rules':[{'source_question_id':instance,'operator':'eq','value':'a','target_question_id':'END'}]}).json()
    if change=='change_type': edited=question('text')
    else:
        edited=deepcopy(q);edited['options']=[o for o in edited['options'] if o['id']!=('a' if change=='remove_used' else 'c')]
    v2=append(ctx,item,edited)
    before=deepcopy(c.app.state.db.surveys.find_one({'_id':s['id']}))
    updated=update_instance(ctx,s,instance,v2['latest']['id'],status=200 if change=='remove_unused' else 422)
    if change=='remove_unused':
        assert updated['rules']==s['rules'] and updated['questions'][0]['id']==instance
    else: assert c.app.state.db.surveys.find_one({'_id':s['id']})==before


def test_shared_new_version_hint_only_uses_granted_versions(ctx):
    c,h,other=ctx;v1=create_question(ctx);share(ctx,v1)
    recipient=(c,other,h);s=imported(recipient,create_survey(recipient),v1)
    v2=append(ctx,v1,{**question(),'title':'尚未共享的新版本'})
    endpoint='/api/surveys/'+s['id']
    assert c.get(endpoint,headers=other).json()['question_sources'][0]['available_update'] is None
    update_instance(ctx,s,s['questions'][0]['id'],v2['latest']['id'],auth=other,status=404)
    grant=share(ctx,v2)
    assert c.get(endpoint,headers=other).json()['question_sources'][0]['available_update']['id']==v2['latest']['id']
    s=update_instance(ctx,s,s['questions'][0]['id'],v2['latest']['id'],auth=other)
    assert revoke(ctx,v2,grant).status_code==200
    assert c.get(endpoint,headers=other).json()['questions'][0]['title']=='尚未共享的新版本'


def test_concurrent_version_saves_have_one_winner_and_preserve_immutable_history(ctx,monkeypatch):
    c,h,_=ctx;item=create_question(ctx)
    barrier,original=Barrier(2),LibraryRepository.append_version
    def synchronized(self,*args,**kwargs):
        barrier.wait(timeout=10);return original(self,*args,**kwargs)
    monkeypatch.setattr(LibraryRepository,'append_version',synchronized)
    path=f'/api/library/questions/{item["id"]}/versions'
    payload={'expected_version':1,'base_version_id':item['latest']['id'],'question':question()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.post(path,headers=h,json=payload),range(2)))
    assert sorted(r.status_code for r in results)==[201,409]
    db=c.app.state.db
    assert db.question_versions.count_documents({'template_id':item['id']})==2
    stored=db.question_templates.find_one({'_id':item['id']})
    assert stored['edit_version']==2 and 'pending_version' not in stored


@pytest.mark.parametrize('failure',['before_version_insert','before_head_commit'])
def test_interrupted_version_append_recovers_once_on_reload(ctx,monkeypatch,failure):
    c,h,_=ctx;item=create_question(ctx);fired=False
    original=LibraryRepository.recover_pending if failure=='before_version_insert' else Collection.update_one
    if failure=='before_version_insert':
        def fail_once(self,template):
            nonlocal fired
            if template.get('pending_version') and not fired:
                fired=True;raise AutoReconnect('injected interruption')
            return original(self,template)
        monkeypatch.setattr(LibraryRepository,'recover_pending',fail_once)
    else:
        def fail_once(self,criteria,update,*args,**kwargs):
            nonlocal fired
            if self.name=='question_templates' and '$unset' in update and not fired:
                fired=True;raise AutoReconnect('injected commit interruption')
            return original(self,criteria,update,*args,**kwargs)
        monkeypatch.setattr(Collection,'update_one',fail_once)
    append(ctx,item,{**question(),'title':'应只产生一次'},expected_status=503)
    assert fired
    for _ in range(2):
        detail=c.get('/api/library/questions/'+item['id'],headers=h).json()
        assert [v['number'] for v in detail['versions']]==[2,1]
        assert detail['edit_version']==2
    assert c.app.state.db.question_versions.count_documents({'template_id':item['id']})==2


def test_revoke_between_preview_and_admission_denies_new_import(ctx,monkeypatch):
    c,h,other=ctx;item=create_question(ctx);grant=share(ctx,item)
    recipient=(c,other,h);s=create_survey(recipient)
    original=LibraryService.snapshot
    def after_preview(self,*args,**kwargs):
        value=original(self,*args,**kwargs)
        timestamp=c.app.state.db.question_grants.find_one({'_id':grant['id']})['updated_at']
        self.repo.revoke(item['id'],item['latest']['id'],grant['grantee_id'],grant['revision'],timestamp)
        return value
    monkeypatch.setattr(LibraryService,'snapshot',after_preview)
    imported(recipient,s,item,expected_status=403)
    assert c.app.state.db.surveys.find_one({'_id':s['id']})['questions']==[]


def test_share_revision_conflict_and_unregistered_or_self_target(ctx):
    c,h,_=ctx;item=create_question(ctx)
    path=f'/api/library/questions/{item["id"]}/versions/{item["latest"]["id"]}/shares'
    assert c.post(path,headers=h,json={'username':'missing'}).status_code==404
    assert c.post(path,headers=h,json={'username':'Owner'}).status_code==422
    grant=share(ctx,item);new_grant=share(ctx,item)
    assert new_grant['revision']==grant['revision']+1
    assert revoke(ctx,item,grant).status_code==409
    assert revoke(ctx,item,new_grant).status_code==200
    assert c.app.state.db.question_grants.count_documents({})==1


def test_admission_before_revoke_can_finish_but_later_import_is_denied(ctx,monkeypatch):
    c,h,other=ctx;item=create_question(ctx);grant=share(ctx,item)
    recipient=(c,other,h);s=create_survey(recipient)
    original=LibraryService.admit_source
    def admitted_then_revoke(self,source,user):
        original(self,source,user)
        timestamp=c.app.state.db.question_grants.find_one({'_id':grant['id']})['updated_at']
        self.repo.revoke(item['id'],item['latest']['id'],grant['grantee_id'],grant['revision'],timestamp)
    monkeypatch.setattr(LibraryService,'admit_source',admitted_then_revoke)
    s=imported(recipient,s,item)
    assert len(s['questions'])==1
    imported(recipient,s,item,expected_status=404)
    assert c.app.state.db.question_grants.find_one({'_id':grant['id']})['admissions']==1


def test_migrated_instance_explicit_update_and_repeated_migration_preserve_origin(ctx):
    c,h,_=ctx;s=create_survey(ctx)
    s=c.put('/api/surveys/'+s['id']+'/questions',headers=h,json={'expected_version':1,'questions':[question()]}).json()
    migration=import_module('database.migrations.002_question_library');db=c.app.state.db
    migration.migrate(db,apply=True)
    item=c.get('/api/library/questions',headers=h).json()[0]
    legacy=deepcopy(db.question_usages.find_one({'survey_id':s['id']}))
    v2=append(ctx,item,{**question(),'title':'明确升级迁移题'})
    updated=update_instance(ctx,s,'original_local_id',v2['latest']['id'])
    assert updated['questions'][0]['id']=='original_local_id'
    assert updated['questions'][0]['title']=='明确升级迁移题'
    for _ in range(2):
        assert migration.migrate(db,apply=True)['created']=={name:0 for name in migration.TARGETS}
    assert db.question_usages.find_one({'survey_id':s['id']})==legacy
    assert db.question_versions.count_documents({'template_id':item['id']})==2
