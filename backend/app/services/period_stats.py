"""阶段性复盘统计服务：按时间段聚合交易与复盘数据"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import ReviewReport, Trade

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
