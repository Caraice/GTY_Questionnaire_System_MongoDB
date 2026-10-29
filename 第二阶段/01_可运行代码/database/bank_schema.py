"""Additive bank membership schema; never replace existing validators."""
from pymongo.errors import CollectionInvalid, OperationFailure

from database.library_schema import initialize_library
from database.schema import object_schema

SCHEMAS = {
    'question_grants': object_schema(
        ['_id', 'schema_version', 'template_id', 'version_id', 'owner_id', 'grantee_id',
         'active', 'revision', 'created_at', 'updated_at'],
        {'_id': {'bsonType': 'string'}, 'schema_version': {'enum': [1]},
         'template_id': {'bsonType': 'string'}, 'version_id': {'bsonType': 'string'},
         'owner_id': {'bsonType': 'string'}, 'grantee_id': {'bsonType': 'string'},
         'active': {'bsonType': 'bool'}, 'revision': {'bsonType': 'int', 'minimum': 1},
         'created_at': {'bsonType': 'date'}, 'updated_at': {'bsonType': 'date'}}),
    'question_banks': object_schema(
        ['_id', 'schema_version', 'owner_id', 'name', 'created_at'],
        {'_id': {'bsonType': 'string'}, 'schema_version': {'enum': [1]},
         'owner_id': {'bsonType': 'string'}, 'name': {'bsonType': 'string', 'minLength': 1},
         'created_at': {'bsonType': 'date'}}),
    'question_bank_memberships': object_schema(
        ['_id', 'schema_version', 'bank_id', 'template_id', 'created_at'],
        {'_id': {'bsonType': 'string'}, 'schema_version': {'enum': [1]},
         'bank_id': {'bsonType': 'string'}, 'template_id': {'bsonType': 'string'},
         'created_at': {'bsonType': 'date'}}),
}


def initialize_banks(db):
    initialize_library(db)
    for name, schema in SCHEMAS.items():
        existing = next(db.list_collections(filter={'name': name}), None)
        if existing is None:
            try:
                db.create_collection(name, validator=schema, validationLevel='strict')
            except (CollectionInvalid, OperationFailure) as error:
                if isinstance(error, OperationFailure) and error.code != 48:
                    raise
            existing = next(db.list_collections(filter={'name': name}))
        options = existing.get('options', {})
        if (options.get('validator') != schema or options.get('validationLevel', 'strict') != 'strict'
                or options.get('validationAction', 'error') != 'error'):
            raise ValueError(f'Bank schema conflicts: {name}')
    db.question_banks.create_index([('owner_id', 1), ('created_at', -1)], name='bank_owner_list')
    db.question_banks.create_index([('owner_id', 1), ('created_at', -1), ('_id', 1)], name='bank_owner_page')
    db.question_bank_memberships.create_index([('bank_id', 1), ('template_id', 1)],
                                             unique=True, name='bank_template_unique')
    db.question_bank_memberships.create_index([('template_id', 1), ('bank_id', 1)], name='template_banks')
    db.surveys.create_index('question_sources.template_version_id', name='imported_version_usage')
    db.surveys.create_index([('owner_id', 1), ('question_sources.template_version_id', 1), ('_id', 1)], name='owned_version_usage')
    db.question_templates.create_index([('owner_id', 1), ('updated_at', -1), ('_id', 1)], name='owned_question_page')
    db.surveys.create_index([('owner_id', 1), ('question_sources.template_id', 1)], name='owned_template_usage')
    db.question_grants.create_index([('version_id', 1), ('grantee_id', 1)], unique=True, name='version_grantee_unique')
    db.question_grants.create_index([('grantee_id', 1), ('active', 1), ('template_id', 1)], name='received_versions')
