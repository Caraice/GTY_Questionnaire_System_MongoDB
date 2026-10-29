from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.domain.availability import utc_now, availability
from app.domain.question_content import content_of, content_hash
from app.repositories.library import LibraryRepository
from app.domain.version_diff import compare_content


def public(document):
    result = dict(document)
    result['id'] = result.pop('_id')
    result.pop('pending_version', None)
    return result


def stored_now():
    now = utc_now()
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


class LibraryService:
    def __init__(self, db):
        self.repo = LibraryRepository(db)

    def require_bank(self, bank_id, owner):
        doc = self.repo.bank(bank_id, owner)
        if doc is None:
            raise HTTPException(404, '题库不存在')
        return doc

    def require_template(self, template_id, owner):
        doc = self.repo.template(template_id, owner)
        if doc is None:
            raise HTTPException(404, '题目不存在')
        try:
            return self.repo.recover_pending(doc)
        except (ValueError, DuplicateKeyError):
            raise HTTPException(409, '预留版本与历史记录冲突，已停止恢复；请保留数据并检查，不能覆盖历史版本') from None

    def accessible_versions(self, template_id, user):
        owned = self.repo.template(template_id, user)
        if owned:
            committed = self.require_template(template_id, user)
            head = self.repo.version(template_id, committed['latest_version_id'])
            if head is None:
                raise HTTPException(409, '题目当前版本缺失，请检查数据')
            return [v for v in self.repo.versions(template_id) if v['number'] <= head['number']], True
        ids = {g['version_id'] for g in self.repo.received(user, template_id)}
        versions = [v for v in self.repo.versions(template_id) if v['_id'] in ids]
        if not versions:
            raise HTTPException(404, '题目不存在或未获共享权限')
        return versions, False

    def version_for(self, template_id, version_id, user):
        version = self.repo.version(template_id, version_id)
        owned = self.repo.template(template_id, user)
        grant = None if owned else self.repo.grant(template_id, version_id, user)
        if owned:
            self.require_template(template_id, user)
        if version is None or (owned is None and grant is None):
            raise HTTPException(404, '题目版本不存在或共享已撤销')
        return version, grant

    def admit_source(self, source, user):
        if source.get('access_grant_id') and self.repo.admit_grant(source['access_grant_id'], user, stored_now()) is None:
            raise HTTPException(403, '共享已撤销，不能再导入或另存此版本')

    def banks(self, owner):
        return [public(d) for d in self.repo.banks(owner)]

    def create_bank(self, owner, data):
        doc = {'_id': str(uuid4()), 'schema_version': 1, 'owner_id': owner,
               'name': data.name, 'created_at': stored_now()}
        self.repo.create_bank(doc)
        return public(doc)

    def questions(self, owner, bank_id=None):
        if bank_id:
            self.require_bank(bank_id, owner)
        result = []
        for doc in self.repo.templates(owner, bank_id):
            doc = self.repo.recover_pending(doc)
            version = self.repo.version(doc['_id'], doc['latest_version_id'])
            result.append({**public(doc), 'latest': public(version) if version else None,
                           'bank_ids': self.repo.memberships(doc['_id']), 'can_edit': True})
        return result

    def shared_questions(self, user):
        result = []
        for template_id in dict.fromkeys(g['template_id'] for g in self.repo.received(user)):
            versions, _ = self.accessible_versions(template_id, user)
            result.append({'id': template_id, 'can_edit': False, 'edit_version': None,
                           'latest': public(versions[0]), 'bank_ids': []})
        return result

    def versions(self, template_id, owner):
        versions, _ = self.accessible_versions(template_id, owner)
        return [public(v) for v in versions]

    def create_question(self, owner, data, derived_from=None):
        now, template_id, version_id = stored_now(), str(uuid4()), str(uuid4())
        content = content_of(data.question.model_dump())
        origin = {'kind': 'fork' if derived_from else 'library'}
        if derived_from:
            origin.update(template_id=derived_from['template_id'], version_id=derived_from['_id'],
                          version_number=derived_from['number'], title=derived_from['content']['title'])
        template = {'_id': template_id, 'schema_version': 1, 'owner_id': owner,
                    'latest_version_id': version_id, 'edit_version': 1, 'visibility': 'private',
                    'origin': origin, 'created_at': now, 'updated_at': now}
        version = {'_id': version_id, 'schema_version': 1, 'template_id': template_id,
                   'number': 1, 'parent_version_id': None, 'derived_from_version_id': derived_from['_id'] if derived_from else None,
                   'restored_from_version_id': None, 'content': content,
                   'content_hash': content_hash(content), 'created_by': owner,
                   'created_at': now, 'origin': origin}
        self.repo.create_question(template, version)
        return {**public(template), 'latest': public(version), 'bank_ids': [], 'can_edit': True}

    def save_version(self, template_id, owner, data, *, restore=False):
        template = self.require_template(template_id, owner)
        if template['edit_version'] != data.expected_version:
            raise HTTPException(409, '题目已产生新版本，请重新加载后再保存')
        base_id = data.source_version_id if restore else data.base_version_id
        base = self.repo.version(template_id, base_id)
        head = self.repo.version(template_id, template['latest_version_id'])
        if base is None or head is None:
            raise HTTPException(404, '来源版本不存在')
        if restore:
            try:
                content_of({**base['content'], 'id': 'restore'})
            except ValidationError:
                raise HTTPException(422, '历史版本不符合当前题型校验，不能直接恢复') from None
            content = deepcopy(base['content'])
        else:
            content = content_of(data.question.model_dump())
        if content_hash(base['content']) != base['content_hash']:
            raise HTTPException(409, '来源版本内容校验失败')
        now = stored_now()
        version = {'_id': str(uuid4()), 'schema_version': 1, 'template_id': template_id,
                   'number': head['number'] + 1, 'parent_version_id': head['_id'],
                   'derived_from_version_id': base_id if not restore and base_id != head['_id'] else None,
                   'restored_from_version_id': base_id if restore else None,
                   'content': content, 'content_hash': content_hash(content), 'created_by': owner,
                   'created_at': now, 'origin': {'kind': 'restore' if restore else 'edit', 'base_version_id': base_id}}
        try:
            updated = self.repo.append_version(template, data.expected_version, version)
        except (ValueError, DuplicateKeyError):
            raise HTTPException(409, '版本预留发生冲突，未覆盖历史；请重新加载检查当前版本') from None
        if updated is None:
            raise HTTPException(409, '题目版本已变化，请重新加载后再保存')
        return {**public(updated), 'latest': public(version), 'can_edit': True}

    def fork(self, template_id, user, data):
        version, grant = self.version_for(template_id, data.source_version_id, user)
        if content_hash(version['content']) != version['content_hash']:
            raise HTTPException(409, '来源版本内容校验失败')
        if grant and self.repo.admit_grant(grant['_id'], user, stored_now()) is None:
            raise HTTPException(403, '共享已撤销，不能另存此版本')
        return self.create_question(user, data, derived_from=version)

    def share(self, template_id, version_id, owner, data):
        self.require_template(template_id, owner)
        self.version_for(template_id, version_id, owner)
        user = self.repo.user_by_username(data.username)
        if user is None:
            raise HTTPException(404, '接收账号不存在，请先注册')
        if user['_id'] == owner:
            raise HTTPException(422, '自己的题目无需共享给自己')
        return public(self.repo.share(template_id, version_id, owner, user['_id'], stored_now()))

    def shares(self, template_id, version_id, owner):
        self.require_template(template_id, owner)
        self.version_for(template_id, version_id, owner)
        return [public(g) for g in self.repo.shares(template_id, version_id)]

    def revoke(self, template_id, version_id, grantee, owner, expected_version):
        self.require_template(template_id, owner)
        grant = self.repo.revoke(template_id, version_id, grantee, expected_version, stored_now())
        if grant is None:
            raise HTTPException(409, '共享记录已变化，请刷新后重试')
        return public(grant)

    def detail(self, template_id, user):
        versions, owned = self.accessible_versions(template_id, user)
        visible = {v['_id']: v for v in versions}
        usages = {v['_id']: [] for v in versions}
        # Questionnaire visibility is owner-only in the existing architecture.
        for survey in self.repo.visible_surveys(template_id, user):
            online = {s['instance_id']: s for s in survey.get('question_sources', [])}
            legacy = {s['instance_id']: s for s in self.repo.legacy_sources(survey['_id'])}
            for index, question in enumerate(survey['questions'], 1):
                source = online.get(question['id'], legacy.get(question['id']))
                if source and source['template_id'] == template_id and source['template_version_id'] in usages:
                    version = visible[source['template_version_id']]
                    modified = content_hash(content_of(question)) != content_hash(content_of({'id': question['id'], **version['content']}))
                    usages[source['template_version_id']].append({
                        'survey_id': survey['_id'], 'title': survey['title'], 'status': survey['status'],
                        'availability': availability(survey, stored_now()),
                        'instance_id': question['id'], 'question_number': index, 'modified': modified})
        history = []
        for version in versions:
            parent = visible.get(version['parent_version_id'])
            changes = compare_content(parent['content'], version['content']) if parent else None
            history.append({**public(version), 'changes': changes, 'usages': usages[version['_id']],
                            'comparison_note': '与前一版本比较' if parent else '首版本或前序版本未获访问权限'})
        template = self.require_template(template_id, user) if owned else None
        return {'id': template_id, 'can_edit': owned, 'edit_version': template['edit_version'] if template else None,
                'latest_version_id': template['latest_version_id'] if template else versions[0]['_id'], 'versions': history,
                'usage_scope': '仅显示当前账号有权访问的问卷，每个题目实例单列；不显示他人的私有问卷'}

    def membership(self, bank_id, template_id, owner, add):
        self.require_bank(bank_id, owner)
        self.require_template(template_id, owner)
        if add:
            self.repo.join(bank_id, template_id, utc_now())
        else:
            self.repo.leave(bank_id, template_id)
        return {'bank_id': bank_id, 'template_id': template_id, 'member': add}

    def snapshot(self, template_id, version_id, owner):
        version, grant = self.version_for(template_id, version_id, owner)
        # Reuse all current question constraints, including for migrated snapshots.
        question = {**deepcopy(version['content']), 'id': str(uuid4())}
        try:
            content = content_of(question)
        except ValidationError:
            raise HTTPException(422, '来源版本不符合当前题型校验，请检查题目配置') from None
        if content_hash(version['content']) != version['content_hash']:
            raise HTTPException(409, '来源版本内容校验失败，不能导入')
        question = {'id': question['id'], **content}
        source = {'instance_id': question['id'], 'template_id': template_id,
                  'template_version_id': version_id, 'version_number': version['number'],
                  'title': content['title'],
                  'content_hash': version['content_hash'], 'normalized_hash': content_hash(content),
                  'modified': False}
        if grant: source['access_grant_id'] = grant['_id']
        return question, source
