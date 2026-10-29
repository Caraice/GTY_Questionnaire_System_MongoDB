from uuid import uuid4

from pymongo.errors import DuplicateKeyError
from pymongo import ReturnDocument


class LibraryRepository:
    def __init__(self, db):
        self.db = db

    def banks(self, owner):
        return list(self.db.question_banks.find({'owner_id': owner}).sort('created_at', -1))

    def bank(self, bank_id, owner):
        return self.db.question_banks.find_one({'_id': bank_id, 'owner_id': owner})

    def create_bank(self, doc):
        self.db.question_banks.insert_one(doc)

    def template(self, template_id, owner):
        return self.db.question_templates.find_one({'_id': template_id, 'owner_id': owner})

    def templates(self, owner, bank_id=None):
        query = {'owner_id': owner}
        if bank_id:
            ids = self.db.question_bank_memberships.distinct('template_id', {'bank_id': bank_id})
            query['_id'] = {'$in': ids}
        return list(self.db.question_templates.find(query).sort([('updated_at', -1), ('_id', 1)]))

    def versions(self, template_id):
        return list(self.db.question_versions.find({'template_id': template_id}).sort('number', -1))

    def version(self, template_id, version_id):
        return self.db.question_versions.find_one({'_id': version_id, 'template_id': template_id})

    def create_question(self, template, version):
        # Version first: a failed second write cannot expose a head pointing at missing content.
        self.db.question_versions.insert_one(version)
        self.db.question_templates.insert_one(template)

    def memberships(self, template_id):
        return self.db.question_bank_memberships.distinct('bank_id', {'template_id': template_id})

    def join(self, bank_id, template_id, now):
        key = {'bank_id': bank_id, 'template_id': template_id}
        try:
            self.db.question_bank_memberships.update_one(key, {'$setOnInsert': {
                **key, '_id': str(uuid4()), 'schema_version': 1, 'created_at': now,
            }}, upsert=True)
        except DuplicateKeyError:
            if self.db.question_bank_memberships.find_one(key) is None:
                raise

    def leave(self, bank_id, template_id):
        self.db.question_bank_memberships.delete_one({'bank_id': bank_id, 'template_id': template_id})

    def legacy_sources(self, survey_id):
        return list(self.db.question_usages.find({'survey_id': survey_id}))

    def recover_pending(self, template):
        """Complete an explicitly reserved immutable append after a lost connection.

        Pending content is saved in the identity document before inserting the version.
        Readers finish it instead of exposing a partially committed version history.
        """
        pending = template.get('pending_version')
        if pending is None:
            return template
        try:
            self.db.question_versions.update_one({'_id': pending['_id']}, {'$setOnInsert': pending}, upsert=True)
        except DuplicateKeyError:
            if self.db.question_versions.find_one({'_id': pending['_id']}) is None:
                raise
        actual = self.db.question_versions.find_one({'_id': pending['_id']})
        if actual != pending:
            raise ValueError('Reserved version conflicts with immutable version content')
        self.db.question_templates.update_one(
            {'_id': template['_id'], 'pending_version._id': pending['_id']},
            {'$set': {'latest_version_id': pending['_id'], 'updated_at': pending['created_at']},
             '$inc': {'edit_version': 1}, '$unset': {'pending_version': ''}})
        # Return this reservation's commit token, not a later writer's token paired
        # with our older content. A later save must conflict rather than overwrite.
        committed = {**template, 'latest_version_id': pending['_id'],
                     'updated_at': pending['created_at'], 'edit_version': template['edit_version'] + 1}
        committed.pop('pending_version', None)
        return committed

    def append_version(self, template, expected_version, version):
        reserved = self.db.question_templates.find_one_and_update(
            {'_id': template['_id'], 'owner_id': template['owner_id'], 'edit_version': expected_version,
             'pending_version': {'$exists': False}},
            {'$set': {'pending_version': version}}, return_document=ReturnDocument.AFTER)
        return self.recover_pending(reserved) if reserved else None

    def any_template(self, template_id):
        return self.db.question_templates.find_one({'_id': template_id})

    def grant(self, template_id, version_id, user):
        return self.db.question_grants.find_one({'template_id': template_id, 'version_id': version_id,
                                                'grantee_id': user, 'active': True})

    def admit_grant(self, grant_id, user, now):
        return self.db.question_grants.find_one_and_update(
            {'_id': grant_id, 'grantee_id': user, 'active': True},
            {'$inc': {'admissions': 1}, '$set': {'last_admitted_at': now}}, return_document=ReturnDocument.AFTER)

    def received(self, user, template_id=None):
        query = {'grantee_id': user, 'active': True}
        if template_id: query['template_id'] = template_id
        return list(self.db.question_grants.find(query))

    def user_by_username(self, username):
        return self.db.users.find_one({'username': username.lower()})

    def share(self, template_id, version_id, owner, grantee, now):
        key = {'version_id': version_id, 'grantee_id': grantee}
        update = {'$set': {'active': True, 'updated_at': now}, '$inc': {'revision': 1},
                  '$setOnInsert': {'_id': str(uuid4()), 'schema_version': 1, 'template_id': template_id,
                                   'owner_id': owner, 'created_at': now}}
        try:
            return self.db.question_grants.find_one_and_update(key, update, upsert=True, return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            return self.db.question_grants.find_one_and_update(key, update, return_document=ReturnDocument.AFTER)

    def shares(self, template_id, version_id):
        result = []
        for grant in self.db.question_grants.find({'template_id': template_id, 'version_id': version_id}):
            user = self.db.users.find_one({'_id': grant['grantee_id']})
            result.append({**grant, 'username': user['username'] if user else '账号已不存在'})
        return result

    def revoke(self, template_id, version_id, grantee, revision, now):
        return self.db.question_grants.find_one_and_update(
            {'template_id': template_id, 'version_id': version_id, 'grantee_id': grantee, 'revision': revision},
            {'$set': {'active': False, 'updated_at': now}, '$inc': {'revision': 1}}, return_document=ReturnDocument.AFTER)

    def visible_surveys(self, template_id, owner):
        legacy_ids = self.db.question_usages.distinct('survey_id', {'template_id': template_id})
        return list(self.db.surveys.find({'owner_id': owner, '$or': [
            {'question_sources.template_id': template_id}, {'_id': {'$in': legacy_ids}}]}))
