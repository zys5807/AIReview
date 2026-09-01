"""交易计划接口（多用户：按当前用户隔离）"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Trade, TradePlan, User
from ..routers.auth import get_current_user
from ..schemas import (
    MessageOut,
    TradePlanCreate,
    TradePlanExecuteIn,
    TradePlanOut,
    TradePlanUpdate,
)
from ..services.analysis import AnalysisError, plan_comparison, plan_review

router = APIRouter(prefix="/api/trade-plans", tags=["交易计划"])


def _get_owned_plan(db: Session, plan_id: int, user_id: int) -> TradePlan:
    plan = db.get(TradePlan, plan_id)
    if not plan or plan.user_id != user_id:
        raise HTTPException(status_code=404, detail="交易计划不存在")
    return plan


@router.post("", response_model=TradePlanOut, status_code=201)
def create_plan(
    data: TradePlanCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    payload = data.model_dump()
    payload["user_id"] = user.id
    plan = TradePlan(**payload)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.get("", response_model=list[TradePlanOut])
def list_plans(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    status: str | None = None,
    plan_date: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    """交易计划列表（可按状态/计划日期筛选）"""
    from datetime import date as _date

    q = db.query(TradePlan).filter(TradePlan.user_id == user.id)
    if status:
        q = q.filter(TradePlan.status == status)
    if plan_date:
        q = q.filter(TradePlan.plan_date == _date.fromisoformat(plan_date))
    if start:
        q = q.filter(TradePlan.plan_date >= _date.fromisoformat(start))
    if end:
        q = q.filter(TradePlan.plan_date <= _date.fromisoformat(end))
    return q.order_by(TradePlan.plan_date.desc(), TradePlan.created_at.desc()).all()


@router.get("/{plan_id}", response_model=TradePlanOut)
def get_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_owned_plan(db, plan_id, user.id)


@router.put("/{plan_id}", response_model=TradePlanOut)
def update_plan(
    plan_id: int,
    data: TradePlanUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    plan = _get_owned_plan(db, plan_id, user.id)
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(plan, field, value)
    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/{plan_id}", response_model=MessageOut)
def delete_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    plan = _get_owned_plan(db, plan_id, user.id)
    db.delete(plan)
    db.commit()
    return {"message": "删除成功"}


@router.post("/{plan_id}/execute", response_model=TradePlanOut)
def execute_plan(
    plan_id: int,
    data: TradePlanExecuteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """标记计划为已执行，并关联实际交易"""
    plan = _get_owned_plan(db, plan_id, user.id)
    plan.status = "executed"
    plan.linked_trade_id = data.linked_trade_id
    db.commit()
    db.refresh(plan)
    return plan


@router.post("/{plan_id}/cancel", response_model=TradePlanOut)
def cancel_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    plan = _get_owned_plan(db, plan_id, user.id)
    plan.status = "cancelled"
    db.commit()
    db.refresh(plan)
    return plan


@router.post("/{plan_id}/review", response_model=dict)
def review_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 预评审交易计划"""
    plan = _get_owned_plan(db, plan_id, user.id)
    try:
        result = plan_review(plan, db)
    except AnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e))
    import json as _json

    plan.review_result = _json.dumps(result, ensure_ascii=False)
    db.commit()
    return result


@router.post("/{plan_id}/comparison", response_model=dict)
def compare_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 分析计划-执行对照（需已关联实际交易）"""
    plan = _get_owned_plan(db, plan_id, user.id)
    if plan.status != "executed" or not plan.linked_trade_id:
        raise HTTPException(status_code=400, detail="该计划尚未关联已执行的实际交易")
    trade = db.get(Trade, plan.linked_trade_id)
    if not trade or trade.user_id != user.id:
        raise HTTPException(status_code=404, detail="关联的交易不存在")
    try:
        result = plan_comparison(plan, trade, db)
    except AnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e))
    import json as _json

    plan.comparison_result = _json.dumps(result, ensure_ascii=False)
    db.commit()
    return result
