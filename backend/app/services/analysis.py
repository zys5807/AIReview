"""AI 交易分析引擎：组装数据 → 调用大模型 → 解析结构化结果"""
import json
import re

from sqlalchemy.orm import Session

from ..models import Trade, TradingSystem
from .llm import LLMError, chat
from .prompts import (
    DIMENSIONS,
    build_analysis_messages,
    build_plan_comparison_messages,
    build_plan_review_messages,
    build_screenshot_texts,
)


class AnalysisError(RuntimeError):
    """分析异常"""


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON（清洗 markdown 代码块和多余文字）"""
    text = text.strip()
    # 去掉 markdown 代码块围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # 提取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AnalysisError("模型输出中未找到有效 JSON")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise AnalysisError(f"模型输出 JSON 解析失败: {e}") from e


def _normalize_result(data: dict) -> dict:
    """校验并规范化分析结果"""
    if not isinstance(data, dict):
        raise AnalysisError("分析结果格式错误")

    score = data.get("score")
    if not isinstance(score, (int, float)):
        score = 0
    score = max(0, min(100, float(score)))

    # 规范化维度：缺失维度补 0 分
    dim_map = {}
    for d in data.get("dimensions", []):
        if isinstance(d, dict) and d.get("name"):
            dim_map[d["name"]] = {
                "name": d["name"],
                "score": max(0, min(100, float(d.get("score", 0)))),
                "comment": str(d.get("comment", "")),
            }
    dimensions = []
    for name in DIMENSIONS:
        dim = dim_map.get(name)
        if not dim:
            dim = {"name": name, "score": 0, "comment": "未评分"}
        dimensions.append(dim)

    def _str_list(v):
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if x][:4]

    return {
        "score": round(score, 1),
        "dimensions": dimensions,
        "strengths": _str_list(data.get("strengths")),
        "weaknesses": _str_list(data.get("weaknesses")),
        "improvements": _str_list(data.get("improvements")),
        "summary": str(data.get("summary", "")),
    }


def analyze_trade(trade: Trade, system: TradingSystem | None, db: Session | None = None) -> dict:
    """对一笔交易执行 AI 分析，返回结构化结果

    db 非空时会对关联的多周期截图执行识别并注入分析上下文。
    """
    screenshot_texts = build_screenshot_texts(db, trade) if db else None
    messages = build_analysis_messages(trade, system, screenshot_texts)
    try:
        raw = chat(messages, db=db)
    except LLMError as e:
        raise AnalysisError(str(e)) from e

    data = _extract_json(raw)
    return _normalize_result(data)


# ---------- 阶段性复盘分析 ----------
PHASE_OUTPUT_FORMAT = """请严格按照以下 JSON 格式输出（不要输出其他文字）：
{
  "summary": "该阶段整体表现的一句话结论（结合盈亏、执行、评分）",
  "best_trades": ["表现最好的1-2笔交易及具体原因"],
  "worst_trades": ["表现最差的1-2笔交易及具体原因"],
  "patterns": ["交易者反复出现的行为/执行模式，如：追涨杀跌、入场犹豫、不设止损、提前止盈等"],
  "recurring_issues": [{"issue": "反复出现的问题", "count": 出现次数(数字), "suggestion": "针对该问题的具体改进方法"}],
  "system_feedback": ["对用户交易系统/策略本身的优化建议（如入场条件、止损止盈、周期选择、仓位规则等）"],
  "next_actions": ["下一交易周期最值得执行的3条具体行动计划"],
  "continuity": [{"item": "上一期或更早总结中提出的改进点/问题", "status": "已改进|部分改进|未改进|无法判断", "evidence": "依据（本期统计数字或本期手写总结内容）"}]
}"""


def _build_phase_messages(
    trades, systems_text: str, stats: dict | None = None, manual: list | None = None
) -> list[dict]:
    """构造阶段性分析的消息列表；manual: 手写总结列表（V1.008，时间顺序，最后一条为本期）"""
    lines = [
        "你是专业的交易复盘分析师。请对用户在一段时间内的所有交易做一次【阶段性复盘分析】，",
        "找出整体表现、反复出现的问题、交易者行为模式，以及交易系统/策略本身可优化的方向。",
        "要具体、可执行，不要泛泛而谈。",
        "",
        f"本次共分析 {len(trades)} 笔交易。",
    ]
    if stats:
        lines += [
            "",
            "【本阶段系统精确统计 —— 在 summary 中直接引用这些数字，禁止自行计算盈亏】",
            f"  笔数：{stats['count']}，盈利笔数：{stats['win']}，亏损笔数：{stats['loss']}，"
            f"胜率：{stats['win_rate'] * 100:.1f}%",
            f"  合计盈亏：{stats['total_pnl']}（系统已精确计算，直接使用该数值）",
            f"  总手续费：{stats['total_fee']}",
        ]
    lines += [
        "",
        "=== 各笔交易明细 ===",
    ]
    for i, t in enumerate(trades, 1):
        lines.append(
            f"交易{i} [{t.entry_time.strftime('%Y-%m-%d %H:%M')}] "
            f"品种={t.instrument_name or t.instrument_code} "
            f"方向={'做多' if t.direction == 'long' else '做空'} "
            f"入场={t.entry_price} 出场={t.exit_price} "
            f"手数={t.volume} "
            f"盈亏={round(t.pnl - (t.fee or 0), 2) if t.pnl is not None else '未填'}（净盈亏，已扣手续费）"
            f"手续费={t.fee if t.fee is not None else 0} "
            f"初始止损={'已设置(' + str(t.stop_loss) + ')' if t.stop_loss else '未设置'}"
        )
        if t.trading_system_id:
            lines.append(f"  适用系统：{t.trading_system.name if t.trading_system else '未知'}")
        if t.timeframe_notes:
            lines.append(f"  入场信号依据：{t.timeframe_notes[:120]}")
        if t.notes:
            lines.append(f"  交易复盘（心得）：{t.notes[:200]}")
        if t.score is not None:
            lines.append(f"  AI评分：{t.score}")
        if t.latest_issues:
            lines.append(f"  AI指出的问题：{t.latest_issues}")

    if manual:
        period_label = {"week": "周复盘", "month": "月复盘", "custom": "AI分析"}
        lines += [
            "",
            "=== 用户手写阶段总结（时间顺序，最后一条为本期；AI 应尊重并引用用户自己的视角）===",
        ]
        for m in manual:
            tag = f"{period_label.get(m['period_type'], m['period_type'])} · {m['instrument'] or '全部'}"
            head = f"【{m['period']} · {tag}】"
            if m.get("title"):
                head += f" ({m['title']})"
            lines.append(head)
            lines.append(f"  本期总结：{m['content'][:300]}")
        lines += [
            "",
            "要求：",
            "1. 如果存在【上一期及更早】的总结，逐条对比其中提出的'问题/改进计划'与本期数据、本期总结，",
            "   判断是否落实，输出到 continuity 字段（status 仅限：已改进/部分改进/未改进/无法判断）。",
            "2. 手写总结与统计/交易明细冲突时，以统计为准，并在 summary 中说明差异。",
        ]

    if systems_text:
        lines += ["", "=== 用户使用的交易系统规则 ===", systems_text]

    lines += [
        "",
        "请从以上数据中分析并输出：",
        PHASE_OUTPUT_FORMAT,
    ]
    return [
        {"role": "system", "content": "你是严格的交易复盘分析师，输出必须是可以直接落地执行的复盘结论。"},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _load_manual_reviews(db, user_id, start, end, instrument_type=None, limit_hist=3):
    """V1.008：加载手写阶段总结 —— 本期（时间相交）+ 历史最近 3 期（end_date < start）。

    返回时间升序列表（早→晚，最后一条为本期），每条含 id/period/period_type/instrument/title/content。
    """
    from ..models import PhaseReview

    def _base_q():
        q = db.query(PhaseReview).filter(PhaseReview.user_id == user_id)
        if instrument_type:
            q = q.filter(PhaseReview.instrument_type == instrument_type)
        return q

    # 本期：与筛选范围相交（start_date <= end 且 end_date >= start）
    cur = _base_q().filter(
        PhaseReview.start_date <= end.date(),
        PhaseReview.end_date >= start.date(),
    ).order_by(PhaseReview.end_date.desc()).all()
    # 历史：end_date < start，倒序取最近 limit_hist 期
    hist = _base_q().filter(PhaseReview.end_date < start.date()).order_by(
        PhaseReview.end_date.desc(), PhaseReview.id.desc()
    ).limit(limit_hist).all()

    # 合并去重（按 id）：历史在前，本期在后；总数上限 5 条
    merged = []
    seen = set()
    for r in list(reversed(hist)) + cur:
        if r.id in seen:
            continue
        seen.add(r.id)
        merged.append(r)
        if len(merged) >= 5:
            break

    return [
        {
            "id": r.id,
            "period": f"{r.start_date.strftime('%m-%d')} ~ {r.end_date.strftime('%m-%d')}",
            "period_type": r.period_type,
            "instrument": r.instrument_type,
            "title": r.title,
            "content": r.content or "",
        }
        for r in merged
    ]


def _save_ai_result(db, user_id, start, end, instrument_type, period_type, result: dict) -> int | None:
    """V1.008 决策4：AI 结果顺带保存 —— 命中本期匹配记录则更新 ai_result，否则创建占位记录。返回记录 id。"""
    from ..models import PhaseReview

    cur = (
        db.query(PhaseReview)
        .filter(
            PhaseReview.user_id == user_id,
            PhaseReview.start_date <= end.date(),
            PhaseReview.end_date >= start.date(),
        )
        .order_by(PhaseReview.end_date.desc())
    )
    if instrument_type:
        cur = cur.filter(PhaseReview.instrument_type == instrument_type)
    review = cur.first()
    payload = json.dumps(result, ensure_ascii=False)
    if review:
        review.ai_result = payload
    else:
        review = PhaseReview(
            user_id=user_id,
            period_type=period_type if period_type in ("week", "month", "quarter", "year") else "custom",
            start_date=start.date(),
            end_date=end.date(),
            instrument_type=instrument_type or "",
            title="AI 阶段分析",
            content="",
            ai_result=payload,
        )
        db.add(review)
    db.commit()
    db.refresh(review)
    return review.id


def phase_summary(
    db: Session,
    user_id: int,
    start,
    end,
    instrument_type: str | None = None,
    limit: int = 30,
    currency: str = "CNY",
    include_manual: bool = True,
    period_type: str = "custom",
) -> dict:
    """对一段时间内该用户的所有交易做 AI 阶段性复盘分析。

    按【离场时间】筛选，与阶段复盘统计口径一致（未平仓交易不计入）。
    V1.007.1：支持币种筛选（按交易盈亏归属币种过滤，数字货币→USD，A股/期货→CNY）。
    V1.008：include_manual=True 时注入本期+历史最近3期手写阶段总结，输出 continuity 追踪，
            并把 AI 结果保存到对应 phase_reviews 记录（决策4）。
    """
    from ..models import ReviewReport
    from .account import TRADE_CURRENCY_MAP

    q = db.query(Trade).filter(
        Trade.user_id == user_id,
        Trade.exit_time >= start,
        Trade.exit_time <= end,
    )
    if instrument_type:
        q = q.filter(Trade.instrument_type == instrument_type)
    # 币种筛选：与阶段复盘统计口径一致
    if currency:
        types = [t for t, c in TRADE_CURRENCY_MAP.items() if c == currency]
        if instrument_type:
            if TRADE_CURRENCY_MAP.get(instrument_type, "CNY") != currency:
                q = q.filter(False)
        elif types:
            q = q.filter(Trade.instrument_type.in_(types))
    trades = q.order_by(Trade.exit_time.asc()).limit(limit).all()
    if not trades:
        raise AnalysisError("该时间段暂无交易记录")

    # 系统精确计算本阶段统计（供 prompt 引用，避免 LLM 自行计算不稳定）；盈亏按净额（pnl - fee）
    pnls = [round(t.pnl - (t.fee or 0), 2) for t in trades if t.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_pnl = round(sum(pnls), 2)
    stats = {
        "count": len(trades),
        "win": len(wins),
        "loss": len(losses),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0,
        "total_pnl": total_pnl,
        "total_fee": round(sum(t.fee or 0 for t in trades), 2),
    }

    # 为每笔交易补充最新报告评分与问题摘要（供 prompt 使用）
    systems = {}
    for t in trades:
        report = (
            db.query(ReviewReport)
            .filter(
                ReviewReport.trade_id == t.id,
                ReviewReport.user_id == user_id,
            )
            .order_by(ReviewReport.id.desc())
            .first()
        )
        t.score = report.score if report else None
        issues = []
        if report and report.analysis:
            try:
                a = json.loads(report.analysis)
                issues = (a.get("weaknesses") or [])[:3]
            except Exception:
                pass
        t.latest_issues = "；".join(issues)[:200]
        if t.trading_system_id and t.trading_system:
            systems[t.trading_system_id] = t.trading_system

    systems_lines = []
    for sys_ in systems.values():
        systems_lines.append(f"- 系统「{sys_.name}」: 多空周期={sys_.trend_timeframe}，方向周期={sys_.direction_timeframe}，交易周期={sys_.entry_timeframe}")
        systems_lines.append(f"  趋势规则：{sys_.trend_rule or '未设置'}")
        systems_lines.append(f"  仓位规则：{sys_.position_rule or '未设置'}")
        strategies = sorted(sys_.trade_strategies, key=lambda x: x.sort_order)
        for st in strategies:
            systems_lines.append(
                f"  策略「{st.name or '未命名'}」: 入场={st.entry_rule} 止损={st.stop_loss_rule} 止盈={st.take_profit_rule}"
            )

    # V1.008：加载手写阶段总结（本期 + 历史最近 3 期）
    manual = []
    if include_manual:
        manual = _load_manual_reviews(db, user_id, start, end, instrument_type)

    messages = _build_phase_messages(trades, "\n".join(systems_lines), stats, manual)
    try:
        raw = chat(messages, temperature=0.4, max_tokens=3000, db=db)
    except LLMError as e:
        raise AnalysisError(str(e)) from e

    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise AnalysisError("阶段性分析结果格式错误")

    def _str_list(v):
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if x]

    def _issue_list(v):
        out = []
        if not isinstance(v, list):
            return out
        for x in v:
            if isinstance(x, dict) and x.get("issue"):
                out.append(
                    {
                        "issue": str(x["issue"]),
                        "count": int(x.get("count", 1) or 1),
                        "suggestion": str(x.get("suggestion", "")),
                    }
                )
        return out

    def _continuity_list(v):
        out = []
        valid = {"已改进", "部分改进", "未改进", "无法判断"}
        if not isinstance(v, list):
            return out
        for x in v:
            if isinstance(x, dict) and x.get("item"):
                status = str(x.get("status", "无法判断"))
                if status not in valid:
                    status = "无法判断"
                out.append(
                    {
                        "item": str(x["item"])[:200],
                        "status": status,
                        "evidence": str(x.get("evidence", ""))[:300],
                    }
                )
        return out

    result = {
        "summary": str(data.get("summary", "")),
        "best_trades": _str_list(data.get("best_trades")),
        "worst_trades": _str_list(data.get("worst_trades")),
        "patterns": _str_list(data.get("patterns")),
        "recurring_issues": _issue_list(data.get("recurring_issues")),
        "system_feedback": _str_list(data.get("system_feedback")),
        "next_actions": _str_list(data.get("next_actions")),
        "continuity": _continuity_list(data.get("continuity")),
        "analyzed_count": len(trades),
        "stats": stats,
        "manual_review_count": len(manual),
    }

    # V1.008 决策4：AI 结果顺带保存（失败不阻断主流程）
    try:
        result["saved_to_review_id"] = _save_ai_result(
            db, user_id, start, end, instrument_type, period_type, result
        )
    except Exception:
        result["saved_to_review_id"] = None
    return result


# ---------- 交易计划 ----------
def _str_list(v):
    if not isinstance(v, list):
        return []
    return [str(x) for x in v if x]


def _plan_system_text(system) -> str:
    """把交易系统规则转成文本（供计划评审/对照使用）"""
    if not system:
        return ""
    lines = [
        f"系统「{system.name}」: 多空周期={system.trend_timeframe}，方向周期={system.direction_timeframe}，交易周期={system.entry_timeframe}",
        f"趋势规则：{system.trend_rule or '未设置'}",
        f"仓位规则：{system.position_rule or '未设置'}",
        f"风险规则：{system.risk_rule or '未设置'}",
    ]
    for st in sorted(system.trade_strategies, key=lambda x: x.sort_order):
        lines.append(
            f"策略「{st.name or '未命名'}」: 入场={st.entry_rule} 止损={st.stop_loss_rule} 止盈={st.take_profit_rule}"
        )
    return "\n".join(lines)


def plan_review(plan, db: Session | None = None) -> dict:
    """对交易计划做 AI 预评审"""
    messages = build_plan_review_messages(plan, _plan_system_text(plan.trading_system))
    try:
        raw = chat(messages, temperature=0.3, max_tokens=1500, db=db)
    except LLMError as e:
        raise AnalysisError(str(e)) from e
    data = _extract_json(raw)
    return {
        "verdict": str(data.get("verdict", "")),
        "assessment": str(data.get("assessment", "")),
        "strengths": _str_list(data.get("strengths")),
        "risks": _str_list(data.get("risks")),
        "suggestions": _str_list(data.get("suggestions")),
    }


def plan_comparison(plan, trade, db: Session | None = None) -> dict:
    """AI 分析计划与执行对照"""
    messages = build_plan_comparison_messages(
        plan, trade, _plan_system_text(plan.trading_system)
    )
    try:
        raw = chat(messages, temperature=0.3, max_tokens=1500, db=db)
    except LLMError as e:
        raise AnalysisError(str(e)) from e
    data = _extract_json(raw)
    deviations = []
    for d in data.get("deviations", []) or []:
        if isinstance(d, dict):
            deviations.append(
                {
                    "item": str(d.get("item", "")),
                    "planned": str(d.get("planned", "")),
                    "actual": str(d.get("actual", "")),
                    "impact": str(d.get("impact", "")),
                }
            )
    return {
        "execution_summary": str(data.get("execution_summary", "")),
        "discipline_score": max(0, min(100, float(data.get("discipline_score", 0) or 0))),
        "deviations": deviations,
        "comments": _str_list(data.get("comments")),
    }
