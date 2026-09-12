# -*- coding: utf-8 -*-
"""A股每日复盘 —— 扩展数据模块（V1.009.2）

四类数据，全部有明确数据源与可复核口径：

1. **成交额环比** `amount_change`
   今日两市成交额 vs 上一交易日（同源同口径：优先取快照缓存，回退个股日K聚合）

2. **股票热度榜** `hot_list` / `hot_list_history`
   实时（最近交易日）：同花顺热榜（dq.10jqka.com.cn，含热度值/排名变化/概念标签/上榜原因）
   + 东财人气榜（emappdata.eastmoney.com，含人气排名与上次排名）。
   历史日：**榜单视角无历史源，但个股视角有** —— 东财人气榜的
   `stockrank/getHisList` 按个股返回逐日全市场人气排名（实测窗口约 120 个自然日）。
   于是对「成交额大 或 单日波动大」的候选股各取一次序列落库（实测 0.016 秒/只、
   8 并发），即可在本地组装任意历史日的人气榜，名次与排名变化均为真实历史数据。
   代价：同花顺的「热度值」「上榜原因」只在实时榜有，历史榜单里留空。

3. **严重异动提醒** `abnormal_watch`
   自建口径：偏离值 = 个股区间累计涨跌幅 − 对应基准指数区间累计涨跌幅，
   按 3 / 10 / 30 日窗口滚动累计，对照交易所异常波动与严重异常波动标准出提醒。
   完全由个股日K回溯计算，**历史日期同样可算**

4. **游资动向** `youzi_flow`
   东财龙虎榜买卖席位明细（RPT_BILLBOARD_DAILYDETAILSBUY/SELL），
   按营业部聚合买卖金额与个股，并识别知名游资常用席位别名。
   龙虎榜历史数据可回溯（实测 2026-03-10 仍可查）
"""

from __future__ import annotations

import json
import time
import traceback
import urllib.parse
import urllib.request

from app.services import market_data as md


def _dm():
    """惰性引用 daily_market（避免与它形成模块级循环导入）

    daily_market 在函数内 `from app.services import daily_extra as _dx`，
    如果这里也模块级反向导入，会在 import 次序不同时炸掉；函数内 import 只查
    sys.modules，开销可忽略。用于统一涨跌停判定口径（limit_state）。
    """
    from app.services import daily_market as _m
    return _m

# ---------------------------------------------------------------------------
# 基础请求（datacenter-web / emappdata 只支持 POST 或需独立域，这里自建工具）
# ---------------------------------------------------------------------------

_EM_H = lambda: dict(md.EM_HEADERS)  # noqa: E731


