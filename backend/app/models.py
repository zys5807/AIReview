"""数据库表结构定义"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    """用户表"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Integer, default=0)  # 管理员（第一个注册用户）
    is_active: Mapped[bool] = mapped_column(Integer, default=1)  # 账号是否可用
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Screenshot(Base):
    """K线截图文件"""

    __tablename__ = "screenshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)  # 归属用户
    filename: Mapped[str] = mapped_column(String(255), nullable=False)  # 原始文件名
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)  # 存储相对路径
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(100), default="")
    source_platform: Mapped[str] = mapped_column(String(50), default="")  # 来源平台：同花顺/富途/TradingView/币安...
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    trades: Mapped[list["Trade"]] = relationship(back_populates="screenshot")


class EntryStrategy(Base):
    """旧表：入场策略（保留兼容旧数据，新系统改用 TradeStrategy）"""

    __tablename__ = "entry_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trading_system_id: Mapped[int] = mapped_column(
        ForeignKey("trading_systems.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), default="")  # 策略名称
    rule: Mapped[str] = mapped_column(Text, default="")  # 入场规则描述
    is_active: Mapped[bool] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    trading_system: Mapped["TradingSystem"] = relationship(back_populates="entry_strategies")


class TradeStrategy(Base):
    """交易策略：一套完整的入场+止损+止盈。
    一个交易系统可有多个交易策略，多个策略是"或"关系（符合任一即可入场）。
    趋势判断/仓位/风险由交易系统级共用。
    """

    __tablename__ = "trade_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trading_system_id: Mapped[int] = mapped_column(
        ForeignKey("trading_systems.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), default="")  # 策略名称
    entry_rule: Mapped[str] = mapped_column(Text, default="")  # 入场策略
    stop_loss_rule: Mapped[str] = mapped_column(Text, default="")  # 初始止损策略
    take_profit_rule: Mapped[str] = mapped_column(Text, default="")  # 止盈策略
    is_active: Mapped[bool] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    trading_system: Mapped["TradingSystem"] = relationship(back_populates="trade_strategies")


class TradingSystem(Base):
    """交易系统配置（支持多周期 + 多个入场策略）"""

    __tablename__ = "trading_systems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)  # 归属用户
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # 系统名称
    description: Mapped[str] = mapped_column(Text, default="")  # 自由文本描述
    # 多周期职责
    trend_timeframe: Mapped[str] = mapped_column(String(20), default="")  # 趋势/多空判断周期，如 日线
    direction_timeframe: Mapped[str] = mapped_column(String(20), default="")  # 方向判断周期，如 1小时
    entry_timeframe: Mapped[str] = mapped_column(String(20), default="")  # 入场/离场周期，如 15分钟
    # 各周期规则
    trend_rule: Mapped[str] = mapped_column(Text, default="")  # 趋势判断规则（大周期多空）
    entry_rule: Mapped[str] = mapped_column(Text, default="")  # 入场条件
    exit_rule: Mapped[str] = mapped_column(Text, default="")  # 出场条件
    position_rule: Mapped[str] = mapped_column(Text, default="")  # 仓位管理
    risk_rule: Mapped[str] = mapped_column(Text, default="")  # 风险控制
    is_active: Mapped[bool] = mapped_column(Integer, default=1)  # 是否启用
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    trades: Mapped[list["Trade"]] = relationship(back_populates="trading_system")
    entry_strategies: Mapped[list["EntryStrategy"]] = relationship(
        back_populates="trading_system", cascade="all, delete-orphan"
    )
    trade_strategies: Mapped[list["TradeStrategy"]] = relationship(
        back_populates="trading_system", cascade="all, delete-orphan"
    )


class TradeScreenshot(Base):
    """交易-截图关联表（一笔交易可关联多张截图，各自带角色：背景1/背景2/次级别1/后续等）"""

    __tablename__ = "trade_screenshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"), nullable=False)
    screenshot_id: Mapped[int] = mapped_column(ForeignKey("screenshots.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="")  # 角色：背景1/背景2/次级别1/后续/其他
    sort_order: Mapped[int] = mapped_column(Integer, default=0)  # 排序

    trade: Mapped["Trade"] = relationship(back_populates="screenshot_links")
    screenshot: Mapped["Screenshot"] = relationship()


class Trade(Base):
    """交易记录"""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)  # 归属用户

    # 品种信息
    instrument_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # A股 / 商品期货 / 数字货币
    instrument_code: Mapped[str] = mapped_column(String(50), default="")  # 品种代码 600519 / RB2510 / BTCUSDT
    instrument_name: Mapped[str] = mapped_column(String(100), default="")  # 品种名称 贵州茅台 / 螺纹钢 / BTC
    exchange: Mapped[str] = mapped_column(String(50), default="")  # 交易所
    contract_type: Mapped[str] = mapped_column(String(50), default="")  # 合约类型：期货合约月份 / 数字货币永续/交割
    timeframe: Mapped[str] = mapped_column(String(20), default="")  # K线周期：1m/15m/1H/4H/日线...

    # 交易数据
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # long 做多 / short 做空
    entry_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 完全平仓时间（未平仓=空）
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 完全平仓价格（未平仓=空）
    volume: Mapped[float] = mapped_column(Float, default=1.0)  # 交易手数/数量
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)  # 初始止损位
    # 加仓（一次加仓；用于分析整个交易周期的动作）
    scale_in_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 加仓时间
    scale_in_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 加仓价格
    scale_in_volume: Mapped[float | None] = mapped_column(Float, nullable=True)  # 加仓数量/手数
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)  # 手续费（仅记录展示，不参与指标计算）
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # 盈亏金额（可空，允许系统计算）
    # 导入合并状态
    remaining_volume: Mapped[float] = mapped_column(Float, default=0.0)  # 当前未平仓数量（0=已平仓）
    import_cost: Mapped[float] = mapped_column(Float, default=0.0)  # 导入累计买入成本
    import_revenue: Mapped[float] = mapped_column(Float, default=0.0)  # 导入累计卖出收入
    notes: Mapped[str] = mapped_column(Text, default="")  # 备注
    psychology_notes: Mapped[str] = mapped_column(
        Text, default=""
    )  # 持仓过程中的心理状态（手工填写，供AI分析情绪/纪律维度）
    timeframe_notes: Mapped[str] = mapped_column(
        Text, default=""
    )  # 各周期判断依据（手填，如"日线K线在EMA20上方；1小时回踩EMA55不破"）
    # 本笔实际使用的多空/方向/交易周期（可偏离交易系统的默认周期，空=用系统默认）
    trend_timeframe_used: Mapped[str] = mapped_column(String(20), default="")
    direction_timeframe_used: Mapped[str] = mapped_column(String(20), default="")
    entry_timeframe_used: Mapped[str] = mapped_column(String(20), default="")

    # 外键
    screenshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("screenshots.id"), nullable=True
    )
    trading_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("trading_systems.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    screenshot: Mapped["Screenshot | None"] = relationship(back_populates="trades")
    trading_system: Mapped["TradingSystem | None"] = relationship(
        back_populates="trades"
    )
    screenshot_links: Mapped[list["TradeScreenshot"]] = relationship(
        back_populates="trade", cascade="all, delete-orphan"
    )
    review_reports: Mapped[list["ReviewReport"]] = relationship(
        back_populates="trade", cascade="all, delete-orphan"
    )
    position_actions: Mapped[list["TradePositionAction"]] = relationship(
        back_populates="trade", cascade="all, delete-orphan"
    )
    linked_plans: Mapped[list["TradePlan"]] = relationship(
        back_populates="linked_trade"
    )


class TradePositionAction(Base):
    """持仓操作（加仓/减仓）：数量为正=加仓，为负=减仓。一笔交易可有多次操作"""

    __tablename__ = "trade_position_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"), nullable=False)
    action_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # 操作时间
    price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 成交价格
    volume: Mapped[float] = mapped_column(Float, default=0)  # 数量（正=加仓，负=减仓）
    note: Mapped[str] = mapped_column(String(200), default="")  # 备注（可选）
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    trade: Mapped["Trade"] = relationship(back_populates="position_actions")


