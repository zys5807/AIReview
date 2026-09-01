"""API 请求/响应数据模型（Pydantic v2）"""
import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 品种类型
InstrumentType = Literal["A股", "商品期货", "数字货币"]
Direction = Literal["long", "short"]


# ---------- 通用 ----------
class MessageOut(BaseModel):
    message: str


# ---------- 用户 ----------
class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_admin: bool
    is_active: bool
    created_at: datetime


# ---------- 截图 ----------
class ScreenshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    stored_path: str
    file_size: int
    content_type: str
    source_platform: str
    created_at: datetime


# ---------- 入场策略 ----------
class EntryStrategyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rule: str = ""
    is_active: bool = True


class EntryStrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    rule: str
    is_active: bool
    sort_order: int


class TradeStrategyIn(BaseModel):
    """交易策略：入场+初始止损+止盈（一套完整策略）"""
    name: str = ""
    entry_rule: str = ""  # 入场策略
    stop_loss_rule: str = ""  # 初始止损策略
    take_profit_rule: str = ""  # 止盈策略
    is_active: bool = True


class TradeStrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    entry_rule: str
    stop_loss_rule: str
    take_profit_rule: str
    is_active: bool
    sort_order: int


# ---------- 交易系统 ----------
class TradingSystemBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    trend_timeframe: str = ""  # 趋势/多空判断周期
    direction_timeframe: str = ""  # 方向判断周期
    entry_timeframe: str = ""  # 入场/离场周期
    trend_rule: str = ""
    entry_rule: str = ""
    exit_rule: str = ""
    position_rule: str = ""
    risk_rule: str = ""
    is_active: bool = True
    entry_strategies: list[EntryStrategyIn] = []  # 旧字段：多个入场策略（兼容旧数据）
    trade_strategies: list[TradeStrategyIn] = []  # 交易策略列表（"或"关系，含入场/止损/止盈）


class TradingSystemCreate(TradingSystemBase):
    pass


class TradingSystemUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    trend_timeframe: str | None = None
    direction_timeframe: str | None = None
    entry_timeframe: str | None = None
    trend_rule: str | None = None
    entry_rule: str | None = None
    exit_rule: str | None = None
    position_rule: str | None = None
    risk_rule: str | None = None
    is_active: bool | None = None
    entry_strategies: list[EntryStrategyIn] | None = None  # 传空数组=清空全部
    trade_strategies: list[TradeStrategyIn] | None = None  # 交易策略列表