def _get(url: str, headers=None, timeout: float = 15, retries: int = 1) -> str | None:
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or md.EM_HEADERS)
            with md._DIRECT_OPENER.open(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            if i < retries:
                time.sleep(0.6)
    return None


def _post_json(url: str, body: dict, headers=None, timeout: float = 15,
               retries: int = 1) -> str | None:
    data = json.dumps(body, ensure_ascii=False).encode()
    h = _EM_H()
    h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with md._DIRECT_OPENER.open(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            if i < retries:
                time.sleep(0.6)
    return None


def _dc(report: str, filt: str, page_size: int = 500, page: int = 1,
        sort_col: str | None = None, sort_type: str = "-1",
        timeout: float = 15) -> list[dict]:
    """东财数据中心通用报表查询 → 行列表（失败返回空列表）"""
    p = {
        "reportName": report, "columns": "ALL", "filter": filt,
        "pageNumber": page, "pageSize": page_size, "source": "WEB", "client": "WEB",
    }
    if sort_col:
        p["sortColumns"] = sort_col
        p["sortTypes"] = sort_type
    txt = _get("https://datacenter-web.eastmoney.com/api/data/v1/get?"
               + urllib.parse.urlencode(p), timeout=timeout)
    if not txt:
        return []
    try:
        j = json.loads(txt)
    except Exception:  # noqa: BLE001
        return []
    return list(((j or {}).get("result") or {}).get("data") or [])


def _f(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


# ---------------------------------------------------------------------------
# 1) 两市成交额环比
# ---------------------------------------------------------------------------

def amount_change(today_amt, prev_amt, prev_date: str = "",
                  today_source: str = "", prev_source: str = "") -> dict:
    """两市成交额较上一交易日的增减

    两个数必须同口径才有意义（都用精确成交额或都用估算值），
    因此调用方应保证 today/prev 来自同一来源链路。
    """
    t = _f(today_amt)
    p = _f(prev_amt)
    out = {
        "today": t, "prev": p, "prev_date": prev_date or "",
        "delta": None, "delta_pct": None,
        "today_source": today_source, "prev_source": prev_source,
        "available": bool(t and p and p > 0),
    }
    if out["available"]:
        out["delta"] = t - p
        out["delta_pct"] = round((t - p) / p * 100.0, 2)
    return out


def turnover_from_klines(klines: dict, date_str: str) -> float | None:
    """由个股日K缓存聚合某日全市场成交额（仅统计当日有K线的个股）"""
    if not klines:
        return None
    total = 0.0
    hit = 0
    for _code, bars in klines.items():
        for b in bars:
            if b[0] == date_str:
                amt = b[3] if len(b) > 3 else None
                if isinstance(amt, (int, float)) and amt > 0:
                    total += float(amt)
                else:
                    total += float(b[1]) * float(b[2] or 0)
                hit += 1
                break
    return total if hit else None


# ---------------------------------------------------------------------------
# 2) 股票热度榜
# ---------------------------------------------------------------------------

_THS_HOT = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
_EM_RANK = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
_EM_GID = "786e4c21-70dc-435a-93bb-38"
_THS_HEADERS = {"User-Agent": md.UA, "Referer": "https://eq.10jqka.com.cn/"}


def hot_rank_ths(size: int = 100) -> list[dict]:
    """同花顺热榜 → [{code,name,pct,rank,rate,rank_chg,concepts,tag,reason,topic}]

    rate 为热度值（越大越热），order 为榜单排名，hot_rank_chg 为排名变化。
    """
    txt = _get("%s?stock_type=a&type=hour&list_type=normal" % _THS_HOT,
               headers=_THS_HEADERS, timeout=12)
    if not txt:
        return []
    try:
        j = json.loads(txt)
    except Exception:  # noqa: BLE001
        return []
    rows = ((j or {}).get("data") or {}).get("stock_list") or []
    out = []
    for r in rows[:size]:
        tag = r.get("tag") or {}
        try:
            rate = float(r.get("rate") or 0)
        except Exception:  # noqa: BLE001
            rate = 0.0
        out.append({
            "code": str(r.get("code") or ""),
            "name": (r.get("name") or "").strip(),
            "pct": _f(r.get("rise_and_fall")),
            "rank": int(r.get("order") or 0),
            "rate": round(rate, 1),
            "rank_chg": r.get("hot_rank_chg"),
            "concepts": list(tag.get("concept_tag") or []),
            "tag": (tag.get("popularity_tag") or "") or "",
            "reason": (r.get("analyse") or "").strip(),
            "reason_title": (r.get("analyse_title") or "").strip(),
            "topic": r.get("topic"),
        })
    return out


def hot_rank_em(size: int = 100) -> list[dict]:
    """东财人气榜 → [{code, rank, prev_rank, rank_chg}]

    接口只返回代码与排名（无名称/涨跌幅），名称需由调用方补齐。
    实测 pageSize 上限 100。
    """
    txt = _post_json(_EM_RANK, {
        "appId": "appId01", "globalId": _EM_GID, "marketType": "",
        "pageNo": 1, "pageSize": min(100, size),
    }, timeout=12)
    if not txt:
        return []
    try:
        j = json.loads(txt)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in (j.get("data") or []):
        sc = str(r.get("sc") or "")            # 形如 SZ000636 / SH603936
        code = sc[2:] if len(sc) > 2 else sc
        rk = r.get("rk")
        prev = r.get("hisRc")
        chg = None
        if isinstance(rk, int) and isinstance(prev, int) and prev > 0:
            chg = prev - rk                     # 正数 = 排名上升
        out.append({"code": code, "rank": rk, "prev_rank": prev, "rank_chg": chg})
    return out


# --- 历史人气榜：绕开"榜单视角无历史"的关键 ---------------------------------

_EM_RANK_HIS = "https://emappdata.eastmoney.com/stockrank/getHisList"


def _em_sc(code: str) -> str:
    """6 位代码 → 东财人气榜的证券编码（SZ/SH 前缀）

    实测（2026-09-12 标定）：
      - 沪市 6xx/688/689 → SH + code
      - 深市 0xx/3xx    → SZ + code
      - **北交所 920/43/83/87 段走 `SZ` 前缀**（`SZ920819` 有 120 条序列；
        `BJ920819` / `SH920819` 均返回空），这是该接口与 push2 的 secid 规则不同之处
    """
    c = (code or "").strip()
    if not c:
        return ""
    return ("SH" if c.startswith("6") else "SZ") + c


def hot_rank_hist_one(code: str, retries: int = 2) -> dict:
    """单只个股的逐日人气排名 → {date: rank}（失败或空返回 {}）

    东财人气榜「个股视角」接口。返回的 rank 是**全市场名次**（实测样本范围 1~5419），
    日频，窗口约 120 个自然日。命中不了（无前缀、已退市代码等）时返回空。
    """
    sc = _em_sc(code)
    if not sc:
        return {}
    txt = _post_json(_EM_RANK_HIS, {
        "appId": "appId01", "globalId": _EM_GID, "marketType": "",
        "srcSecurityCode": sc,
    }, timeout=12, retries=retries)
    if not txt:
        return {}
    try:
        j = json.loads(txt)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, int] = {}
    for r in (j.get("data") or []):
        d = r.get("calcTime")
        rk = r.get("rank")
        if not d or not isinstance(rk, int) or rk <= 0:
            continue
        out[str(d)[:10]] = rk
    return out


def hot_rank_hist(codes: list[str], workers: int = 8,
                  on_progress=None) -> dict:
    """并发抓多只个股的人气排名序列 → {code: {date: rank}}

    实测成本：0.016 秒/只（8 并发），3000 只约 48 秒。
    """
    import concurrent.futures as _cf

    out: dict = {}
    if not codes:
        return out
    done = 0
    total = len(codes)
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(hot_rank_hist_one, c): c for c in codes}
        for fu in _cf.as_completed(futs):
            c = futs[fu]
            try:
                s = fu.result()
            except Exception:  # noqa: BLE001
                s = {}
            if s:
                out[c] = s
            done += 1
            if on_progress and (done % 50 == 0 or done == total):
                try:
                    on_progress(done, total)
                except Exception:  # noqa: BLE001
                    pass
    return out


_HOT_AMT_TOP = 500      # 每个交易日按成交额取前 N 只入候选
_HOT_PCT_ABS = 7.0      # 单日涨跌幅绝对值 ≥ 此值入候选


def hot_candidates(klines: dict, dates: list[str], top_amt: int = _HOT_AMT_TOP,
                   pct_abs: float = _HOT_PCT_ABS) -> list[str]:
    """构造「可能出现在人气榜头部」的候选股集合

    人气榜没有全市场按日榜单接口，只能逐只反查 —— 因此候选集必须尽量小、又不漏。
    热门股的两个必要特征：**成交额大** 或 **单日波动大**。故取并集：
      ① 每个交易日成交额前 `top_amt` 只
      ② 每个交易日 |涨跌幅| ≥ `pct_abs` 的（涨跌停 / 接近涨跌停 / 异动）

    实测：60 个交易日窗口下候选集约 2000~3000 只，可覆盖各日人气榜前 100 的绝大多数。
    涨跌幅由相邻两根前复权收盘算出，故需按**全部**日K逐根推进（窗口外的也要走一遍，
    否则窗口首日的前收会取错）。
    """
    if not klines or not dates:
        return []
    want = set(dates)
    kept: set[str] = set()
    day_amt: dict[str, list] = {d: [] for d in dates}
    for code, bars in klines.items():
        prev_c = None
        for b in bars:
            d, c = b[0], b[1]
            if d in want:
                if prev_c and prev_c > 0 and c:
                    p = (float(c) - float(prev_c)) / float(prev_c) * 100.0
                    if abs(p) >= pct_abs:
                        kept.add(code)
                amt = b[3] if len(b) > 3 and isinstance(b[3], (int, float)) and b[3] else None
                if amt is None:
                    amt = float(c or 0) * float(b[2] or 0)
                day_amt[d].append((float(amt or 0), code))
            prev_c = c or prev_c
    for d, lst in day_amt.items():
        if not lst:
            continue
        lst.sort(key=lambda x: -x[0])
        for _a, c in lst[:top_amt]:
            kept.add(c)
    return sorted(kept)


def hot_list_history(series: dict, date_str: str, name_map: dict | None = None,
                     klines: dict | None = None, prev_date: str | None = None,
                     concepts_map: dict | None = None, size: int = 50) -> dict:
    """由本地人气排名序列组装**历史某个交易日**的人气榜

    series：{code: {date: rank}}（来自 StockHotRankCache）
    klines：用于补当日涨跌幅（榜单自身不含行情）
    prev_date：上一交易日，用于算排名变化（正数 = 排名上升）
    concepts_map：{code: [概念名]}，由板块成分股缓存反查

    返回结构与实时热榜 `hot_list()` 同构，便于前端与报告复用同一套渲染。
    与实时榜的差异（已知且不可消除）：无同花顺「热度值」与「上榜原因」——
    这两个字段只在实时的同花顺热榜里有，任何历史源都不提供。
    """
    names = name_map or {}
    cmap = concepts_map or {}
    rows: list[tuple[int, str]] = []
    for code, s in series.items():
        rk = s.get(date_str)
        if rk:
            rows.append((int(rk), code))
    if not rows:
        return {"available": False,
                "note": "本地人气排名缓存中没有该日数据（候选集未覆盖或尚未抓取）"}
    rows.sort()

    def _amt_pct(code: str) -> tuple:
        """当日成交额与涨跌幅（用于在榜内补充行情列）"""
        if not klines:
            return 0.0, None
        bars = klines.get(code)
        if not bars:
            return 0.0, None
        for i, b in enumerate(bars):
            if b[0] != date_str:
                continue
            amt = b[3] if len(b) > 3 and isinstance(b[3], (int, float)) and b[3] else None
            if amt is None:
                amt = float(b[1] or 0) * float(b[2] or 0)
            pct = None
            if i > 0:
                p0 = bars[i - 1][1]
                if p0 and b[1]:
                    pct = (float(b[1]) - float(p0)) / float(p0) * 100.0
            return float(amt or 0), pct
        return 0.0, None

    items: list[dict] = []
    for rk, code in rows[:max(size, 50)]:
        _amt, pct = _amt_pct(code)
        prev_rk = (series.get(code) or {}).get(prev_date) if prev_date else None
        chg = None
        if isinstance(prev_rk, int) and prev_rk > 0:
            chg = prev_rk - rk            # 正数 = 排名上升
        items.append({
            "code": code, "name": names.get(code, ""),
            "pct": None if pct is None else round(pct, 2),
            "rank": rk, "rate": None, "rank_chg": chg,
            "concepts": list(cmap.get(code) or [])[:6],
            "tag": "", "reason": "", "reason_title": "", "topic": None,
            "em_rank": rk, "em_rank_chg": chg,
        })
    items = items[:size]

    cc: dict[str, int] = {}
    for r in items:
        for c in (r.get("concepts") or []):
            cc[c] = cc.get(c, 0) + 1
    top_concepts = sorted(({"name": k, "count": v} for k, v in cc.items()),
                          key=lambda x: -x["count"])[:12]

    return {
        "available": True,
        "source": "东财人气榜（个股历史排名回溯）",
        "source_ths": False, "source_em": True,
        "count": len(items),
        "items": items,
        "top_concepts": top_concepts,
        "ranked_pool": len(rows),
        "cached_stocks": len(series),
        "note": ("历史日热度榜由东财人气榜的**个股逐日排名**回溯组装：对候选股逐只取完整"
                 "排名序列（实测窗口约 120 个自然日），再按当日名次排序取前 %d。"
                 "候选集 = 各日成交额前 %d ∪ 单日涨跌幅绝对值 ≥ %.0f%%，"
                 "共覆盖本地缓存的 %d 只个股、该日有排名者 %d 只。"
                 "**名次与排名变化为真实历史数据**；「热度值」与「上榜原因」仅同花顺实时"
                 "热榜提供、任何历史源都没有，故留空；「热门概念」由板块成分股映射反查，"
                 "为当前成分口径（概念标签本身是慢变量，影响有限）。"
                 % (len(items), _HOT_AMT_TOP, _HOT_PCT_ABS, len(series), len(rows))),
    }


def hot_fill_concepts(hot: dict, concepts_map: dict) -> None:
    """就地补齐热度榜条目的「热门概念」，并重算概念分布

    用途：重建流程里概念映射（板块成分股缓存）要等板块抓取完成后才可用，晚于逐日组装，
    故在重建收尾时统一补挂。`concepts_map` 为 {代码: [概念名]}。
    """
    if not isinstance(hot, dict) or not hot.get("available") or not concepts_map:
        return
    miss = 0
    for it in (hot.get("items") or []):
        c = it.get("code")
        if not c:
            continue
        lst = concepts_map.get(c) or []
        if not it.get("concepts"):
            it["concepts"] = list(lst)[:6]
            if not lst:
                miss += 1
    cc: dict[str, int] = {}
    for it in (hot.get("items") or []):
        for c in (it.get("concepts") or []):
            cc[c] = cc.get(c, 0) + 1
    hot["top_concepts"] = sorted(({"name": k, "count": v} for k, v in cc.items()),
                                 key=lambda x: -x["count"])[:12]
    if miss and hot.get("note"):
        hot["note"] = hot["note"] + "（该日榜单中有 %d 只未匹配到概念标签）" % miss


def hot_list(size: int = 50, name_map: dict | None = None) -> dict:
    """股票热度榜（主榜 = 同花顺热榜，附东财人气排名）

    行情/名称优先用榜单自带字段；东财人气榜只有代码，用 name_map 补名。
    仅用于「最近交易日」的实盘抓取；历史日期请用 `hot_list_history()`。
    """
    ths = hot_rank_ths(100)
    em = hot_rank_em(100)
    if not ths and not em:
        return {"available": False, "note": "热度榜数据源均不可用（同花顺热榜 / 东财人气榜）"}

    em_map = {x["code"]: x for x in em}
    items: list[dict] = []
    for r in ths:
        e = em_map.get(r["code"]) or {}
        r = dict(r)
        r["em_rank"] = e.get("rank")
        r["em_rank_chg"] = e.get("rank_chg")
        items.append(r)
    # 东财人气榜独有的票（不在同花顺热榜里）追加在后
    seen = {x["code"] for x in items}
    for e in em:
        if e["code"] in seen:
            continue
        items.append({
            "code": e["code"], "name": (name_map or {}).get(e["code"], ""),
            "pct": None, "rank": None, "rate": None, "rank_chg": None,
            "concepts": [], "tag": "", "reason": "", "reason_title": "",
            "topic": None, "em_rank": e.get("rank"), "em_rank_chg": e.get("rank_chg"),
        })
    items = items[:size]

    # 热榜概念分布（谁在榜单里扎堆 = 当下人气所在）
    cc: dict[str, int] = {}
    for r in items:
        for c in (r.get("concepts") or []):
            cc[c] = cc.get(c, 0) + 1
    top_concepts = sorted(({"name": k, "count": v} for k, v in cc.items()),
                          key=lambda x: -x["count"])[:12]

    return {
        "available": True,
        "source": "同花顺热榜 + 东财人气榜",
        "source_ths": bool(ths), "source_em": bool(em),
        "count": len(items),
        "items": items,
        "top_concepts": top_concepts,
        "note": ("热度榜两个数据源均只提供「当前」榜单，历史日期无榜单数据可回溯；"
                 "同花顺的「热榜排名」按小时更新，东财人气榜为实时人气排名。"),
    }


# ---------------------------------------------------------------------------
# 3) 严重异动提醒
# ---------------------------------------------------------------------------

# 基准指数（个股按板块归属，取对应宽基作为"大盘"基准，用于计算偏离值）
def _base_index(code: str) -> str:
    if code.startswith(("300", "301")):
        return "创业板指"
    if code.startswith(("688", "689")):
        return "科创50"
    if code.startswith(("8", "4", "920")):
        return "北证50"
    if code.startswith("6"):
        return "上证指数"
    return "深证成指"


# 偏离值阈值（参考沪深交易所异常波动 / 严重异常波动认定标准；仅作程序化风险提示）
_ABN_LIMITS = {
    "chinext": {"d3": 30.0, "d10": 150.0, "d30": 300.0},   # 创业板 / 科创板
    "bj": {"d3": 40.0, "d10": 150.0, "d30": 300.0},         # 北交所
    "st": {"d3": 15.0, "d10": 100.0, "d30": 200.0},         # 主板 ST
    "main": {"d3": 20.0, "d10": 100.0, "d30": 200.0},       # 主板
}


def _abn_limits(code: str, name: str) -> dict:
    if code.startswith(("300", "301", "688", "689")):
        return _ABN_LIMITS["chinext"]
    if code.startswith(("8", "4", "920")):
        return _ABN_LIMITS["bj"]
    if "ST" in (name or "").upper():
        return _ABN_LIMITS["st"]
    return _ABN_LIMITS["main"]


def is_st_stock(name: str) -> bool:
    """是否风险警示股（ST / *ST / SST / S*ST）

    A股名称里的风险警示标记有四种写法，统一规则：先去掉一个前导 `S`（仅当它不是 `ST` 的开头），
    再吃掉开头的 `*`，最后看是否以 `ST` 开头。

    名称缺失时返回 False —— 宁可不剔也不能误剔（缺名多为北交所或次新股，本身不是 ST）。
    """
    s = (name or "").replace(" ", "").replace("\u3000", "").upper()
    if s.startswith("S") and not s.startswith("ST"):
        s = s[1:]
    s = s.lstrip("*")
    return s.startswith("ST")


def abnormal_watch(klines: dict, date_str: str, names: dict | None = None,
                   index_pct: dict | None = None, top_n: int = 40,
                   up_only: bool = False, lhb_map: dict | None = None) -> dict:
    """严重异动提醒（偏离值口径，可回溯历史）

    偏离值定义：个股区间累计涨跌幅 − 对应基准指数区间累计涨跌幅，
    按 3 / 10 / 30 个交易日窗口滚动累计，窗口的最后一日为 date_str。

    index_pct: {指数名: {日期: 涨跌幅%}}；缺失时仅用个股涨跌幅（并从 note 中说明）。
    lhb_map  : {code: {name, explanation}} 龙虎榜日榜明细，用于补充"交易所认定的异动原因"
               并补召回"连续三个交易日偏离值累计达到 20%"这类被交易所点名的标的。
    返回 {"available","date","items":[...],"counts","basis","note"}
    """
    names = names or {}
    ip = index_pct or {}
    lp = lhb_map or {}
    _dmod = _dm()          # 统一涨跌停/连板判定口径
    # 龙虎榜上榜原因中真正属于"异常波动"的表述（日涨跌幅偏离前5名等属于日常上榜，不算异动）
    _LHB_ABN_KEYS = ("连续三个交易日", "异常波动", "严重异常波动")
    out: list[dict] = []
    seen: set[str] = set()

    for code, bars in (klines or {}).items():
        if not bars:
            continue
        idx = None
        for i, b in enumerate(bars):
            if b[0] == date_str:
                idx = i
                break
        if idx is None or idx < 1:
            continue
        nm = names.get(code, "")
        # 风险警示股（ST / *ST）不纳入异动提醒：主板 ST 日涨跌幅仅 5%，其偏离值
        # 与 3/10/30 日阈值都和正常股不可比，混进榜单只会稀释信号。
        if is_st_stock(nm):
            continue
        base = _base_index(code)
        ser = (ip.get(base) or {})
        lim = _abn_limits(code, nm)

        # 逐日涨跌幅（个股 / 基准指数）
        dev_seq: list[float] = []          # 每日偏离值（%）
        pct_seq: list[float] = []
        for i in range(1, idx + 1):
            d0, c0 = bars[i - 1][0], bars[i - 1][1]
            d1, c1 = bars[i][0], bars[i][1]
            if not c0:
                dev_seq.append(0.0)
                pct_seq.append(0.0)
                continue
            sp = (c1 - c0) / c0 * 100.0
            xp = ser.get(d1)
            dev_seq.append(sp - (xp if isinstance(xp, (int, float)) else 0.0))
            pct_seq.append(sp)
        if not dev_seq:
            continue

        def _sum(n: int) -> float | None:
            if len(dev_seq) < n:
                return None
            return round(sum(dev_seq[-n:]), 2)

        d3, d10, d30 = _sum(3), _sum(10), _sum(30)
        chg3 = round(sum(pct_seq[-3:]), 2) if len(pct_seq) >= 3 else None
        chg10 = round(sum(pct_seq[-10:]), 2) if len(pct_seq) >= 10 else None
        chg30 = round(sum(pct_seq[-30:]), 2) if len(pct_seq) >= 30 else None

        # 连续涨停天数（回溯到 date_str 为止）
        # 用 dm.limit_state 而非 _limit_price(qfq前收)：涨跌停基数必须是除权参考价，
        # 前复权前收在除权日之前会被整体缩水，会漏计连板（与全市场重建同口径）。
        lbc = 0
        for i in range(idx, 0, -1):
            _fl, _z1, _z2 = _dmod.limit_state(bars[i - 1], bars[i], code, nm)
            if _fl == 1:
                lbc += 1
            else:
                break

        # 龙虎榜中被交易所点名的异动原因
        lhb = lp.get(code) or {}
        lhb_exp = (lhb.get("explanation") or "")
        lhb_abn = lhb_exp if any(k in lhb_exp for k in _LHB_ABN_KEYS) else ""

        level, reasons = "", []
        if d10 is not None and d10 >= lim["d10"]:
            level = "严重异动"
            reasons.append("10日累计偏离 %+.1f%%（阈值 +%.0f%%）" % (d10, lim["d10"]))
        if d30 is not None and d30 >= lim["d30"]:
            level = "严重异动"
            reasons.append("30日累计偏离 %+.1f%%（阈值 +%.0f%%）" % (d30, lim["d30"]))
        if not level and d3 is not None and d3 >= lim["d3"]:
            level = "异常波动(向上)"
            reasons.append("3日累计偏离 %+.1f%%（阈值 +%.0f%%）" % (d3, lim["d3"]))
        if not level and not up_only and d3 is not None and d3 <= -lim["d3"]:
            level = "异常波动(向下)"
            reasons.append("3日累计偏离 %+.1f%%（阈值 -%.0f%%）" % (d3, lim["d3"]))
        if lbc >= (3 if "ST" in nm.upper() else 5):
            if not level:
                level = "连板风险"
            reasons.append("连续 %d 个涨停" % lbc)
        if not level and lhb_abn:
            level = "龙虎榜异动"
            reasons.append("龙虎榜：%s" % lhb_abn)
        if not level:
            continue

        cur = bars[idx][1]
        pre = bars[idx - 1][1]
        seen.add(code)
        out.append({
            "code": code, "name": nm, "close": round(cur, 2),
            "pct": round((cur - pre) / pre * 100.0, 2) if pre else None,
            "lbc": lbc, "level": level, "reasons": reasons,
            "dev3": d3, "dev10": d10, "dev30": d30,
            "chg3": chg3, "chg10": chg10, "chg30": chg30,
            "base": base, "limit": lim, "lhb": lhb_abn or "",
            "lhb_net": lhb.get("net"),
        })

    # 补召回：被龙虎榜点名异动、但偏离值未达阈值的标的（同样剔除 ST / *ST）
    for code, r in lp.items():
        if code in seen:
            continue
        if is_st_stock(r.get("name") or ""):
            continue
        exp = (r.get("explanation") or "")
        if not any(k in exp for k in _LHB_ABN_KEYS):
            continue
        out.append({
            "code": code, "name": r.get("name") or "", "close": r.get("close"),
            "pct": r.get("pct"), "lbc": 0, "level": "龙虎榜异动",
            "reasons": ["龙虎榜：%s" % exp],
            "dev3": None, "dev10": None, "dev30": None,
            "chg3": None, "chg10": None, "chg30": None,
            "base": _base_index(code), "limit": _abn_limits(code, r.get("name") or ""),
            "lhb": exp, "lhb_net": r.get("net"),
        })

    rank = {"严重异动": 0, "异常波动(向上)": 1, "龙虎榜异动": 2,
            "连板风险": 3, "异常波动(向下)": 4}
    out.sort(key=lambda x: (rank.get(x["level"], 9),
                            -(x["dev10"] if x["dev10"] is not None else -999)))
    counts: dict[str, int] = {}
    for x in out:
        counts[x["level"]] = counts.get(x["level"], 0) + 1

    has_idx = any(bool(v) for v in ip.values())
    note = ("偏离值口径：个股区间累计涨跌幅 − 对应基准指数区间累计涨跌幅"
            "（沪主板→上证指数、深主板→深证成指、创业板→创业板指、科创板→科创50、北交所→北证50），"
            "窗口为连续 3 / 10 / 30 个交易日。阈值参考交易所异常波动/严重异常波动认定标准，"
            "仅作风险提示，不等同交易所正式认定；「龙虎榜异动」为交易所龙虎榜上榜原因中"
            "明确出现连续交易日偏离或异常波动表述的标的。已剔除 ST / *ST 风险警示股。")
    if not has_idx:
        note += " 本次未取到基准指数数据，偏离值退化为个股区间涨跌幅，结果偏保守。"
    return {
        # ok    = 检测是否成功执行完（含「执行成功但当日无标的」）
        # available = 是否有结果可展示。两者分开：报告里的 ✓/✗ 要看 ok，
        # 否则「当天确实没有异动股」会被误读成「检测失败」。
        "ok": True,
        "available": len(out) > 0, "date": date_str,
        "items": out[:top_n], "counts": counts, "total": len(out),
        "index_ok": has_idx, "basis": "偏离值(3/10/30日窗口)", "note": note,
    }


# ---------------------------------------------------------------------------
# 4) 游资动向（龙虎榜营业部席位）
# ---------------------------------------------------------------------------

# 非游资席位（机构 / 北向 / 外资券商 / 社保），单独归类，不参与游资排行
_INST_KEYWORDS = ("机构专用", "深股通专用", "沪股通专用", "北向", "QFII", "RQFII",
                  "全国社保", "社保基金", "养老金", "企业年金",
                  "高盛", "摩根", "瑞银", "野村", "汇丰", "花旗", "德意志", "巴克莱",
                  "渣打", "星展", "大和", "麦格理", "法国巴黎", "瑞士信贷", "美林")


def is_inst_seat(name: str) -> bool:
    nm = name or ""
    return any(k in nm for k in _INST_KEYWORDS)


# 知名游资常用席位别名：(关键字组, 别名) —— 关键字需**全部命中**该席位名称
# 采用多关键字是为避免"上海分公司"这类泛化后缀误命中不同券商的席位。
# 别名依公开市场资料整理，仅供识别参考，席位归属可能随时间变化，
# 因此前端同时展示未做别名判断的原始营业部名称与金额。
_YOUZI_ALIAS: list[tuple[tuple[str, ...], str]] = [
    (("拉萨团结路",), "东财拉萨（散户/量化集散）"),
    (("拉萨东环路",), "东财拉萨（散户/量化集散）"),
    (("拉萨金珠西路",), "东财拉萨（散户/量化集散）"),
    (("拉萨金融城南环路",), "东财拉萨（散户/量化集散）"),
    (("拉萨北京东路",), "东财拉萨（散户/量化集散）"),
    (("拉萨江苏路",), "东财拉萨（散户/量化集散）"),
    (("华鑫", "上海分公司"), "上海超短帮"),
    (("上海江苏路",), "章盟主"),
    (("上海溧阳路",), "炒股养家"),
    (("南京太平南路",), "作手新一"),
    (("银河", "绍兴"), "赵老哥"),
    (("杭州上塘路",), "杭州帮"),
    (("杭州四季路",), "杭州帮"),
    (("杭州体育场路",), "杭州帮"),
    (("深圳益田路荣超商务中心",), "深圳帮"),
    (("深圳金田路",), "深圳金田路"),
    (("深圳欢乐海岸",), "欢乐海岸"),
    (("佛山季华六路",), "佛山系"),
    (("佛山季华五路",), "佛山系"),
    (("宁波解放南路",), "宁波桑田路"),
    (("宁波大庆南路",), "宁波桑田路"),
    (("宁波桑田路",), "宁波桑田路"),
    (("厦门厦禾路",), "炒新一族"),
    (("成都北一环路",), "成都系"),
    (("成都蜀金路",), "成都系"),
    (("淄博",), "山东帮"),
    (("温州",), "温州帮"),
]


def youzi_alias(seat_name: str) -> str:
    """席位名称 → 知名游资别名（未命中或为机构席位返回空串）"""
    nm = seat_name or ""
    if is_inst_seat(nm):
        return ""
    for kws, alias in _YOUZI_ALIAS:
        if all(k in nm for k in kws):
            return alias
    return ""


def lhb_daily(date_str: str) -> dict:
    """龙虎榜日榜明细 → {code: {name, pct, close, net, explanation, ratio}}

    SECURITY_NAME_ABBR 只有这张表有（席位明细表不含名称），故需单独拉一次。
    """
    rows = _dc("RPT_DAILYBILLBOARD_DETAILSNEW",
               "(TRADE_DATE='%s')" % date_str, page_size=1000,
               sort_col="BILLBOARD_NET_AMT")
    out: dict = {}
    for r in rows:
        c = str(r.get("SECURITY_CODE") or "")
        if not c:
            continue
        out[c] = {
            "code": c,
            "name": (r.get("SECURITY_NAME_ABBR") or "").strip(),
            "pct": _f(r.get("CHANGE_RATE")),
            "close": _f(r.get("CLOSE_PRICE")),
            "net": _f(r.get("BILLBOARD_NET_AMT")),
            "buy": _f(r.get("BILLBOARD_BUY_AMT")),
            "sell": _f(r.get("BILLBOARD_SELL_AMT")),
            "explanation": (r.get("EXPLANATION") or "").strip(),
            "turnover_rate": _f(r.get("TURNOVERRATE")),
            "deal_ratio": _f(r.get("DEAL_AMOUNT_RATIO")),
        }
    return out


def lhb_seats(date_str: str, side: str = "buy", size: int = 600) -> list[dict]:
    """龙虎榜席位明细（side = buy / sell）"""
    rep = ("RPT_BILLBOARD_DAILYDETAILSBUY" if side == "buy"
           else "RPT_BILLBOARD_DAILYDETAILSSELL")
    rows = _dc(rep, "(TRADE_DATE='%s')" % date_str, page_size=size)
    out = []
    for r in rows:
        seat = (r.get("OPERATEDEPT_NAME") or "").strip()
        c = str(r.get("SECURITY_CODE") or "")
        if not seat or not c:
            continue
        out.append({
            "seat": seat, "code": c,
            "buy": _f(r.get("BUY")) or 0.0,
            "sell": _f(r.get("SELL")) or 0.0,
            "net": _f(r.get("NET")) or 0.0,
            "pct": _f(r.get("CHANGE_RATE")),
            "explanation": (r.get("EXPLANATION") or "").strip(),
            "side": side,
        })
    return out


def youzi_flow(date_str: str, top_n: int = 20, with_alias: bool = True,
               daily: dict | None = None) -> dict:
    """游资动向：按营业部聚合龙虎榜买卖席位 + 知名游资别名识别

    daily: 已取到的龙虎榜日榜明细（{code: {...}}），传入可省一次请求。

    返回 {"available","date","seat_count","total_buy","total_sell",
          "seats":[{name,alias,buy,sell,net,stocks:[{code,name,pct,net}],count}],
          "hots":[{alias,name,buy,stocks:[...]}],"inst":[...],"note"}
    """
    buy = lhb_seats(date_str, "buy")
    sell = lhb_seats(date_str, "sell")
    if not buy and not sell:
        return {"available": False, "date": date_str,
                "note": "该日无龙虎榜席位数据（非交易日或数据源未更新）"}

    daily = daily if daily is not None else lhb_daily(date_str)

    agg: dict[str, dict] = {}

    def _touch(seat: str) -> dict:
        if seat not in agg:
            agg[seat] = {"name": seat, "buy": 0.0, "sell": 0.0, "net": 0.0,
                         "stocks": {}, "buy_cnt": 0, "sell_cnt": 0}
        return agg[seat]

    for r in buy:
        a = _touch(r["seat"])
        a["buy"] += r["buy"] or 0.0
        a["sell"] += r["sell"] or 0.0
        a["net"] += r["net"] or 0.0
        a["buy_cnt"] += 1
        s = a["stocks"].setdefault(r["code"], {
            "code": r["code"], "name": (daily.get(r["code"]) or {}).get("name", ""),
            "pct": (daily.get(r["code"]) or {}).get("pct", r.get("pct")),
            "net": 0.0, "buy": 0.0})
        s["net"] += r["net"] or 0.0
        s["buy"] += r["buy"] or 0.0
    for r in sell:
        a = _touch(r["seat"])
        a["buy"] += r["buy"] or 0.0
        a["sell"] += r["sell"] or 0.0
        a["net"] += r["net"] or 0.0
        a["sell_cnt"] += 1
        s = a["stocks"].setdefault(r["code"], {
            "code": r["code"], "name": (daily.get(r["code"]) or {}).get("name", ""),
            "pct": (daily.get(r["code"]) or {}).get("pct", r.get("pct")),
            "net": 0.0, "buy": 0.0})
        s["net"] += r["net"] or 0.0

    seats = []
    for nm, a in agg.items():
        st = sorted(a["stocks"].values(), key=lambda x: -abs(x["net"]))
        seats.append({
            "name": nm,
            "alias": (youzi_alias(nm) if with_alias else ""),
            "inst": is_inst_seat(nm),
            "buy": round(a["buy"], 0), "sell": round(a["sell"], 0),
            "net": round(a["net"], 0),
            "count": len(st), "buy_cnt": a["buy_cnt"], "sell_cnt": a["sell_cnt"],
            "stocks": [{"code": x["code"], "name": x["name"], "pct": x["pct"],
                        "net": round(x["net"], 0)} for x in st[:12]],
        })

    # 机构 / 北向席位（"机构专用""深股通专用"等）不是游资，单独归类免于混入游资榜
    inst = sorted([s for s in seats if s["inst"]], key=lambda x: -x["buy"])[:8]
    hot_seats = [s for s in seats if not s["inst"]]
    hot_seats.sort(key=lambda x: -x["buy"])
    top = hot_seats[:top_n]

    hots = [{"alias": s["alias"], "name": s["name"], "buy": s["buy"],
             "net": s["net"], "count": s["count"], "stocks": s["stocks"][:10]}
            for s in hot_seats if s["alias"]][:14]

    raw_buy = sum(r["buy"] or 0.0 for r in buy)
    raw_sell = sum(r["sell"] or 0.0 for r in sell)
    return {
        "available": True, "date": date_str,
        "seat_count": len(hot_seats),
        "record_count": len(buy) + len(sell),
        "total_buy": round(raw_buy, 0), "total_sell": round(raw_sell, 0),
        "seats": top, "hots": hots, "inst": inst,
        "lhb_count": len(daily),
        "note": ("游资动向取自东财龙虎榜买卖席位明细（RPT_BILLBOARD_DAILYDETAILSBUY/SELL），"
                 "按营业部聚合：买入=该席位买入额、净额=买入−卖出。"
                 "「机构专用」「深股通专用」「沪股通专用」等为机构/北向通道，已单独归类、"
                 "不计入游资席位。席位别名依公开市场资料整理，仅供识别参考，"
                 "席位归属可能随时间变化，请以原始营业部名称为准。"),
    }


# ---------------------------------------------------------------------------
# 汇总入口
# ---------------------------------------------------------------------------

def collect_extra(date_str: str, klines: dict | None = None,
                  names: dict | None = None, index_pct: dict | None = None,
                  is_latest: bool = False, with_lhb: bool = True,
                  with_hot: bool = True) -> dict:
    """采集四类扩展数据 → {"amount_hint","hot","abnormal","youzi"}

    - hot 仅最近交易日有数据源，历史日期返回 available=False
    - abnormal / youzi 历史可回溯
    """
    out: dict = {}

    # 龙虎榜日榜明细：异动原因佐证与游资个股名称共用，故只取一次
    lhb_map: dict = {}
    if with_lhb:
        try:
            lhb_map = lhb_daily(date_str)
        except Exception:  # noqa: BLE001
            lhb_map = {}

    if with_hot and is_latest:
        try:
            out["hot"] = hot_list(50, names)
        except Exception as e:  # noqa: BLE001
            out["hot"] = {"available": False, "note": "热度榜采集失败：%s" % e}
    elif with_hot:
        # 历史日的热度榜由重建流程按本地人气排名缓存组装（daily_extra.hot_list_history），
        # 这里只是「未走重建」时的兜底说明。
        out["hot"] = {"available": False,
                      "note": "热度榜未采集（历史日请先执行「历史数据重建」，"
                              "会按已落库的个股人气排名序列离线组装）"}

    if klines:
        try:
            abn = abnormal_watch(klines, date_str, names, index_pct, lhb_map=lhb_map)
            if not abn.get("available") and abn.get("ok") \
                    and turnover_from_klines(klines, date_str) is None:
                abn["note"] = ("个股日K缓存未覆盖该交易日（盘中或缓存尚未更新），"
                               "异动检测需要该日及其之前至少 30 个交易日的日K数据。"
                               "可先执行「历史数据重建」补齐。")
            out["abnormal"] = abn
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            out["abnormal"] = {"available": False, "ok": False,
                               "note": "异动检测失败：%s: %s" % (type(e).__name__, e)}
    else:
        out["abnormal"] = {"available": False, "ok": False,
                           "note": "缺少个股日K缓存，无法做异动检测（可先执行「历史数据重建」）"}

    if with_lhb:
        try:
            out["youzi"] = youzi_flow(date_str, daily=lhb_map)
        except Exception as e:  # noqa: BLE001
            out["youzi"] = {"available": False, "note": "游资动向采集失败：%s" % e}

    return out
