"""数据库表结构定义"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)  # 手续费（统计口径：净盈亏 = pnl - fee）
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # 盈亏金额（毛盈亏不含手续费，可空）
    invested_capital: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # 占用资金/本金（收益率分母，自动按品种计算可手动修改）
    # V1.007 品种参数快照（审计追溯：这笔交易当时用的什么参数）
    matched_variety: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 匹配到的标准品种名（如 沪铝）
    multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)  # 合约乘数（如 5）
    margin_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # 保证金率（0.17 = 17%）
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
    # V1.006 仓位比例（创建时按当时账户资金计算，存快照）
    planned_invested: Mapped[float | None] = mapped_column(Float, nullable=True)  # 计划占用资金
    position_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)  # 单笔仓位比例 %（快照）
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


class AccountFlow(Base):
    """账户资金流水：初始资金(initial)/入金(deposit)/出金(withdraw)

    balance_after 由系统按 (flow_date, id) 顺序自动重算，不允许手填。
    支持任意日期补录历史：初始资金是哪天由用户指定，中途可随时追加修正记录。
    currency: 币种 CNY(人民币)/USD(美元，USDT 1:1 并入)；各币种独立累计余额。
    instrument_type: V1.007.1 品种类型维度 ""=全部/通用、A股/商品期货/数字货币；
    资金按 (currency, instrument_type) 组合独立累计，实现分品种资金管理
    （如 A股 50万CNY + 商品期货 30万CNY + 数字货币 5000USD）。
    """

    __tablename__ = "account_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    flow_date: Mapped[date] = mapped_column(Date, nullable=False)  # 资金变动日期
    flow_type: Mapped[str] = mapped_column(String(20), default="initial")  # initial/deposit/withdraw
    currency: Mapped[str] = mapped_column(String(10), default="CNY")  # CNY/USD（USDT 并入 USD）
    instrument_type: Mapped[str] = mapped_column(String(20), default="")  # V1.007.1 ""=全部/A股/商品期货/数字货币
    amount: Mapped[float] = mapped_column(Float, nullable=False)  # 变动金额（正数）
    balance_after: Mapped[float | None] = mapped_column(Float, nullable=True)  # 该笔后账户总资金（自动重算）
    note: Mapped[str] = mapped_column(String(200), default="")  # 备注
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PhaseReview(Base):
    """阶段复盘手写总结（V1.008.1）

    period_type: week=周复盘 / month=月复盘 / quarter=季度复盘 / year=年度复盘 / custom=AI结果占位
    start_date/end_date: 归一后的阶段起止日（周=周一~周日；月=1号~月末最后一日，均包含结束日）
    instrument_type: 绑定维度（决策2：只选品种，不考虑币种）''=全部/通用、A股/商品期货/数字货币
    唯一约束 (user_id, period_type, start_date, end_date, instrument_type)：同键一条，重复提交覆盖
    ai_result: 最近一次 AI 阶段分析结果 JSON（决策4：AI 结果顺带保存）
    """

    __tablename__ = "phase_reviews"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "period_type", "start_date", "end_date", "instrument_type",
            name="uq_phase_review_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, index=True)
    period_type: Mapped[str] = mapped_column(String(10), default="week")  # week / month / custom
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(20), default="")  # ''=全部/通用
    title: Mapped[str] = mapped_column(String(100), default="")  # 标题（可选）
    content: Mapped[str] = mapped_column(Text, default="")  # 手写总结正文（''=仅AI结果占位）
    ai_result: Mapped[str] = mapped_column(Text, default="")  # 最近一次 AI 分析结果 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class MarketReview(Base):
    """阶段盘面综述（V1.008.2 功能1）

    按 品种类型 × 起止日期 生成一份供写阶段总结参考的盘面综述报告：
    - A股：大盘指数（价格/区间涨跌/振幅/量）+ 行业/概念板块表现 + 区间领涨个股
    - 商品期货：各板块与主要活跃品种的区间表现（按新浪主连日K计算）
    - 数字货币：BTC/ETH 区间表现（Gate.io 日K）
    唯一约束 (user_id, instrument_type, start_date, end_date)：同键一条，重新生成覆盖。
    content: 完整 markdown 报告（数据速览为程序生成，点评为 AI 生成，数值以速览为准）
    data_json: 采集的原始结构化行情快照（可回看/核对口径）
    note: 生成时的数据缺失/降级说明
    """

    __tablename__ = "market_reviews"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "instrument_type", "start_date", "end_date",
            name="uq_market_review_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True, index=True)
    instrument_type: Mapped[str] = mapped_column(String(20), default="A股")  # A股/商品期货/数字货币
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(100), default="")  # 如：2026年8月 · A股 盘面综述
    content: Mapped[str] = mapped_column(Text, default="")  # markdown 报告
    data_json: Mapped[str] = mapped_column(Text, default="")  # 结构化行情快照 JSON
    note: Mapped[str] = mapped_column(Text, default="")  # 数据口径/缺失说明
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class FuturesConfig(Base):
    """期货品种参数配置（合约乘数 + 保证金率）

    level 区分两级：
    - "variety"（品种级默认）：code=AL，乘数 5，保证金率随东财每日同步更新
    - "contract"（合约级覆盖）：code=AL2609，个别合约单独费率（覆盖品种默认）
    查询优先级：合约级 > 品种级 > 内置默认（investment.FUTURES_DEFAULT_MARGIN）
    乘数以内置静态表为基准（FUTURES_MULTIPLIERS），本表乘数仅供补录覆盖。
    """

    __tablename__ = "futures_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(10), default="variety", index=True)  # variety / contract
    exchange: Mapped[str] = mapped_column(String(30), default="")  # 上期所/大商所/郑商所/广期所/能源中心/中金所
    code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # 品种代码 AL / 合约代码 AL2609
    name: Mapped[str] = mapped_column(String(50), default="")  # 品种名 沪铝
    multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)  # 合约乘数（NULL=沿用内置表）
    margin_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # 保证金率 0.17（NULL=沿用内置默认）
    margin_source: Mapped[str] = mapped_column(String(30), default="")  # eastmoney / manual / builtin
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )
