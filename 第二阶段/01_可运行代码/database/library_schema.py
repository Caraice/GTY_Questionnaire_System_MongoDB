"""Explicit, additive schema for phase-two question identities and snapshots.

The normal stage-one initializer deliberately does not run this migration.
Existing surveys, responses, validators and indexes are never replaced here.
"""
from pymongo.errors import CollectionInvalid, OperationFailure

from database.schema import object_schema


SCHEMAS = {
    "question_templates": object_schema(
        ["_id", "schema_version", "owner_id", "latest_version_id", "edit_version",
         "visibility", "origin", "created_at", "updated_at"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "owner_id": {"bsonType": "string"}, "latest_version_id": {"bsonType": "string"},
         "edit_version": {"bsonType": "int", "minimum": 1},
         "visibility": {"enum": ["private", "shared"]}, "origin": {"bsonType": "object"},
         "created_at": {"bsonType": "date"}, "updated_at": {"bsonType": "date"}},
    ),
    "question_versions": object_schema(
        ["_id", "schema_version", "template_id", "number", "parent_version_id",
         "derived_from_version_id", "restored_from_version_id", "content", "content_hash",
         "created_by", "created_at", "origin"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "template_id": {"bsonType": "string"}, "number": {"bsonType": "int", "minimum": 1},
         "parent_version_id": {"bsonType": ["string", "null"]},
         "derived_from_version_id": {"bsonType": ["string", "null"]},
         "restored_from_version_id": {"bsonType": ["string", "null"]},
         "content": {"bsonType": "object"}, "content_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
         "created_by": {"bsonType": ["string", "null"]}, "created_at": {"bsonType": "date"},
         "origin": {"bsonType": "object"}},
    ),
    "question_usages": object_schema(
        ["_id", "schema_version", "survey_id", "instance_id", "template_id", "template_version_id",
         "content_hash", "source_survey_version", "created_at", "origin"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "survey_id": {"bsonType": "string"}, "instance_id": {"bsonType": "string"},
         "template_id": {"bsonType": "string"}, "template_version_id": {"bsonType": "string"},
         "content_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
         "source_survey_version": {"bsonType": "int", "minimum": 1},
         "created_at": {"bsonType": "date"}, "origin": {"bsonType": "object"}},
    ),
    "schema_migrations": object_schema(
        ["_id", "schema_version", "number", "completed_at", "initial_surveys", "initial_questions"],
        {"_id": {"bsonType": "string"}, "schema_version": {"enum": [1]},
         "number": {"enum": [2]}, "completed_at": {"bsonType": "date"},
         "initial_surveys": {"bsonType": "int", "minimum": 0},
         "initial_questions": {"bsonType": "int", "minimum": 0}},
    ),
}

INDEXES = {
    "question_templates": [
        ([('owner_id', 1), ('updated_at', -1), ('_id', -1)], {"name": "template_owner_list"}),
        ([('origin.migration', 1), ('origin.survey_id', 1), ('origin.instance_id', 1)],
         {"name": "legacy_template_origin", "unique": True,
          "partialFilterExpression": {"origin.migration": "002_question_library"}}),
    ],
    "question_versions": [
        ([('template_id', 1), ('number', 1)], {"name": "template_version_number", "unique": True}),
        ([('parent_version_id', 1)], {"name": "version_parent"}),
        ([('derived_from_version_id', 1)], {"name": "version_derivation"}),
    ],
    "question_usages": [
        ([('survey_id', 1), ('instance_id', 1)], {"name": "survey_question_instance", "unique": True}),
        ([('template_version_id', 1), ('survey_id', 1)], {"name": "version_usage"}),
        ([('template_id', 1), ('survey_id', 1)], {"name": "template_usage"}),
    ],
}


def validate_existing_collections(db):
    """Do not silently adopt or replace a conflicting collection validator."""
    for item in db.list_collections():
        if item['name'] not in SCHEMAS:
            continue
        options = item.get('options', {})
        if (options.get('validator') != SCHEMAS[item['name']]
                or options.get('validationLevel', 'strict') != 'strict'
                or options.get('validationAction', 'error') != 'error'):
            raise ValueError(f"New collection schema conflicts: {item['name']}")
        existing_indexes = db[item['name']].index_information()
        for keys, expected in INDEXES.get(item['name'], []):
            actual = existing_indexes.get(expected['name'])
            if actual is not None and (
                list(actual['key']) != keys or bool(actual.get('unique', False)) != expected.get('unique', False)
                or actual.get('partialFilterExpression') != expected.get('partialFilterExpression')
            ):
                raise ValueError(f"New collection index conflicts: {item['name']}/{expected['name']}")


def initialize_library(db):
    validate_existing_collections(db)
    existing = set(db.list_collection_names())
    for name, validator in SCHEMAS.items():
        if name not in existing:
            try:
                db.create_collection(name, validator=validator, validationLevel='strict')
            except CollectionInvalid:
                # Another invocation may have created it after the inventory.
                validate_existing_collections(db)
            except OperationFailure as error:
                if error.code != 48:  # NamespaceExists after a concurrent create.
                    raise
                validate_existing_collections(db)
    for name, indexes in INDEXES.items():
        for keys, options in indexes:
            db[name].create_index(keys, **options)
