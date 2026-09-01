"""交易记录接口（多用户：所有操作按当前用户隔离）"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Screenshot, Trade, TradePositionAction, TradeScreenshot, User
from ..routers.auth import get_current_user
from ..schemas import (
    CapitalCalcIn,
    CapitalCalcOut,
    MessageOut,
    TradeCreate,
    TradeListOut,
    TradeOut,
    TradeUpdate,
)
from ..services.investment import compute_invested_capital, resolve_multiplier

# 品种分类
INSTRUMENT_TYPES = ["A股", "商品期货", "数字货币"]

router = APIRouter(prefix="/api/trades", tags=["交易记录"])


def _validate_trade(data: TradeCreate | TradeUpdate):
    """校验交易数据合法性"""
    entry = data.entry_time
    exit_ = data.exit_time
    if entry and exit_ and exit_ <= entry:
        raise HTTPException(status_code=422, detail="出场时间必须晚于入场时间")


def _sync_screenshots(db: Session, trade: Trade, screenshots: list, user_id: int):
    """重建交易-截图关联（先清空旧的，再按传入列表重建），并同步主截图字段

    screenshots 是 TradeScreenshotIn 的 dict 列表（model_dump 后）或对象列表
    """

    def _sid(s):
        return s["screenshot_id"] if isinstance(s, dict) else s.screenshot_id

    def _role(s):
        return s.get("role", "") if isinstance(s, dict) else (s.role or "")

    trade.screenshot_links.clear()
    for i, s in enumerate(screenshots):
        shot = db.get(Screenshot, _sid(s))
        if not shot or shot.user_id != user_id:
            raise HTTPException(status_code=404, detail=f"截图 {_sid(s)} 不存在或不属于当前用户")
        trade.screenshot_links.append(
            TradeScreenshot(screenshot_id=_sid(s), role=_role(s), sort_order=i)
        )
    # 主截图 = 第一张（兼容旧的 screenshot_id 字段 / 前端"截图"链接）
    if screenshots:
        trade.screenshot_id = _sid(screenshots[0])


def _sync_position_actions(db: Session, trade: Trade, actions: list):
    """重建交易的加仓/减仓操作列表（按传入顺序）"""
    def _get(s, key, default=None):
        return s.get(key, default) if isinstance(s, dict) else getattr(s, key, default)

    trade.position_actions.clear()
    for i, a in enumerate(actions):
        trade.position_actions.append(
            TradePositionAction(
                action_time=_get(a, "action_time"),
                price=_get(a, "price"),
                volume=_get(a, "volume") or 0,
                note=_get(a, "note") or "",
                sort_order=i,
            )
        )


@router.post("", response_model=TradeOut, status_code=201)
def create_trade(
    data: TradeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新建交易记录"""
    _validate_trade(data)
    payload = data.model_dump()
    screenshots = payload.pop("screenshots", [])
    position_actions = payload.pop("position_actions", [])
    payload["user_id"] = user.id
    # 占用资金未提供时自动计算（按品种类型/代码/价格/数量）
    if payload.get("invested_capital") is None:
        payload["invested_capital"] = compute_invested_capital(
            payload.get("instrument_type", ""),
            payload.get("instrument_code", ""),
            payload.get("instrument_name", ""),
            payload.get("entry_price"),
            payload.get("volume") or 1.0,
        )
    trade = Trade(**payload)
    db.add(trade)
    db.flush()  # 先拿到 trade.id
    _sync_screenshots(db, trade, screenshots, user.id)
    _sync_position_actions(db, trade, position_actions)
    db.commit()
    db.refresh(trade)
    return trade


