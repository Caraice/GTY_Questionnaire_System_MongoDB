"""Bank membership, immutable version import and instance-level answer/rule isolation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib import import_module
import os
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import create_app
from app.domain.question_content import content_hash
from app.repositories.mongo import SurveyRepository
from database.bank_schema import initialize_banks

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('RUN_MONGO_TESTS') != '1', reason='Requires real MongoDB')]


@pytest.fixture
def ctx():
    name = 'survey_library_' + uuid4().hex
    with TestClient(create_app(Settings(os.getenv('MONGODB_URI', 'mongodb://127.0.0.1:27019'), name))) as client:
        auth = {}
        for username in ['Owner', 'Other']:
            credentials = {'username': username, 'password': 'Test-pass-123'}
            assert client.post('/api/auth/register', json=credentials).status_code == 201
            token = client.post('/api/auth/login', json=credentials).json()['access_token']
            auth[username] = {'Authorization': 'Bearer ' + token}
        yield client, auth['Owner'], auth['Other']
    print('\nRetained library test database: ' + name)


def question(kind='number'):
    result = {'id': 'original_local_id', 'type': kind, 'title': '复用' + kind, 'required': True}
    if kind in ('single', 'multiple'):
        result['options'] = [{'id': 'a', 'label': '甲'}, {'id': 'b', 'label': '乙'}]
        if kind == 'multiple': result['validation'] = {'exact_choices': 1}
    elif kind == 'text': result['validation'] = {'min_length': 1, 'max_length': 10}
    else: result['validation'] = {'minimum': 0, 'maximum': 10, 'integer_only': True}
    return result


def create_question(ctx, kind='number'):
    c, h, _ = ctx
    r = c.post('/api/library/questions', headers=h, json={'question': question(kind)})
    assert r.status_code == 201, r.text
    return r.json()


def create_survey(ctx):
    c, h, _ = ctx
    return c.post('/api/surveys', headers=h, json={'title': '重复来源测试', 'allow_multiple': True}).json()


def imported(ctx, survey, template, version_id=None, expected_status=200):
    c, h, _ = ctx
    r = c.post(f'/api/surveys/{survey["id"]}/imports', headers=h, json={
        'expected_version': survey['version'], 'template_id': template['id'],
        'template_version_id': version_id or template['latest']['id']})
    assert r.status_code == expected_status, r.text
    return r.json()


@pytest.mark.parametrize('kind', ['single', 'multiple', 'text', 'number'])
def test_four_types_reuse_models_and_version_content(ctx, kind):
    c, h, _ = ctx
    item = create_question(ctx, kind)
    assert item['latest']['number'] == 1 and 'id' not in item['latest']['content']
    versions = c.get(f'/api/library/questions/{item["id"]}/versions', headers=h).json()
    assert versions == [item['latest']]
    survey = imported(ctx, create_survey(ctx), item)
    q, source = survey['questions'][0], survey['question_sources'][0]
    assert q['id'] != question(kind)['id'] and q['type'] == kind
    assert source['template_id'] == item['id'] and source['template_version_id'] == item['latest']['id']
    assert source['instance_id'] == q['id'] and not source['modified']
    assert {k: v for k, v in q.items() if k != 'id'} == item['latest']['content']


@pytest.mark.parametrize('bad', ['blank_title', 'blank_option', 'duplicate_option', 'count', 'length', 'range', 'boolean_version'])
def test_invalid_content_is_rejected_without_writes(ctx, bad):
    c, h, _ = ctx
    q = question('multiple')
    if bad == 'blank_title': q['title'] = ' \t '
    if bad == 'blank_option': q['options'][0]['label'] = '  '
    if bad == 'duplicate_option': q['options'][1]['id'] = 'a'
    if bad == 'count': q['validation']['exact_choices'] = 3
    if bad == 'length': q = question('text'); q['validation'] = {'min_length': 5, 'max_length': 1}
    if bad == 'range': q = question(); q['validation']['maximum'] = -1
    if bad == 'boolean_version':
        item, survey = create_question(ctx), create_survey(ctx)
        before = c.app.state.db.surveys.find_one({'_id': survey['id']})
        r = c.post(f'/api/surveys/{survey["id"]}/imports', headers=h, json={
            'expected_version': True, 'template_id': item['id'], 'template_version_id': item['latest']['id']})
        assert r.status_code == 422
        assert c.app.state.db.surveys.find_one({'_id': survey['id']}) == before
        return
    r = c.post('/api/library/questions', headers=h, json={'question': q})
    assert r.status_code == 422, r.text
    assert c.app.state.db.question_templates.count_documents({}) == 0
    assert c.app.state.db.question_versions.count_documents({}) == 0


def test_multiple_bank_memberships_remove_only_relation_and_repeat_safely(ctx):
    c, h, _ = ctx
    assert c.post('/api/question-banks', headers=h, json={'name': '   '}).status_code == 422
    banks = [c.post('/api/question-banks', headers=h, json={'name': name}).json() for name in ['甲库', '乙库']]
    item = create_question(ctx)
    for bank in banks:
        path = f'/api/question-banks/{bank["id"]}/questions/{item["id"]}'
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert all(r.status_code == 200 for r in pool.map(lambda _: c.put(path, headers=h), range(2)))
    db = c.app.state.db
    before = db.question_versions.find_one({'_id': item['latest']['id']})
    assert db.question_bank_memberships.count_documents({}) == 2
    path = f'/api/question-banks/{banks[0]["id"]}/questions/{item["id"]}'
    assert c.delete(path, headers=h).status_code == c.delete(path, headers=h).status_code == 200
    assert c.get('/api/library/questions', params={'bank_id': banks[0]['id']}, headers=h).json() == []
    assert len(c.get('/api/library/questions', params={'bank_id': banks[1]['id']}, headers=h).json()) == 1
    assert db.question_versions.find_one({'_id': item['latest']['id']}) == before
    assert db.question_templates.count_documents({}) == 1
    initialize_banks(db)
    assert db.question_bank_memberships.count_documents({}) == 1
    assert db.question_bank_memberships.index_information()['bank_template_unique']['unique']


def test_auth_ownership_and_version_pairing(ctx):
    c, h, other = ctx
    item, another = create_question(ctx), create_question(ctx, 'text')
    bank = c.post('/api/question-banks', headers=h, json={'name': '私有库'}).json()
    for path in ['/api/question-banks', '/api/library/questions', f'/api/library/questions/{item["id"]}/versions']:
        assert c.get(path).status_code == 401
    assert c.get('/api/library/questions', headers=other).json() == []
    assert c.get(f'/api/library/questions/{item["id"]}/versions', headers=other).status_code == 404
    for method in [c.put, c.delete]:
        assert method(f'/api/question-banks/{bank["id"]}/questions/{item["id"]}', headers=other).status_code == 404
    assert c.get('/api/library/questions', headers=other, params={'bank_id': bank['id']}).status_code == 404
    survey = create_survey(ctx)
    imported(ctx, survey, item, another['latest']['id'], expected_status=404)
    r = c.post(f'/api/surveys/{survey["id"]}/imports', headers=other, json={
        'expected_version': 1, 'template_id': item['id'], 'template_version_id': item['latest']['id']})
    assert r.status_code == 404
    other_survey = c.post('/api/surveys', headers=other, json={'title': '其他人的问卷'}).json()
    r = c.post(f'/api/surveys/{other_survey["id"]}/imports', headers=other, json={
        'expected_version': 1, 'template_id': item['id'], 'template_version_id': item['latest']['id']})
    assert r.status_code == 404


def test_select_old_version_independent_snapshots_and_local_edit(ctx):
    c, h, _ = ctx
    item = create_question(ctx)
    db = c.app.state.db
    # Seed an existing v2 as a future-version fixture; no version-edit API is claimed.
    v2 = deepcopy(db.question_versions.find_one({'_id': item['latest']['id']}))
    v2.update(_id=str(uuid4()), number=2, parent_version_id=item['latest']['id'])
    v2['content']['title'] = '新版本题干'
    v2['content_hash'] = content_hash(v2['content'])
    db.question_versions.insert_one(v2)
    db.question_templates.update_one({'_id': item['id']}, {'$set': {'latest_version_id': v2['_id']}})
    versions = c.get(f'/api/library/questions/{item["id"]}/versions', headers=h).json()
    assert [v['number'] for v in versions] == [2, 1]
    s = imported(ctx, create_survey(ctx), item)
    s = imported(ctx, s, item, v2['_id'])
    s = imported(ctx, s, item)
    original_ids = [q['id'] for q in s['questions']]
    assert len(set(original_ids)) == 3
    assert [q['title'] for q in s['questions']] == ['复用number', '新版本题干', '复用number']
    endpoint = '/api/surveys/' + s['id']
    ordered = list(reversed(s['questions']))
    ordered[0]['title'] = '只改当前实例'
    ordered.pop(1)
    saved = c.put(endpoint + '/questions', headers=h, json={'expected_version': s['version'], 'questions': ordered}).json()
    fresh = c.get(endpoint, headers=h).json()
    assert fresh['question_sources'] == saved['question_sources']
    assert {source['instance_id']: source['modified'] for source in fresh['question_sources']} == {
        original_ids[0]: False, original_ids[2]: True}
    assert fresh['questions'][1]['title'] == '复用number'
    assert db.question_versions.find_one({'_id': item['latest']['id']})['content']['title'] == '复用number'
    # Clients cannot invent provenance via the full-array edit API.
    assert c.put(endpoint + '/questions', headers=h, json={
        'expected_version': saved['version'], 'questions': ordered, 'question_sources': []}).status_code == 422


def test_duplicate_instance_rules_answers_zero_and_statistics(ctx):
    c, h, _ = ctx
    item = create_question(ctx)
    s = imported(ctx, create_survey(ctx), item)
    s = imported(ctx, s, item)
    a, b = [q['id'] for q in s['questions']]
    base = '/api/surveys/' + s['id']
    r = c.put(base + '/rules', headers=h, json={'expected_version': s['version'], 'rules': [
        {'source_question_id': a, 'operator': 'eq', 'value': 0, 'target_question_id': 'END'}]})
    assert r.status_code == 200, r.text
    s = r.json()
    r = c.post(base + '/publish', headers=h, json={'expected_version': s['version']})
    assert r.status_code == 200, r.text
    s = r.json()
    for answers in [{a: 0}, {a: 2, b: 8}]:
        r = c.post(f'/api/fill/{s["share_id"]}/responses', headers=h, json={
            'survey_version': s['published_version'], 'submission_key': uuid4().hex, 'answers': answers})
        assert r.status_code == 201, r.text
    responses = list(c.app.state.db.responses.find({'survey_id': s['id']}).sort('submitted_at', 1))
    assert {tuple(row['path']) for row in responses} == {(a,), (a, b)}
    stats = c.get(base + '/statistics', headers=h).json()
    by_id = {q['id']: q for q in stats['questions']}
    assert stats['total_submissions'] == 2
    assert by_id[a]['answered_count'] == 2 and by_id[a]['average'] == 1
    assert by_id[b]['answered_count'] == 1 and by_id[b]['average'] == 8
    imported(ctx, s, item, expected_status=409)
    before = c.app.state.db.surveys.find_one({'_id': s['id']})
    assert c.put(base + '/questions', headers=h, json={'expected_version': s['version'], 'questions': []}).status_code == 409
    assert c.app.state.db.surveys.find_one({'_id': s['id']}) == before


def test_import_cas_stale_conflict_and_concurrent_publish(ctx, monkeypatch):
    c, h, _ = ctx
    item = create_question(ctx)
    s = imported(ctx, create_survey(ctx), item)
    stale = {**s, 'version': 1}
    imported(ctx, stale, item, expected_status=409)
    barrier = Barrier(2)
    original = SurveyRepository.update_owned
    def synchronized(self, *args, **kwargs):
        barrier.wait(timeout=10)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(SurveyRepository, 'update_owned', synchronized)
    base = '/api/surveys/' + s['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(c.post, base + '/imports', headers=h, json={
            'expected_version': s['version'], 'template_id': item['id'], 'template_version_id': item['latest']['id']}),
            pool.submit(c.post, base + '/publish', headers=h, json={'expected_version': s['version']})]
        results = [f.result() for f in futures]
    assert sorted(r.status_code for r in results) == [200, 409]
    stored = c.app.state.db.surveys.find_one({'_id': s['id']})
    assert stored['version'] == s['version'] + 1
    assert len(stored['questions']) == len(stored['question_sources'])


def test_migrated_legacy_question_can_join_bank_and_import_without_changing_old_ids(ctx):
    c, h, _ = ctx
    s = create_survey(ctx)
    s = c.put('/api/surveys/' + s['id'] + '/questions', headers=h, json={
        'expected_version': 1, 'questions': [question('single')]}).json()
    db = c.app.state.db
    before = deepcopy(db.surveys.find_one({'_id': s['id']}))
    migration = import_module('database.migrations.002_question_library')
    migration.migrate(db, apply=True)
    assert db.surveys.find_one({'_id': s['id']}) == before
    item = c.get('/api/library/questions', headers=h).json()[0]
    bank = c.post('/api/question-banks', headers=h, json={'name': '迁移题库'}).json()
    assert c.put(f'/api/question-banks/{bank["id"]}/questions/{item["id"]}', headers=h).status_code == 200
    fresh = c.get('/api/surveys/' + s['id'], headers=h).json()
    assert fresh['question_sources'][0]['instance_id'] == 'original_local_id'
    assert fresh['questions'][0]['id'] == 'original_local_id'
    copied = imported(ctx, create_survey(ctx), item)
    assert copied['questions'][0]['id'] != 'original_local_id'
    assert copied['questions'][0]['options'] == question('single')['options']
    assert db.surveys.find_one({'_id': s['id']}) == before
    counts = {name: db[name].count_documents({}) for name in migration.TARGETS}
    repeated = migration.migrate(db, apply=True)
    assert repeated['created'] == {name: 0 for name in migration.TARGETS}
    assert {name: db[name].count_documents({}) for name in migration.TARGETS} == counts


def test_pages_and_import_limit_and_tampered_version(ctx):
    c, h, _ = ctx
    for path in ['/library', '/library/new', '/edit/test']:
        assert c.get(path).status_code == 200
    item, s = create_question(ctx), create_survey(ctx)
    db = c.app.state.db
    questions = [{**question(), 'id': 'q' + str(i)} for i in range(200)]
    db.surveys.update_one({'_id': s['id']}, {'$set': {'questions': questions}})
    imported(ctx, s, item, expected_status=422)
    db.surveys.update_one({'_id': s['id']}, {'$set': {'questions': []}})
    db.question_versions.update_one({'_id': item['latest']['id']}, {'$set': {'content.title': '被错误篡改'}})
    imported(ctx, s, item, expected_status=409)
    assert db.surveys.find_one({'_id': s['id']})['questions'] == []


@pytest.mark.parametrize('kind,values', [('single', ['a', 'b']), ('multiple', [['a'], ['b']]), ('text', ['第一份', '第二份'])])
def test_duplicate_choices_and_text_answers_do_not_merge_by_template_or_option_id(ctx, kind, values):
    c, h, _ = ctx
    item = create_question(ctx, kind)
    s = imported(ctx, create_survey(ctx), item)
    s = imported(ctx, s, item)
    a, b = [q['id'] for q in s['questions']]
    base = '/api/surveys/' + s['id']
    s = c.post(base + '/publish', headers=h, json={'expected_version': s['version']}).json()
    r = c.post(f'/api/fill/{s["share_id"]}/responses', headers=h, json={
        'survey_version': s['published_version'], 'submission_key': uuid4().hex, 'answers': {a: values[0], b: values[1]}})
    assert r.status_code == 201, r.text
    saved = c.app.state.db.responses.find_one({'survey_id': s['id']})
    assert saved['answers'] == {a: values[0], b: values[1]}
    stats = c.get(base + '/statistics', headers=h).json()['questions']
    assert all(q['answered_count'] == 1 for q in stats)
    if kind == 'text':
        assert [q['values'] for q in stats] == [['第一份'], ['第二份']]
    else:
        assert [{o['id']: o['count'] for o in q['options']} for q in stats] == [{'a': 1, 'b': 0}, {'a': 0, 'b': 1}]


def test_two_concurrent_imports_commit_exactly_one_snapshot_and_source(ctx, monkeypatch):
    c, h, _ = ctx
    item, s = create_question(ctx), create_survey(ctx)
    barrier, original = Barrier(2), SurveyRepository.update_owned
    def synchronized(self, *args, **kwargs):
        barrier.wait(timeout=10)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(SurveyRepository, 'update_owned', synchronized)
    payload = {'expected_version': s['version'], 'template_id': item['id'], 'template_version_id': item['latest']['id']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: c.post(f'/api/surveys/{s["id"]}/imports', headers=h, json=payload), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    stored = c.app.state.db.surveys.find_one({'_id': s['id']})
    assert stored['version'] == 2
    assert len(stored['questions']) == len(stored['question_sources']) == 1
    assert stored['questions'][0]['id'] == stored['question_sources'][0]['instance_id']


def test_repeated_legacy_migration_does_not_duplicate_online_imports(ctx):
    c, _, _ = ctx
    item, s = create_question(ctx), create_survey(ctx)
    s = imported(ctx, s, item)
    s = imported(ctx, s, item)
    db = c.app.state.db
    before = deepcopy(db.surveys.find_one({'_id': s['id']}))
    migration = import_module('database.migrations.002_question_library')
    for _ in range(2):
        report = migration.migrate(db, apply=True)
        assert report['created'] == {name: 0 for name in migration.TARGETS}
    assert db.surveys.find_one({'_id': s['id']}) == before
    assert db.question_templates.count_documents({}) == db.question_versions.count_documents({}) == 1
    assert db.question_usages.count_documents({}) == 0
