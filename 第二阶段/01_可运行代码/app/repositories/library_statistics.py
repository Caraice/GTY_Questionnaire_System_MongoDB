"""Counts and answer metrics stay in MongoDB; Python only plans authorized metadata."""
from app.repositories.statistics import valid_answer_expression, normalized_answer_expression

VALUE_LIMIT = 100


def _literal(value):
    return {'$literal': value}


def _choose(branches, default=None):
    return {'$switch': {'branches': branches, 'default': default}}


class LibraryStatisticsRepository:
    def __init__(self, db):
        self.collection = db.surveys

    def summarize(self, user, scopes, plans, display_question, value_page=1):
        by_survey = {}
        for scope in scopes:
            by_survey.setdefault(scope['survey_id'], []).append({
                'instance_id': scope['instance_id'], 'version_id': scope['version_id']})
        pairs = list({(s['survey_id'], s['survey_version']) for s in scopes})
        instances = _choose([{'case': {'$eq': ['$_id', _literal(sid)]}, 'then': _literal(items)}
                             for sid, items in by_survey.items()], []) if by_survey else _literal([])
        lookup = {'$lookup': {'from': 'responses', 'let': {'sid': '$_id', 'ver': '$frozen_version'},
                  'pipeline': [{'$match': {'$expr': {'$and': [{'$eq': ['$survey_id', '$$sid']},
                                                            {'$eq': ['$survey_version', '$$ver']}]}}},
                               {'$sort': {'submitted_at': 1, '_id': 1}},
                               {'$project': {'answers': 1, 'path': 1}}], 'as': 'response'}}
        responses = [lookup, {'$unwind': '$response'}]
        valid = _choose([{'case': {'$eq': ['$instances.version_id', _literal(p['version']['_id'])]},
                          'then': valid_answer_expression(p['question'])} for p in plans], False) if plans else False
        normalize = normalized_answer_expression(display_question)
        map_branches = []
        for plan in plans:
            mapping = plan.get('mapping')
            if mapping:
                value = '$$option' if display_question['type'] == 'multiple' else '$value'
                mapped = _choose([{'case': {'$eq': [value, _literal(key)]}, 'then': _literal(target)}
                                  for key, target in mapping.items()])
                if display_question['type'] == 'multiple':
                    mapped = {'$map': {'input': '$value', 'as': 'option', 'in': mapped}}
                map_branches.append({'case': {'$eq': ['$instances.version_id', _literal(plan['version']['_id'])]}, 'then': mapped})
        if map_branches: normalize = _choose(map_branches, '$value')
        visited = {'$in': ['$instances.instance_id', {'$cond': [{'$isArray': '$path'}, '$path', []]}]}
        candidates = [*responses, {'$unwind': '$instances'},
                   {'$project': {'instances': 1, 'path': '$response.path',
                                 'value': {'$getField': {'field': '$instances.instance_id', 'input': '$response.answers'}}}}]
        answers = [*candidates, {'$match': {'$expr': {'$and': [visited, valid]}}},
                   {'$set': {'value': normalize}}]
        group = {'_id': None, 'answer_count': {'$sum': 1}}
        kind = display_question['type']
        if kind == 'number': group['average'] = {'$avg': {'$toDecimal': {'$toString': '$value'}}}
        if kind == 'multiple': group['selection_count'] = {'$sum': {'$size': '$value'}}
        summary = [*answers, {'$group': group}]
        if kind == 'number': summary.append({'$set': {'average': {'$toDouble': '$average'}}})
        facets = {'surveys': [{'$count': 'count'}], 'responses': [*responses, {'$count': 'count'}], 'summary': summary}
        facets['excluded_answers'] = [*candidates, {'$match': {'$expr': {'$not': [{'$and': [visited, valid]}]}}},
            {'$group': {'_id': {'$cond': [visited, '空白或不符合原版本题型/限制', '实际路径跳过']}, 'count': {'$sum': 1}}}]
        if kind in ('single', 'multiple'):
            facets['options'] = [*answers, *([{'$unwind': '$value'}] if kind == 'multiple' else []),
                                 {'$group': {'_id': '$value', 'count': {'$sum': 1}}}]
        else:
            facets['values'] = [*answers, {'$skip': (value_page-1)*VALUE_LIMIT}, {'$limit': VALUE_LIMIT}, {'$project': {'_id': 0, 'value': 1}}]
        pipeline = [{'$match': {'owner_id': user, 'status': {'$in': ['published', 'closed']}, '_id': {'$in': list(by_survey)}}},
                    {'$set': {'frozen_version': {'$ifNull': ['$published_version', '$version']}}},
                    {'$match': {'$expr': {'$in': [['$_id', '$frozen_version'], _literal([list(p) for p in pairs])]}}},
                    {'$sort': {'_id': 1}}, {'$set': {'instances': instances}}, {'$facet': facets}]
        result = next(self.collection.aggregate(pipeline, allowDiskUse=True))
        summary = next(iter(result['summary']), {})
        metrics = {'survey_count': next(iter(result['surveys']), {}).get('count', 0),
                   'response_count': next(iter(result['responses']), {}).get('count', 0),
                   'answer_count': summary.get('answer_count', 0), 'type': kind,
                   'excluded_answer_counts': [{'reason': r['_id'], 'count': r['count']} for r in result['excluded_answers']]}
        if kind in ('single', 'multiple'):
            counts = {o['_id']: o['count'] for o in result['options']}
            metrics['options'] = [{**o, 'count': counts.get(o['id'], 0)} for o in display_question['options']]
            if kind == 'multiple': metrics['selection_count'] = summary.get('selection_count', 0)
        else:
            metrics['values'] = [row['value'] for row in result['values']]
            metrics['values_limit'] = VALUE_LIMIT
            metrics['values_page'] = value_page
            metrics['values_has_more'] = metrics['answer_count'] > value_page*VALUE_LIMIT
            metrics['values_truncated'] = metrics['answer_count'] > len(metrics['values'])
            if kind == 'number': metrics['average'] = summary.get('average')
        return metrics
