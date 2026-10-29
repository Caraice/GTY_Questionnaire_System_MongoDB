"""Real MongoDB migration, restart/retry, immutable snapshots and stage-one regression."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
import json
import os
from uuid import uuid4

from bson import BSON
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import AutoReconnect
import pytest

from app.config import Settings
from app.main import create_app
from database.library_schema import SCHEMAS, initialize_library
from database.schema import initialize
from scripts.migrate_question_library import main

migration = import_module('database.migrations.002_question_library')
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv('RUN_MONGO_TESTS') != '1', reason='Requires real MongoDB')]
LEGACY_COLLECTIONS = ('users', 'surveys', 'responses', 'app_settings')


@pytest.fixture
def db():
    name = 'survey_migration_' + uuid4().hex
    client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://127.0.0.1:27019'), tz_aware=True)
    initialize(client[name])
    yield client[name]
    client.close()
    print('\nRetained migration test database: ' + name)


def questions():
    return [
        {'id': 's', 'type': 'single', 'title': '入口', 'required': True,
         'options': [{'id': 'a', 'label': '完整'}, {'id': 'b', 'label': '跳过'}]},
        {'id': 'm', 'type': 'multiple', 'title': '多选', 'required': True,
         'options': [{'id': 'x', 'label': '甲'}, {'id': 'y', 'label': '乙'}],
         'validation': {'min_choices': 1, 'max_choices': 2}},
        {'id': 't', 'type': 'text', 'title': '建议', 'required': True,
         'validation': {'min_length': 2, 'max_length': 20}},
        {'id': 'n', 'type': 'number', 'title': '年龄', 'required': True,
         'validation': {'minimum': 0, 'maximum': 120, 'integer_only': True}},
    ]


def seed(db, *, owner='owner', status='draft', question_list=None):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    doc = {'_id': str(uuid4()), 'schema_version': 1, 'version': 3, 'owner_id': owner,
           'share_id': uuid4().hex, 'title': '迁移旧问卷', 'description': '',
           'allow_anonymous': True, 'allow_multiple': True, 'deadline': None, 'status': status,
           'questions': questions() if question_list is None else deepcopy(question_list),
           'rules': [{'source_question_id': 's', 'operator': 'eq', 'value': 'b', 'target_question_id': 'n'}],
           'created_at': now, 'updated_at': now}
    if not doc['questions']:
        doc['rules'] = []
    if status != 'draft':
        doc['published_version'] = 2
    db.surveys.insert_one(doc)
    return doc


def snapshot(db, names=LEGACY_COLLECTIONS):
    return {name: [BSON.encode(d) for d in db[name].find({}).sort('_id', 1)] for name in names}


def test_dry_run_is_completely_read_only(db):
    seed(db)
    before = snapshot(db)
    collection_names = set(db.list_collection_names())
    report = migration.migrate(db)
    assert report['status'] == 'ready' and report['mode'] == 'dry-run'
    assert report['pending'] == {n: 4 for n in migration.TARGETS}
    assert snapshot(db) == before
    assert set(db.list_collection_names()) == collection_names


def test_apply_and_repeat_do_not_change_any_legacy_or_duplicate_targets(db):
    survey = seed(db)
    before = snapshot(db)
    validators = {c['name']: c.get('options', {}) for c in db.list_collections()}
    indexes = {n: db[n].index_information() for n in LEGACY_COLLECTIONS}
    first = migration.migrate(db, apply=True)
    assert first['created'] == {n: 4 for n in migration.TARGETS}
    all_names = tuple(db.list_collection_names())
    after_first = snapshot(db, all_names)
    second = migration.migrate(db, apply=True)
    assert second['pending'] == second['created'] == {n: 0 for n in migration.TARGETS}
    assert snapshot(db, all_names) == after_first
    assert snapshot(db) == before
    assert db.schema_migrations.count_documents({}) == 1
    for c in db.list_collections():
        if c['name'] in validators:
            assert c.get('options', {}) == validators[c['name']]
    assert {n: db[n].index_information() for n in LEGACY_COLLECTIONS} == indexes
    for question in survey['questions']:
        usage = db.question_usages.find_one({'survey_id': survey['_id'], 'instance_id': question['id']})
        version = db.question_versions.find_one({'_id': usage['template_version_id']})
        assert BSON.encode({'id': question['id'], **version['content']}) == BSON.encode(question)
        assert version['content_hash'] == migration.fingerprint(version['content'])
        assert version['created_by'] is None  # The old schema did not record the original question author.
        assert version['created_at'] != survey['created_at']


@pytest.mark.parametrize('status', ['draft', 'published', 'closed'])
def test_all_statuses_and_missing_legacy_publication_field_are_compatible(db, status):
    survey = seed(db, status=status)
    db.surveys.update_one({'_id': survey['_id']}, {'$unset': {'published_version': ''}})
    before = snapshot(db)
    migration.migrate(db, apply=True)
    assert snapshot(db) == before
    assert db.question_usages.count_documents({'source_survey_version': 3}) == 4


def test_same_ids_or_same_content_do_not_merge_unrelated_surveys_or_owners(db):
    a, b, c = seed(db), seed(db), seed(db, owner='other')
    migration.migrate(db, apply=True)
    assert db.question_templates.count_documents({}) == 12
    usages = list(db.question_usages.find({'instance_id': 's'}))
    assert len({u['template_id'] for u in usages}) == 3
    assert {u['survey_id'] for u in usages} == {a['_id'], b['_id'], c['_id']}
    assert {t['visibility'] for t in db.question_templates.find({})} == {'private'}


def test_empty_database_or_empty_draft_creates_no_questions(db):
    seed(db, question_list=[])
    report = migration.migrate(db, apply=True)
    assert report['questions'] == 0 and report['surveys'] == 1
    assert all(db[n].count_documents({}) == 0 for n in migration.TARGETS)


@pytest.mark.parametrize('change', ['duplicate_id', 'blank_title', 'invalid_limits', 'future_schema', 'bool_version'])
def test_invalid_legacy_data_stops_before_creating_collections(db, change):
    survey = seed(db)
    if change == 'duplicate_id': survey['questions'][1]['id'] = 's'
    elif change == 'blank_title': survey['questions'][0]['title'] = '  '
    elif change == 'invalid_limits': survey['questions'][-1]['validation']['minimum'] = 200
    elif change == 'future_schema': survey['schema_version'] = 2
    else: survey['version'] = True
    db.surveys.replace_one({'_id': survey['_id']}, survey, bypass_document_validation=True)
    before = snapshot(db)
    for apply in [False, True]:
        with pytest.raises(migration.MigrationConflict): migration.migrate(db, apply=apply)
        assert not set(SCHEMAS).intersection(db.list_collection_names())
        assert snapshot(db) == before


def test_legacy_defaults_and_whitespace_are_not_normalized(db):
    q = {'id': 'raw', 'type': 'single', 'title': ' 原始题干 ',
         'options': [{'id': 'A', 'label': ' 原始选项 '}, {'id': 'B', 'label': '乙'}]}
    survey = seed(db, question_list=[q])
    before = snapshot(db)
    migration.migrate(db, apply=True)
    version = db.question_versions.find_one({})
    assert 'required' not in version['content'] and 'validation' not in version['content']
    assert version['content']['title'] == ' 原始题干 '
    assert version['content']['options'][0]['label'] == ' 原始选项 '
    assert db.surveys.find_one({'_id': survey['_id']})['questions'] == [q]
    assert snapshot(db) == before


@pytest.mark.parametrize('target', ['question_templates', 'question_versions', 'question_usages'])
def test_conflicting_target_is_never_overwritten(db, target):
    survey = seed(db)
    docs = migration.documents_for(survey, survey['questions'][0])
    initialize_library(db)
    bad = deepcopy(docs[target])
    if target == 'question_templates': bad['owner_id'] = 'other'
    elif target == 'question_versions': bad['content']['title'] = '不能覆盖我'
    else: bad['content_hash'] = '0' * 64
    db[target].insert_one(bad)
    before = snapshot(db, tuple(db.list_collection_names()))
    with pytest.raises(migration.MigrationConflict): migration.migrate(db, apply=True)
    assert snapshot(db, tuple(db.list_collection_names())) == before


def test_same_instance_with_different_reference_is_reported(db):
    survey = seed(db)
    doc = migration.documents_for(survey, survey['questions'][0])['question_usages']
    initialize_library(db)
    doc['_id'] = str(uuid4())
    db.question_usages.insert_one(doc)
    with pytest.raises(migration.MigrationConflict, match='another source'):
        migration.migrate(db, apply=True)
    assert db.question_versions.count_documents({}) == 0


@pytest.mark.parametrize('name', ['question_templates', 'question_versions'])
def test_other_id_with_same_unique_origin_or_version_blocks_preflight(db, name):
    survey = seed(db)
    initialize_library(db)
    doc = migration.documents_for(survey, survey['questions'][0])[name]
    doc['_id'] = str(uuid4())
    db[name].insert_one(doc)
    before = snapshot(db, tuple(db.list_collection_names()))
    with pytest.raises(migration.MigrationConflict, match='another source'):
        migration.migrate(db, apply=True)
    assert snapshot(db, tuple(db.list_collection_names())) == before


def test_source_change_before_first_insert_is_detected(db, monkeypatch):
    survey = seed(db)
    real_init = migration.initialize_library
    def edit_during_initialize(database):
        real_init(database)
        database.surveys.update_one({'_id': survey['_id']}, {'$set': {'questions.0.title': '并发修改'}, '$inc': {'version': 1}})
    monkeypatch.setattr(migration, 'initialize_library', edit_during_initialize)
    with pytest.raises(migration.MigrationConflict, match='changed during migration'):
        migration.migrate(db, apply=True)
    assert all(db[n].count_documents({}) == 0 for n in SCHEMAS)


def test_conflicting_named_index_is_not_silently_rebuilt(db):
    seed(db)
    initialize_library(db)
    db.question_usages.drop_index('version_usage')
    db.question_usages.create_index('wrong_field', name='version_usage')
    before = snapshot(db, tuple(db.list_collection_names()))
    with pytest.raises(ValueError, match='index conflicts'):
        migration.migrate(db, apply=True)
    assert snapshot(db, tuple(db.list_collection_names())) == before
    assert db.question_usages.index_information()['version_usage']['key'] == [('wrong_field', 1)]


def test_existing_invalid_completion_marker_blocks_without_writing(db):
    seed(db)
    initialize_library(db)
    db.schema_migrations.insert_one({'_id': migration.MIGRATION_ID, 'number': 99}, bypass_document_validation=True)
    with pytest.raises(migration.MigrationConflict, match='marker is invalid'):
        migration.migrate(db, apply=True)
    assert db.question_versions.count_documents({}) == 0


def test_interrupted_write_can_resume_and_retry_without_duplicates(db, monkeypatch):
    seed(db)
    before = snapshot(db)
    real_update = Collection.update_one
    injected = False
    def lose_ack(collection, *args, **kwargs):
        nonlocal injected
        result = real_update(collection, *args, **kwargs)
        if collection.name == 'question_versions' and not injected:
            injected = True
            raise AutoReconnect('injected after insert')
        return result
    monkeypatch.setattr(Collection, 'update_one', lose_ack)
    with pytest.raises(AutoReconnect): migration.migrate(db, apply=True)
    assert db.question_versions.count_documents({}) == 1
    assert db.schema_migrations.count_documents({}) == 0
    monkeypatch.setattr(Collection, 'update_one', real_update)
    report = migration.migrate(db, apply=True)
    assert report['created'] == {'question_versions': 3, 'question_templates': 4, 'question_usages': 4}
    assert snapshot(db) == before
    assert migration.migrate(db, apply=True)['created'] == {n: 0 for n in migration.TARGETS}


def test_later_template_head_is_not_reset_by_rerun(db):
    seed(db)
    migration.migrate(db, apply=True)
    old = db.question_versions.find_one({})
    new = deepcopy(old)
    new.update({'_id': str(uuid4()), 'number': 2, 'parent_version_id': old['_id']})
    new['content']['title'] = '第二版'
    new['content_hash'] = migration.fingerprint(new['content'])
    db.question_versions.insert_one(new)
    db.question_templates.update_one({'_id': old['template_id']}, {'$set': {
        'latest_version_id': new['_id'], 'edit_version': 2, 'visibility': 'shared'}})
    before = snapshot(db, tuple(SCHEMAS))
    migration.migrate(db, apply=True)
    assert snapshot(db, tuple(SCHEMAS)) == before


def test_new_surveys_are_not_skipped_due_to_a_previous_completion_marker(db):
    seed(db)
    migration.migrate(db, apply=True)
    old_templates = snapshot(db, ('question_templates', 'question_versions', 'question_usages', 'schema_migrations'))
    seed(db)
    report = migration.migrate(db, apply=True)
    assert report['surveys'] == 2 and report['questions'] == 8
    assert report['created'] == {n: 4 for n in migration.TARGETS}
    for name, documents in old_templates.items():
        assert set(documents).issubset(set(snapshot(db, (name,))[name]))
    before = snapshot(db, tuple(db.list_collection_names()))
    migration.migrate(db, apply=True)
    assert snapshot(db, tuple(db.list_collection_names())) == before


def test_changed_draft_is_reported_without_rewriting_its_first_snapshot(db):
    survey = seed(db)
    migration.migrate(db, apply=True)
    db.surveys.update_one({'_id': survey['_id']}, {'$set': {'questions.0.title': '后续编辑'}, '$inc': {'version': 1}})
    before = snapshot(db, tuple(db.list_collection_names()))
    with pytest.raises(migration.MigrationConflict): migration.migrate(db, apply=True)
    assert snapshot(db, tuple(db.list_collection_names())) == before


def test_unknown_existing_validator_is_not_replaced(db):
    seed(db)
    db.create_collection('question_templates', validator={'$jsonSchema': {'bsonType': 'object'}})
    before = list(db.list_collections())
    with pytest.raises(ValueError, match='schema conflicts'): migration.migrate(db, apply=True)
    assert list(db.list_collections()) == before


def test_concurrent_migration_does_not_duplicate_or_change_sources(db):
    seed(db)
    before = snapshot(db)
    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = list(pool.map(lambda _: migration.migrate(db, apply=True), range(3)))
    assert all(r['status'] == 'complete' for r in reports)
    assert {n: db[n].count_documents({}) for n in migration.TARGETS} == {n: 4 for n in migration.TARGETS}
    assert db.schema_migrations.count_documents({}) == 1 and snapshot(db) == before


def test_namespace_creation_race_is_verified_before_continuing(db, monkeypatch):
    from pymongo.database import Database
    from pymongo.errors import OperationFailure
    seed(db)
    real_create = Database.create_collection
    injected = False
    def concurrent_create(database, name, *args, **kwargs):
        nonlocal injected
        result = real_create(database, name, *args, **kwargs)
        if name in SCHEMAS and not injected:
            injected = True
            raise OperationFailure('Namespace already exists', code=48)
        return result
    monkeypatch.setattr(Database, 'create_collection', concurrent_create)
    assert migration.migrate(db, apply=True)['status'] == 'complete'
    assert db.question_versions.count_documents({}) == 4


def test_cli_is_read_only_by_default_and_reports_a_real_failure(db, capsys):
    seed(db)
    args = ['--uri', os.getenv('MONGODB_URI', 'mongodb://127.0.0.1:27019'), '--database', db.name]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['mode'] == 'dry-run'
    assert 'question_templates' not in db.list_collection_names()
    assert main(args + ['--apply']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'complete'
    db.surveys.update_one({}, {'$set': {'questions.0.title': '改题'}})
    assert main(args + ['--apply']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'failed'


def test_fingerprint_preserves_json_type_and_array_order():
    assert len({migration.fingerprint({'n': x}) for x in [True, 1, 1.0]}) == 3
    assert migration.fingerprint({'a': 0, 'b': 1}) == migration.fingerprint({'b': 1, 'a': 0})
    assert migration.fingerprint([0, 1]) != migration.fingerprint([1, 0])


def test_new_indexes_and_base_schema_enforce_distinct_identity(db):
    seed(db)
    migration.migrate(db, apply=True)
    assert db.question_versions.index_information()['template_version_number']['unique']
    assert db.question_usages.index_information()['survey_question_instance']['unique']
    assert db.question_templates.index_information()['legacy_template_origin']['unique']
    assert 'version_usage' in db.question_usages.index_information()
    from pymongo.errors import DuplicateKeyError, WriteError
    with pytest.raises(WriteError): db.question_versions.insert_one({'_id': 'invalid'})
    doc = db.question_versions.find_one({})
    doc['_id'] = str(uuid4())
    with pytest.raises(DuplicateKeyError): db.question_versions.insert_one(doc)


def test_real_old_answers_history_statistics_and_future_submissions_survive(db):
    settings = Settings(os.getenv('MONGODB_URI', 'mongodb://127.0.0.1:27019'), db.name)
    with TestClient(create_app(settings)) as c:
        auth = {}
        for name in ['Owner', 'Reader']:
            body = {'username': name, 'password': 'Test-pass-123'}
            assert c.post('/api/auth/register', json=body).status_code == 201
            auth[name] = {'Authorization': 'Bearer ' + c.post('/api/auth/login', json=body).json()['access_token']}
        survey = c.post('/api/surveys', headers=auth['Owner'], json={
            'title': '迁移前后真实链路', 'allow_anonymous': True, 'allow_multiple': True}).json()
        base = '/api/surveys/' + survey['id']
        assert c.put(base + '/questions', headers=auth['Owner'], json={'expected_version': 1, 'questions': questions()}).status_code == 200
        assert c.put(base + '/rules', headers=auth['Owner'], json={'expected_version': 2, 'rules': [
            {'source_question_id': 's', 'operator': 'eq', 'value': 'b', 'target_question_id': 'n'}]}).status_code == 200
        survey = c.post(base + '/publish', headers=auth['Owner'], json={'expected_version': 3}).json()
        endpoint = '/api/fill/' + survey['share_id'] + '/responses'
        request = {'survey_version': 4, 'submission_key': uuid4().hex, 'anonymous': True, 'answers': {'s': 'b', 'n': 0}}
        receipt = c.post(endpoint, headers=auth['Reader'], json=request).json()
        full = {**request, 'submission_key': uuid4().hex, 'anonymous': False,
                'answers': {'s': 'a', 'm': ['x', 'y'], 't': '旧答卷', 'n': 10}}
        assert c.post(endpoint, headers=auth['Reader'], json=full).status_code == 201
        old_db = snapshot(db)
        stats = c.get(base + '/statistics', headers=auth['Owner']).json()
        history = c.get('/api/me/responses/' + receipt['id'], headers=auth['Reader']).json()
        frozen = c.get('/api/fill/' + survey['share_id'], headers=auth['Reader']).json()
        first, second = migration.migrate(db, apply=True), migration.migrate(db, apply=True)
        assert first['questions'] == 4 and all(v == 0 for v in second['created'].values())
        assert snapshot(db) == old_db  # Includes anonymous identity secret and original answer BSON bytes.
        assert c.get(base + '/statistics', headers=auth['Owner']).json() == stats
        assert stats['total_submissions'] == 2 and stats['questions'][-1]['average'] == 5
        assert c.get('/api/me/responses/' + receipt['id'], headers=auth['Reader']).json() == history
        assert history['path'] == ['s', 'n'] and history['questions'][-1]['answer'] == 0
        reloaded = c.get('/api/fill/' + survey['share_id'], headers=auth['Reader']).json()
        assert reloaded['questions'] == frozen['questions'] and reloaded['rules'] == frozen['rules']
        assert c.post(endpoint, headers=auth['Reader'], json=request).status_code == 200
        assert c.put(base + '/questions', headers=auth['Owner'], json={'expected_version': 4, 'questions': questions()}).status_code == 409
        next_request = {**full, 'submission_key': uuid4().hex, 'answers': {**full['answers'], 'n': 20}}
        assert c.post(endpoint, headers=auth['Reader'], json=next_request).status_code == 201
        assert c.get(base + '/statistics', headers=auth['Owner']).json()['questions'][-1]['average'] == 10
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/me/responses/' + receipt['id'], headers=auth['Reader']).json() == history
        assert restarted.get(base + '/statistics', headers=auth['Owner']).json()['total_submissions'] == 3
