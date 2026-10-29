"""Owner-only statistics over the frozen question definition and persisted responses."""
from fastapi import HTTPException

from app.repositories.mongo import SurveyRepository
from app.repositories.statistics import StatisticsRepository


class StatisticsService:
    def __init__(self, db):
        self.surveys = SurveyRepository(db)
        self.statistics = StatisticsRepository(db)

    def summarize(self, survey_id, owner_id, question_id=None):
        survey = self.surveys.get_owned(survey_id, owner_id)
        if survey is None:
            raise HTTPException(404, "问卷不存在")
        questions = survey["questions"]
        if question_id is not None:
            questions = [question for question in questions if question["id"] == question_id]
            if not questions:
                raise HTTPException(404, "题目不存在")
        version = survey.get("published_version", survey["version"])
        aggregate = self.statistics.summarize(survey_id, version, questions)
        result = []
        for index, question in enumerate(questions):
            summary = next(iter(aggregate[f"q{index}"]), {})
            item = {"id": question["id"], "title": question["title"], "type": question["type"],
                    "answered_count": summary.get("answered_count", 0)}
            if question["type"] in {"single", "multiple"}:
                counts = {entry["_id"]: entry["count"] for entry in aggregate[f"q{index}_options"]}
                item["options"] = [{**option, "count": counts.get(option["id"], 0)} for option in question["options"]]
            if question["type"] == "multiple":
                item["selection_count"] = summary.get("selection_count", 0)
            if question["type"] in {"text", "number"}:
                item["values"] = summary.get("values", [])
            if question["type"] == "number":
                item["average"] = summary.get("average")
            result.append(item)
        return {"survey_id": survey_id, "survey_version": version,
                "total_submissions": next(iter(aggregate["total"]), {}).get("count", 0), "questions": result}

