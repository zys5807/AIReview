"""交易分析 Prompt 模板：按品种特性区分，输出结构化 JSON"""

# 评分维度定义
DIMENSIONS = [
    "入场时机",
    "出场时机",
    "趋势判断",
    "风险控制",
    "仓位管理",
    "交易纪律",
    "盈亏比",
    "情绪控制",
]

# 各品种特性提示（注入系统提示词，让分析更贴合市场规则）
INSTRUMENT_PROFILES = {
    "A股": """
【品种特性：A股】
- 交易规则：T+1交易制度，买入当日不可卖出；涨跌停限制（主板10%，创业板/科创板20%）
- 交易时间：周一至周五 9:30-11:30、13:00-15:00
- 分析要点：关注大盘环境、板块联动、涨跌停附近的流动性风险
- 评分注意：止损位设置需考虑日内波动和涨跌停导致的无法成交风险
""",
    "商品期货": """
【品种特性：商品期货】
- 交易规则：保证金杠杆交易（通常5%-15%保证金）；有合约月份，临近交割月流动性下降
- 交易时间：日盘+夜盘（部分品种），不同品种规则有差异
- 分析要点：关注杠杆放大风险和收益、合约换月、基差变动
- 评分注意：仓位管理需结合杠杆倍数评估，止损必须严格执行，防止爆仓风险
""",
    "数字货币": """
【品种特性：数字货币】
- 交易规则：7×24小时连续交易，无涨跌停限制，波动极大
- 合约类型：现货/永续合约/交割合约，永续合约有资金费率
- 分析要点：关注极端行情下的插针风险、资金费率成本、杠杆倍数
- 评分注意：高波动下止损设置尤其重要，仓位管理需更保守
""",
}

# 评分要求
SCORING_RULES = """
【评分要求】
- 每个维度 0-100 分，60 分及格
- 综合评分 = 各维度合理加权，不是简单平均
- 评分要严格对照用户的交易系统，系统没规定的规则不要过度扣分
- 只有用户明确设定的规则才作为"是否符合系统"的评判依据
"""

# ---------- 交易计划 ----------
PLAN_REVIEW_FORMAT = """请严格按照以下 JSON 格式输出（不要输出其他文字）：
{
  "verdict": "可执行 / 需调整 / 不建议",
  "assessment": "对这份交易计划的整体评估（2-3句话）",
  "strengths": ["计划做得好的1-2点"],
  "risks": ["计划存在的风险/漏洞，如止损不合理、仓位过重、入场条件不明确等"],
  "suggestions": ["具体的调整建议"]
}"""

PLAN_COMPARISON_FORMAT = """请严格按照以下 JSON 格式输出（不要输出其他文字）：
{
  "execution_summary": "执行情况的一句话结论（是否按计划执行）",
  "discipline_score": 0到100的纪律执行评分,
  "deviations": [
    {"item": "偏离项（入场价/止损/仓位/时机等）", "planned": "计划值", "actual": "实际值", "impact": "对这笔交易的影响"}
  ],
  "comments": ["针对纪律执行的具体评价与改进建议"]
}"""


def build_plan_review_messages(plan, system_text: str) -> list[dict]:
    """构造交易计划预评审消息"""
    lines = [
        "你是专业的交易计划评审师。请对用户提交的一份交易计划做预评审，",
        "评估其合理性、风险点和可执行性，并给出具体调整建议。",
        "",
        "=== 交易计划 ===",
        f"计划名称：{plan.name}",
        f"标的：{plan.instrument_type or ''} {plan.instrument_name or plan.instrument_code or ''} "
        f"方向：{'做多' if plan.direction == 'long' else '做空'}",
        f"入场方式：{plan.entry_method or '未填写'}",
        f"计划入场价：{plan.planned_entry_price if plan.planned_entry_price is not None else '未设置'}",
        f"入场理由：{plan.entry_reason or '未填写'}",
        f"初始止损位：{plan.stop_loss if plan.stop_loss is not None else '未设置'}",
        f"最大亏损金额：{plan.max_loss_amount if plan.max_loss_amount is not None else '未设置'}",
        f"计划手数：{plan.planned_volume if plan.planned_volume is not None else '未设置'}",
        f"目标价1：{plan.target1 if plan.target1 is not None else '未设置'}",
        f"目标价2：{plan.target2 if plan.target2 is not None else '未设置'}",
        f"预期盈亏比：{plan.risk_reward or '未设置'}",
        f"市场背景/计划逻辑：{plan.market_context or '未填写'}",
    ]
    if system_text:
        lines += ["", "=== 该计划关联的交易系统规则 ===", system_text]
    lines += ["", "请基于以上内容输出评审结果：", PLAN_REVIEW_FORMAT]
    return [
        {"role": "system", "content": "你是严格的交易计划评审师，给出的调整建议必须具体可落地。"},
        {"role": "user", "content": "\n".join(lines)},
    ]


