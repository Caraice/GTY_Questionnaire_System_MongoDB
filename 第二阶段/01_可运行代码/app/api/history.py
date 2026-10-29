from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_user
from app.db import get_db
from app.services.history import HistoryService

router = APIRouter(prefix='/api/me/responses', tags=['我的填写记录'])


@router.get('')
def list_responses(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
                   user=Depends(current_user), db=Depends(get_db)):
    return HistoryService(db).list_owned(user['_id'], limit, offset)


@router.get('/{response_id}')
def response_detail(response_id: str, user=Depends(current_user), db=Depends(get_db)):
    return HistoryService(db).detail(response_id, user['_id'])
