from fastapi import HTTPException
from pydantic import ValidationError

from app.domain.availability import availability, utc_now
from app.domain.question_content import content_of, content_hash
from app.domain.statistics_merge import compatibility
from app.repositories.library import LibraryRepository
from app.repositories.library_statistics import LibraryStatisticsRepository
from app.services.library import LibraryService

POLICY = {
    'permission': '只统计当前账号拥有结果查看权限的问卷；题目共享不授予他人的答卷权限，不展示其问卷信息或数量。',
    'survey_count': '已发布或关闭且含合规引用的问卷数，包括尚无答卷的问卷；草稿排除。',
    'response_count': '参与问卷当前冻结定义下的答卷总数，含跳题答卷；每份答卷在本组统计中只计一次。',
    'answer_count': '实际路径中符合来源版本限制的实例回答次数；重复题分别计数，跳题、空白和非法历史答案不计。',
    'identity': '不按账号、匿名标识或跨问卷人员身份去重；统计回答次数，不声称独立人数。',
    'local_changes': '与来源版本内容不一致的本地修改实例排除，不把它冒充该版本的回答。',
    'limits': '回答按各自原版本的题型与限制校验，合并不套用基准版本的长度、数量或数值限制。',
}


class LibraryStatisticsService:
    def __init__(self, db):
        self.repo = LibraryRepository(db)
        self.library = LibraryService(db)
        self.statistics = LibraryStatisticsRepository(db)

    def _context(self, template_id, user):
        surveys = self.repo.visible_surveys(template_id, user)
        sources = {}
        for survey in surveys:
            combined = {s['instance_id']: s for s in self.repo.legacy_sources(survey['_id'])}
            combined.update({s['instance_id']: s for s in survey.get('question_sources', [])})
            sources[survey['_id']] = {key: s for key, s in combined.items() if s['template_id'] == template_id}
        owned = self.repo.template(template_id, user)
        grants = [] if owned else self.repo.received(user, template_id)
        if owned:
            versions, _ = self.library.accessible_versions(template_id, user)
        else:
            # An owned frozen snapshot remains readable after the source grant is revoked.
            visible_ids = {s['template_version_id'] for items in sources.values() for s in items.values()}
            visible_ids.update(g['version_id'] for g in grants)
            versions = [v for v in self.repo.versions(template_id) if v['_id'] in visible_ids]
        if not versions:
            raise HTTPException(404, '题目不存在，或没有可查看的版本及问卷结果')
        return versions, surveys, sources, bool(owned or grants)

    def _plan(self, versions, surveys, sources):
        by_id = {v['_id']: v for v in versions}
        scopes, excluded = [], []
        for survey in surveys:
            questions = {q['id']: (index, q) for index, q in enumerate(survey['questions'], 1)}
            for instance_id, source in sources[survey['_id']].items():
                version = by_id.get(source['template_version_id'])
                found = questions.get(instance_id)
                row = {'survey_id': survey['_id'], 'title': survey['title'], 'status': survey['status'],
                       'availability': availability(survey, utc_now()), 'instance_id': instance_id,
                       'question_number': found[0] if found else None, 'version_id': source['template_version_id'],
                       'version_number': version['number'] if version else None}
                reasons = []
                if not version:
                    reasons.append('来源版本缺失或不属于当前可统计版本')
                if not found:
                    reasons.append('实例已从问卷移除')
                if survey['status'] == 'draft':
                    reasons.append('草稿问卷尚未发布')
                if found and version:
                    try:
                        canonical = content_of({**version['content'], 'id': instance_id})
                        actual = content_of(found[1])
                        if content_hash(version['content']) != version['content_hash'] or source['content_hash'] != version['content_hash']:
                            reasons.append('来源版本或引用的内容摘要校验失败')
                        if content_hash(actual) != content_hash(canonical):
                            reasons.append('问卷内题目已本地修改，与来源版本不一致')
                    except (ValidationError, KeyError, TypeError):
                        reasons.append('题目定义不符合当前题型校验')
                if reasons:
                    excluded.append({**row, 'reasons': reasons})
                else:
                    scopes.append({**row, 'survey_version': survey.get('published_version', survey['version'])})
        return scopes, excluded

    @staticmethod
    def _version_info(version):
        return {'id': version['_id'], 'number': version['number'], 'content': version['content']}

    @staticmethod
    def _definition_errors(version):
        try:
            content_of({**version['content'], 'id': 'statistics'})
            if content_hash(version['content']) != version['content_hash']:
                return ['来源版本内容摘要校验失败']
        except (ValidationError, KeyError, TypeError):
            return ['来源版本定义不符合当前题型校验']
        return []

    def summarize(self, template_id, user, value_page=1):
        versions, surveys, sources, history_available = self._context(template_id, user)
        scopes, excluded = self._plan(versions, surveys, sources)
        items = []
        for version in versions:
            matching = [s for s in scopes if s['version_id'] == version['_id']]
            info = self._version_info(version)
            question = {'id': 'statistics', **version['content']}
            errors = self._definition_errors(version)
            items.append({**info, 'definition_errors': errors,
                          'metrics': None if errors else self.statistics.summarize(user, matching, [{'version': version, 'question': question}], question, value_page),
                          'included_instances': matching,
                          'excluded_instances': [s for s in excluded if s['version_id'] == version['_id']]})
        return {'template_id': template_id, 'policy': POLICY, 'versions': items,
                'source_history_available': history_available,
                'excluded_instances': excluded, 'generated_at': utc_now()}

    def merge(self, template_id, user, data, value_page=1):
        versions, surveys, sources, _ = self._context(template_id, user)
        visible = {v['_id']: v for v in versions}
        if not set(data.version_ids).issubset(visible):
            raise HTTPException(404, '所选版本不存在或当前账号无权统计')
        selected = [visible[key] for key in data.version_ids]
        base = visible[data.base_version_id]
        base_unit = base['content'].get('unit') or data.unit_confirmations.get(base['_id'])
        included, excluded_versions, plans = [], [], []
        for version in selected:
            errors = self._definition_errors(version) or self._definition_errors(base)
            result = ({'reasons': errors, 'warnings': [], 'mapping': None, 'unit': None} if errors else
                      compatibility(base, version, data.option_mappings.get(version['_id']),
                                    data.unit_confirmations.get(version['_id']), base_unit))
            if version['_id'] == base['_id'] and result['mapping'] and any(k != v for k, v in result['mapping'].items()):
                result['reasons'].append('基准版本的选项不能重新映射')
            info = {**self._version_info(version), **result,
                    'unit_source': '版本声明' if version['content'].get('unit') else '本次明确确认' if version['_id'] in data.unit_confirmations else None}
            if result['reasons']:
                excluded_versions.append(info)
            else:
                included.append(info)
                plans.append({'version': version, 'question': {'id': 'statistics', **version['content']}, 'mapping': result['mapping']})
        # An invalid base must not silently be replaced by a different selected version.
        base_ok = base['_id'] in {v['id'] for v in included}
        scopes, excluded_instances = self._plan(selected, surveys, sources)
        included_ids = {v['id'] for v in included}
        used = [s for s in scopes if s['version_id'] in included_ids]
        reasons_by_version = {v['id']: v['reasons'] for v in excluded_versions}
        excluded_instances.extend({**s, 'reasons': reasons_by_version[s['version_id']]}
                                  for s in scopes if s['version_id'] in reasons_by_version)
        performed = base_ok and len(included) >= 2
        question = {'id': 'statistics', **base['content']}
        return {'template_id': template_id, 'policy': POLICY, 'base_version_id': base['_id'],
                'performed': performed, 'message': '已按下列版本与明确配置合并' if performed else '不足两个兼容版本，或基准版本未通过检查，未执行合并',
                'included_versions': included, 'excluded_versions': excluded_versions,
                'included_instances': used if performed else [], 'excluded_instances': excluded_instances,
                'metrics': self.statistics.summarize(user, used, plans, question, value_page) if performed else None,
                'unit': base_unit if question['type'] == 'number' else None,
                'configuration': data.model_dump(), 'generated_at': utc_now()}
