"""阶段性复盘统计服务：按时间段聚合交易与复盘数据"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import ReviewReport, Trade
from .account import balance_at, equity_before

# 常见问题关键词（用于从复盘报告的不足/改进中聚合高频主题）
COMMON_ISSUE_KEYWORDS = [
    "仓位", "止损", "出场", "入场", "纪律", "情绪",
    "趋势", "盈亏比", "杠杆", "风险", "追涨", "杀跌", "分批", "信号",
]


def _calc_summary(trades: list[Trade]) -> dict:
    """基础统计：笔数/胜率/盈亏比/总盈亏等"""
    count = len(trades)
    if count == 0:
        return {
            "count": 0, "win": 0, "loss": 0, "win_rate": 0,
            "total_pnl": 0, "avg_pnl": 0,
            "gross_profit": 0, "gross_loss": 0, "profit_factor": None,
            "total_volume": 0,
            "total_fee": 0,  # 手续费汇总（仅展示，不参与指标计算）
        }
    pnls = [t.pnl for t in trades if t.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "count": count,
        "win": len(wins),
        "loss": len(losses),
        "win_rate": round(len(wins) / count, 4),
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / count, 2) if pnls else 0,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 3)
        if gross_loss > 0
        else (None if gross_profit == 0 else 999.0),
        "total_volume": round(sum(t.volume or 0 for t in trades), 2),
        "total_fee": round(sum(t.fee or 0 for t in trades), 2),  # 手续费汇总（仅展示）
    }


def _trades_query(db: Session, start: datetime, end: datetime, instrument_type: str | None, user_id: int | None = None):
    # 按离场时间筛选：当天离场的交易计入当天统计
    q = db.query(Trade).filter(Trade.exit_time >= start, Trade.exit_time <= end)
    if user_id is not None:
        q = q.filter(Trade.user_id == user_id)
    if instrument_type:
        q = q.filter(Trade.instrument_type == instrument_type)
    return q.order_by(Trade.exit_time.asc())


def _calc_metrics(db, trades, start, end, user_id) -> dict:
    """阶段高级指标：平均单笔盈亏比 / 日平均仓位 / 总收益率 / 最大回撤 / 周度回撤 / 卡玛 / 夏普

    资金类指标依赖账户资金流水，无资金记录时返回 None，
    前端应提示"请先设置初始资金"。平均单笔盈亏比不依赖资金。

    期初资金 = 阶段首日交易开始前的账户权益（初始资金+出入金+此前全部已平仓交易盈亏，
    equity_before 口径，不含当日交易盈亏）；期末资金 = 期初资金 + 阶段总盈亏（与收益率口径一致）。
    """
    count = len(trades)
    # ---- 期初资金（阶段首日交易开始前权益，含此前交易盈亏），无流水时 None
    start_balance = equity_before(db, user_id, start.date())
    has_capital = start_balance is not None and start_balance > 0
    if count == 0:
        return {
            "start_balance": start_balance,
            "end_balance": start_balance,
            "avg_pl_ratio": None,
            "avg_daily_position_pct": None,
            "total_return_pct": None,
            "max_drawdown_pct": None,
            "max_weekly_drawdown_pct": None,
            "calmar_ratio": None,
            "sharpe_ratio": None,
            "has_capital": has_capital,
        }

    # ---- 1. 平均单笔盈亏比 = 平均盈利单 / 平均亏损单（亏损含平局，与 _calc_summary 口径一致）
    pnls = [t.pnl for t in trades if t.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    if avg_loss > 0:
        avg_pl_ratio = round(avg_win / avg_loss, 3)
    elif avg_win > 0:
        avg_pl_ratio = 999.0  # 全赢单
    else:
        avg_pl_ratio = None

    # ---- 资金基准：期初资金（start 当日）
    initial = start_balance    # ---- 按日盈亏
    by_day_pnl: dict = defaultdict(float)
    for t in trades:
        if t.pnl is not None:
            by_day_pnl[t.exit_time.date()] += t.pnl

    # ---- 净值曲线（自然日展开，无交易日照记 0）
    days = (end.date() - start.date()).days + 1
    curve: list[tuple] = []
    if has_capital:
        equity = float(initial)
        d = start.date()
        for _ in range(days):
            equity += by_day_pnl.get(d, 0.0)
            curve.append((d, equity))
            d += timedelta(days=1)

    def _max_dd(points: list[tuple]) -> float:
        """峰值到谷值的最大回撤比例"""
        peak = None
        mdd = 0.0
        for _, v in points:
            if peak is None or v > peak:
                peak = v
            if peak and peak > 0:
                dd = (peak - v) / peak
                if dd > mdd:
                    mdd = dd
        return mdd

    # ---- 2. 阶段总收益率
    total_pnl = sum(pnls)
    total_return_pct = round(total_pnl / initial * 100, 2) if has_capital else None

    # ---- 3. 阶段最大回撤（资金曲线）
    max_drawdown_pct = round(_max_dd(curve) * 100, 2) if has_capital and curve else None

    # ---- 4. 最大周度回撤（按 ISO 周聚合盈亏后的曲线）
    weekly_pnl: dict = defaultdict(float)
    for d, pnl in by_day_pnl.items():
        iso = d.isocalendar()
        weekly_pnl[f"{iso[0]}-W{iso[1]:02d}"] += pnl
    max_weekly_drawdown_pct = None
    if has_capital:
        weq = float(initial)
        wcurve = []
        for wk in sorted(weekly_pnl):
            weq += weekly_pnl[wk]
            wcurve.append((wk, weq))
        max_weekly_drawdown_pct = round(_max_dd(wcurve) * 100, 2) if wcurve else None

    # ---- 5. 年化收益率（自然日年化，供卡玛使用）
    annualized_return_pct = None
    if has_capital and days >= 1 and initial > 0:
        final_eq = curve[-1][1] if curve else float(initial)
        if final_eq > 0:
            annualized_return_pct = round(((final_eq / initial) ** (365.0 / days) - 1) * 100, 2)

    # ---- 6. 卡玛比率 = 年化收益率 / 最大回撤
    calmar_ratio = None
    if annualized_return_pct is not None and max_drawdown_pct and max_drawdown_pct > 0:
        calmar_ratio = round(annualized_return_pct / max_drawdown_pct, 3)

    # ---- 7. 夏普比率（日收益率，无风险利率取 0，年化 sqrt(252)）
    sharpe_ratio = None
    if has_capital and len(curve) >= 2:
        rets = []
        prev = None
        for _, v in curve:
            if prev is not None and prev > 0:
                rets.append((v - prev) / prev)
            prev = v
        if len(rets) >= 2:
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
            std_r = math.sqrt(var_r)
            if std_r > 0:
                sharpe_ratio = round(mean_r / std_r * math.sqrt(252), 3)

    # ---- 8. 日平均仓位（含空仓日 0%，反映阶段资金利用率）
    avg_daily_position_pct = None
    if has_capital:
        day_invested: dict = defaultdict(float)
        for t in trades:
            inv = t.invested_capital or 0
            if inv <= 0:
                continue
            d0 = max(t.entry_time.date(), start.date())
            d1 = min((t.exit_time or end).date(), end.date())
            d = d0
            while d <= d1:
                day_invested[d] += inv
                d += timedelta(days=1)
        ratios = []
        d = start.date()
        for _ in range(days):
            bal = equity_before(db, user_id, d)
            if bal and bal > 0:
                ratios.append(day_invested.get(d, 0.0) / bal)
            d += timedelta(days=1)
        if ratios:
            avg_daily_position_pct = round(sum(ratios) / len(ratios) * 100, 2)

    # ---- 期末资金 = 期初资金 + 阶段总盈亏（与 total_return_pct 分子一致）
    end_balance = round(start_balance + total_pnl, 2) if has_capital else None

    return {
        "start_balance": start_balance,
        "end_balance": end_balance,
        "avg_pl_ratio": avg_pl_ratio,
        "avg_daily_position_pct": avg_daily_position_pct,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "max_weekly_drawdown_pct": max_weekly_drawdown_pct,
        "calmar_ratio": calmar_ratio,
        "sharpe_ratio": sharpe_ratio,
        "has_capital": has_capital,
    }


def period_stats(
    db: Session,
    start: datetime,
    end: datetime,
    instrument_type: str | None = None,
    user_id: int | None = None,
) -> dict:
    """时间段统计：汇总 + 按日 + 按品种"""
    trades = _trades_query(db, start, end, instrument_type, user_id).all()

    # 按日聚合
    by_day: dict[str, list] = defaultdict(list)
    for t in trades:
        by_day[t.exit_time.date().isoformat()].append(t)

    day_rows = []
    for date, day_trades in sorted(by_day.items()):
        s = _calc_summary(day_trades)
        day_rows.append(
            {
                "date": date,
                "count": s["count"],
                "win": s["win"],
                "loss": s["loss"],
                "total_pnl": s["total_pnl"],
                "cumulative_pnl": 0,  # 稍后计算累计
            }
        )
    # 累计盈亏
    cum = 0
    for row in day_rows:
        cum += row["total_pnl"]
        row["cumulative_pnl"] = round(cum, 2)

    # 按品种聚合
    by_instrument: dict[str, list] = defaultdict(list)
    for t in trades:
        by_instrument[t.instrument_type].append(t)
    instrument_rows = {
        name: _calc_summary(items)
        for name, items in sorted(by_instrument.items())
    }

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "summary": _calc_summary(trades),
        "metrics": _calc_metrics(db, trades, start, end, user_id),
        "by_day": day_rows,
        "by_instrument": instrument_rows,
    }


def score_trend(
    db: Session,
    start: datetime,
    end: datetime,
    instrument_type: str | None = None,
    user_id: int | None = None,
) -> dict:
    """评分趋势 + 维度平均分 + 常见问题TOP"""
    trades = _trades_query(db, start, end, instrument_type, user_id).all()

    # 每笔交易最新评分
    trend = []
    # 维度聚合
    dim_scores: dict[str, list] = defaultdict(list)
    # 常见问题词频
    issue_counter: dict[str, int] = defaultdict(int)

    for t in trades:
        report = (
            db.query(ReviewReport)
            .filter(ReviewReport.trade_id == t.id)
            .order_by(ReviewReport.id.desc())
            .first()
        )
        score = report.score if report else None
        trend.append(
            {
                "trade_id": t.id,
                "date": t.exit_time.strftime("%Y-%m-%d"),
                "instrument_type": t.instrument_type,
                "instrument_name": t.instrument_name or t.instrument_code,
                "pnl": t.pnl,
                "score": score,
            }
        )

        if report and report.analysis:
            try:
                analysis = json.loads(report.analysis)
            except json.JSONDecodeError:
                analysis = {}
            for d in analysis.get("dimensions", []):
                if d.get("name") and isinstance(d.get("score"), (int, float)):
                    dim_scores[d["name"]].append(d["score"])
            for item in analysis.get("weaknesses", []) + analysis.get("improvements", []):
                for kw in COMMON_ISSUE_KEYWORDS:
                    if kw in str(item):
                        issue_counter[kw] += 1

    # 各维度平均分
    dimension_avg = sorted(
        (
            {
                "name": name,
                "score": round(sum(scores) / len(scores), 1),
                "count": len(scores),
            }
            for name, scores in dim_scores.items()
        ),
        key=lambda x: x["score"],
        reverse=True,
    )

    # 常见问题 TOP（按词频排序）
    common_issues = sorted(
        ({"keyword": kw, "count": cnt} for kw, cnt in issue_counter.items()),
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    # 综合平均评分
    scores = [p["score"] for p in trend if p["score"] is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    return {
        "avg_score": avg_score,
        "score_trend": trend,
        "dimension_avg": dimension_avg,
        "common_issues": common_issues,
    }
