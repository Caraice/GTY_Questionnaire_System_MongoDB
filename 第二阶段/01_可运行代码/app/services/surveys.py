from datetime import datetime, timezone
from math import ceil, floor
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from app.domain.availability import availability, utc_now
from app.domain.routing import validate_rules
from app.models import QuestionSet
from app.repositories.mongo import SurveyRepository
from app.repositories.library import LibraryRepository
from app.domain.question_content import content_of, content_hash


def public_survey(document):
    result = dict(document)
    result["id"] = result.pop("_id")
    result["url"] = f'/survey/{result["share_id"]}'
    now = utc_now()
    result["availability"] = availability(document, now)
    result["server_time"] = now
    return result


class SurveyService:
    def __init__(self, db):
        self.repo = SurveyRepository(db)
        self.library = LibraryRepository(db)
        self.db = db

    def create(self, owner_id, data):
        now = datetime.now(timezone.utc)
        if data.deadline is not None and data.deadline <= now:
            raise HTTPException(422, "截止时间必须晚于当前时间")
        document = {
            "_id": str(uuid4()), "schema_version": 1, "version": 1,
            "owner_id": owner_id, "share_id": uuid4().hex,
            **data.model_dump(), "status": "draft", "questions": [], "rules": [],
            "created_at": now, "updated_at": now,
        }
        self.repo.create(document)
        return public_survey(document)

    def list_owned(self, owner_id):
        return [public_survey(d) for d in self.repo.list_owned(owner_id)]

    def get_owned(self, survey_id, owner_id):
        document = self.repo.get_owned(survey_id, owner_id)
        if document is None:
            raise HTTPException(404, "问卷不存在")
        return self._present(document)

    def _present(self, document):
        owner_id = document['owner_id']
        result = self._with_sources(document)
        from app.services.library import LibraryService
        library = LibraryService(self.db)
        for source in result['question_sources']:
            source['available_update'] = None
            if document['status'] == 'draft':
                try:
                    versions, _ = library.accessible_versions(source['template_id'], owner_id)
                    latest = versions[0]
                    if latest['number'] > source['version_number']:
                        source['available_update'] = {'id': latest['_id'], 'number': latest['number'],
                                                      'title': latest['content']['title']}
                except HTTPException as error:
                    if error.status_code != 404: raise
        return public_survey(result)

    def _with_sources(self, document):
        result = dict(document)
        # Explicit online sources take precedence; old migration usages remain read-only.
        sources = {s['instance_id']: dict(s) for s in document.get('question_sources', [])}
        for legacy in self.library.legacy_sources(document['_id']):
            if legacy['instance_id'] not in sources:
                version = self.library.version(legacy['template_id'], legacy['template_version_id'])
                if version:
                    sources[legacy['instance_id']] = {
                        k: legacy[k] for k in ('instance_id', 'template_id', 'template_version_id', 'content_hash')}
                    sources[legacy['instance_id']].update(
                        version_number=version['number'],
                        title=version['content']['title'],
                        normalized_hash=content_hash(content_of({'id': legacy['instance_id'], **version['content']})))
        result['question_sources'] = self._retain_sources(sources.values(), document['questions'])
        return result

    @staticmethod
    def _retain_sources(sources, questions):
        by_id = {q['id']: q for q in questions}
        result = []
        for source in sources:
            if source['instance_id'] in by_id:
                source = dict(source)
                source.pop('available_update', None)
                source['modified'] = content_hash(content_of(by_id[source['instance_id']])) != source['normalized_hash']
                result.append(source)
        return result

    def import_question(self, survey_id, owner_id, data):
        from app.services.library import LibraryService
        doc = self._versioned_owned(survey_id, owner_id, data.expected_version)
        if doc['status'] != 'draft':
            raise HTTPException(409, '仅草稿问卷可以从题库添加题目')
        if len(doc['questions']) >= 200:
            raise HTTPException(422, '问卷最多支持 200 道题目')
        library = LibraryService(self.db)
        question, source = library.snapshot(data.template_id, data.template_version_id, owner_id)
        sources = self._with_sources(doc)['question_sources'] + [source]
        library.admit_source(source, owner_id)
        return self._update(doc, owner_id, {'questions': doc['questions'] + [question], 'question_sources': sources})

    def update_instance(self, survey_id, instance_id, owner_id, data):
        from app.services.library import LibraryService
        doc = self._versioned_owned(survey_id, owner_id, data.expected_version)
        if doc['status'] != 'draft':
            raise HTTPException(409, '只有草稿可以主动更新引用，已发布问卷保持原版本')
        doc = self._with_sources(doc)
        old_source = next((s for s in doc['question_sources'] if s['instance_id'] == instance_id), None)
        if old_source is None:
            raise HTTPException(404, '此题不是题库引用实例')
        library = LibraryService(self.db)
        replacement, source = library.snapshot(old_source['template_id'], data.template_version_id, owner_id)
        replacement['id'] = source['instance_id'] = instance_id
        old = next(q for q in doc['questions'] if q['id'] == instance_id)
        if old['type'] != replacement['type'] and any(r['source_question_id'] == instance_id for r in doc['rules']):
            raise HTTPException(422, '该实例是跳转来源，不能直接更换题型，请先调整跳转规则')
        questions = [replacement if q['id'] == instance_id else q for q in doc['questions']]
        try:
            validate_rules(questions, doc['rules'])
        except ValueError as error:
            raise HTTPException(422, '更新会破坏跳转引用：' + str(error)) from None
        sources = [source if s['instance_id'] == instance_id else s for s in doc['question_sources']]
        library.admit_source(source, owner_id)
        return self._update(doc, owner_id, {'questions': questions, 'question_sources': sources})

    def save_questions(self, survey_id, owner_id, data):
        original = self.get_owned(survey_id, owner_id)
        if original["status"] != "draft":
            raise HTTPException(409, "仅草稿问卷可以编辑题目")
        if original["version"] != data.expected_version:
            raise HTTPException(409, "问卷已被其他窗口修改，请重新加载最新版本后再保存")
        questions = [question.model_dump() for question in data.questions]
        self._protect_existing_rules(original, questions)
        sources = self._retain_sources(original['question_sources'], questions)
        # Content, provenance, order and management version commit in one document.
        document = self.repo.replace_questions(survey_id, owner_id, data.expected_version,
                                               questions, utc_now(), sources=sources)
        if document is None:
            # A concurrent write may have happened after the preflight check.
            self.get_owned(survey_id, owner_id)
            raise HTTPException(409, "问卷版本或状态已变化，请重新加载最新版本后再保存")
        return self._present(document)

    def _versioned_owned(self, survey_id, owner_id, expected_version):
        document = self.repo.get_owned(survey_id, owner_id)
        if document is None:
            raise HTTPException(404, "问卷不存在")
        if document["version"] != expected_version:
            raise HTTPException(409, "问卷已被其他窗口修改，请刷新后重试")
        return document

    def _update(self, document, owner_id, fields):
        if document["status"] == "published" and "published_version" not in document:
            fields = {**fields, "published_version": document["version"]}
        updated = self.repo.update_owned(document["_id"], owner_id, document["version"],
                                         document["status"], fields, utc_now())
        if updated is None:
            self.get_owned(document["_id"], owner_id)
            raise HTTPException(409, "问卷版本或状态已变化，请刷新后重试")
        return self._present(updated)

    def save_rules(self, survey_id, owner_id, data):
        document = self._versioned_owned(survey_id, owner_id, data.expected_version)
        if document["status"] != "draft":
            raise HTTPException(409, "仅草稿问卷可以修改跳转规则")
        rules = [rule.model_dump() for rule in data.rules]
        try:
            validate_rules(document["questions"], rules)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return self._update(document, owner_id, {"rules": rules})

    def publish(self, survey_id, owner_id, expected_version):
        document = self._versioned_owned(survey_id, owner_id, expected_version)
        if document["status"] != "draft":
            raise HTTPException(409, "仅草稿问卷可以发布，关闭后不能重新开放")
        if not document["questions"]:
            raise HTTPException(422, "问卷至少需要一道题目才能发布")
        if document["deadline"] is not None and document["deadline"] <= utc_now():
            raise HTTPException(422, "截止时间已到，请修改截止时间后再发布")
        try:
            questions = QuestionSet(expected_version=expected_version, questions=document["questions"])
            normalized = [question.model_dump() for question in questions.questions]
            validate_rules(normalized, document["rules"])
            for question in normalized:
                limits = question["validation"]
                if question["required"] and (
                    (question["type"] == "multiple" and (limits.get("max_choices") == 0 or limits.get("exact_choices") == 0))
                    or (question["type"] == "text" and limits.get("max_length") == 0)
                ):
                    raise ValueError("必答题的限制不能使该题无法作答")
                if (question["required"] and question["type"] == "number" and limits.get("integer_only")
                    and limits.get("minimum") is not None and limits.get("maximum") is not None
                    and ceil(limits["minimum"]) > floor(limits["maximum"])):
                    raise ValueError("必答数字题的范围内至少需要一个整数")
        except ValidationError:
            raise HTTPException(422, "题目配置不合法，请检查题干、选项、ID 和校验限制") from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return self._update(document, owner_id, {
            "status": "published", "questions": normalized,
            "published_version": expected_version + 1,
        })

    def close(self, survey_id, owner_id, expected_version):
        document = self._versioned_owned(survey_id, owner_id, expected_version)
        if document["status"] != "published":
            raise HTTPException(409, "只有已发布的问卷可以关闭")
        return self._update(document, owner_id, {"status": "closed"})

    def update_deadline(self, survey_id, owner_id, data):
        document = self._versioned_owned(survey_id, owner_id, data.expected_version)
        if document["status"] == "closed":
            raise HTTPException(409, "已关闭的问卷不能修改截止时间或重新开放")
        if data.deadline is not None and data.deadline <= utc_now():
            raise HTTPException(422, "截止时间必须晚于当前时间，立即停止收集请关闭问卷")
        return self._update(document, owner_id, {"deadline": data.deadline})

    @staticmethod
    def _protect_existing_rules(original, questions):
        """Keep existing rule references safe without implementing the M3 engine."""
        by_id = {q["id"]: q for q in questions}
        old_by_id = {q["id"]: q for q in original["questions"]}
        position = {q["id"]: index for index, q in enumerate(questions)}
        for rule in original.get("rules", []):
            source, target = rule["source_question_id"], rule["target_question_id"]
            if source not in by_id or (target != "END" and target not in by_id):
                raise HTTPException(422, "不能删除跳转规则引用的题目，请先调整规则")
            if target != "END" and position[target] <= position[source]:
                raise HTTPException(422, "排序后跳转目标必须位于来源题之后，请先调整规则")
            old_source = old_by_id.get(source, {})
            if old_source.get("type") != by_id[source]["type"]:
                raise HTTPException(422, "不能修改跳转来源题的题型，请先调整规则")
            old_options = {o["id"] for o in old_source.get("options", [])}
            new_options = {o["id"] for o in by_id[source].get("options", [])}
            if not old_options.issubset(new_options):
                raise HTTPException(422, "不能删除跳转来源题的已有选项，请先调整规则")

