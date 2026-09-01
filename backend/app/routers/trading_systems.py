"""交易系统配置接口（多用户：按当前用户隔离）"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import EntryStrategy, TradingSystem, TradeStrategy, User
from ..routers.auth import get_current_user
from ..schemas import (
    MessageOut,
    TradingSystemCreate,
    TradingSystemOut,
    TradingSystemUpdate,
)

router = APIRouter(prefix="/api/trading-systems", tags=["交易系统"])


def _get_list_field(st, key, default=None):
    """兼容 dict 与对象"""
    return st.get(key, default) if isinstance(st, dict) else getattr(st, key, default)


def _sync_entry_strategies(db: Session, system: TradingSystem, strategies: list):
    """重建系统的旧入场策略（保留兼容，不再在前端使用）"""
    system.entry_strategies.clear()
    for i, st in enumerate(strategies):
        system.entry_strategies.append(
            EntryStrategy(
                name=_get_list_field(st, "name"),
                rule=_get_list_field(st, "rule") or "",
                is_active=_get_list_field(st, "is_active", True),
                sort_order=i,
            )
        )


def _sync_trade_strategies(db: Session, system: TradingSystem, strategies: list):
    """重建系统的交易策略列表（入场+止损+止盈，多策略为"或"关系）"""
    system.trade_strategies.clear()
    for i, st in enumerate(strategies):
        system.trade_strategies.append(
            TradeStrategy(
                name=_get_list_field(st, "name") or "",
                entry_rule=_get_list_field(st, "entry_rule") or "",
                stop_loss_rule=_get_list_field(st, "stop_loss_rule") or "",
                take_profit_rule=_get_list_field(st, "take_profit_rule") or "",
                is_active=_get_list_field(st, "is_active", True),
                sort_order=i,
            )
        )


def _get_owned_system(db: Session, system_id: int, user_id: int) -> TradingSystem:
    system = db.get(TradingSystem, system_id)
    if not system or system.user_id != user_id:
        raise HTTPException(status_code=404, detail="交易系统不存在")
    return system


@router.post("", response_model=TradingSystemOut, status_code=201)
def create_system(
    data: TradingSystemCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新建交易系统"""
    payload = data.model_dump()
    trade_strategies = payload.pop("trade_strategies", [])
    entry_strategies = payload.pop("entry_strategies", [])  # 旧字段兼容
    payload["user_id"] = user.id
    system = TradingSystem(**payload)
    db.add(system)
    db.flush()  # 先拿到 system.id
    if trade_strategies:
        _sync_trade_strategies(db, system, trade_strategies)
    if entry_strategies:
        _sync_entry_strategies(db, system, entry_strategies)
    db.commit()
    db.refresh(system)
    return system


@router.get("", response_model=list[TradingSystemOut])
def list_systems(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """交易系统列表（当前用户）"""
    return (
        db.query(TradingSystem)
        .filter(TradingSystem.user_id == user.id)
        .order_by(TradingSystem.id.desc())
        .all()
    )


@router.get("/{system_id}", response_model=TradingSystemOut)
def get_system(
    system_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_owned_system(db, system_id, user.id)


@router.put("/{system_id}", response_model=TradingSystemOut)
def update_system(
    system_id: int,
    data: TradingSystemUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新交易系统"""
    system = _get_owned_system(db, system_id, user.id)
    payload = data.model_dump(exclude_unset=True)
    trade_strategies = payload.pop("trade_strategies", None)
    entry_strategies = payload.pop("entry_strategies", None)
    for field, value in payload.items():
        setattr(system, field, value)
    if trade_strategies is not None:
        _sync_trade_strategies(db, system, trade_strategies)
    if entry_strategies is not None:
        _sync_entry_strategies(db, system, entry_strategies)
    db.commit()
    db.refresh(system)
    return system


@router.delete("/{system_id}", response_model=MessageOut)
def delete_system(
    system_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    system = _get_owned_system(db, system_id, user.id)
    db.delete(system)
    db.commit()
    return {"message": "删除成功"}
