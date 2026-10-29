"""Statistics are computed by MongoDB, never by loading answer documents in Python."""
from sys import float_info


# Match Python str.strip(), used by domain.validation, including Unicode spaces.
STRIP_CHARS = "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


def _match(expression):
    return {"$match": {"$expr": expression}}


def normalized_answer_expression(question):
    if question['type'] == 'text':
        return {'$cond': [{'$eq': [{'$type': '$value'}, 'string']},
                          {'$trim': {'input': '$value', 'chars': STRIP_CHARS}}, '']}
    return '$value'


def valid_answer_expression(question):
    """Shared Mongo predicate. $cond guards type-sensitive operators lazily."""
    kind, rules = question["type"], question.get("validation", {})
    if kind == "single":
        allowed = [option["id"] for option in question["options"]]
        return {'$and': [{'$eq': [{'$type': '$value'}, 'string']},
                         {'$in': ['$value', {'$literal': allowed}]}]}
    elif kind == "multiple":
        allowed = [option["id"] for option in question["options"]]
        checks = [
            {"$gt": [{"$size": "$value"}, 0]},
            {"$setIsSubset": ["$value", {"$literal": allowed}]},
            {"$eq": [{"$size": "$value"}, {"$size": {"$setUnion": ["$value", []]}}]},
        ]
        for key, operator in [("min_choices", "$gte"), ("max_choices", "$lte"), ("exact_choices", "$eq")]:
            if rules.get(key) is not None:
                checks.append({operator: [{"$size": "$value"}, rules[key]]})
        return {'$cond': [{'$isArray': '$value'}, {'$and': checks}, False]}
    elif kind == "text":
        clean = normalized_answer_expression(question)
        checks = [{"$gt": [{"$strLenCP": clean}, 0]},
                  {"$gte": [{"$strLenCP": clean}, rules.get("min_length", 0)]}]
        if rules.get("max_length") is not None:
            checks.append({"$lte": [{"$strLenCP": clean}, rules["max_length"]]})
        return {'$cond': [{'$eq': [{'$type': '$value'}, 'string']}, {'$and': checks}, False]}
    elif kind == "number":
        # BSON bool is not a number. Finite bounds also exclude NaN and infinities.
        finite = {"$and": [{"$in": [{"$type": "$value"}, ["int", "long", "double"]]},
                           {"$gte": ["$value", -float_info.max]}, {"$lte": ["$value", float_info.max]}]}
        checks = []
        for key, operator in [("minimum", "$gte"), ("maximum", "$lte")]:
            if rules.get(key) is not None:
                checks.append({operator: ["$value", rules[key]]})
        if rules.get("integer_only", False):
            checks.append({"$eq": [{"$mod": ["$value", 1]}, 0]})
        return {'$cond': [finite, {'$and': checks} if checks else True, False]}
    return False


def _valid_answers(question):
    question_id = {'$literal': question['id']}
    return [
        _match({'$in': [question_id, {'$cond': [{'$isArray': '$path'}, '$path', []]}]}),
        {'$project': {'value': {'$getField': {'field': question_id, 'input': '$answers'}}}},
        _match(valid_answer_expression(question)),
        {'$set': {'value': normalized_answer_expression(question)}},
    ]


class StatisticsRepository:
    def __init__(self, db):
        self.collection = db.responses

    def summarize(self, survey_id, survey_version, questions):
        facets = {"total": [{"$count": "count"}]}
        for index, question in enumerate(questions):
            valid = _valid_answers(question)
            group = {"_id": None, "answered_count": {"$sum": 1}}
            kind = question["type"]
            if kind in {"text", "number"}:
                group["values"] = {"$push": "$value"}
            if kind == "number":
                # Decimal accumulator avoids overflow when averaging large finite
                # doubles. Convert only the final bounded average back to JSON double.
                group["average"] = {"$avg": {"$toDecimal": {"$toString": "$value"}}}
            if kind == "multiple":
                group["selection_count"] = {"$sum": {"$size": "$value"}}
            summary = [*valid, {"$group": group}]
            if kind == "number":
                summary.append({"$set": {"average": {"$toDouble": "$average"}}})
            facets[f"q{index}"] = summary
            if kind in {"single", "multiple"}:
                options = [*valid]
                if kind == "multiple":
                    options.append({"$unwind": "$value"})
                options.append({"$group": {"_id": "$value", "count": {"$sum": 1}}})
                facets[f"q{index}_options"] = options
        pipeline = [
            {"$match": {"survey_id": survey_id, "survey_version": survey_version}},
            {"$sort": {"submitted_at": 1, "_id": 1}},
            {"$facet": facets},
        ]
        return next(self.collection.aggregate(pipeline))
