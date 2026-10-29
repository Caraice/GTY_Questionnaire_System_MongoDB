"""Attach independent identities without rewriting a single legacy document.

Stable deterministic IDs plus insert-only writes allow retry after interruption.
No transaction, mass deduplication, automatic upgrade, or source normalization is
implied. Run during a maintenance window; changed sources fail verification.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid5

from bson import json_util
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.models import QuestionSet
from database.library_schema import initialize_library, validate_existing_collections


MIGRATION_ID = '002_question_library'
NAMESPACE = UUID('24478f21-6d73-4f0e-bdd0-a329239614ba')
TARGETS = ('question_versions', 'question_templates', 'question_usages')
IDENTITY_FIELDS = {
    'question_templates': ('_id', 'schema_version', 'owner_id', 'origin'),
    'question_versions': ('_id', 'schema_version', 'template_id', 'number', 'parent_version_id',
                          'derived_from_version_id', 'restored_from_version_id', 'content',
                          'content_hash', 'created_by', 'origin'),
    'question_usages': ('_id', 'schema_version', 'survey_id', 'instance_id', 'template_id',
                       'template_version_id', 'content_hash', 'origin'),
}


class MigrationConflict(ValueError):
    pass


def fingerprint(value):
    # Canonical extended JSON keeps bool/int/float distinct; no lossy coercion.
    data = json_util.dumps(value, json_options=json_util.CANONICAL_JSON_OPTIONS,
                           sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return sha256(data.encode('utf-8')).hexdigest()


def stable_id(kind, *parts):
    return str(uuid5(NAMESPACE, json_util.dumps([MIGRATION_ID, kind, *parts], separators=(',', ':'))))


def source_guard(survey):
    # Exclude runtime admission counters: accepting responses must remain harmless.
    return fingerprint({key: survey.get(key) for key in (
        '_id', 'schema_version', 'owner_id', 'version', 'status', 'published_version',
        'questions', 'rules', 'updated_at', 'question_sources',
    )})


def documents_for(survey, question):
    instance_id = question['id']
    template_id = stable_id('template', survey['_id'], instance_id)
    version_id = stable_id('version', template_id, 1)
    origin = {'migration': MIGRATION_ID, 'survey_id': survey['_id'], 'instance_id': instance_id}
    content = deepcopy(question)
    del content['id']  # The local instance ID is retained in the usage, never globally repurposed.
    digest = fingerprint(content)
    # This is the new catalog record's capture time, not an invented authoring date.
    created_at = datetime.now(timezone.utc)
    return {
        'question_templates': {
            '_id': template_id, 'schema_version': 1, 'owner_id': survey['owner_id'],
            'latest_version_id': version_id, 'edit_version': 1, 'visibility': 'private',
            'created_at': created_at, 'updated_at': created_at, 'origin': origin,
        },
        'question_versions': {
            '_id': version_id, 'schema_version': 1, 'template_id': template_id, 'number': 1,
            'parent_version_id': None, 'derived_from_version_id': None, 'restored_from_version_id': None,
            'content': content, 'content_hash': digest, 'created_by': None,
            'created_at': created_at, 'origin': origin,
        },
        'question_usages': {
            '_id': stable_id('usage', survey['_id'], instance_id), 'schema_version': 1,
            'survey_id': survey['_id'], 'instance_id': instance_id,
            'template_id': template_id, 'template_version_id': version_id, 'content_hash': digest,
            'source_survey_version': survey.get('published_version', survey['version']),
            'created_at': created_at, 'origin': origin,
        },
    }


def assert_same(collection_name, actual, expected):
    keys = IDENTITY_FIELDS[collection_name]
    left = {k: actual.get(k) for k in keys}
    right = {k: expected[k] for k in keys}
    if fingerprint(left) != fingerprint(right):
        raise MigrationConflict(f"Content or identity conflicts: {collection_name}/{expected['_id']}")


def build_plan(db):
    """Validate every source and pre-existing target before the first write."""
    validate_existing_collections(db)
    validate_marker(db)
    plan, guards = [], {}
    for survey in db.surveys.find({}).sort('_id', 1):
        survey_id = survey.get('_id')
        if (not isinstance(survey_id, str) or not isinstance(survey.get('owner_id'), str)
                or not survey['owner_id'] or type(survey.get('schema_version')) is not int or survey['schema_version'] != 1
                or survey.get('status') not in ('draft', 'published', 'closed')
                or not isinstance(survey.get('created_at'), datetime)
                or type(survey.get('version')) is not int or survey['version'] < 1
                or ('published_version' in survey and
                    (type(survey['published_version']) is not int or survey['published_version'] < 1))):
            raise MigrationConflict(f'Invalid or unsupported source survey: {survey_id}')
        try:
            # Validate but do not save model_dump: defaults/trim would change the old snapshot.
            QuestionSet(expected_version=survey['version'], questions=survey['questions'])
        except (KeyError, ValidationError) as error:
            raise MigrationConflict(f'Invalid legacy questions: {survey_id}') from error
        guards[survey_id] = source_guard(survey)
        for question in survey['questions']:
            online = next((s for s in survey.get('question_sources', [])
                           if s.get('instance_id') == question['id']), None)
            legacy_usage = db.question_usages.find_one({'_id': stable_id('usage', survey_id, question['id'])})
            if online is not None:
                # M2 imports already have an authoritative source in the survey's
                # atomic snapshot. Do not turn a copied instance into another template.
                version = db.question_versions.find_one({'_id': online.get('template_version_id'),
                                                        'template_id': online.get('template_id')})
                template = db.question_templates.find_one({'_id': online.get('template_id')})
                if (version is None or template is None or version['content_hash'] != online.get('content_hash')
                        or fingerprint(version['content']) != version['content_hash']):
                    raise MigrationConflict(f'Invalid online question source: {survey_id}/{question["id"]}')
                continue
            docs = documents_for(survey, question)
            for collection_name, document in docs.items():
                actual = db[collection_name].find_one({'_id': document['_id']})
                if actual is not None:
                    assert_same(collection_name, actual, document)
                unique_key = ({f'origin.{k}': v for k, v in document['origin'].items()} if collection_name == 'question_templates' else
                              {'template_id': document['template_id'], 'number': document['number']}
                              if collection_name == 'question_versions' else
                              {'survey_id': survey_id, 'instance_id': question['id']})
                other = db[collection_name].find_one(unique_key)
                if other is not None and other['_id'] != document['_id']:
                    raise MigrationConflict(f'Instance or version already has another source: {survey_id}/{question["id"]}')
            plan.append(docs)
    return plan, guards


def assert_sources_unchanged(db, guards):
    current = {s['_id']: source_guard(s) for s in db.surveys.find({})}
    if current != guards:
        raise MigrationConflict('Surveys changed during migration; stop editing and rerun the preflight')


def insert_only(db, name, document):
    try:
        result = db[name].update_one({'_id': document['_id']}, {'$setOnInsert': document}, upsert=True)
        inserted = result.upserted_id is not None
    except DuplicateKeyError:
        # Exact concurrent invocations may race at an insert; verify the winner.
        inserted = False
    actual = db[name].find_one({'_id': document['_id']})
    if actual is None:
        raise MigrationConflict(f'Unique source or version conflict: {name}/{document["_id"]}')
    assert_same(name, actual, document)
    return inserted


def validate_marker(db):
    marker = db.schema_migrations.find_one({'_id': MIGRATION_ID})
    if marker is not None and (
        type(marker.get('schema_version')) is not int or marker['schema_version'] != 1
        or type(marker.get('number')) is not int or marker['number'] != 2
        or not isinstance(marker.get('completed_at'), datetime)
        or any(type(marker.get(k)) is not int or marker[k] < 0 for k in ('initial_surveys', 'initial_questions'))
    ):
        raise MigrationConflict('Existing migration marker is invalid; review it before retrying')


def migrate(db, *, apply=False):
    plan, guards = build_plan(db)
    report = {'migration': MIGRATION_ID, 'database': db.name, 'mode': 'apply' if apply else 'dry-run',
              'surveys': len(guards), 'questions': len(plan),
              'pending': {name: sum(db[name].find_one({'_id': p[name]['_id']}, {'_id': 1}) is None
                                    for p in plan) for name in TARGETS},
              'created': {name: 0 for name in TARGETS}, 'legacy_documents_rewritten': 0}
    assert_sources_unchanged(db, guards)
    if not apply:
        return {**report, 'status': 'ready'}
    initialize_library(db)
    assert_sources_unchanged(db, guards)
    for docs in plan:
        for name in TARGETS:
            report['created'][name] += int(insert_only(db, name, docs[name]))
    assert_sources_unchanged(db, guards)
    try:
        db.schema_migrations.update_one({'_id': MIGRATION_ID}, {'$setOnInsert': {
            '_id': MIGRATION_ID, 'schema_version': 1, 'number': 2,
            'completed_at': datetime.now(timezone.utc), 'initial_surveys': len(guards), 'initial_questions': len(plan),
        }}, upsert=True)
    except DuplicateKeyError:
        if db.schema_migrations.find_one({'_id': MIGRATION_ID}) is None:
            raise
    validate_marker(db)
    return {**report, 'status': 'complete'}
