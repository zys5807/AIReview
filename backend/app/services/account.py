"""账户资金服务：初始资金/入金/出金流水 + 余额自动重算 + 仓位比例计算

核心设计：
- account_flows 表存流水，balance_after 不允许手填，按 (flow_date, id) 排序自动重算
- 支持多币种：CNY(人民币) / USD(美元，USDT 1:1 并入)，各币种独立累计余额
- "当前总资金" = 最后一笔流水的余额；"某日总资金" = 截至该日最后一笔流水的余额
- 初始资金是哪天由用户指定（第一条 initial 流水的日期），中途可随时追加修正记录
- 交易计划仓位比例 = 计划占用资金 ÷ 截至计划日期的对应币种账户资金（创建时快照）
- 交易盈亏币种归类：A股/商品期货 → CNY；数字货币 → USD（USDT 并入 USD）
"""
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import AccountFlow, Trade
from .investment import compute_invested_capital

# 币种常量
CNY = "CNY"
USD = "USD"
CURRENCIES = (CNY, USD)

# 交易盈亏币种归类：品种类型 → 币种
TRADE_CURRENCY_MAP = {
    "A股": CNY,
    "商品期货": CNY,
    "数字货币": USD,
}


def _trade_currency(t: Trade) -> str:
    """一笔交易的盈亏归属币种（A股/期货→CNY，数字货币→USD）"""
    return TRADE_CURRENCY_MAP.get(t.instrument_type or "", CNY)


def recalc_balances(db: Session, user_id: int | None) -> None:
    """按 (currency, flow_date, id) 顺序重算该用户所有流水的 balance_after（各币种独立）"""
    flows = (
        db.query(AccountFlow)
        .filter(AccountFlow.user_id == user_id)
        .order_by(AccountFlow.flow_date, AccountFlow.id)
        .all()
    )
    balance: dict[str, float] = {}
    for f in flows:
        cur = f.currency or CNY
        if f.flow_type == "withdraw":
            balance[cur] = balance.get(cur, 0.0) - f.amount
        else:  # initial / deposit
            balance[cur] = balance.get(cur, 0.0) + f.amount
        f.balance_after = round(balance[cur], 2)
    db.commit()


def list_currencies(db: Session, user_id: int | None) -> list[str]:
    """该用户出现过的所有币种（按流水出现顺序去重）"""
    return [
        r[0]
        for r in (
            db.query(AccountFlow.currency)
            .filter(AccountFlow.user_id == user_id)
            .distinct()
            .all()
        )
    ]


def balance_at(
    db: Session,
    user_id: int | None,
    d: date | None = None,
    currency: str = CNY,
) -> float | None:
    """截至某日（含当日）指定币种的账户总资金；d=None 表示最新余额

    无任何流水返回 None；返回 None 时前端应提示先设置初始资金
    """
    q = db.query(AccountFlow).filter(
        AccountFlow.user_id == user_id, AccountFlow.currency == currency
    )
    if d is not None:
        q = q.filter(AccountFlow.flow_date <= d)
    flow = q.order_by(AccountFlow.flow_date.desc(), AccountFlow.id.desc()).first()
    return flow.balance_after if flow else None


def current_balance(
    db: Session, user_id: int | None, currency: str = CNY
) -> float | None:
    """指定币种当前账户总资金（最后一笔流水的余额）"""
    return balance_at(db, user_id, None, currency)


def equity_before(
    db: Session,
    user_id: int | None,
    d: date | None = None,
    currency: str = CNY,
) -> float | None:
    """截至某日交易开始前、指定币种的账户权益（含此前全部已平仓交易盈亏）

    语义：阶段期初资金 = 初始资金 + 出入金 + 该日之前所有已平仓交易的盈亏累计，
    即"到这一天的交易发生之前，账户上有多少钱"。

    - 余额部分：balance_at 口径（截至 d 日含当日的出入金流水余额）
    - 盈亏部分：仅统计 exit_time < d（当日之前已平仓）且归属该币种的交易，
      不含当日交易盈亏。盈亏按净额（pnl - fee，手续费计入）计算。
      币种归类：A股/商品期货→CNY，数字货币→USD
    - d=None 表示当前时点（含至今全部交易盈亏）
    - 无任何流水记录返回 None
    """
    bal = balance_at(db, user_id, d, currency)
    if bal is None:
        return None
    net_expr = Trade.pnl - func.coalesce(Trade.fee, 0.0)
    q = (
        db.query(func.coalesce(func.sum(net_expr), 0.0))
        .filter(Trade.user_id == user_id, Trade.pnl.isnot(None))
    )
    if currency == USD:
        q = q.filter(Trade.instrument_type == "数字货币")
    else:
        q = q.filter(Trade.instrument_type.in_(["A股", "商品期货"]))
    if d is not None:
        q = q.filter(Trade.exit_time < datetime.combine(d, time.min))
    return round(bal + float(q.scalar() or 0.0), 2)


def compute_plan_position(
    db: Session,
    user_id: int | None,
    instrument_type: str = "",
    instrument_code: str = "",
    instrument_name: str = "",
    planned_entry_price: float | None = None,
    planned_volume: float | None = None,
    plan_date: date | None = None,
) -> tuple[float | None, float | None]:
    """计算交易计划的仓位快照，返回 (planned_invested, position_ratio%)

    - planned_invested：计划占用资金（复用 investment 的品种计算）
    - position_ratio：占用资金 ÷ 截至计划日期的对应币种账户资金 × 100
      （数字货币→USD，A股/商品期货→CNY）
    - 账户无对应币种资金记录时 position_ratio 返回 None（前端提示先设置初始资金）
    """
    invested = compute_invested_capital(
        instrument_type, instrument_code, instrument_name,
        planned_entry_price, planned_volume or 1.0,
    )
    if invested is None:
        return None, None
    cur = TRADE_CURRENCY_MAP.get(instrument_type or "", CNY)
    balance = balance_at(db, user_id, plan_date, cur)
    if not balance:
        return invested, None
    return round(invested, 2), round(invested / balance * 100, 2)