def build_plan_comparison_messages(plan, trade, system_text: str) -> list[dict]:
    """构造交易计划-执行对照分析消息"""
    lines = [
        "你是专业的交易纪律审计师。请把一份交易计划与实际执行情况做对照，",
        "找出每一次偏离，评估对交易的影响，并给出纪律执行评分。",
        "",
        "=== 交易计划 ===",
        f"计划名称：{plan.name}",
        f"方向：{'做多' if plan.direction == 'long' else '做空'}",
        f"计划入场价：{plan.planned_entry_price if plan.planned_entry_price is not None else '未设置'}",
        f"初始止损位：{plan.stop_loss if plan.stop_loss is not None else '未设置'}",
        f"计划手数：{plan.planned_volume if plan.planned_volume is not None else '未设置'}",
        f"目标价1：{plan.target1 if plan.target1 is not None else '未设置'}",
        f"目标价2：{plan.target2 if plan.target2 is not None else '未设置'}",
        f"入场理由：{plan.entry_reason or '未填写'}",
        "",
        "=== 实际执行情况 ===",
        f"实际入场时间：{trade.entry_time.strftime('%Y-%m-%d %H:%M')}",
        f"实际出场时间：{trade.exit_time.strftime('%Y-%m-%d %H:%M')}",
        f"实际入场价：{trade.entry_price}",
        f"实际出场价：{trade.exit_price}",
        f"实际手数：{trade.volume}",
        f"实际止损位：{trade.stop_loss if trade.stop_loss is not None else '未设置'}",
        f"实际盈亏：{trade.pnl if trade.pnl is not None else '未填'}",
    ]
    if trade.timeframe_notes:
        lines.append(f"实际入场理由：{trade.timeframe_notes[:200]}")
    if trade.notes:
        lines.append(f"用户复盘心得：{trade.notes[:200]}")
    if system_text:
        lines += ["", "=== 关联的交易系统规则 ===", system_text]
    lines += ["", "请对照计划与执行，输出：", PLAN_COMPARISON_FORMAT]
    return [
        {"role": "system", "content": "你是严格的交易纪律审计师，评估必须客观，偏离计划要明确指出。"},
        {"role": "user", "content": "\n".join(lines)},
    ]

# JSON 输出格式要求
OUTPUT_FORMAT = """
【输出格式】必须输出纯 JSON，不要包含 markdown 代码块标记，不要有任何解释性文字。JSON 结构如下：
{
  "score": 综合评分0-100,
  "dimensions": [
    {"name": "入场时机", "score": 分值, "comment": "一句话点评"},
    {"name": "出场时机", "score": 分值, "comment": "一句话点评"},
    {"name": "趋势判断", "score": 分值, "comment": "一句话点评"},
    {"name": "风险控制", "score": 分值, "comment": "一句话点评"},
    {"name": "仓位管理", "score": 分值, "comment": "一句话点评"},
    {"name": "交易纪律", "score": 分值, "comment": "一句话点评"},
    {"name": "盈亏比", "score": 分值, "comment": "一句话点评"},
    {"name": "情绪控制", "score": 分值, "comment": "一句话点评"}
  ],
  "strengths": ["优点1", "优点2", "优点3"],
  "weaknesses": ["不足1", "不足2", "不足3"],
  "improvements": ["具体改进建议1", "具体改进建议2", "具体改进建议3"],
  "summary": "200字以内的整体交易评价"
}
strengths/weaknesses/improvements 每个数组 2-4 条，要具体、可执行，不要空话。
"""


