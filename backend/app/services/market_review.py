# -*- coding: utf-8 -*-
"""盘面综述服务（V1.008.2 功能1） + 快速盘面概览（功能2：AI 阶段分析自动结合）

功能1：generate_market_review()
  盘面数据(程序采集) → 「数据速览」markdown(程序生成，数值可信) + AI 点评(LLM 基于速览撰写)
  → 完整报告落库 MarketReview（同 品种×起止 覆盖），前端可回看/插入手写总结。

功能2：quick_market_overview_text()
  供 phase_summary 每次 AI 阶段分析自动注入的【本阶段市场环境概览】文本（轻量，数秒完成）。
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..models import MarketReview
from . import market_data as md
from .llm import LLMError, chat


# ---------------------------------------------------------------------------
# 数据速览 → markdown（机器生成，数值精确，禁止 LLM 篡改）
# ---------------------------------------------------------------------------

def build_data_markdown(snap: dict) -> str:
    instr = snap.get("instrument_type", "")
    lines = []

    if instr == "A股":
        d = snap.get("data", {})
        # 指数
        idx = d.get("indices", [])
        if idx:
            lines.append("### 大盘指数（区间表现）")
            lines.append("")
            lines.append("| 指数 | 区间首收 | 区间末收 | 区间涨跌 | 区间振幅 |")
            lines.append("|---|---|---|---|---|")
            for it in idx:
                lines.append("| %s | %s | %s | %s | %s |" % (
                    it["name"], md._fmt_price(it["start_close"]), md._fmt_price(it["end_close"]),
                    md._fmt_pct(it["change_pct"]), md._fmt_pct(it["amplitude_pct"])))
            lines.append("")
        # 板块区间（候选）
        bi = [b for b in d.get("boards_interval", []) if b.get("change_pct") is not None]
        if bi:
            lines.append("### 阶段强势 / 弱势板块（区间涨幅，候选口径）")
            lines.append("")
            lines.append("| 板块 | 区间涨跌 | 阶段末日单日 |")
            lines.append("|---|---|---|")
            for b in sorted(bi, key=lambda x: x["change_pct"], reverse=True)[:14]:
                lines.append("| %s | %s | %s |" % (b["name"], md._fmt_pct(b["change_pct"]),
                                                   md._fmt_pct(b.get("day_pct"))))
            lines.append("")
        # 领涨个股
        st = d.get("stocks", [])
        if st:
            lines.append("### 区间领涨个股（候选池重算口径，前 12）")
            lines.append("")
            lines.append("| 个股 | 所属行业 | 区间涨幅 |")
            lines.append("|---|---|---|")
            for s in st:
                pct = s.get("interval_pct") if s.get("interval_pct") is not None else s.get("day_pct")
                lines.append("| %s(%s) | %s | %s |" % (
                    s.get("name") or "-", s.get("code") or "", s.get("industry") or "-",
                    md._fmt_pct(pct)))
            lines.append("")
        # 阶段末日板块快照
        bs = d.get("board_snapshot", [])
        up = [b for b in bs if b.get("pct") is not None and float(b["pct"]) >= 0][:8]
        dn = [b for b in bs if b.get("pct") is not None and float(b["pct"]) < 0][-6:]
        if up or dn:
            lines.append("### 阶段末日板块快照（当日口径，供参考）")
            lines.append("")
            if up:
                lines.append("当日领涨板块：" + "、".join(
                    f"{b['name']}({md._fmt_pct(b['pct'])})" for b in up))
            if dn:
                lines.append("")
                lines.append("当日领跌板块：" + "、".join(
                    f"{b['name']}({md._fmt_pct(b['pct'])})" for b in dn))
            lines.append("")

    elif instr == "商品期货":
        d = snap.get("data", {})
        sec = d.get("sectors", [])
        if sec:
            lines.append("### 期货板块表现（品种区间涨跌均值）")
            lines.append("")
            lines.append("| 板块 | 品种数 | 平均涨跌 | 代表品种 |")
            lines.append("|---|---|---|---|")
            for s in sec:
                tops = "、".join(f"{v['name']} {md._fmt_pct(v['close_pct'])}"
                                 for v in s["varieties"][:3])
                lines.append("| %s | %d | %s | %s |" % (s["sector"], s["count"],
                                                        md._fmt_pct(s["avg_pct"]), tops))
            lines.append("")
        lines.append("### 主要活跃品种（区间涨跌排序，前 20）")
        lines.append("")
        lines.append("| 品种 | 板块 | 区间涨跌(收盘) | 区间高低振幅 |")
        lines.append("|---|---|---|---|")
        vs = sorted(d.get("varieties", []), key=lambda v: v["close_pct"], reverse=True)
        for v in vs[:20]:
            lines.append("| %s | %s | %s | %s |" % (
                v["name"], v["sector"], md._fmt_pct(v["close_pct"]),
                md._fmt_pct(v.get("amp_pct"))))
        lines.append("")
        q = d.get("quotes", {})
        if q:
            lines.append("### 阶段末日（最近交易日）报价参考")
            lines.append("")
            rows = []
            for sym, v in q.items():
                if v.get("price"):
                    rows.append(f"{v['name']} {md._fmt_price(v['price'])}"
                                f"（当日 {md._fmt_pct(v.get('change_pct'))}）")
            lines.append("；".join(rows[:16]))
            lines.append("")

    elif instr == "数字货币":
        d = snap.get("data", {})
        coins = d.get("coins", [])
        lines.append("### BTC / ETH 区间表现")
        lines.append("")
        lines.append("| 币种 | 区间首价 | 区间末价 | 区间涨跌 | 区间最高/最低 | 平均日成交 |")
        lines.append("|---|---|---|---|---|---|")
        for c in coins:
            lines.append("| %s | %s | %s | %s | %s / %s | %s |" % (
                c["label"], md._fmt_price(c["start_price"]), md._fmt_price(c["end_price"]),
                md._fmt_pct(c["change_pct"]), md._fmt_price(c["high"]), md._fmt_price(c["low"]),
                md._fmt_vol(c.get("avg_vol"))))
        lines.append("")
        lines.append("注：数字货币 7×24 交易，区间按 UTC 日K对齐，与本地日期可能有 8 小时错位。")
        lines.append("")

    else:  # 通用（多市场概览，quick 口径）
        data = snap.get("data", {})
        for sub in ("A股", "商品期货", "数字货币"):
            d = data.get(sub, {})
            if sub == "A股":
                for it in d.get("indices", [])[:6]:
                    lines.append(f"- {it['name']} 区间 {md._fmt_pct(it['change_pct'])}，"
                                 f"振幅 {md._fmt_pct(it['amplitude_pct'])}")
            elif sub == "商品期货":
                for s in d.get("sectors", [])[:6]:
                    lines.append(f"- 期货[{s['sector']}] 平均 {md._fmt_pct(s['avg_pct'])}")
            else:
                for c in d.get("coins", []):
                    lines.append(f"- {c['label']} 区间 {md._fmt_pct(c['change_pct'])}")
            lines.append("")
    return "\n".join(lines).strip()


def _fmt_notes(snap: dict) -> str:
    """收集数据说明（不含 markdown 前缀，由调用方按需加 '> '）"""
    notes = snap.get("notes") or []
    # notes 可能嵌套在 data 子级
    data = snap.get("data") or {}
    for k, v in data.items():
        if isinstance(v, dict) and v.get("notes"):
            notes += v["notes"]
    uniq = []
    for n in notes:
        if n and n not in uniq:
            uniq.append(n)
    if not uniq:
        return ""
    return "数据说明：" + "；".join(uniq[:8])


# ---------------------------------------------------------------------------
# AI 盘面点评 prompt
# ---------------------------------------------------------------------------

REVIEW_PROMPT = """你是专业的A股/商品期货/数字货币市场分析师。用户是期货与股票交易者，在做阶段复盘时需要一个【{instrument} 盘面综述】作参考。

