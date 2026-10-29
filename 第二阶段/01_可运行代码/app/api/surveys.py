from fastapi import APIRouter, Depends

from app.api.dependencies import current_user
from app.db import get_db
from app.models import DeadlineUpdate, QuestionSet, RuleSet, SurveyCreate, VersionedAction
from app.services.surveys import SurveyService
from app.services.statistics import StatisticsService

router = APIRouter(prefix="/api/surveys", tags=["问卷管理"])


@router.post("", status_code=201)
def create(data: SurveyCreate, user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).create(user["_id"], data)


@router.get("")
def list_owned(user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).list_owned(user["_id"])


@router.get("/{survey_id}")
def detail(survey_id: str, user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).get_owned(survey_id, user["_id"])


@router.put("/{survey_id}/questions")
def questions(survey_id: str, data: QuestionSet, user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).save_questions(survey_id, user["_id"], data)


@router.put("/{survey_id}/rules")
def rules(survey_id: str, data: RuleSet, user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).save_rules(survey_id, user["_id"], data)


@router.post("/{survey_id}/publish")
def publish(survey_id: str, data: VersionedAction, user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).publish(survey_id, user["_id"], data.expected_version)


@router.post("/{survey_id}/close")
def close(survey_id: str, data: VersionedAction, user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).close(survey_id, user["_id"], data.expected_version)


@router.patch("/{survey_id}/settings")
def settings(survey_id: str, data: DeadlineUpdate, user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).update_deadline(survey_id, user["_id"], data)


@router.get("/{survey_id}/statistics")
def statistics(survey_id: str, question_id: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    return StatisticsService(db).summarize(survey_id, user["_id"], question_id)