def build_system_prompt(instrument_type: str) -> str:
    """构建系统提示词"""
    profile = INSTRUMENT_PROFILES.get(instrument_type, INSTRUMENT_PROFILES["A股"])
    return f"""你是一位专业的交易复盘分析师，精通A股、商品期货、数字货币等多种交易市场。
你的任务是基于用户的交易记录和交易系统，客观、严格地分析这笔交易，找出优点和不足，并给出可执行的改进建议。

{profile}
{SCORING_RULES}
{OUTPUT_FORMAT}

分析原则：
1. 优点和不足都要具体，结合交易数据和系统规则，不要泛泛而谈
2. 改进建议必须可执行，给出明确的动作
3. 如果交易明显违反交易系统，交易纪律维度要严厉扣分
4. 即使交易盈利，如果过程不符合系统，也要指出问题；反之亏损交易如果执行正确，也应认可
5. 若用户填写了"交易复盘"（心得体会），请仔细阅读并参考，结合系统与策略的执行情况，在改进建议中明确指出交易系统或策略本身可优化之处（如入场/止损/止盈条件、周期选择、仓位规则等）
6. 若交易包含加仓/减仓操作，请逐次评估操作时机/价格/数量的合理性（是否顺势加仓/减仓、成本价与风险敞口的变化、是否违反原定仓位纪律），并将加仓/减仓决策纳入趋势判断与仓位管理的点评

多周期核对要求：
如果用户提供了多张K线截图（不同周期/角色），请逐周期核对该交易是否符合交易系统对应周期的约束：
- "背景1/多空判断"截图 → 核对大周期（如日线）多空状态是否符合系统的趋势判断规则
- "背景2/方向判断"截图 → 核对方向周期（如1小时）是否顺势
- "次级别1/入场离场"截图 → 核对入场点/离场点是否符合系统的入场/出场条件
- 截图上的标注箭头（入场/出场）表示用户实际的下单位置；截图上的用户备注是用户当时的解读
每个周期请给出明确的"符合 / 部分符合 / 不符合 / 无法判断"结论，并写入对应维度评分（如趋势判断、入场时机、出场时机）的点评里。没有截图信息的周期标注"无截图，无法核对"。"""


def _trade_to_text(trade, system, screenshot_texts: list[str] | None = None) -> str:
    """把交易数据和交易系统组装成分析输入文本"""
    direction = "做多" if trade.direction == "long" else "做空"
    lines = [
        "=== 交易数据 ===",
        f"品种类型：{trade.instrument_type}",
        f"品种：{trade.instrument_name or trade.instrument_code or '-'}",
        f"品种代码：{trade.instrument_code or '-'}",
        f"交易所：{trade.exchange or '-'}",
        f"合约类型：{trade.contract_type or '-'}",
        f"K线周期：{trade.timeframe or '-'}",
        f"交易方向：{direction}",
        f"入场时间：{trade.entry_time.strftime('%Y-%m-%d %H:%M')}",
        f"出场时间：{trade.exit_time.strftime('%Y-%m-%d %H:%M')}",
        f"入场价格：{trade.entry_price}",
        f"出场价格：{trade.exit_price}",
        f"交易手数：{trade.volume}",
        f"初始止损位：{trade.stop_loss if trade.stop_loss is not None else '未设置'}",
        f"盈亏金额：{trade.pnl if trade.pnl is not None else '未填写'}",
    ]
    # 加仓/减仓操作（多次；正数=加仓，负数=减仓），兼容旧单次加仓字段
    actions = sorted(trade.position_actions, key=lambda x: x.action_time) if trade.position_actions else []
    if actions:
        lines.append("该交易的加仓/减仓操作（按时间顺序，正数为加仓、负数为减仓）：")
        for a in actions:
            kind = "加仓" if (a.volume or 0) > 0 else "减仓"
            lines.append(
                f"  {a.action_time.strftime('%Y-%m-%d %H:%M')} {kind} "
                f"{abs(a.volume or 0)} @ {a.price if a.price is not None else '-'}"
            )
    elif trade.scale_in_time:
        kind = "加仓" if (trade.scale_in_volume or 0) >= 0 else "减仓"
        lines.append(
            f"{kind}动作：时间={trade.scale_in_time.strftime('%Y-%m-%d %H:%M')}，"
            f"价格={trade.scale_in_price if trade.scale_in_price is not None else '-'}，"
            f"数量={trade.scale_in_volume if trade.scale_in_volume is not None else '-'}"
        )
    if trade.notes:
        lines.append(
            f"交易复盘（用户对这笔交易的心得体会，请仔细阅读并在分析中参考，"
            f"可用于优化其交易系统和策略）：{trade.notes}"
        )
    if trade.psychology_notes:
        lines.append(f"持仓过程中的心理状态（用户填写）：{trade.psychology_notes}")
    if trade.timeframe_notes:
        lines.append(f"各周期判断依据（用户入场时看到的信号）：{trade.timeframe_notes}")

    if system:
        lines += [
            "",
            "=== 用户的交易系统 ===",
            f"系统名称：{system.name}",
            f"系统描述：{system.description or '无'}",
            f"趋势/多空判断周期：{system.trend_timeframe or '未设置'}",
            f"方向判断周期：{system.direction_timeframe or '未设置'}",
            f"入场/离场周期：{system.entry_timeframe or '未设置'}",
            f"趋势判断规则（所有交易策略共用）：{system.trend_rule or '未设置'}",
            f"仓位管理（所有交易策略共用）：{system.position_rule or '未设置'}",
            f"风险控制（所有交易策略共用）：{system.risk_rule or '未设置'}",
        ]
        # 交易策略列表（多策略为"或"关系：符合任一即可入场，按该策略止损/止盈执行）
        strategies = sorted(system.trade_strategies, key=lambda x: x.sort_order)
        if strategies:
            lines.append(
                "该系统的交易策略（多个策略为“或”关系，行情符合其中一个即可按该策略入场，"
                "并执行该策略的止损/止盈）："
            )
            for i, st in enumerate(strategies, 1):
                status = "启用" if st.is_active else "停用"
                lines.append(f"  策略{i}[{status}] {st.name or '未命名'}")
                lines.append(f"    入场策略：{st.entry_rule or '未设置'}")
                lines.append(f"    初始止损策略：{st.stop_loss_rule or '未设置'}")
                lines.append(f"    止盈策略：{st.take_profit_rule or '未设置'}")
        else:
            # 兼容旧数据：无交易策略时回退到旧的入场/出场条件
            lines.append("（该系统尚未配置交易策略，以下为旧的入场/出场条件：）")
            lines.append(f"  入场条件：{system.entry_rule or '未设置'}")
            lines.append(f"  出场条件：{system.exit_rule or '未设置'}")
        # 本笔实际周期（可偏离系统默认；多周期核对时以实际周期为准）
        used_trend = trade.trend_timeframe_used or system.trend_timeframe or "未设置"
        used_dir = trade.direction_timeframe_used or system.direction_timeframe or "未设置"
        used_entry = trade.entry_timeframe_used or system.entry_timeframe or "未设置"
        if trade.trend_timeframe_used or trade.direction_timeframe_used or trade.entry_timeframe_used:
            lines.append(
                "本笔交易实际使用的周期（与系统标准周期不同，多周期核对请以实际周期为准）："
            )
            lines.append(f"  实际多空判断周期：{used_trend}")
            lines.append(f"  实际方向判断周期：{used_dir}")
            lines.append(f"  实际交易周期：{used_entry}")
        else:
            lines.append(
                "本笔交易使用系统默认周期进行多周期核对："
                f"多空={used_trend}、方向={used_dir}、交易={used_entry}。"
            )
        lines.append(
            "分析提示：请结合'各周期判断依据'和'入场策略'，判断该笔交易符合哪个入场策略，"
            "以及入场时机是否符合该策略的条件。"
        )
    else:
        lines += ["", "=== 用户的交易系统 ===", "（该笔交易未关联交易系统，请基于通用交易原则分析）"]

    # 多周期截图识别结果
    if screenshot_texts:
        lines += ["", "=== 多周期K线截图识别结果（用于核对各周期是否符合系统） ==="]
        lines += screenshot_texts

    return "\n".join(lines)