下面是从公开行情接口采集的【{period} · {instrument} 数据速览】（程序计算，数值可信，引用时不得改动）。

---数据速览开始---
{data_md}
---数据速览结束---

请基于以上数据写一份盘面点评（markdown 格式，直接输出正文，不要包裹代码块），包括以下小节：
1. 【阶段总评】2-4句话概括该阶段市场整体环境（涨跌方向、风格、情绪冷暖），{instrument} 数据缺失的部分要如实说明，不要编造。
2. 【主线与活跃方向】结合数据速览中涨幅居前的板块/品种/个股，归纳该阶段的主线与扩散逻辑（2-4 条要点）。
3. 【弱势与风险】表现垫底的方向、以及需要警惕的风险（2-3 条）。
4. 【对阶段交易复盘的意义】这段行情环境对同期交易（顺势/逆势、板块选择、品种波动放大/收窄）可能意味着什么，帮用户对照自己的交易记录找原因。

要求：观点必须能从数据速览中读出依据；不要虚构数据速览之外的行情；语言精炼、可读性强，总篇幅 600-1000 字。"""


# ---------------------------------------------------------------------------
# 功能1：生成并落库
# ---------------------------------------------------------------------------

def generate_market_review(db: Session, user_id: int, instrument_type: str,
                           start_date, end_date) -> dict:
    """采集全量盘面数据 → 数据速览 + AI 点评 → 覆盖保存 MarketReview。

    返回 {id, title, content, data_json, note, created_at, updated_at}
    """
    start = start_date.strftime("%Y-%m-%d")
    end = end_date.strftime("%Y-%m-%d")
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")

    snap = md.collect_market(instrument_type, start, end, quick=False)
    data_md = build_data_markdown(snap) or "（未采集到任何行情数据，请检查网络后重试）"
    note = _fmt_notes(snap)

    # AI 点评（失败不阻断：报告仍返回数据速览部分）
    ai_part = ""
    try:
        prompt = REVIEW_PROMPT.format(
            instrument=instrument_type or "三大市场", period=f"{start} ~ {end}", data_md=data_md)
        ai_part = chat(
            [{"role": "system", "content": "你是严谨的市场分析师，只依据给定数据写作，绝不编造。"},
             {"role": "user", "content": prompt}],
            temperature=0.5, max_tokens=3500, db=db,
        ).strip()
    except LLMError:
        ai_part = "> （AI 点评生成失败：请检查 API 设置后重新生成；上方数据速览仍可直接参考使用）"

    footer = ("> 数据源：A股指数/个股=腾讯日K；板块快照=东财；板块区间=东财(尽力而为)；"
              "期货=新浪主连日K；数字货币=Gate.io。")
    if note:
        footer += "\n> " + note.replace("\n", "；")

    title = f"{end[:4]}年{int(end[5:7])}月" if end[:6] != start[:6] else f"{start[:4]}年{int(start[5:7])}月"
    if end[:6] != start[:6]:
        title = f"{start[:4]}.{int(start[5:7])} ~ {end[:4]}.{int(end[5:7])}"
    title += f" · {instrument_type or '三大市场'} 盘面综述"

    content = "\n".join([
        f"# {title}",
        "",
        f"> 数据采集时间：{snap.get('collected_at', '')}；"
        f"范围：{start} ~ {end}；耗时 {snap.get('elapsed_sec', '-')} 秒",
        "",
        "## 一、行情数据速览（程序采集计算）",
        "",
        data_md,
        "",
        "## 二、AI 盘面点评",
        "",
        ai_part,
        "",
        "---",
        "",
        footer,
    ]).strip()

    # upsert（user + instrument + start + end 唯一）
    cur = (db.query(MarketReview)
           .filter(MarketReview.user_id == user_id,
                   MarketReview.instrument_type == instrument_type,
                   MarketReview.start_date == start_date,
                   MarketReview.end_date == end_date)
           .first())
    if cur:
        cur.title = title
        cur.content = content
        cur.data_json = json.dumps(snap, ensure_ascii=False)
        cur.note = note.strip()
    else:
        cur = MarketReview(
            user_id=user_id, instrument_type=instrument_type,
            start_date=start_date, end_date=end_date,
            title=title, content=content,
            data_json=json.dumps(snap, ensure_ascii=False),
            note=note.strip(),
        )
        db.add(cur)
    db.commit()
    db.refresh(cur)
    return _serialize(cur)


def _serialize(r: MarketReview) -> dict:
    return {
        "id": r.id,
        "instrument_type": r.instrument_type,
        "start": r.start_date.isoformat(),
        "end": r.end_date.isoformat(),
        "title": r.title,
        "content": r.content,
        "data_json": r.data_json,  # 结构化快照（前端可解析）
        "note": r.note,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else "",
    }


# ---------------------------------------------------------------------------
# 功能2：AI 阶段分析注入的轻量盘面概览
# ---------------------------------------------------------------------------

def quick_market_overview_text(user_id: int, start, end, instrument_type: str | None) -> str:
    """采集轻量盘面概览文本（数秒），供 AI 阶段分析注入 prompt。

    失败时返回空串（调用方静默降级，不影响原交易分析流程）。
    """
    try:
        s = start.strftime("%Y-%m-%d")
        e = end.strftime("%Y-%m-%d")
        instr = instrument_type or "通用"
        snap = md.collect_market(instr, s, e, quick=True)
        md_text = build_data_markdown(snap)
        if not md_text:
            return ""
        notes = _fmt_notes(snap)
        return (
            "【本阶段市场环境（盘面）概览】以下行情数据由系统在分析时实时联网采集"
            f"（{snap.get('collected_at', '')}），仅作解读交易的环境参照：\n"
            + md_text
            + (notes + "\n" if notes else "")
            + "要求：结合上述市场环境解读本阶段交易（如方向是否顺势、板块/品种选择、"
              "波动环境对止损的影响），写入 market_context / market_insights 字段。\n"
        )
    except Exception:
        return ""
