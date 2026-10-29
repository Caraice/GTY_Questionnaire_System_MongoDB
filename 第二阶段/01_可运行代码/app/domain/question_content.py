"""Canonical content identity, independent of questionnaire instance IDs."""
from hashlib import sha256

from bson import json_util
from pydantic import TypeAdapter

from app.models import Question

adapter = TypeAdapter(Question)


def content_of(question):
    normalized = adapter.validate_python(question).model_dump()
    # Adding an optional unit must not change historical normalized fingerprints.
    if normalized.get('type') == 'number' and normalized.get('unit') is None:
        normalized.pop('unit', None)
    return {k: v for k, v in normalized.items() if k != 'id'}


def content_hash(content):
    canonical = json_util.dumps(content, json_options=json_util.CANONICAL_JSON_OPTIONS,
                                sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return sha256(canonical.encode('utf-8')).hexdigest()