@router.get("", response_model=TradeListOut)
def list_trades(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    instrument_type: str | None = Query(None, description="按品种类型筛选"),
    start: datetime | None = Query(None, description="开始时间"),
    end: datetime | None = Query(None, description="结束时间"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """交易记录列表（支持按品种/时间段筛选，分页）"""
    query = db.query(Trade).filter(Trade.user_id == user.id)
    if instrument_type:
        query = query.filter(Trade.instrument_type == instrument_type)
    if start:
        query = query.filter(Trade.entry_time >= start)
    if end:
        query = query.filter(Trade.entry_time <= end)

    total = query.count()
    items = (
        query.order_by(Trade.entry_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"total": total, "items": items}


@router.post("/calc-capital", response_model=CapitalCalcOut)
def calc_capital(
    data: CapitalCalcIn,
    user: User = Depends(get_current_user),
):
    """按品种自动计算占用资金（前端录入实时预填用，单一数据源在后端）"""
    invested = compute_invested_capital(
        data.instrument_type,
        data.instrument_code,
        data.instrument_name,
        data.entry_price,
        data.volume,
    )
    mult, matched = resolve_multiplier(
        data.instrument_code, data.instrument_name
    ) if data.instrument_type == "商品期货" else (1.0, None)
    return {"invested_capital": invested, "matched_name": matched, "multiplier": mult}


@router.get("/stats", response_model=dict)
def trade_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按品种类型统计：笔数、胜率、总盈亏（净盈亏 = pnl - fee）"""
    net_expr = Trade.pnl - func.coalesce(Trade.fee, 0.0)
    rows = (
        db.query(
            Trade.instrument_type,
            func.count(Trade.id).label("count"),
            func.sum(net_expr).label("total_pnl"),
            func.sum(case((net_expr > 0, 1), else_=0)).label("win"),
        )
        .filter(Trade.user_id == user.id)
        .group_by(Trade.instrument_type)
        .all()
    )

    result = {}
    for r in rows:
        total_pnl = r.total_pnl or 0
        count = r.count
        win = r.win or 0
        result[r.instrument_type] = {
            "count": count,
            "win": win,
            "loss": count - win,
            "win_rate": round(win / count, 4) if count else 0,
            "total_pnl": total_pnl,
        }

    # 保证三种分类都存在，方便前端展示
    for t in INSTRUMENT_TYPES:
        if t not in result:
            result[t] = {"count": 0, "win": 0, "loss": 0, "win_rate": 0, "total_pnl": 0}
    return result


def _get_owned_trade(db: Session, trade_id: int, user_id: int) -> Trade:
    """获取当前用户的交易，否则 404"""
    trade = db.get(Trade, trade_id)
    if not trade or trade.user_id != user_id:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    return trade


@router.get("/{trade_id}", response_model=TradeOut)
def get_trade(
    trade_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_owned_trade(db, trade_id, user.id)


@router.put("/{trade_id}", response_model=TradeOut)
def update_trade(
    trade_id: int,
    data: TradeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新交易记录"""
    trade = _get_owned_trade(db, trade_id, user.id)
    _validate_trade(data)
    payload = data.model_dump(exclude_unset=True)
    screenshots = payload.pop("screenshots", None)
    position_actions = payload.pop("position_actions", None)
    # 占用资金未显式修改时，按更新后的品种/价格/数量重新自动计算
    if "invested_capital" not in payload:
        merged = {**payload, "instrument_type": payload.get("instrument_type", trade.instrument_type),
                  "instrument_code": payload.get("instrument_code", trade.instrument_code),
                  "instrument_name": payload.get("instrument_name", trade.instrument_name)}
        payload["invested_capital"] = compute_invested_capital(
            merged["instrument_type"], merged["instrument_code"], merged["instrument_name"],
            merged.get("entry_price", trade.entry_price), merged.get("volume", trade.volume) or 1.0,
        )
    for field, value in payload.items():
        setattr(trade, field, value)
    if screenshots is not None:
        _sync_screenshots(db, trade, screenshots, user.id)
    if position_actions is not None:
        _sync_position_actions(db, trade, position_actions)
    db.commit()
    db.refresh(trade)
    return trade


@router.delete("/{trade_id}", response_model=MessageOut)
def delete_trade(
    trade_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    trade = _get_owned_trade(db, trade_id, user.id)

    # 记录本次交易关联的截图，删除交易后再清理不再被引用的截图
    linked_shot_ids = [link.screenshot_id for link in trade.screenshot_links]

    db.delete(trade)  # 级联删除 review_reports / trade_screenshots
    db.flush()

    # 同步清理：仅当截图不再被任何交易引用时，删除记录 + 磁盘文件
    cleaned = 0
    for sid in linked_shot_ids:
        if db.query(TradeScreenshot).filter_by(screenshot_id=sid).first():
            continue  # 仍被其他交易引用，保留
        shot = db.get(Screenshot, sid)
        if shot:
            from .screenshots import delete_shot_file

            delete_shot_file(shot)
            db.delete(shot)
            cleaned += 1

    db.commit()
    if cleaned:
        return {"message": f"删除成功，并清理了 {cleaned} 张已无引用的截图"}
    return {"message": "删除成功"}