class TradingSystemOut(TradingSystemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    # 旧字段：入场策略（兼容旧数据）
    entry_strategies: list[EntryStrategyOut] = Field(
        default_factory=list, validation_alias="entry_strategies"
    )
    # 交易策略列表（"或"关系，含入场/止损/止盈）
    trade_strategies: list[TradeStrategyOut] = Field(
        default_factory=list, validation_alias="trade_strategies"
    )

    @field_validator("entry_strategies", mode="before")
    @classmethod
    def fill_strategies(cls, v):
        if not v:
            return []
        return [
            {
                "id": s.id,
                "name": s.name,
                "rule": s.rule or "",
                "is_active": bool(s.is_active),
                "sort_order": s.sort_order,
            }
            for s in sorted(v, key=lambda x: x.sort_order)
        ]

    @field_validator("trade_strategies", mode="before")
    @classmethod
    def fill_trade_strategies(cls, v):
        if not v:
            return []
        return [
            {
                "id": s.id,
                "name": s.name or "",
                "entry_rule": s.entry_rule or "",
                "stop_loss_rule": s.stop_loss_rule or "",
                "take_profit_rule": s.take_profit_rule or "",
                "is_active": bool(s.is_active),
                "sort_order": s.sort_order,
            }
            for s in sorted(v, key=lambda x: x.sort_order)
        ]


# ---------- 交易记录 ----------
class TradeBase(BaseModel):
    instrument_type: InstrumentType
    instrument_code: str = ""
    instrument_name: str = ""
    exchange: str = ""
    contract_type: str = ""
    timeframe: str = ""
    direction: Direction
    entry_time: datetime
    exit_time: datetime | None = None  # 完全平仓时间（未平仓=空）
    entry_price: float = Field(..., gt=0)
    exit_price: float | None = Field(None, gt=0)  # 完全平仓价格（未平仓=空）
    volume: float = Field(1.0, gt=0)
    stop_loss: float | None = None
    # 加仓（一次加仓）
    scale_in_time: datetime | None = None
    scale_in_price: float | None = None
    scale_in_volume: float | None = None
    fee: float | None = None  # 手续费（计入净盈亏：统计盈亏 = pnl - fee）
    remaining_volume: float = 0.0  # 当前未平仓数量（0=已平仓）
    pnl: float | None = None
    invested_capital: float | None = None  # 占用资金/本金（收益率分母）
    notes: str = ""
    psychology_notes: str = ""  # 持仓过程中的心理状态（手工填写）
    timeframe_notes: str = ""  # 各周期判断依据（手填）
    trend_timeframe_used: str = ""  # 本笔实际多空判断周期（空=用系统默认）
    direction_timeframe_used: str = ""  # 本笔实际方向周期
    entry_timeframe_used: str = ""  # 本笔实际交易周期
    screenshot_id: int | None = None
    trading_system_id: int | None = None
    screenshots: list["TradeScreenshotIn"] = []  # 多截图关联（带角色）
    position_actions: list["PositionActionIn"] = []  # 加仓/减仓操作（多次，volume正加负减）


class TradeScreenshotIn(BaseModel):
    screenshot_id: int
    role: str = ""  # 角色：背景1/背景2/次级别1/后续/其他


class PositionActionIn(BaseModel):
    """持仓操作：加仓/减仓（volume 正=加仓，负=减仓）"""
    action_time: datetime
    price: float | None = None
    volume: float = 0
    note: str = ""


class PositionActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action_time: datetime
    price: float | None
    volume: float
    note: str
    sort_order: int


class TradeScreenshotOut(BaseModel):
    id: int
    screenshot_id: int
    role: str
    filename: str = ""
    stored_path: str = ""


class TradeCreate(TradeBase):
    pass


class CapitalCalcIn(BaseModel):
    """占用资金自动计算请求"""
    instrument_type: str = ""
    instrument_code: str = ""
    instrument_name: str = ""
    entry_price: float | None = None
    volume: float = 1.0


class CapitalCalcOut(BaseModel):
    invested_capital: float | None = None
    matched_name: str | None = None  # 匹配到的标准品种名；None=未识别（按名义本金估算）
    multiplier: float = 1.0  # 命中的合约乘数


class TradeUpdate(BaseModel):
    instrument_type: InstrumentType | None = None
    instrument_code: str | None = None
    instrument_name: str | None = None
    exchange: str | None = None
    contract_type: str | None = None
    timeframe: str | None = None
    direction: Direction | None = None
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    entry_price: float | None = Field(None, gt=0)
    exit_price: float | None = Field(None, gt=0)
    volume: float | None = Field(None, gt=0)
    stop_loss: float | None = None
    scale_in_time: datetime | None = None
    scale_in_price: float | None = None
    scale_in_volume: float | None = None
    fee: float | None = None
    remaining_volume: float | None = None
    pnl: float | None = None
    invested_capital: float | None = None
    notes: str | None = None
    psychology_notes: str | None = None
    timeframe_notes: str | None = None
    trend_timeframe_used: str | None = None
    direction_timeframe_used: str | None = None
    entry_timeframe_used: str | None = None
    screenshot_id: int | None = None
    trading_system_id: int | None = None
    screenshots: list[TradeScreenshotIn] | None = None  # 多截图关联（传空数组=清空全部）
    position_actions: list[PositionActionIn] | None = None  # 持仓操作（传空数组=清空全部）


class TradeOut(TradeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    # from_attributes 时从 trade.screenshot_links 读取，序列化输出字段名仍是 screenshots
    screenshots: list[TradeScreenshotOut] = Field(
        default_factory=list, validation_alias="screenshot_links"
    )
    position_actions: list[PositionActionOut] = Field(
        default_factory=list, validation_alias="position_actions"
    )
    linked_plan: dict | None = Field(default=None, validation_alias="linked_plans")

    @field_validator("linked_plan", mode="before")
    @classmethod
    def fill_linked_plan(cls, v):
        """从 trade.linked_plans 取第一个计划作为展示信息（双向关联）"""
        if not v:
            return None
        plans = v if isinstance(v, list) else [v]
        if not plans:
            return None
        p = plans[0]
        return {"id": p.id, "name": p.name, "status": p.status}

    @field_validator("screenshots", mode="before")
    @classmethod
    def fill_screenshots(cls, v):
        """把 trade.screenshot_links 组装成带文件名/路径的结构"""
        if not v:
            return []
        result = []
        for link in v:
            shot = link.screenshot if hasattr(link, "screenshot") else None
            result.append(
                {
                    "id": link.id,
                    "screenshot_id": link.screenshot_id,
                    "role": link.role or "",
                    "filename": getattr(shot, "filename", "") or "",
                    "stored_path": getattr(shot, "stored_path", "") or "",
                }
            )
        return result


class TradeListOut(BaseModel):
    total: int
    items: list[TradeOut]


# ---------- 复盘报告 ----------
class ReviewReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trade_id: int
    score: float | None = None
    analysis: dict = {}
    model_name: str = ""
    created_at: datetime

    @field_validator("analysis", mode="before")
    @classmethod
    def parse_analysis(cls, v):
        """将数据库中的 JSON 字符串解析为 dict"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return v


# ---------- 交易计划 ----------
class TradePlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    instrument_type: str = ""
    instrument_code: str = ""
    instrument_name: str = ""
    direction: str = "long"
    trading_system_id: int | None = None
    entry_method: str = ""
    planned_entry_price: float | None = None
    entry_reason: str = ""
    stop_loss: float | None = None
    max_loss_amount: float | None = None
    planned_volume: float | None = None
    target1: float | None = None
    target2: float | None = None
    risk_reward: str = ""
    market_context: str = ""
    plan_date: date | None = None  # 计划日期（收盘后制定，精确到日）
    status: str = "pending"  # pending/executed/cancelled
    linked_trade_id: int | None = None


class TradePlanUpdate(BaseModel):
    name: str | None = None
    instrument_type: str | None = None
    instrument_code: str | None = None
    instrument_name: str | None = None
    direction: str | None = None
    trading_system_id: int | None = None
    entry_method: str | None = None
    planned_entry_price: float | None = None
    entry_reason: str | None = None
    stop_loss: float | None = None
    max_loss_amount: float | None = None
    planned_volume: float | None = None
    target1: float | None = None
    target2: float | None = None
    risk_reward: str | None = None
    market_context: str | None = None
    plan_date: date | None = None
    status: str | None = None
    linked_trade_id: int | None = None


class TradePlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    instrument_type: str
    instrument_code: str
    instrument_name: str
    direction: str
    trading_system_id: int | None = None
    trading_system_name: str = Field(default="", validation_alias="trading_system")
    entry_method: str
    planned_entry_price: float | None = None
    entry_reason: str
    stop_loss: float | None = None
    max_loss_amount: float | None = None
    planned_volume: float | None = None
    planned_invested: float | None = None  # 计划占用资金（自动计算）
    position_ratio: float | None = None  # 单笔仓位比例 %（快照，创建时按当时账户资金）
    target1: float | None = None
    target2: float | None = None
    risk_reward: str
    market_context: str
    plan_date: date | None = None
    status: str
    linked_trade_id: int | None = None
    linked_trade_name: str = Field(default="", validation_alias="linked_trade")
    review_result: dict = {}
    comparison_result: dict = {}
    created_at: datetime
    updated_at: datetime

    @field_validator("review_result", "comparison_result", mode="before")
    @classmethod
    def parse_json_fields(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {}
        return v

    @field_validator("trading_system_name", "linked_trade_name", mode="before")
    @classmethod
    def fill_relation_names(cls, v):
        """从 relationship 对象提取可读名称"""
        if not v:
            return ""
        if hasattr(v, "name") and isinstance(v.name, str):
            return v.name
        if hasattr(v, "instrument_name"):
            return f"{v.instrument_name or v.instrument_code} {('做多' if v.direction == 'long' else '做空')}"
        return str(v)


class TradePlanExecuteIn(BaseModel):
    """标记执行并关联实际交易"""
    linked_trade_id: int | None = None


# ---------- 账户资金流水 ----------
# 币种：CNY 人民币 / USD 美元（USDT 1:1 并入 USD）
Currency = Literal["CNY", "USD"]


class AccountFlowCreate(BaseModel):
    flow_date: date  # 资金变动日期（可任意指定，补录历史）
    flow_type: Literal["initial", "deposit", "withdraw"] = "initial"
    currency: Currency = "CNY"  # 币种（默认人民币）
    amount: float = Field(..., gt=0, description="变动金额（正数）")
    note: str = ""


class AccountFlowUpdate(BaseModel):
    flow_date: date | None = None
    flow_type: Literal["initial", "deposit", "withdraw"] | None = None
    currency: Currency | None = None
    amount: float | None = Field(None, gt=0)
    note: str | None = None


class AccountFlowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    flow_date: date
    flow_type: str
    currency: str = "CNY"
    amount: float
    balance_after: float | None = None
    note: str = ""
    created_at: datetime