def build_analysis_messages(trade, system, screenshot_texts: list[str] | None = None) -> list[dict]:
    """构建分析用的消息列表"""
    return [
        {"role": "system", "content": build_system_prompt(trade.instrument_type)},
        {"role": "user", "content": _trade_to_text(trade, system, screenshot_texts)},
    ]


def build_screenshot_texts(db, trade) -> list[str]:
    """对交易关联的每张截图执行K线识别，转成结构化文本供AI分析"""
    from ..config import BASE_DIR
    from .kline_recognize import recognize_screenshot

    texts = []
    links = sorted(trade.screenshot_links, key=lambda x: x.sort_order)
    for link in links:
        shot = link.screenshot
        if not shot:
            continue
        path = BASE_DIR / shot.stored_path
        label = link.role or "未命名角色"
        if not path.exists():
            texts.append(f"### 截图[{label}]：{shot.filename}（文件丢失，无法识别）")
            continue
        try:
            d = recognize_screenshot(path).to_dict()
            lines = [f"### 截图[{label}]：{shot.filename}"]
            info = []
            if d.get("instrument"):
                info.append(f"品种={d.get('instrument')}")
            if d.get("exchange"):
                info.append(f"交易所={d.get('exchange')}")
            if d.get("timeframe_label"):
                info.append(f"周期={d.get('timeframe_label')}")
            if d.get("price_min") or d.get("price_max"):
                info.append(f"价格区间={d.get('price_min')}~{d.get('price_max')}")
            if info:
                lines.append("识别信息：" + " / ".join(info))
            for a in d.get("arrows", []):
                role_name = {"entry": "入场", "exit": "出场"}.get(a.get("role"), "标记")
                lines.append(
                    f"标注箭头：{role_name}({a.get('direction')}) 位置=({a.get('x')},{a.get('y')})"
                )
            notes = d.get("notes", [])
            if notes:
                lines.append("用户备注：" + "；".join(n.get("text", "") for n in notes))
            if len(lines) == 1:
                lines.append("（未能提取出有效信息）")
            texts.append("\n".join(lines))
        except Exception as e:
            texts.append(f"### 截图[{label}]：{shot.filename}（识别失败：{e}）")
    return texts
