"""账户资金接口：初始资金/入金/出金流水管理

- 流水按 (currency, flow_date, id) 排序，balance_after 系统自动重算，不允许手填
- 支持多币种（CNY 人民币 / USD 美元，USDT 1:1 并入 USD）
- 支持任意日期补录历史（初始资金是哪天由用户指定）
- 新增/修改/删除流水后自动重算该用户全部余额
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AccountFlow, User
from ..routers.auth import get_current_user
from ..schemas import AccountFlowCreate, AccountFlowOut, AccountFlowUpdate, MessageOut
from ..services.account import CNY, USD, balance_at, current_balance, recalc_balances

router = APIRouter(prefix="/api/accounts", tags=["账户资金"])


@router.post("/flows", response_model=AccountFlowOut, status_code=201)
def create_flow(
    data: AccountFlowCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    flow = AccountFlow(
        user_id=user.id,
        flow_date=data.flow_date,
        flow_type=data.flow_type,
        currency=data.currency,
        amount=data.amount,
        note=data.note,
    )
    db.add(flow)
    db.commit()
    recalc_balances(db, user.id)
    db.refresh(flow)
    return flow


@router.get("/flows", response_model=list[AccountFlowOut])
def list_flows(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return (
        db.query(AccountFlow)
        .filter(AccountFlow.user_id == user.id)
        .order_by(AccountFlow.flow_date, AccountFlow.id)
        .all()
    )


@router.put("/flows/{flow_id}", response_model=AccountFlowOut)
def update_flow(
    flow_id: int,
    data: AccountFlowUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    flow = db.get(AccountFlow, flow_id)
    if not flow or flow.user_id != user.id:
        raise HTTPException(status_code=404, detail="资金流水不存在")
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(flow, field, value)
    db.commit()
    recalc_balances(db, user.id)
    db.refresh(flow)
    return flow


@router.delete("/flows/{flow_id}", response_model=MessageOut)
def delete_flow(
    flow_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    flow = db.get(AccountFlow, flow_id)
    if not flow or flow.user_id != user.id:
        raise HTTPException(status_code=404, detail="资金流水不存在")
    db.delete(flow)
    db.commit()
    recalc_balances(db, user.id)
    return {"message": "删除成功"}


@router.get("/summary", response_model=dict)
def summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前账户总资金（按币种）+ 流水统计（前端卡片用）

    返回 balances/initial_amounts 两个按币种 dict（如 {"CNY": x, "USD": y}），
    以及兼容字段 current_balance（CNY 余额，无 CNY 时取唯一币种）。
    """
    balances = {
        cur: balance_at(db, user.id, None, cur) for cur in (CNY, USD)
    }
    balances = {k: v for k, v in balances.items() if v is not None}
    flows = (
        db.query(AccountFlow)
        .filter(AccountFlow.user_id == user.id)
        .order_by(AccountFlow.flow_date, AccountFlow.id)
        .all()
    )
    initial_amounts: dict[str, float] = {}
    for f in flows:
        if f.flow_type == "initial" and f.currency not in initial_amounts:
            initial_amounts[f.currency or CNY] = f.amount
    current = balances.get(CNY) or next(iter(balances.values()), None)
    return {
        "current_balance": current,
        "balances": balances,
        "initial_amount": initial_amounts.get(CNY),
        "initial_amounts": initial_amounts,
        "flow_count": len(flows),
        "first_date": flows[0].flow_date.isoformat() if flows else None,
        "last_date": flows[-1].flow_date.isoformat() if flows else None,
    }
