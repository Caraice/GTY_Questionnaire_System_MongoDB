from fastapi import APIRouter, Depends, Query
from app.services.library_pages import LibraryPages

from app.api.dependencies import current_user
from app.db import get_db
from app.models import (BankCreate, LibraryQuestionCreate, QuestionImport, LibraryVersionCreate,
                        LibraryFork, LibraryRestore, ShareCreate, VersionedAction, InstanceVersionUpdate, LibraryStatisticsMerge)
from app.services.library import LibraryService
from app.services.surveys import SurveyService
from app.services.library_statistics import LibraryStatisticsService

router = APIRouter(prefix='/api', tags=['题库'])


@router.get('/library/questions/{template_id}/statistics')
def library_statistics(template_id: str, value_page: int = Query(1, ge=1, le=10000), user=Depends(current_user), db=Depends(get_db)):
    return LibraryStatisticsService(db).summarize(template_id, user['_id'], value_page)


@router.post('/library/questions/{template_id}/statistics/merge')
def merge_statistics(template_id: str, data: LibraryStatisticsMerge, value_page: int = Query(1, ge=1, le=10000), user=Depends(current_user), db=Depends(get_db)):
    return LibraryStatisticsService(db).merge(template_id, user['_id'], data, value_page)


@router.get('/question-banks')
def banks(page: int | None = Query(None, ge=1, le=10000), page_size: int = Query(20, ge=1, le=100),
          user=Depends(current_user), db=Depends(get_db)):
    if page is not None:
        return LibraryPages(db).banks(user['_id'], page, page_size)
    return LibraryService(db).banks(user['_id'])


@router.post('/question-banks', status_code=201)
def create_bank(data: BankCreate, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).create_bank(user['_id'], data)


@router.get('/library/questions')
def questions(bank_id: str | None = None, page: int | None = Query(None, ge=1, le=10000),
              page_size: int = Query(20, ge=1, le=100), user=Depends(current_user), db=Depends(get_db)):
    if page is not None:
        return LibraryPages(db).questions(user['_id'], page, page_size, bank_id)
    return LibraryService(db).questions(user['_id'], bank_id)


@router.post('/library/questions', status_code=201)
def create_question(data: LibraryQuestionCreate, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).create_question(user['_id'], data)


@router.get('/library/shared')
def shared(page: int | None = Query(None, ge=1, le=10000), page_size: int = Query(20, ge=1, le=100),
           user=Depends(current_user), db=Depends(get_db)):
    if page is not None:
        return LibraryPages(db).questions(user['_id'], page, page_size, shared=True)
    return LibraryService(db).shared_questions(user['_id'])


@router.get('/library/questions/{template_id}')
def detail(template_id: str, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).detail(template_id, user['_id'])


@router.post('/library/questions/{template_id}/versions', status_code=201)
def save_version(template_id: str, data: LibraryVersionCreate, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).save_version(template_id, user['_id'], data)


@router.get('/library/questions/{template_id}/history-page')
def history_page(template_id: str, before: int | None = Query(None, ge=1),
                 page_size: int = Query(10, ge=1, le=100), user=Depends(current_user), db=Depends(get_db)):
    return LibraryPages(db).history(template_id, user['_id'], before, page_size)


@router.get('/library/questions/{template_id}/versions/{version_id}/usages')
def usage_page(template_id: str, version_id: str, page: int = Query(1, ge=1, le=10000),
               page_size: int = Query(20, ge=1, le=100), user=Depends(current_user), db=Depends(get_db)):
    return LibraryPages(db).usages(template_id, version_id, user['_id'], page, page_size)


@router.post('/library/questions/{template_id}/fork', status_code=201)
def fork(template_id: str, data: LibraryFork, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).fork(template_id, user['_id'], data)


@router.post('/library/questions/{template_id}/restore', status_code=201)
def restore(template_id: str, data: LibraryRestore, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).save_version(template_id, user['_id'], data, restore=True)


@router.get('/library/questions/{template_id}/versions/{version_id}/shares')
def shares(template_id: str, version_id: str, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).shares(template_id, version_id, user['_id'])


@router.post('/library/questions/{template_id}/versions/{version_id}/shares')
def share(template_id: str, version_id: str, data: ShareCreate, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).share(template_id, version_id, user['_id'], data)


@router.delete('/library/questions/{template_id}/versions/{version_id}/shares/{grantee_id}')
def revoke(template_id: str, version_id: str, grantee_id: str, data: VersionedAction,
           user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).revoke(template_id, version_id, grantee_id, user['_id'], data.expected_version)


@router.get('/library/questions/{template_id}/versions')
def versions(template_id: str, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).versions(template_id, user['_id'])


@router.put('/question-banks/{bank_id}/questions/{template_id}')
def join(bank_id: str, template_id: str, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).membership(bank_id, template_id, user['_id'], True)


@router.delete('/question-banks/{bank_id}/questions/{template_id}')
def leave(bank_id: str, template_id: str, user=Depends(current_user), db=Depends(get_db)):
    return LibraryService(db).membership(bank_id, template_id, user['_id'], False)


@router.post('/surveys/{survey_id}/imports')
def import_question(survey_id: str, data: QuestionImport, user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).import_question(survey_id, user['_id'], data)


@router.post('/surveys/{survey_id}/instances/{instance_id}/version')
def update_instance(survey_id: str, instance_id: str, data: InstanceVersionUpdate,
                    user=Depends(current_user), db=Depends(get_db)):
    return SurveyService(db).update_instance(survey_id, instance_id, user['_id'], data)
