"""账户资金接口：初始资金/入金/出金流水管理

- 流水按 (currency, flow_date, id) 排序，balance_after 系统自动重算，不允许手填
- 支持多币种（CNY 人民币 / USD 美元，USDT 1:1 并入 USD）
- V1.007.1 分品种资金管理：流水可指定品种类型（""=全部/通用、A股/商品期货/数字货币）
- 支持任意日期补录历史（初始资金是哪天由用户指定）
- 新增/修改/删除流水后自动重算该用户全部余额
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AccountFlow, User
from ..routers.auth import get_current_user
from ..schemas import AccountFlowCreate, AccountFlowOut, AccountFlowUpdate, MessageOut
from ..services.account import (
    CNY,
    USD,
    INSTRUMENT_TYPES,
    balance_at,
    balance_for_instrument,
    current_balance,
    recalc_balances,
)

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
        instrument_type=data.instrument_type or "",
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
    """当前账户资金（按币种 + 按品种类型）+ 流水统计（前端卡片用）

    返回：
    - balances / initial_amounts：按币种 dict（{"CNY": x, "USD": y}），
      balances[cur] = 该币种全部资金（通用 + 各品种类型合计，兼容旧语义）
    - balances_by_type：{品种类型: {币种: 余额}}，如
      {"A股": {"CNY": 500000}, "商品期货": {"CNY": 300000}, "数字货币": {"USD": 5000}}
      供交易计划按品种类型资金计算仓位比例（V1.007.1）
    - initial_amounts_by_type：各品种类型初始资金
    - current_balance：CNY 余额（无 CNY 时取唯一币种）
    """
    # 各品种类型 × 各币种余额（精确子账户；""=全部/通用）
    balances_by_type: dict[str, dict[str, float]] = {}
    for itype in ("", *INSTRUMENT_TYPES):
        for cur in (CNY, USD):
            bal = balance_at(db, user.id, None, cur, itype)
            if bal is not None:
                balances_by_type.setdefault(itype, {})[cur] = bal
    # 各币种总额（通用 + 各品种类型合计）
    balances = {
        cur: balance_for_instrument(db, user.id, None, cur, None) for cur in (CNY, USD)
    }
    balances = {k: v for k, v in balances.items() if v is not None}
    flows = (
        db.query(AccountFlow)
        .filter(AccountFlow.user_id == user.id)
        .order_by(AccountFlow.flow_date, AccountFlow.id)
        .all()
    )
    initial_amounts: dict[str, float] = {}
    initial_amounts_by_type: dict[str, dict[str, float]] = {}
    for f in flows:
        if f.flow_type == "initial":
            cur = f.currency or CNY
            itype = f.instrument_type or ""
            if cur not in initial_amounts:
                initial_amounts[cur] = f.amount
            if itype not in initial_amounts_by_type:
                initial_amounts_by_type[itype] = {cur: f.amount}
            elif cur not in initial_amounts_by_type[itype]:
                initial_amounts_by_type[itype][cur] = f.amount
    current = balances.get(CNY) or next(iter(balances.values()), None)
    return {
        "current_balance": current,
        "balances": balances,
        "balances_by_type": balances_by_type,
        "initial_amount": initial_amounts.get(CNY),
        "initial_amounts": initial_amounts,
        "initial_amounts_by_type": initial_amounts_by_type,
        "flow_count": len(flows),
        "first_date": flows[0].flow_date.isoformat() if flows else None,
        "last_date": flows[-1].flow_date.isoformat() if flows else None,
    }
