"""Statistics are computed by MongoDB, never by loading answer documents in Python."""
from sys import float_info


# Match Python str.strip(), used by domain.validation, including Unicode spaces.
STRIP_CHARS = "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"


def _match(expression):
    return {"$match": {"$expr": expression}}


def _valid_answers(question):
    """Guard operators explicitly: MongoDB may merge adjacent match stages.

    Historical malformed answers are excluded from this question's metrics;
    their containing response still contributes to total_submissions.
    """
    question_id = {"$literal": question["id"]}
    stages = [
        _match({"$in": [question_id, "$path"]}),
        {"$project": {"value": {"$getField": {"field": question_id, "input": "$answers"}}}},
    ]
    kind, rules = question["type"], question.get("validation", {})
    if kind == "single":
        allowed = [option["id"] for option in question["options"]]
        stages += [_match({"$eq": [{"$type": "$value"}, "string"]}),
                   _match({"$in": ["$value", {"$literal": allowed}]})]
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
        stages += [{"$set": {"value": {"$cond": [{"$isArray": "$value"}, "$value", []]}}},
                   _match({"$and": checks})]
    elif kind == "text":
        checks = [{"$gt": [{"$strLenCP": "$value"}, 0]},
                  {"$gte": [{"$strLenCP": "$value"}, rules.get("min_length", 0)]}]
        if rules.get("max_length") is not None:
            checks.append({"$lte": [{"$strLenCP": "$value"}, rules["max_length"]]})
        stages += [{"$set": {"value": {"$cond": [
                       {"$eq": [{"$type": "$value"}, "string"]},
                       {"$trim": {"input": "$value", "chars": STRIP_CHARS}}, ""]}}},
                   _match({"$and": checks})]
    elif kind == "number":
        # BSON bool is not a number. Finite bounds also exclude NaN and infinities.
        finite = {"$and": [{"$in": [{"$type": "$value"}, ["int", "long", "double"]]},
                           {"$gte": ["$value", -float_info.max]}, {"$lte": ["$value", float_info.max]}]}
        checks = [{"$ne": ["$value", None]}]
        for key, operator in [("minimum", "$gte"), ("maximum", "$lte")]:
            if rules.get(key) is not None:
                checks.append({operator: ["$value", rules[key]]})
        stages += [{"$set": {"value": {"$cond": [finite, "$value", None]}}},
                   _match({"$and": checks})]
        if rules.get("integer_only", False):
            stages.append(_match({"$eq": [{"$mod": ["$value", 1]}, 0]}))
    return stages


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