class ImportFill(Base):
    """已导入的交割单成交记录指纹（防止重复导入重复合并）"""

    __tablename__ = "import_fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)  # code|datetime|price|volume|direction
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ReviewReport(Base):
    """复盘报告（AI 分析结果）"""

    __tablename__ = "review_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)  # 归属用户
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"), nullable=False)

    raw_kline_data: Mapped[str] = mapped_column(Text, default="")  # K线识别出的行情数据 JSON
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 综合评分 0-100
    analysis: Mapped[str] = mapped_column(Text, default="")  # 分析内容 JSON（优点/不足/改进/各维度评分）
    model_name: Mapped[str] = mapped_column(String(100), default="")  # 使用的模型
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    trade: Mapped["Trade"] = relationship(back_populates="review_reports")


class TradePlan(Base):
    """交易计划：下单前制定，成交后对照执行（纪律管理）"""

    __tablename__ = "trade_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # 计划名称
    # 标的
    instrument_type: Mapped[str] = mapped_column(String(20), default="")  # A股/商品期货/数字货币
    instrument_code: Mapped[str] = mapped_column(String(50), default="")
    instrument_name: Mapped[str] = mapped_column(String(100), default="")
    direction: Mapped[str] = mapped_column(String(10), default="long")  # long/short
    # 关联
    trading_system_id: Mapped[int | None] = mapped_column(
        ForeignKey("trading_systems.id"), nullable=True
    )  # 计划基于的交易系统
    linked_trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("trades.id"), nullable=True
    )  # 已执行后关联的实际交易
    # 入场
    entry_method: Mapped[str] = mapped_column(String(20), default="")  # 突破/回踩/挂单/条件单
    planned_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 计划入场价
    entry_reason: Mapped[str] = mapped_column(Text, default="")  # 入场理由（周期信号）
    # 风险
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)  # 初始止损位
    max_loss_amount: Mapped[float | None] = mapped_column(Float, nullable=True)  # 最大亏损金额
    planned_volume: Mapped[float | None] = mapped_column(Float, nullable=True)  # 计划手数/数量
    # 目标
    target1: Mapped[float | None] = mapped_column(Float, nullable=True)  # 目标价1
    target2: Mapped[float | None] = mapped_column(Float, nullable=True)  # 目标价2
    risk_reward: Mapped[str] = mapped_column(String(20), default="")  # 预期盈亏比，如 "1:2"
    # 其他
    market_context: Mapped[str] = mapped_column(Text, default="")  # 市场背景/计划逻辑
    plan_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 计划日期（收盘后制定，精确到日）
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/executed/cancelled
    review_result: Mapped[str] = mapped_column(Text, default="")  # AI预评审结果 JSON
    comparison_result: Mapped[str] = mapped_column(Text, default="")  # 执行对照AI分析结果 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )

    trading_system: Mapped["TradingSystem | None"] = relationship()
    linked_trade: Mapped["Trade | None"] = relationship(back_populates="linked_plans")


class AppSetting(Base):
    """应用级设置（key-value）：API 配置等，软件内管理，避免手改 .env 出格式问题"""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
