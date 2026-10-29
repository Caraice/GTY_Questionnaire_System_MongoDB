from fastapi import APIRouter, Depends, Response

from app.api.dependencies import current_user
from app.db import get_db
from app.models import Submission
from app.services.responses import ResponseService

router = APIRouter(prefix="/api/fill", tags=["填写与提交"])


@router.get("/{share_id}")
def get_fill(share_id: str, user=Depends(current_user), db=Depends(get_db)):
    return ResponseService(db).load(share_id)


@router.post("/{share_id}/responses", status_code=201, responses={
    200: {"description": "相同请求已保存，返回原回执，不重复插入"},
    409: {"description": "禁止重复、幂等键冲突或内容版本变化"},
    410: {"description": "新提交时问卷已关闭或到期"},
})
def submit(share_id: str, data: Submission, response: Response, user=Depends(current_user), db=Depends(get_db)):
    receipt = ResponseService(db).submit(share_id, user, data)
    response.status_code = 200 if receipt["replayed"] else 201
    return receipt

