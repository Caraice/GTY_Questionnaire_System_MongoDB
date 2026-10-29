"""Bounded library/history/reference queries; cursors never confer access."""
from app.services.library import LibraryService, public
from app.domain.version_diff import compare_content
from app.domain.question_content import content_of, content_hash
from app.domain.availability import availability, utc_now


class LibraryPages:
    def __init__(self, db):
        self.db, self.service = db, LibraryService(db)

    def banks(self, user, page, size):
        rows = list(self.db.question_banks.find({'owner_id': user}).sort([('created_at', -1), ('_id', 1)]).skip((page-1)*size).limit(size+1))
        return {'items': [public(r) for r in rows[:size]], 'page': page, 'has_more': len(rows)>size}

    def questions(self, user, page, size, bank=None, shared=False):
        query = {'owner_id': user}
        if shared:
            query = {'_id': {'$in': self.db.question_grants.distinct('template_id', {'grantee_id': user, 'active': True})}}
        elif bank:
            self.service.require_bank(bank, user)
            query['_id'] = {'$in': self.db.question_bank_memberships.distinct('template_id', {'bank_id': bank})}
        docs = list(self.db.question_templates.find(query).sort([('updated_at', -1), ('_id', 1)]).skip((page-1)*size).limit(size+1))
        result = []
        for doc in docs[:size]:
            if shared:
                versions, _ = self.service.accessible_versions(doc['_id'], user)
                version, editable = versions[0], False
            else:
                doc = self.service.require_template(doc['_id'], user)
                version, editable = self.service.repo.version(doc['_id'], doc['latest_version_id']), True
            identity = public(doc) if editable else {'id': doc['_id'], 'edit_version': None}
            result.append({**identity, 'latest': public(version) if version else None,
                           'bank_ids': self.service.repo.memberships(doc['_id']) if editable else [], 'can_edit': editable})
        return {'items': result, 'page': page, 'page_size': size, 'has_more': len(docs)>size}

    def history(self, template, user, before, size):
        owned = self.service.repo.template(template, user)
        query = {'template_id': template}
        if owned:
            head = self.service.require_template(template, user)
            maximum = self.service.repo.version(template, head['latest_version_id'])['number']
            query['number'] = {'$lte': maximum, **({'$lt': before} if before else {})}
        else:
            # This also enforces access when the grant set is empty.
            versions, _ = self.service.accessible_versions(template, user)
            query['_id'] = {'$in': [v['_id'] for v in versions]}
            if before: query['number'] = {'$lt': before}
            head = None
        docs = list(self.db.question_versions.find(query).sort('number', -1).limit(size+1))
        items = []
        for v in docs[:size]:
            parent = self.service.repo.version(template, v['parent_version_id']) if v['parent_version_id'] else None
            if parent and not owned and parent['_id'] not in query['_id']['$in']: parent = None
            items.append({**public(v), 'changes': compare_content(parent['content'], v['content']) if parent else None,
                          'usages': [], 'comparison_note': '首版本或前序版本未获访问权限'})
        return {'id': template, 'can_edit': bool(owned), 'edit_version': head['edit_version'] if head else None,
                'versions': items, 'next_before': items[-1]['number'] if len(docs)>size else None,
                'usage_scope': '仅显示本人有结果权限的问卷；引用按版本独立分页'}

    def usages(self, template, version_id, user, page, size):
        version, _ = self.service.version_for(template, version_id, user)
        legacy = self.db.question_usages.distinct('survey_id', {'template_id': template, 'template_version_id': version_id})
        pipeline = [{'$match': {'owner_id': user, '$or': [
            {'question_sources': {'$elemMatch': {'template_id': template, 'template_version_id': version_id}}},
            {'_id': {'$in': legacy}}]}},
            {'$lookup': {'from': 'question_usages', 'localField': '_id', 'foreignField': 'survey_id', 'as': 'old_sources'}},
            {'$set': {'sources': {'$concatArrays': [{'$ifNull': ['$question_sources', []]}, '$old_sources']}}},
            {'$unwind': {'path': '$questions', 'includeArrayIndex': 'position'}},
            {'$set': {'source': {'$arrayElemAt': [{'$filter': {'input': '$sources', 'as': 's',
                'cond': {'$eq': ['$$s.instance_id', '$questions.id']}}}, 0]}}},
            {'$match': {'source.template_id': template, 'source.template_version_id': version_id}},
            {'$sort': {'_id': 1, 'position': 1}}, {'$skip': (page-1)*size}, {'$limit': size+1},
            {'$project': {'questions': 1, 'title': 1, 'status': 1, 'deadline': 1, 'position': 1}}]
        rows = list(self.db.surveys.aggregate(pipeline))
        items = [{'survey_id': r['_id'], 'title': r['title'], 'status': r['status'],
                  'availability': availability(r, utc_now()), 'instance_id': r['questions']['id'],
                  'question_number': r['position']+1,
                  'modified': content_hash(content_of(r['questions'])) != content_hash(content_of({'id':'source', **version['content']}))}
                 for r in rows[:size]]
        return {'items': items, 'page': page, 'page_size': size, 'has_more': len(rows)>size}
