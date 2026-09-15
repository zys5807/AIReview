"""A股每日复盘：盘面数据采集 + 报告生成 + AI 点评（V1.009 新模块）

数据源（东财公开接口，全部直连不走系统代理）：
- 涨停池 getTopicZTPool / 炸板池 getTopicZBPool / 跌停池 getTopicDTPool
  —— 支持按日期回溯，池内字段含连板数 lbc、首/末封板时间、炸板次数、行业、涨停统计
- 全市场个股快照 push2 clist —— 用于涨跌家数分布（实时快照，仅最近交易日完整）
- 指数日K push2his kline —— 点位 / 涨跌幅 / 成交额（支持历史日期）

口径说明（对齐通达信 880005 涨跌家数）：
- 涨停 / 跌停家数取自池子总数 tc；分布统计时把池内个股从各档位剔除，避免重复计数
- 上涨家数 = >7% + 5~7% + 3~5% + 0~3%（不含涨停与停牌）
- 下跌家数 = 0~-3% + -3~-5% + -5~-7% + < -7%（不含跌停）
- 总品种数 = 上涨 + 下跌 + 平盘停牌（不含涨跌停，与通达信口径一致）
- 炸板率 = 炸板数 / (涨停数 + 炸板数)；炸板金额率 = 炸板额 / (涨停额 + 炸板额)
- ST 剔除：池内名称含 "ST" 的单独扣除，给出 ex_st 口径
"""
from __future__ import annotations

import concurrent.futures as _cf
import datetime as _dt
import json
import bisect
import random
import re as _re
import time
import traceback as _tb
import urllib.parse
from decimal import Decimal as _DEC, ROUND_HALF_UP, ROUND_DOWN, ROUND_CEILING

from . import market_data as md
from . import theme_taxonomy as _tax

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# push2ex（涨停板行情）必须用专用 ut：换成行情主站的 ut 会返回 rc=205（实测）
_UT = "7eea3edcaed734bea9cbfc24409ed989"
_POOL_BASE = "https://push2ex.eastmoney.com/"
_API_ZT = "getTopicZTPool"
_API_ZB = "getTopicZBPool"
_API_DT = "getTopicDTPool"

# 全市场列表走延迟行情域名（push2 主站对批量分页更易限流；收盘后复盘无差别）
_SNAPSHOT_BASE = "https://push2delay.eastmoney.com/api/qt/clist/get?"

# 沪深京 A 股（含北交所），与东财行情中心"沪深京A股"一致
_FS_ALL_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

# 指数：腾讯日K代码 → 名称（腾讯日K稳定，东财 push2his 易限流）
INDEX_LIST = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
    ("bj899050", "北证50"),
]

_SNAPSHOT_PAGE = 100
_SNAPSHOT_MAX_PAGES = 62
_SNAPSHOT_WORKERS = 4


# ---------------------------------------------------------------------------
# 基础抓取
# ---------------------------------------------------------------------------

def _compact(d: str) -> str:
    """'2026-09-11' → '20260911'"""
    return d.replace("-", "")


def _pool(api: str, date_str: str, sort: str, size: int = 300):
    """拉取涨停/炸板/跌停池：返回 (tc 总数, pool 列表)

    sort 形如 "fbt:asc"（内部会做 URL 编码）；失败返回 (0, [])。
    """
    url = "%s%s?ut=%s&dpt=wz.ztzt&Pageindex=0&pagesize=%d&sort=%s&date=%s" % (
        _POOL_BASE, api, _UT, size, urllib.parse.quote(sort, safe=""), _compact(date_str),
    )
    j = md._fetch_json(url, headers=md.EM_HEADERS, timeout=15, retries=2)
    if not j:
        return 0, []
    d = j.get("data") or {}
    try:
        return int(d.get("tc") or 0), list(d.get("pool") or [])
    except Exception:  # noqa: BLE001
        return 0, []


def _snapshot_page(pn: int):
    """全市场快照单页（含重试 + 抖动；东财对高并发敏感）"""
    q = urllib.parse.urlencode({
        "pn": pn, "pz": _SNAPSHOT_PAGE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        # f15=最高价、f18=昨收 —— 供 ST 股涨跌停判定（含 ST 口径）使用
        "fid": "f3", "fs": _FS_ALL_A, "fields": "f2,f3,f6,f12,f14,f15,f18",
    })
    url = _SNAPSHOT_BASE + q
    for attempt in range(3):
        j = md._fetch_json(url, headers=md.EM_HEADERS, timeout=12, retries=1)
        if j:
            d = j.get("data") or {}
            if d:
                return list(d.get("diff") or []), int(d.get("total") or 0)
        time.sleep(0.35 * (attempt + 1) + random.uniform(0, 0.25))
    return [], 0


def _market_snapshot():
    """全市场个股快照（并发分页）：返回 (rows, note)

    rows: [{"code","name","pct","price","amount"}]；note 为降级说明（''=完整）
    """
    rows: list[dict] = []
    total = 0
    first, total = _snapshot_page(1)
    if not first:
        return [], "全市场快照采集失败（涨跌分布/总成交额不可用）"
    rows.extend(first)
    pages = min(_SNAPSHOT_MAX_PAGES, max(1, (total + _SNAPSHOT_PAGE - 1) // _SNAPSHOT_PAGE))
    if pages > 1:
        with _cf.ThreadPoolExecutor(max_workers=_SNAPSHOT_WORKERS) as ex:
            for part, _t in ex.map(lambda p: _snapshot_page(p), range(2, pages + 1)):
                rows.extend(part)
    out = []
    for r in rows:
        code = r.get("f12")
        if not code:
            continue
        pct = r.get("f3")
        def _num(v):
            return v if isinstance(v, (int, float)) else None
        out.append({
            "code": code,
            "name": (r.get("f14") or "").replace(" ", ""),
            "price": _num(r.get("f2")),
            "pct": pct if isinstance(pct, (int, float)) else None,
            "amount": r.get("f6") if isinstance(r.get("f6"), (int, float)) else 0.0,
            "high": _num(r.get("f15")),    # 当日最高价
            "prev": _num(r.get("f18")),    # 昨收
        })
    note = "" if len(out) >= total * 0.95 else "部分个股快照缺失（共 %d 只，取到 %d 只）" % (total, len(out))
    return out, note


def _limit_board_from_snapshot(rows: list[dict]) -> dict:
    """全市场快照 → ST/*ST 股的涨停 / 跌停 / 炸板家数（「含 ST」口径专用）

    为什么需要它：东财的涨停池 / 跌停池 / 炸板池**都不收录 ST 股**（实测池内 ST 恒为 0），
    所以主口径（剔 ST）直接取池子总数即可；但「含 ST」口径东财没有现成字段，
    只能自己从全市场快照算 —— 快照本来就要拉（算涨跌家数分布），因此零额外开销。

    档位用 _limit_rate_for 按当日最高价反推，可覆盖 ST 摘帽日 5%→10% 的切换；
    容差必须用 _LIMIT_TOL(0.005)：放宽到 0.011 会把 ST龙大 2.60→2.48（跌停价 2.47）
    这种「差 1 分」的误判成跌停（实测踩到）。

    返回 {"limit_up", "limit_down", "broken"}，均为 ST 股口径。
    """
    zt = dt = zb = 0
    for r in rows:
        name = r.get("name") or ""
        if not _is_st(name):
            continue
        code = str(r.get("code") or "")
        close, prev, high = r.get("price"), r.get("prev"), r.get("high")
        if not isinstance(prev, (int, float)) or prev <= 0:
            continue
        if not isinstance(close, (int, float)):
            continue
        px = high if isinstance(high, (int, float)) else close
        rate = _limit_rate_for(prev, code, name, px)
        rates = [rate] if rate else _limit_rates(code, name)
        hit = None
        for rr in rates:
            if abs(close - round(prev * (1 + rr), 2)) < _LIMIT_TOL:
                hit = "up"
                break
            if abs(close - round(prev * (1 - rr), 2)) < _LIMIT_TOL:
                hit = "down"
                break
        if hit == "up":
            zt += 1
            continue
        if hit == "down":
            dt += 1
            continue
        # 炸板：最高价触及涨停价、收盘未封住
        for rr in rates:
            lim = round(prev * (1 + rr), 2)
            if isinstance(high, (int, float)) and high >= lim - _LIMIT_TOL and close < lim - _LIMIT_TOL:
                zb += 1
                break
    return {"limit_up": zt, "limit_down": dt, "broken": zb}


def _recent_trade_days(end_str: str, n: int) -> list[str]:
    """最近 n 个交易日（升序，含 end_str 当日或之前的最后 n 个交易日）

    以上证指数日K（腾讯源）的日期序列为交易日历。
    """
    end_d = _dt.datetime.strptime(end_str, "%Y-%m-%d").date()
    beg = (end_d - _dt.timedelta(days=int(n * 2.2) + 40)).strftime("%Y-%m-%d")
    bars = md._tencent_bars("sh000001", beg, end_str, max_n=n + 15)
    days: list[str] = []
    if bars:
        for b in bars:
            try:
                d = b[0]
                if d <= end_str:
                    days.append(d)
            except Exception:  # noqa: BLE001
                continue
    if not days:
        for i in range(n):
            d = end_d - _dt.timedelta(days=i)
            if d.weekday() < 5:
                days.append(d.strftime("%Y-%m-%d"))
        days.reverse()
        return days[-n:]
    return days[-n:]


def _prev_trade_day(date_str: str) -> str | None:
    """上一个交易日（无则 None）"""
    days = _recent_trade_days(date_str, 10)
    prev = [d for d in days if d < date_str]
    return prev[-1] if prev else None


def _index_quotes(date_str: str) -> list[dict]:
    """指数当日表现：收盘点位 / 涨跌幅（腾讯日K，支持历史日期回溯）"""
    end_d = _dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    beg = (end_d - _dt.timedelta(days=25)).strftime("%Y-%m-%d")

    def one(item):
        code, name = item
        bars = md._tencent_bars(code, beg, date_str, max_n=20)
        empty = {"name": name, "date": None, "close": None, "pct": None, "volume": None}
        if not bars:
            return empty
        idx = None
        for i, b in enumerate(bars):
            try:
                if b[0] <= date_str:
                    idx = i
            except Exception:  # noqa: BLE001
                continue
        if idx is None:
            return empty
        try:
            close = float(bars[idx][2])
            prev = float(bars[idx - 1][2]) if idx > 0 else None
            pct = round((close - prev) / prev * 100, 2) if prev else None
            vol = float(bars[idx][5]) if len(bars[idx]) > 5 else None
            return {"name": name, "date": bars[idx][0], "close": round(close, 2),
                    "pct": pct, "volume": vol}
        except Exception:  # noqa: BLE001
            return empty

    with _cf.ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(one, INDEX_LIST))


# ---------------------------------------------------------------------------
# 池子 → 结构化统计
# ---------------------------------------------------------------------------

def _fmt_time(v) -> str:
    """92500 → '09:25:00'"""
    try:
        s = str(int(v)).zfill(6)
        return "%s:%s:%s" % (s[0:2], s[2:4], s[4:6])
    except Exception:  # noqa: BLE001
        return ""


def _is_st(name: str) -> bool:
    """是否风险警示股（ST / *ST / SST / S*ST）

    四种写法统一规则：去前导 S（仅当它不是 `ST` 的开头）→ 吃掉开头 `*` → 以 `ST` 开头。
    名称缺失时返回 False（宁可不标也不误标）。原实现为简单的 `"ST" in name`，
    会把 `S佳通` 之类名称误判，已统一到本口径。
    与 `daily_extra.is_st_stock` 同一规则（那边为避免循环导入独立实现了一份）。
    """
    n = (name or "").replace(" ", "").replace("\u3000", "").upper()
    if n.startswith("S") and not n.startswith("ST"):
        n = n[1:]
    return n.lstrip("*").startswith("ST")


_is_st_name = _is_st  # 语义化别名（涨跌幅比例判定处使用）


def _zt_stocks(tc: int, pool: list) -> dict:
    """涨停池 → 个股列表 + 连板梯队 + 行业分布"""
    stocks = []
    for it in pool:
        name = (it.get("n") or "").replace(" ", "")
        zttj = it.get("zttj") or {}
        days, ct = zttj.get("days"), zttj.get("ct")
        stocks.append({
            "code": it.get("c"),
            "name": name,
            "price": round((it.get("p") or 0) / 1000.0, 3),
            "pct": round(it.get("zdp") or 0, 2),
            "amount": it.get("amount") or 0,
            "ltsz": it.get("ltsz") or 0,
            "hs": round(it.get("hs") or 0, 2),
            "lbc": int(it.get("lbc") or 1),
            "fbt": _fmt_time(it.get("fbt")),
            "lbt": _fmt_time(it.get("lbt")),
            "zbc": int(it.get("zbc") or 0),
            "industry": it.get("hybk") or "",
            "stat": ("%s天%s板" % (days, ct)) if days and ct else "",
            "is_st": _is_st(name),
        })
    stocks.sort(key=lambda x: (-x["lbc"], x["fbt"] or "99:99:99"))

    # 连板梯队（按连板数分组，降序）
    ladder_map: dict[int, list] = {}
    for s in stocks:
        ladder_map.setdefault(s["lbc"], []).append(s)
    ladder = [
        {
            "lbc": k,
            "count": len(v),
            "amount": sum(x["amount"] for x in v),
            "stocks": v,
        }
        for k, v in sorted(ladder_map.items(), key=lambda kv: -kv[0])
    ]

    # 行业分布
    ind: dict[str, dict] = {}
    for s in stocks:
        k = s["industry"] or "其他"
        d = ind.setdefault(k, {"name": k, "count": 0, "amount": 0.0})
        d["count"] += 1
        d["amount"] += s["amount"]
    industries = sorted(ind.values(), key=lambda x: -x["count"])

    _n = tc or len(stocks)
    return {
        "count": _n,           # 主口径：剔 ST（东财池本身不收录 ST）
        "count_inc_st": _n,    # 含 ST：待全市场快照补算后覆盖
        "count_ex_st": _n,     # 兼容旧字段，与 count 同义
        "st_count": 0,         # 待全市场快照补算后覆盖
        "amount": sum(s["amount"] for s in stocks),
        "max_lbc": max([s["lbc"] for s in stocks] or [0]),
        "stocks": stocks,
        "ladder": ladder,
        "industries": industries,
    }


def _dt_stocks(tc: int, pool: list) -> dict:
    stocks = []
    for it in pool:
        name = (it.get("n") or "").replace(" ", "")
        stocks.append({
            "code": it.get("c"),
            "name": name,
            "price": round((it.get("p") or 0) / 1000.0, 3),
            "pct": round(it.get("zdp") or 0, 2),
            "amount": it.get("amount") or 0,
            "ltsz": it.get("ltsz") or 0,
            "hs": round(it.get("hs") or 0, 2),
            "lbc": int(it.get("lbc") or 1),
            "fbt": _fmt_time(it.get("fbt")),
            "industry": it.get("hybk") or "",
            "is_st": _is_st(name),
        })
    stocks.sort(key=lambda x: x["pct"])
    _n = tc or len(stocks)
    return {
        "count": _n,           # 主口径：剔 ST
        "count_inc_st": _n,    # 含 ST：待全市场快照补算后覆盖
        "count_ex_st": _n,     # 兼容旧字段
        "st_count": 0,
        "amount": sum(s["amount"] for s in stocks),
        "stocks": stocks,
    }


def _zb_stocks(tc: int, pool: list) -> dict:
    """炸板池 → 炸板个股（含炸板次数 / 涨停价 / 振幅）"""
    stocks = []
    for it in pool:
        name = (it.get("n") or "").replace(" ", "")
        stocks.append({
            "code": it.get("c"),
            "name": name,
            "price": round((it.get("p") or 0) / 1000.0, 3),
            "zt_price": round((it.get("ztp") or 0) / 1000.0, 3),
            "pct": round(it.get("zdp") or 0, 2),
            "amount": it.get("amount") or 0,
            "ltsz": it.get("ltsz") or 0,
            "hs": round(it.get("hs") or 0, 2),
            "zbc": int(it.get("zbc") or 0),
            "amplitude": round(it.get("zf") or 0, 2),
            "fbt": _fmt_time(it.get("fbt")),
            "industry": it.get("hybk") or "",
            "is_st": _is_st(name),
        })
    stocks.sort(key=lambda x: -x["amount"])
    _n = tc or len(stocks)
    return {
        "count": _n,           # 主口径：剔 ST
        "count_inc_st": _n,    # 含 ST：待全市场快照补算后覆盖
        "count_ex_st": _n,     # 兼容旧字段
        "st_count": 0,
        "amount": sum(s["amount"] for s in stocks),
        "stocks": stocks,
    }


def _breadth(snap_rows: list[dict], zt_codes: set, dt_codes: set) -> dict:
    """涨跌家数分布（剔除涨跌停，口径对齐通达信 880005）"""
    b = {
        "up_gt7": 0, "up_5_7": 0, "up_3_5": 0, "up_0_3": 0,
        "down_0_3": 0, "down_3_5": 0, "down_5_7": 0, "down_gt7": 0,
        "flat": 0, "up_count": 0, "down_count": 0, "total": 0,
        "total_amount": 0.0,
    }
    for r in snap_rows:
        code = r["code"]
        pct = r.get("pct")
        # 无涨跌幅（停牌 / 未上市 / 数据源缺失）不计入任何档位，也不计入总家数，
        # 否则会被误统计成"平盘"，把平盘家数整体抬高（实测可虚增 300+ 家）
        if pct is None:
            continue
        b["total_amount"] += r.get("amount") or 0
        if code in zt_codes or code in dt_codes:
            continue  # 涨跌停单独计数
        if pct > 7:
            b["up_gt7"] += 1
        elif pct > 5:
            b["up_5_7"] += 1
        elif pct > 3:
            b["up_3_5"] += 1
        elif pct > 0:
            b["up_0_3"] += 1
        elif pct == 0:
            b["flat"] += 1
        elif pct >= -3:
            b["down_0_3"] += 1
        elif pct >= -5:
            b["down_3_5"] += 1
        elif pct >= -7:
            b["down_5_7"] += 1
        else:
            b["down_gt7"] += 1
    b["up_count"] = b["up_gt7"] + b["up_5_7"] + b["up_3_5"] + b["up_0_3"]
    b["down_count"] = b["down_0_3"] + b["down_3_5"] + b["down_5_7"] + b["down_gt7"]
    b["total"] = b["up_count"] + b["down_count"] + b["flat"]
    return b


def _prev_limit_up_perf(prev_date: str, today_rows: list[dict], today_zt: set,
                        today_date: str | None = None) -> dict:
    """昨日涨停股今日平均表现

    有当日全市场快照时走快照口径；历史日期改用个股日K回溯当日涨跌幅（新浪源）。
    """
    empty = {"prev_date": prev_date, "count": 0, "valid": 0, "items": [],
             "avg_pct": None, "up_count": 0, "down_count": 0,
             "flat_count": 0, "limit_up_again": 0, "source": "", "scope": "ex_st"}
    ptc, ppool = _pool(_API_ZT, prev_date, "fbt:asc")
    if not ppool:
        return empty

    pct_map: dict[str, float] = {}
    source = ""
    if today_rows:
        for r in today_rows:
            if isinstance(r.get("pct"), (int, float)):
                pct_map[r["code"]] = r["pct"]
        source = "snapshot"
    elif today_date:
        kd = fetch_klines_multi([it.get("c") for it in ppool if it.get("c")],
                                datalen=12, workers=8)
        for c, rows in kd.items():
            for i in range(1, len(rows)):
                if rows[i][0] == today_date and rows[i - 1][1] > 0:
                    pct_map[c] = (rows[i][1] - rows[i - 1][1]) / rows[i - 1][1] * 100.0
                    break
        source = "kline"

    items = []
    for it in ppool:
        code = it.get("c")
        name = (it.get("n") or "").replace(" ", "")
        pct = pct_map.get(code)
        items.append({
            "code": code, "name": name,
            "prev_lbc": int(it.get("lbc") or 1),
            "prev_stat": (lambda z: ("%s天%s板" % (z.get("days"), z.get("ct"))) if z else "")(
                it.get("zttj") or {}),
            "pct": round(pct, 2) if isinstance(pct, (int, float)) else None,
            "again_limit_up": code in today_zt,
        })
    vals = [x["pct"] for x in items if isinstance(x["pct"], (int, float))]
    items.sort(key=lambda x: (-(x["pct"] if isinstance(x["pct"], (int, float)) else -999)))
    return {
        "prev_date": prev_date,
        "count": len(items),
        "valid": len(vals),
        "avg_pct": round(sum(vals) / len(vals), 2) if vals else None,
        "up_count": sum(1 for v in vals if v > 0),
        "down_count": sum(1 for v in vals if v < 0),
        "flat_count": sum(1 for v in vals if v == 0),
        "limit_up_again": sum(1 for x in items if x["again_limit_up"]),
        "items": items,
        "source": source,
        "scope": "ex_st",       # 主口径：已剔除 ST/*ST（东财池本身不含 ST）
    }


# ---------------------------------------------------------------------------
# 概念板块情绪周期（V1.009.1 新增）
# ---------------------------------------------------------------------------

_API_CLIST = "https://push2delay.eastmoney.com/api/qt/clist/get?"
_API_SLIST = "https://push2delay.eastmoney.com/api/qt/slist/get?"

_FS_CONCEPT = "m:90+t:3"   # 东财概念板块（约 504 个）
_FS_INDUSTRY = "m:90+t:2"  # 东财行业板块（约 496 个）

# 板块列表字段：点位 / 涨跌幅 / 换手率 / 代码 / 名称 / 总市值 / 主力净流入
#               / 上涨家数 / 下跌家数 / 平盘家数 / 领涨股 / 领涨股涨幅 / 领跌股 / 领跌股跌幅
_BOARD_FIELDS = "f2,f3,f8,f12,f14,f20,f62,f104,f105,f106,f128,f136,f140,f207,f208,f222"
_BOARD_PAGE = 100
_BOARD_WORKERS = 4
_CONCEPT_STOCK_LIMIT = 180   # 统计涨停股概念归属时的最大个股数（控制请求量）

# 非题材属性的"伪板块"（宽基 / 资金属性 / 交易标签 / 风格统计），不参与主线题材识别
_BOARD_NOISE = {
    "权重股", "大盘股", "中盘股", "小盘股", "茅指数", "机构重仓", "基金重仓", "社保重仓",
    "QFII重仓", "养老金", "保险重仓", "券商重仓", "信托重仓", "北向资金", "融资融券",
    "标准普尔", "富时罗素", "MSCI中国", "深成500", "上证180_", "沪深300_",
    "中证500_", "中证1000", "创业板综", "AH股", "B股", "GDR", "转债标的", "深股通",
    "沪股通", "高送转", "预盈预增", "预亏预减", "业绩预增", "业绩预亏", "北交所概念",
    "注册制次新股", "次新股", "新股与次新股", "破净股", "长期破净", "低价股",
    "超跌股", "百元股", "高股息", "高股息股", "绩优股", "白马股", "蓝筹股", "成长股",
    "价值股", "题材股", "趋势股", "反转股", "强势股", "弱势股", "活跃股", "龙头股",
    "热门股", "冷门股", "绩差股", "亏损股", "微利股", "举牌", "并购重组",
    "重组概念", "壳资源",
    "近期新高", "百日新高", "历史新高", "近期新低", "百日新低", "昨日涨停",
    "昨日连板", "昨日触板", "涨停股", "跌停股", "低价股板块", "机构调研", "股东增持",
    "股东减持", "股份回购", "股权质押", "解禁", "送转预期", "摘帽概念", "ST股",
    # V1.009.5 补：区域统计板块 + 估值/规模风格板块 + 破发破净类交易标签。
    # 原有的地域规则只覆盖东财「XX板块」命名（广东板块 / 江苏板块），
    # 「西部大开发」「长江三角」「深圳特区」这类同义命名会整片漏进去
    # ——实测 63 个交易日里「西部大开发」进了 24 天主线，排在第 3 位。
    "破发股", "破增发价股", "AB股", "西部大开发", "长江三角", "京津冀", "粤港澳",
    "东北振兴", "海峡西岸", "珠三角", "环渤海", "皖江区域", "中原经济区",
    "小盘成长", "中盘成长", "大盘成长", "小盘价值", "中盘价值", "大盘价值",
    # V1.009.5 补（第二批）：大类赛道归纳时全量导出 410 个"有效概念"做人工审计，
    # 发现下面这 23 个仍是统计/风格类伪板块。它们不属于任何真实题材，
    # 却会被吸附到"零售/消费""金融"等大类里，污染赛道聚合口径。
    "央视50", "HS300", "红利股", "红利破净股", "周期股", "微盘股", "微盘精选",
    "宁组合", "创业成份", "证金持股", "密集调研", "超级品牌", "高成长股",
    "行业龙头", "独角兽", "IPO受益", "贬值受益", "稀缺资源",
    "股权分散", "股权激励", "股权转让", "股权集中",
}
_NOISE_PREFIX = ("昨日", "最近", "东方财富", "龙虎榜", "融资", "机构", "基金", "社保",
                 "北向", "外资", "险资", "养老金", "公募", "私募", "知名", "活跃")
_NOISE_WORDS = ("高振幅", "高换手", "热股", "含一字", "打二板", "触板", "中字头",
                "连板", "炸板", "封板",
                # V1.009.5 补：下面这些原本漏网。_BOARD_NOISE 是**精确匹配**，
                # 名单里写了「并购重组」而真名是「并购重组概念」→ 根本没拦住；
                # 改用子串匹配才能覆盖「XX概念」这类后缀变体。
                "并购重组", "重组", "参股", "市净率", "市盈率", "股息率",
                "特区", "ST",
                # V1.009.5 补（第二批）：纯度/规模/调研类标签，全部用子串覆盖变体。
                # 「风格」覆盖面最广：先进制造风格 / 医药医疗风格 / 消费风格 /
                # 科技风格 / 金融地产风格 —— 实测这 5 个全都在 504 个概念里。
                "风格", "股权", "红利", "微盘", "成份", "调研")
# 业绩/风格统计类板块名：2026中报预减 / 2026三季报首亏 / 2025年报预盈 …
_NOISE_RE = _re.compile(
    # 指数类板块（中证500 / 上证180 / 沪深300 / 创业板指…）。_BOARD_NOISE 里那几个
    # 写成了带下划线的「中证500_」，而东财真名是「中证500」—— 精确匹配根本拦不住，
    # 实测 2026-06-15 的「中证500」直接进了主线。改用前缀正则覆盖所有指数变体。
    r"^(上证|深证|沪深|中证|国证|北证|科创|创业板|中小板|A50|A100|MSCI|标普|富时)"
    r"|^\d{4}\s*(年)?\s*(中报|一季报|三季报|半年报|年报)"
    r"|^HS\d|^央视\d"                      # HS300_ / 央视50_：带下划线，精确匹配拦不住
    r"|(新高|新低|预减|预增|预盈|预亏|首亏|减亏|扭亏|摘帽|破净|重仓|举牌|增减持)$")


def _is_noise_board(name: str) -> bool:
    """是否非题材属性板块（宽基 / 资金属性 / 交易行为标签 / 风格统计 / 地域统计）

    这类板块成分股动辄数百只，涨停家数天然偏高，会淹没真正的细分题材主线。
    """
    nm = (name or "").strip()
    if not nm:
        return True
    if nm in _BOARD_NOISE:
        return True
    if nm.startswith(_NOISE_PREFIX):
        return True
    if any(w in nm for w in _NOISE_WORDS):
        return True
    if _NOISE_RE.search(nm):
        return True
    # 东财地域板块统一以"XX板块"命名（广东板块 / 江苏板块）
    if nm.endswith("板块") and len(nm) <= 6:
        return True
    return False


def _diff_list(d) -> list:
    """东财 clist/slist 的 diff 字段可能是 list 或 {序号: 行} 两种形态"""
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        try:
            return [d[k] for k in sorted(d.keys(), key=lambda x: int(x))]
        except Exception:  # noqa: BLE001
            return list(d.values())
    return []


def _secid(code: str) -> str:
    """股票代码 → 东财 secid（1=沪市，0=深市/北交所）"""
    return ("1." if code.startswith("6") else "0.") + code


def _clist(fs: str, fields: str, pn: int = 1, pz: int = _BOARD_PAGE,
           fid: str = "f3", po: int = 1, retries: int = 2):
    """东财 clist 通用查询 → (行列表, 总数)；失败返回 ([], 0)"""
    q = urllib.parse.urlencode({
        "pn": pn, "pz": pz, "po": po, "np": 1, "fltt": 2, "invt": 2,
        "fid": fid, "fs": fs, "fields": fields,
    })
    for attempt in range(retries + 1):
        j = md._fetch_json(_API_CLIST + q, headers=md.EM_HEADERS, timeout=12, retries=1)
        d = (j or {}).get("data") or {}
        if d.get("diff"):
            return _diff_list(d["diff"]), int(d.get("total") or 0)
        time.sleep(0.3 * (attempt + 1) + random.uniform(0, 0.2))
    return [], 0


def _board_row(r: dict) -> dict:
    def num(v):
        return v if isinstance(v, (int, float)) else None
    return {
        "code": r.get("f12"),
        "name": (r.get("f14") or "").replace(" ", ""),
        "price": num(r.get("f2")),
        "pct": num(r.get("f3")),
        "turnover": num(r.get("f8")),
        "mktcap": num(r.get("f20")),
        "mainflow": num(r.get("f62")),
        "up": int(num(r.get("f104")) or 0),
        "down": int(num(r.get("f105")) or 0),
        "flat": int(num(r.get("f106")) or 0),
        "lead_name": (r.get("f128") or "").replace(" ", ""),
        "lead_pct": num(r.get("f136")),
        "lag_name": (r.get("f207") or "").replace(" ", ""),
        "lag_pct": num(r.get("f222")),
    }


def board_list(fs: str = _FS_CONCEPT, max_pages: int = 12) -> list[dict]:
    """板块全量列表（概念约 504 / 行业约 496），分页并发抓取

    仅实时可用（东财板块指数不提供历史值），历史日期由成分股日K聚合重建。
    """
    first, total = _clist(fs, _BOARD_FIELDS, 1)
    if not first:
        return []
    rows = list(first)
    pages = min(max_pages, max(1, (total + _BOARD_PAGE - 1) // _BOARD_PAGE))
    if pages > 1:
        def one(pn):
            diff, _t = _clist(fs, _BOARD_FIELDS, pn)
            return diff

        with _cf.ThreadPoolExecutor(max_workers=_BOARD_WORKERS) as ex:
            for part in ex.map(one, range(2, pages + 1)):
                rows.extend(part)
    out = []
    seen = set()
    for r in rows:
        code = r.get("f12")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(_board_row(r))
    return out


def _stock_boards(code: str, spt: int = 3) -> list[dict]:
    """个股所属板块（spt=3 概念 / spt=2 行业）→ [{code,name,pct}]

    实时接口（成分归属为准静态数据，用于历史回溯也成立）。
    """
    q = urllib.parse.urlencode({
        "spt": spt, "fltt": 2, "invt": 2, "fields": "f12,f14,f3",
        "secid": _secid(code), "pn": 1, "pz": 80, "po": 1, "fid": "f3",
    })
    j = md._fetch_json(_API_SLIST + q, headers=md.EM_HEADERS, timeout=10, retries=1)
    d = (j or {}).get("data") or {}
    out = []
    for x in _diff_list(d.get("diff")):
        c = x.get("f12")
        if not c:
            continue
        out.append({"code": c, "name": (x.get("f14") or "").replace(" ", ""),
                    "pct": x.get("f3") if isinstance(x.get("f3"), (int, float)) else None})
    return out


def _zt_concept_contrib(zt_stocks: list[dict], size_map: dict | None = None,
                        concept_codes: set | None = None) -> tuple[list[dict], dict]:
    """涨停股 → 概念板块分布（识别当日主线题材）

    返回 (贡献榜, {板块代码: 涨停股列表})。贡献榜按涨停家数降序，家数相同按板块涨幅降序。

    concept_codes: 概念全集白名单（可选）。**务必传** —— 个股板块接口用的 spt=3
    实际返回「概念 + 行业 + 交易标签」的混合列表，其中的行业板块（电子 / 元件 /
    印制电路板…）不在概念全集里，因此拿不到 size，会以 size=0、ratio=None 的形式
    混进主线题材（实测 2026-09-11 的 12 个主线里占了 3 个）。重建路径的 boards 本
    就来自概念全集，这里做同一道校正，两条路径的口径才一致。
    """
    stocks = [s for s in zt_stocks if not s.get("is_st")][:_CONCEPT_STOCK_LIMIT]
    if not stocks:
        return [], {}

    def one(s):
        try:
            return s, _stock_boards(s["code"], spt=3)
        except Exception:  # noqa: BLE001
            return s, []

    pairs: list[tuple] = []
    with _cf.ThreadPoolExecutor(max_workers=6) as ex:
        for s, bs in ex.map(one, stocks):
            pairs.append((s, bs))

    agg: dict[str, dict] = {}
    stock_map: dict[str, list[dict]] = {}
    for s, bs in pairs:
        for b in bs:
            c = b["code"]
            if not c:
                continue
            d = agg.setdefault(c, {"code": c, "name": b.get("name") or c,
                                   "pct": b.get("pct"), "count": 0,
                                   "lbc_max": 0, "lbc2": 0, "amt": 0.0, "codes": []})
            d["count"] += 1
            _lb = int(s.get("lbc") or 1)
            d["lbc_max"] = max(d["lbc_max"], _lb)
            if _lb >= 2:
                d["lbc2"] += 1
            _amt = s.get("amount")
            if isinstance(_amt, (int, float)):
                d["amt"] += float(_amt)
            d["codes"].append({"code": s["code"], "name": s["name"],
                               "lbc": _lb})
            stock_map.setdefault(c, []).append(s)

    # 先按概念全集白名单校正，再过滤宽基/资金属性/地域统计等伪板块
    if concept_codes:
        agg = {c: v for c, v in agg.items() if c in concept_codes}
    contrib = [v for v in agg.values() if not _is_noise_board(v["name"])]
    sizes = size_map or {}
    for v in contrib:
        v["codes"].sort(key=lambda x: -x["lbc"])
        sz = int(sizes.get(v["code"]) or 0)
        v["size"] = sz
        v["ratio"] = round(v["count"] / sz * 100, 1) if sz else None
    contrib.sort(key=lambda x: (-x["count"], -(x["pct"] if isinstance(x["pct"], (int, float)) else -99)))
    return contrib, stock_map


_STAGE_ORDER = ["冰点", "启动", "发酵", "高潮", "退潮", "震荡"]


def _stage_of(pct, zt: int, seq: list[dict]) -> tuple[str, str]:
    """概念板块周期阶段判定

    pct: 今日板块涨跌幅；zt: 今日板块涨停家数
    seq: 含今日在内的近 N 日序列升序 [{date,pct,zt}]
    返回 (阶段, 判定依据)
    """
    zts = [int(x.get("zt") or 0) for x in seq] or [zt]
    pcts = [x.get("pct") or 0 for x in seq]
    prev1 = zts[-2] if len(zts) >= 2 else 0
    prev2 = zts[-3] if len(zts) >= 3 else 0
    zt_sum = sum(zts)
    peak = max(zts)
    cum = sum(pcts)
    p = pct or 0

    if zt_sum == 0:
        if cum <= -3:
            return "冰点", "近%d日无涨停且累计 %.2f%%" % (len(zts), cum)
        return "震荡", "近%d日无涨停" % len(zts)
    if zt >= 3 and zt >= peak and p > 0:
        return "高潮", "今日涨停 %d 家（近%d日最高）" % (zt, len(zts))
    if (prev1 >= 3 or prev2 >= 3) and p < 0:
        return "退潮", "前日涨停 %d 家后转弱，今日 %.2f%%" % (max(prev1, prev2), p)
    if zt >= 1 and prev1 >= 2:
        return "发酵", "连续有涨停（%d→%d 家）" % (prev1, zt)
    if zt >= 1 and prev1 == 0 and prev2 == 0:
        return "启动", "近 3 日首次出现涨停"
    if zt >= 1 and p > 0:
        return "发酵", "今日 %.2f%% 且涨停 %d 家" % (p, zt)
    if p < -2 and zt == 0:
        return "退潮", "今日 %.2f%% 且无涨停" % p
    if cum > 0:
        return "震荡", "近%d日累计 %.2f%%，今日涨停 %d 家" % (len(zts), cum, zt)
    return "震荡", "近%d日累计 %.2f%%" % (len(zts), cum)


def _stage_today(pct, zt: int, ratio=None) -> tuple[str, str]:
    """无历史序列时的"当日强度"口径（周期阶段需要历史序列，见 _stage_of）"""
    p = pct or 0
    if zt >= 5 and p > 2:
        return "高潮", "涨停 %d 家且板块 %+.2f%%，题材高度聚焦" % (zt, p)
    if zt >= 3 and p > 0:
        return "发酵", "涨停 %d 家、板块 %+.2f%%，资金持续介入" % (zt, p)
    if zt >= 3 and p <= 0:
        return "分歧", "涨停 %d 家但板块 %+.2f%%，资金分化" % (zt, p)
    if zt >= 1 and p > 0:
        return "活跃", "涨停 %d 家、板块 %+.2f%%" % (zt, p)
    if zt >= 1:
        return "分歧", "涨停 %d 家但板块 %+.2f%%" % (zt, p)
    if p > 1.5:
        return "补涨", "无涨停但板块 %+.2f%%，跟风补涨" % p
    if p < -2:
        return "退潮", "无涨停且板块 %+.2f%%" % p
    return "平淡", "板块 %+.2f%%、涨停 %d 家" % (p, zt)


def _merge_today_seq(hist: dict | None, code: str, date_str: str | None,
                     pct, zt: int, extra: dict | None = None) -> list[dict]:
    """把当日数据并入板块历史序列（同日覆盖 / 更早则插入），返回末尾 12 日

    extra: 可选的当日附加字段（ratio / lbc_max / lbc2 / amt / size）。
    V1.009.5 起主线四维打分要在**当日**也用这些字段，若并序列时丢掉，
    当日就会退化成"只有 pct/zt"的残缺行，导致打分与历史日期口径分叉。
    """
    seq = list((hist or {}).get(code) or [])
    today = {"date": date_str, "pct": pct, "zt": int(zt or 0)}
    if extra:
        today.update(extra)
    if not seq:
        return [today]
    last_d = seq[-1].get("date") or ""
    if date_str and last_d == date_str:
        seq[-1] = today
    elif date_str and last_d and last_d > date_str:
        seq = [x for x in seq if (x.get("date") or "") < date_str] + [today]
    else:
        seq.append(today)
    return seq[-12:]


# ===================== 板块角色分层：龙头 / 中军 / 跟风 / 补涨 =====================
# 同一板块内的个股承担的角色不同，可参与性与风险完全不同（游资情绪周期框架）：
#   龙头 leader     板块空间高度（连板最高 → 当日涨幅 → 成交额），情绪的旗帜
#   中军 main_force 板块体量中枢（大市值 + 大成交额），大资金载体，通常走趋势
#   跟风 follower   紧随龙头联动的二线，情绪扩散的证明
#   补涨 catchup    板块中后段才启动的低位品种（今日启动 + 前期滞涨），资金外溢产物
# 四类互斥（一只票只归一类），挑选顺序：龙头 → 中军 → 跟风 → 补涨。

_ROLE_CUM_WIN = 5          # 补涨判定回看的交易日数
_ROLE_CATCHUP_LAG = 5.0    # 补涨要求「近 N 日累计涨幅」落后板块中位至少这么多百分点
_ROLE_MIN_FOLLOW = 3.0     # 跟风的最低当日涨幅（未涨停时）
_ROLE_MIN_CATCHUP = 2.0    # 补涨的最低当日涨幅（低于此不算「启动」，只是没跌）
_ROLE_MAX_FOLLOW = 5       # 跟风 / 补涨 各自输出上限
_ROLE_MAX_MF = 2           # 中军输出上限


def roles_ctx_build(klines: dict, names: dict | None = None,
                    cum_win: int = _ROLE_CUM_WIN) -> dict:
    """由全市场日K构建「涨跌停 + 角色判定」所需的全部个股指标（全站唯一入口）

    返回 {"pct","lim","extra"}，三者均为 {code: {date: value}}：
      pct   : 当日涨跌幅（前复权收盘算，跨除权日连续可比）
      lim   : 当日涨跌停标记（1 涨停 / -1 跌停），只有涨跌停日才有键
      extra : [成交额(元), 连板数|None, 近 cum_win 日累计涨幅%|None]

    `rebuild_board_daily` 与 live 路径都调用本函数。**不要各写一份** —— 涨跌停基数、
    取整方向、连板断档这些口径一旦分叉，历史与实时就会对不上（这个坑已经踩过）。

    连板判定与全站统一走 limit_state（即「除权参考价」基数 + 分市场取整方向）；
    停牌造成的K线断档会让连板归零（用全量交易日轴识别，不能只看相邻两行）。
    """
    names = names or {}
    axis = sorted({r[0] for rows in klines.values() for r in rows})
    apos = {d: i for i, d in enumerate(axis)}
    out_pct: dict = {}
    out_lim: dict = {}
    out_extra: dict = {}
    for code, rows in klines.items():
        nm = names.get(code, "")
        prev = None
        prev_bar = None
        prev_d = None
        lbc = 0
        closes: list = []
        m: dict = {}
        lim: dict = {}
        ex: dict = {}
        for _bar in rows:
            d, close = _bar[0], _bar[1]
            _fl = 0
            if prev is not None and prev > 0:
                m[d] = (close - prev) / prev * 100.0
                # 只有相邻交易日才能判涨跌停（停牌断档后首日不适用，故也不续连板）
                if prev_bar is not None and prev_d in apos and d in apos \
                        and apos[d] - apos[prev_d] == 1:
                    _fl = limit_state(prev_bar, _bar, code, nm)[0]
                    if _fl:
                        lim[d] = _fl
            lbc = lbc + 1 if _fl == 1 else 0
            closes.append(close)
            cum = None
            if len(closes) > cum_win:
                _b0 = closes[-cum_win - 1]
                if _b0 and close:
                    cum = (close - _b0) / _b0 * 100.0
            ex[d] = [round(bar_amount(_bar) or 0.0, 0), lbc or None,
                     None if cum is None else round(cum, 3)]
            prev, prev_bar, prev_d = close, _bar, d
        if m:
            out_pct[code] = m
            out_extra[code] = ex
            if lim:
                out_lim[code] = lim
    return {"pct": out_pct, "lim": out_lim, "extra": out_extra}


def roles_ctx_live(snap_rows: list[dict], zt_stocks: list[dict] | None,
                   klines: dict | None, date_str: str,
                   cum_win: int = _ROLE_CUM_WIN) -> dict:
    """实时口径的个股指标，与 roles_ctx_build 返回**同构** → {"pct","lim","extra"}

    数据来源与历史路径不同，但口径必须一致，否则同一天在「今天」与「昨天」会看到
    不同的龙头：
      pct / amt  ← 实时全市场快照（f3 涨跌幅 / f6 成交额）
      lbc        ← 涨停池（东财已算好连板数；非涨停股记 0，而龙头本就要求涨停）
      cum        ← 用日K缓存的「截至昨日」收盘序列 + 当日实时价外推，
                    等价于历史口径 close[t]/close[t−N]−1（盘中日K尚无当日 bar，
                    故不能直接用日K算当日累计涨幅）
    """
    pct_m: dict = {}
    lim_m: dict = {}
    ex_m: dict = {}
    lbc_map: dict = {}
    for s in (zt_stocks or []):
        c = s.get("code")
        if c:
            lbc_map[c] = int(s.get("lbc") or 1)

    # 截至昨日的收盘序列 → (N 个交易日前收盘, 昨收)，用于外推当日累计涨幅
    prev_ref: dict = {}
    for c, brs in (klines or {}).items():
        hist_rows = [b for b in brs if (b[0] or "") < date_str and len(b) > 1 and b[1]]
        if len(hist_rows) >= cum_win:
            base = hist_rows[-cum_win][1]
            last = hist_rows[-1][1]
            if base:
                prev_ref[c] = (base, last)

    for r in (snap_rows or []):
        c = r.get("code")
        p = r.get("pct")
        if not c or not isinstance(p, (int, float)):
            continue
        p = float(p)
        lbc = lbc_map.get(c) or 0
        cum = None
        ref = prev_ref.get(c)
        if ref:
            px = r.get("price")
            px = float(px) if isinstance(px, (int, float)) and px else ref[1] * (1 + p / 100.0)
            cum = (px / ref[0] - 1) * 100.0
        pct_m.setdefault(c, {})[date_str] = p
        ex_m.setdefault(c, {})[date_str] = [
            round(float(r.get("amount") or 0.0), 0), lbc or None,
            None if cum is None else round(cum, 3)]
        if lbc:
            lim_m.setdefault(c, {})[date_str] = 1
    return {"pct": pct_m, "lim": lim_m, "extra": ex_m}


def _role_row(r: dict) -> dict:
    """角色行的精简输出（快照体积敏感，只留前端/报告真正要用的字段）"""
    return {
        "code": r["code"], "name": r["name"],
        "pct": round(r["pct"], 2),
        "lbc": (int(r["lbc"]) if r.get("lbc") else None),
        "amount": round(r.get("amount") or 0.0, 0),
        "cap": round(r.get("cap") or 0.0, 0),
        "cum": (None if r.get("cum") is None else round(r["cum"], 2)),
        "reason": r.get("reason") or "",
    }


def board_roles(members: list[dict], ctx: dict, date: str,
                board_pct=None) -> dict:
    """板块成分股 + 当日个股指标 → 四类角色（互斥，各带判定依据）

    members : 板块成分股 [{"code","name","mktcap"}]（来自 board_member_cache，带市值）
    ctx     : roles_ctx_build 的结果
    board_pct: 板块当日涨跌幅（仅用于 note 里的对比描述，可为 None）

    口径（与前端备注 TIP 表、报告文案逐字对应，改一处必须同步另两处）：
      共同前提：剔除 ST/*ST（与严重异动提醒口径一致 —— ST 不作为交易标的）
      龙头 = 板块内涨停股中「连板数 → 当日涨幅 → 成交额」最高者；无涨停股则不输出
      中军 = 板块内市值 ≥ 中位数、当日收涨且**未涨停**的个股里成交额最大者（至多 2 只）
             —— 涨停股已由龙头/跟风覆盖，中军的价值在于「体量大、资金重、涨幅温和」
      跟风 = 其余个股中「涨停或当日涨幅 ≥ 3%」者，按涨幅降序（至多 5 只）
      补涨 = 其余个股中「当日涨幅 ≥ 2% 且近 5 日累计涨幅 ≤ 板块中位 − 5pct」者，
             按「近 5 日累计涨幅」升序（越滞涨越优先，至多 5 只）
    """
    pct_m = ctx.get("pct") or {}
    extra_m = ctx.get("extra") or {}
    lim_m = ctx.get("lim") or {}

    rows: list[dict] = []
    st_n = 0
    for mem in (members or []):
        c = mem.get("code")
        if not c:
            continue
        nm = mem.get("name") or ""
        if _is_st(nm):
            st_n += 1
            continue                    # ST 不参与角色分层
        p = (pct_m.get(c) or {}).get(date)
        if p is None:
            continue                    # 当日无成交（停牌 / 尚未上市）
        ex = (extra_m.get(c) or {}).get(date) or [0.0, None, None]
        rows.append({
            "code": c,
            "name": nm,
            "pct": float(p),
            "amount": float(ex[0] or 0.0),
            "lbc": int(ex[1] or 0),
            "cum": ex[2],
            "cap": float(mem.get("mktcap") or 0.0),
            "limit": (lim_m.get(c) or {}).get(date) or 0,
        })

    out = {"leader": [], "main_force": [], "follower": [], "catchup": [],
           "pool": len(rows), "st_excluded": st_n, "note": ""}
    if not rows:
        out["note"] = "该板块成分股当日无交易数据"
        return out

    cap_arr = sorted(r["cap"] for r in rows if r["cap"] > 0)
    med_cap = cap_arr[len(cap_arr) // 2] if cap_arr else 0.0
    notes: list[str] = []
    used: set = set()

    # ① 龙头 —— 必须涨停（不涨停称不上龙头），连板高度优先
    #    排序键一律**先四舍五入到 2 位**（与界面/报告显示一致）：实时快照与日K的涨跌幅
    #    存在 0.005pct 级差异，若按原始浮点排序，两只显示同为 +10.02% 的票顺序会随机抖动，
    #    看起来像"判定不稳定"。舍入后同值再按成交额、代码决定，结果完全确定。
    zt_rows = sorted([r for r in rows if r["limit"] == 1],
                     key=lambda r: (-r["lbc"], -round(r["pct"], 2), -r["amount"], r["code"]))
    if zt_rows:
        L = zt_rows[0]
        used.add(L["code"])
        if L["lbc"] >= 2:
            L["reason"] = "%d 连板，板块内空间最高；成交额 %.2f 亿" % (L["lbc"], L["amount"] / 1e8)
        else:
            L["reason"] = ("首板里最强（%+.2f%%）；板块内 %d 只涨停、无更高连板"
                           % (L["pct"], len(zt_rows)))
        out["leader"] = [_role_row(L)]
    else:
        notes.append("板块内无涨停，龙头未确立")

    # ② 中军 —— 板块内大市值（≥ 中位数）里成交额最大，当日收涨且未涨停
    #    （涨停股已由龙头/跟风覆盖；中军的价值在于「体量大、资金重、涨幅温和」）
    mf_pool = [r for r in rows
               if r["code"] not in used and r["cap"] > 0 and r["cap"] >= med_cap
               and r["pct"] > 0 and r["limit"] != 1]
    mf_pool.sort(key=lambda r: (-r["amount"], r["code"]))
    for r in mf_pool[:_ROLE_MAX_MF]:
        used.add(r["code"])
        r["reason"] = ("市值 %.0f 亿（板块市值前 50%%）、成交额 %.2f 亿、当日 %+.2f%%"
                       % (r["cap"] / 1e8, r["amount"] / 1e8, r["pct"]))
        out["main_force"].append(_role_row(r))
    if not out["main_force"]:
        notes.append("中军缺位（板块大市值成分股当日未收涨或已涨停）")

    # ③ 跟风 —— 涨停或涨幅 ≥ 阈值，按涨幅降序
    #    门槛与排序都用「显示精度（2 位小数）」判定，避免 2.999 / 3.001 这类浮点毛刺
    #    让同一只票在实时与历史两边一个进一个不进
    fol_pool = [r for r in rows
                if r["code"] not in used
                and (r["limit"] == 1 or round(r["pct"], 2) >= _ROLE_MIN_FOLLOW)]
    fol_pool.sort(key=lambda r: (-round(r["pct"], 2), -r["amount"], r["code"]))
    for r in fol_pool[:_ROLE_MAX_FOLLOW]:
        used.add(r["code"])
        r["reason"] = ("涨停跟随，成交额 %.2f 亿" % (r["amount"] / 1e8)) if r["limit"] == 1 \
            else ("%+.2f%%、成交额 %.2f 亿" % (r["pct"], r["amount"] / 1e8))
        out["follower"].append(_role_row(r))

    # ④ 补涨 —— 今日明确启动（涨幅 ≥ 下限，不是仅仅没跌）+ 近 N 日累计涨幅显著落后
    #    板块中位（位置低），越滞涨越优先
    cum_arr = sorted(round(r["cum"], 2) for r in rows
                     if r["cum"] is not None and r["code"] not in used)
    if cum_arr:
        med_cum = cum_arr[len(cum_arr) // 2]
        cu_pool = [r for r in rows
                   if r["code"] not in used and round(r["pct"], 2) >= _ROLE_MIN_CATCHUP
                   and r["limit"] != 1          # 涨停已属跟风/龙头，不是「低位补涨」
                   and r["cum"] is not None
                   and round(r["cum"], 2) <= med_cum - _ROLE_CATCHUP_LAG]
        cu_pool.sort(key=lambda r: (round(r["cum"], 2), -round(r["pct"], 2), r["code"]))
        for r in cu_pool[:_ROLE_MAX_FOLLOW]:
            used.add(r["code"])
            r["reason"] = ("近 %d 日累计 %+.2f%%（板块中位 %+.2f%%）后今日启动 %+.2f%%"
                           % (_ROLE_CUM_WIN, r["cum"], med_cum, r["pct"]))
            out["catchup"].append(_role_row(r))
    else:
        notes.append("成分股日K不足 %d 根，无法判定补涨" % (_ROLE_CUM_WIN + 1))

    if st_n:
        notes.append("已剔除 ST %d 只" % st_n)
    out["note"] = "；".join(notes)
    return out


def board_roles_daily(members_map: dict, ctx: dict, date: str,
                      board_codes: list[str] | None = None) -> dict:
    """为指定板块算当日角色 → {board_code: roles}（不给 board_codes 则全量）"""
    want = set(board_codes) if board_codes else None
    out: dict = {}
    for bk, info in (members_map or {}).items():
        if want is not None and bk not in want:
            continue
        out[bk] = board_roles(info.get("members") or [], ctx, date)
    return out


# ===================== 主线题材四维打分（V1.009.5） =====================
# 旧口径只做了一次横截面排行（排序键 = 涨停家数 → 板块涨幅），两个硬伤：
#   ① count 是板块内涨停的**绝对家数**，被成分股数量绑架 —— 20 只成分股里 5 家
#      涨停（25%）永远排不过 200 只里 8 家（4%）；ratio 早就算出来了却没进排序。
#   ② 完全不看时间维度 —— 昨日 0 家、今日 6 家的一日游会压过连续 4 天 4/5/4/5 家的
#      真主线。而"在榜天数"所需的历史序列其实**已经存在本地库里**（hist），现算零成本。
# 新口径把"主线"当成一个**带时间维度的状态**：资格线筛池 → 四维打分 → 分级。
#   聚焦度 0.30  涨停占板块比（候选池内分位）——回答"现在强不强"
#   持续性 0.30  近 5 日进涨停家数前 15 的天数（近端加权）——回答"一直强不强"
#   空间高度 0.30 最高连板 + 二板及以上家数——回答"有没有空间"
#   资金容量 0.10 涨停股成交额合计（分位）——回答"上不上得了仓位"

_THEME_W = {"focus": 0.30, "persist": 0.30, "height": 0.30, "capacity": 0.10}
_THEME_WIN = 5              # 持续性回看窗口（交易日）
_THEME_DECAY = [1.0, 0.8, 0.6, 0.4, 0.2]   # 近端加权，索引 0 = 最近一日
_THEME_ADAPT_STD = 0.05     # 维度在候选池内标准差低于此值 → 无区分力，权重转移
_THEME_QUAL_RATIO = 2.0     # 资格线：涨停占板块比 >= 2%
_THEME_QUAL_PCT_Q = 0.95    # 资格线：板块涨幅 >= 全市场概念 95 分位
_THEME_CORE = 0.66          # 核心主线分数阈值（唯一判据，无 TOP3 兜底，见 _theme_score）
_THEME_SUB = 0.40           # 次级主线阈值
_THEME_MAX1 = 12            # 输出上限

# ---- V1.009.6：持续性口径重写（取代「涨停家数进全市场前 15」）----
# 旧口径（_THEME_TOP=15）的硬伤是**规模歧视**：它把 20 只成分股的板块和 985 只的板块
# 放进同一张「涨停家数」榜比较。实测候选池里 persist>0 的板块，成分股数最小 98 只；
# 按成分股数对半切后，小板块（<=42 只）的 persist>0 命中率是 **0.0%** —— 20 只的培育钻石
# 要冲进全市场涨停家数前 15，需要 7~9 家涨停（35~45% 的涨停率），数学上不可能；
# 而 985 只的央国企改革只要 0.9%。同一个门槛，两个板块的含义差 50 倍。
# 后果：09-14 涨幅榜第 1 / 第 2 名（CRO +4.54% / 培育钻石 +4.48%）持续性得 0 分，
# 分别掉到候选池第 40 / 58 名；涨幅榜前 20 里只有 2 个进了主线表。
#
# 新口径把「在榜日」定义为一个**与成分股数量无关的复合条件**（用户 2026-09-15 提出）：
#   a) 板块涨幅 > 上证指数涨幅   —— 相对强度（跑赢大盘）
#   b) 板块内有涨停              —— 有龙头
#   c) 上涨家数 > 成分股数×60%   —— 板块整体性（普涨，而非一两只拉指数）
# 三条同时成立才算「在榜」。三个判据用的都是比例 / 相对量，规模歧视消失。
#
# ⚠️⚠️ 为什么**不能**再乘覆盖度（V1.009.5 的 `× 命中天数/窗口`）——这里踩过大坑：
# 那层乘子是为**旧定义**校准的：旧定义下真主线能连续 5 天在榜拿 1.0，乘覆盖度用来压
# 一日游。但新定义严到 **65 天里没有任何板块能连续 3 天满足**（55%/60%/65%/70%/75%/80%
# 六个阈值下「>=3 天」全部为 0），加权率最大值只有 ~0.47，再乘 0.2~0.4 的覆盖度
# → 全部压到 0.07 附近 → 候选池内标准差 0.0489 < _THEME_ADAPT_STD
# → **被自适应权重判为「无区分力」并归零**，整个改动等于没做
# （实测 persist 权重变成 0.0，培育钻石仍是第 53 名，与改前几乎一样）。
# 去掉乘子后：标准差 0.1626、65/65 天全部存活、培育钻石 0.0000 → 0.4667。
# 教训：**乘性惩罚的标定依赖上游定义的值域**，换定义必须重新标定，否则会静默失效。
_PERSIST_UP_RATIO = 0.60    # 持续性条件 c：上涨家数 / 成分股数 的阈值

# ---- 资金容量 = 涨停股成交额 + 板块涨幅，各取候选池内分位后加权 ----
# 为什么必须并进板块涨幅：amt 只统计**涨停**股，会把「接近涨停的大涨股」整段漏掉。
# 实测 09-14 培育钻石：实际涨停/大涨股成交额近百亿（黄河旋风 48.93 亿、四方达 28.51 亿、
# 力量钻石 22.56 亿、英诺激光 14.95 亿），系统只算出 3.56 亿 —— 差 30 倍，
# 于是容量分位只有 0.119，把一个近百亿资金流入的板块判成「上不了仓位」。
# 板块涨幅由**全部成分股等权平均**得出（见 rebuild_board_daily 的口径标定），
# 正好补上这块被漏掉的资金强度。
# 配比标定（09-14 实测，amt 权重 w）：w=1.0 → 培育钻石 #17/CRO #16（都不进前 12）；
# w=0.5 → #7/#13；**w=0.4 → #7/#11（两者都进）**；w=0.2 → #6/#10。
# 取 0.4 而非 0：amt 仍占主导，保住「涨停股流动性够不够上仓位」这个原本的语义。
_CAP_AMT_W = 0.4

# ---- 今日启动信号（V1.009.6 新增，方案 A）----
# 四维打分里**历史组占 60%**（持续性 + 空间高度都靠时间积累），所以模型系统性偏好
# 「已确立 3~5 天的主线」，对「启动首日 / 重启首日」的题材在结构上给不了高分。
# 但这个信号恰恰是右侧交易者最需要的买点 —— 启动第一天常是最好的右侧介入点，
# 而模型恰在这天给它最低分。
# 因此单列一条**不参与持续性/高度排序**的信号，只回答「今天资金去了哪个新方向」：
# 它不改变主线表，避免一日游题材污染主线判断（用户明确要求两者分开）。
# 实测 09-14 命中 8 个：CRO(+4.54%/4.1%)、培育钻石(+4.48%/5.0%)、VPN(+3.58%/5.3%)、
# MLCC(+3.31%/5.1%)、汽车整车(+2.81%/13.0%)、复合集流体(+2.26%/3.3%)、
# 数据安全(+2.22%/4.9%)、PCB(+2.19%/4.0%)。
_EMERGING_TOP = 20          # 板块涨幅进全市场概念前 N 名
_EMERGING_RATIO = 3.0       # 且 涨停占板块比 >= 3%
_FIRSTDAY_LOOKBACK = 10     # 「首日启动」判定：近 N 个交易日内没有过「在榜日」


def _rank_pct(pairs: list) -> dict:
    """[(key, value)] → {key: 排名分位 0~1}（值越大分位越高，并列取平均位）

    用分位而非 min-max：避免个别极端值把其余板块全压在一片，
    且候选池大小变化时分数仍然可比。
    """
    vals = sorted([v for _, v in pairs if isinstance(v, (int, float))])
    n = len(vals)
    out: dict = {}
    # 每个 key 都要有值（缺失/全空时给 0.0）—— 让调用方无需再写 .get(k, 0.0) 兜底，
    # 否则"全维度缺失"这种边界会在下游以 KeyError 或静默 0 两种不同形式表现。
    for k, v in pairs:
        if not n or not isinstance(v, (int, float)):
            out[k] = 0.0
            continue
        lo = bisect.bisect_left(vals, v)
        hi = bisect.bisect_right(vals, v)
        out[k] = round((lo + hi) / 2.0 / n, 4)
    return out


def _hist_dates(hist: dict | None, date_str: str | None, win: int) -> list[str]:
    """从 hist 里取「不晚于 date_str」的最近 win 个交易日（升序，含 date_str）

    ⚠️ 必须按 date_str 截**上界**。hist 是整段重建窗口的序列（如 06-16~09-14），
    若直接取 sorted(全集)[-win:]，那么**除最后 5 天以外的所有日期**都会拿到窗口
    末端那几天 —— 实测 6~8 月的每一个日期，持续性窗口都落到了 9 月、
    main_switch.prev_date 恒为 09-11。后果是持续性维度与主线切换信号整体失效，
    而且不报错、不崩溃，只是静默算错（最难发现的一类 bug）。
    历史回看正是本功能的主用途，这个上界是正确性的前提。
    """
    hi = date_str or ""
    ds = set()
    for seq in (hist or {}).values():
        for row in seq:
            d = row.get("date")
            if d and (not hi or d <= hi):
                ds.add(d)
    if date_str:
        ds.add(date_str)
    return sorted(ds)[-max(1, int(win)):]


def _day_index(hist: dict | None, dates: list) -> dict:
    """{date: {code: {pct, zt, up, size}}} —— 持续性三条件判定的统一日截面

    hist 存的是**全部概念板块**的每日序列（504 个），所以每日截面可以现算，零网络成本。

    伪板块（"昨日涨停" / "昨日连板" / 区域 / 估值风格…）必须整片剔掉：
    "昨日涨停"的成分股按定义就是昨日全部涨停股，其涨停家数/上涨家数每天都是全市场最高，
    于是它天天满足三条件、常年霸榜，把真正的题材整体压低。
    name 缺失时**不剔**（旧 hist 没这个字段，宁可少剔也不要把整表剔空）。
    """
    want = set(dates or [])
    out: dict = {}
    for code, seq in (hist or {}).items():
        for row in seq:
            d = row.get("date")
            if d not in want:
                continue
            _nm = row.get("name")
            if _nm and _is_noise_board(_nm):
                continue
            out.setdefault(d, {})[code] = {
                "pct": row.get("pct"),
                "zt": int(row.get("zt") or 0),
                "up": int(row.get("up") or 0),
                "size": int(row.get("size") or 0),
            }
    return out


def _today_day_rows(boards: list, size_map: dict, zt_map: dict | None = None) -> dict:
    """当日实盘板块行 → {code: {pct, zt, up, size}}，用于覆写 _day_index 的当日槽

    为什么当日必须用实盘 boards 而不是 hist：hist 是上次重建的产物，通常**不含今天**
    （重建一般覆盖到昨日）。若只看 hist，当日永远不计入在榜天数 —— 每一天的持续性
    都少算一天，而且是静默少算（不报错、只是分数偏低）。

    ⚠️ `zt` 必须由调用方用 zt_map 补进来：**实时板块行（东财 clist）没有涨停家数字段**
    （`_board_row` 只有 up/down/flat 等），涨停家数是另由「涨停股 → 所属概念」映射
    算出来的（见 concept_emotion 里的 contrib）。少这一环的后果是条件 b「有涨停」
    在实时路径恒为假 → 实时日的持续性恒为 0，而历史重建日正常 —— 又是一个静默分叉。
    重建路径的 boards 行自带 zt，走 fallback 即可。
    """
    out: dict = {}
    zt_map = zt_map or {}
    for b in (boards or []):
        nm = b.get("name")
        if nm and _is_noise_board(nm):
            continue
        code = b.get("code")
        if not code:
            continue
        _zt = zt_map.get(code)
        if _zt is None:
            _zt = b.get("zt")
        if _zt is None:          # 兜底：实时行退化为 count（通常也没有，则记 0）
            _zt = b.get("count")
        out[code] = {
            "pct": b.get("pct"),
            "zt": int(_zt or 0),
            "up": int(b.get("up") or 0),
            # ⚠️ 条件 c 的分母必须与 hist.size 同口径 —— hist 里 size = count（当日有效
            # 成分股数）。重建行的 size_map = up+down（**缺平盘**）会比 count 小，
            # 若当日用 size_map、历史用 count，条件 c 在两条路径上就是两个尺度，
            # 阈值 60% 的含义会漂移。所以优先取 count；实时行没有 count，
            # 但 size_map = up+down+flat 恰好等于 count，天然一致。
            "size": int(b.get("count") or b.get("size") or size_map.get(code) or 0),
        }
    return out


def _onboard(day_idx: dict, code: str, d: str, sh_map: dict | None,
             up_ratio: float | None = None) -> bool:
    """该板块在该日是否「在榜」：a) 涨幅 > 上证 且 b) 有涨停 且 c) 上涨家数 > 成分股×60%

    sh_map 缺失该日时（指数序列未覆盖）退化为「涨幅 > 0」，**不判 False** ——
    判 False 会让整段持续性静默归零，那是最难发现的一类失效。
    """
    row = (day_idx.get(d) or {}).get(code)
    if not row:
        return False
    pct = row.get("pct")
    if not isinstance(pct, (int, float)):
        return False
    sh = (sh_map or {}).get(d)
    if not isinstance(sh, (int, float)):
        sh = 0.0
    if pct <= sh:                       # a 相对强度：跑赢上证
        return False
    if int(row.get("zt") or 0) <= 0:    # b 有龙头
        return False
    size = int(row.get("size") or 0)
    up = int(row.get("up") or 0)
    th = _PERSIST_UP_RATIO if up_ratio is None else up_ratio
    return size > 0 and up > size * th  # c 板块整体性：普涨而非一两只拉指数


def _onboard_set(day_idx: dict, d: str | None, sh_map: dict | None,
                 up_ratio: float | None = None) -> set:
    """某日全市场「在榜」板块代码集合 —— 主线切换信号与持续性必须用同一把尺子"""
    if not d:
        return set()
    return {c for c in (day_idx.get(d) or {})
            if _onboard(day_idx, c, d, sh_map, up_ratio)}


def _firstday_info(day_idx: dict, code: str, dates: list, sh_map: dict | None,
                   date_str: str | None = None, zt_today: int | None = None) -> dict:
    """近 N 日在榜情况 → {onboard_recent, first_day, [zt, launch]}

    口径（V1.009.6）：以 `date_str` 为界，**只看此前** _FIRSTDAY_LOOKBACK 个交易日。
      onboard_recent = 此前在榜天数
      first_day      = 此前从未在榜 → 今天第一次以「有涨停 + 跑赢大盘 + 普涨」的形态出现

    用来把「资金第一次选中这个新方向」与「已经发酵过几天、今天只是延续」分开 ——
    两者的操作含义完全不同（前者是右侧第一买点，后者是追高）。

    ⚠️⚠️ 必须把**当日从窗口里剔掉**（`d < date_str`）。曾经把当日算进去，后果是
    「今天首次在榜」得到 hits=1 → 判为「不是首日」，正好把最该提示的「真·启动首日」
    漏掉：只有从未在榜的板块才会显示首日，语义是反的。
    实测 09-14 修正后，VPN / 减肥药 / 基因测序 这类「今日才首次在榜」的板块能正确
    显示为首日（修正前被误判为否）。

    `zt_today` 传入时额外产出**展示用三态** `launch`（口径收口在这里，避免前端自己
    再推一遍规则）：把「首日」的成立条件与「本系统以涨停为攻击证据」这件事对齐 ——
      「首日」  = 此前无在榜日 且 今日有涨停
      「延续」  = 此前有在榜日
      「无涨停」= 今日 0 涨停 → 启动/延续**都不判定**。
    否则 0 涨停的板块（在榜条件 b 天然不成立 → onboard_recent 恒为 0）会被永久标成
    「首日启动」，实测 09-14 会误标 CAR-T细胞疗法 / 电子纸概念 / 青蒿素 这类
    「涨幅高但无人封板」的板块，按此标签交易是错误信号。
    """
    before = sum(1 for d in dates
                 if (not date_str or d < date_str)
                 and _onboard(day_idx, code, d, sh_map))
    out = {"onboard_recent": before, "first_day": before == 0}
    if zt_today is not None:
        _zt = int(zt_today or 0)
        out["zt"] = _zt
        out["launch"] = "无涨停" if _zt <= 0 else ("首日" if before == 0 else "延续")
    return out


def _persist_score(day_idx: dict, code: str, dates: list,
                   sh_map: dict | None = None) -> tuple:
    """近 win 日「在榜日」的近端加权得分 → (分数, 命中天数)

    这是唯一能区分「一日游」与「真主线」的维度：
    一日游板块今天满足三条件但前面几天都不在榜 → 分数低；
    连续 5 天在榜（实测极罕见，65 天里没有板块做到 3 天）→ 接近 1.0。

    ⚠️⚠️ **绝对不能乘覆盖度**（V1.009.5 的 `× 命中天数/窗口`）。
    那层乘子是为**旧定义**（涨停家数进全市场前 15）校准的：旧定义下真主线能连续
    5 天在榜拿满 1.0，乘覆盖度用来把一日游从 0.33 压到 0.067。
    但新三条件定义严到 65 天里没有任何板块能连续 3 天满足，加权率最大值只有 ~0.47，
    再乘 0.2~0.4 的覆盖度 → 全部候选被压到 0.07 附近 → 候选池内标准差 0.0489
    < _THEME_ADAPT_STD → **被自适应权重判为「无区分力」并归零**，整个改动静默失效
    （实测 persist 权重变成 0.0，培育钻石仍排第 53 名，与改前几乎一样）。
    去掉乘子后标准差 0.1626、65/65 天全部存活、培育钻石 0.0000 → 0.4667。

    值域：最近两日都命中 = 1.0；只今日命中 = 0.3333；只 5 日前命中 = 0.0667。
    """
    if not dates:
        return 0.0, 0
    win = min(len(dates), len(_THEME_DECAY))
    use = dates[-win:]
    tot = sum(_THEME_DECAY[:win]) or 1.0
    got = 0.0
    hits = 0
    for i, d in enumerate(reversed(use)):     # i=0 → 最近一日
        if _onboard(day_idx, code, d, sh_map):
            got += _THEME_DECAY[i]
            hits += 1
    return round(got / tot, 4), hits


def _height_score(lbc_max, lbc2) -> float:
    """空间高度 = 最高连板(0.7) + 二板及以上家数(0.3)，各自封顶

    有龙头的题材才是主线，没高度的是扩散/补涨。
    """
    a = min(int(lbc_max or 0), 6) / 6.0
    b = min(int(lbc2 or 0), 4) / 4.0
    return round(a * 0.7 + b * 0.3, 4)


def _theme_qualified(c: dict, pct_q) -> bool:
    """资格线：满足任一即进入候选池

    ① 涨停家数 >= 2
    ② 涨停占板块比 >= 2%
    ③ 板块涨幅 >= 全市场概念 95 分位 且 至少有 1 家涨停

    池子因此自然伸缩：冰点日可能只有 3 个候选（这本身就是信号），高潮日可能 30 个。
    200 只成分股的板块要 4 家涨停才够 2%，20 只的只要 1 家 —— 资格线本身
    就完成了对成分股数量的校正。
    """
    zt = int(c.get("count") or 0)
    if zt >= 2:
        return True
    ratio = c.get("ratio")
    if isinstance(ratio, (int, float)) and ratio >= _THEME_QUAL_RATIO:
        return True
    pct = c.get("pct")
    if zt >= 1 and pct_q is not None and isinstance(pct, (int, float)) and pct >= pct_q:
        return True
    return False


def _std(vals: list) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return (sum((x - m) ** 2 for x in vals) / len(vals)) ** 0.5


def _theme_score(cands: list, day_idx: dict, dates: list,
                 sh_map: dict | None = None) -> tuple:
    """候选池 → 四维打分 + 分级 → (打分排序后的列表, 实际权重)

    自适应权重：某维在候选池内的标准差 < _THEME_ADAPT_STD 时（如今天所有候选都
    只有 1 只涨停，聚焦度全一样），该维没有区分力，权重按比例转移给其余维度，
    避免"无信息的维度"稀释分数。全部维度都无区分力时保持原权重不变。

    ⚠️ 连续性与这个自适应机制互相牵制：持续性一旦被压到低值域且扎堆，就会静默
    被判死。改持续性口径时必须复算它的候选池内标准差（详见 _PERSIST_UP_RATIO 注释）。
    """
    if not cands:
        return [], {}
    raw = {"focus": [], "persist": [], "height": [], "capacity": []}
    pct_raw: list = []
    meta: dict = {}
    for c in cands:
        code = c["code"]
        _r = c.get("ratio")
        focus_v = float(_r) if isinstance(_r, (int, float)) else 0.0
        p_score, p_hits = _persist_score(day_idx, code, dates, sh_map)
        h_v = _height_score(c.get("lbc_max"), c.get("lbc2"))
        _a = c.get("amt")
        cap_v = float(_a) if isinstance(_a, (int, float)) else 0.0
        _p = c.get("pct")
        raw["focus"].append((code, focus_v))
        raw["persist"].append((code, p_score))
        raw["height"].append((code, h_v))
        raw["capacity"].append((code, cap_v))
        pct_raw.append((code, float(_p) if isinstance(_p, (int, float)) else -999.0))
        meta[code] = {"persist_days": p_hits, "focus": round(focus_v, 2),
                      "height": round(h_v, 4), "capacity": round(cap_v, 0)}

    # 资金容量 = 涨停股成交额(0.4) + 板块涨幅(0.6)，两者各取候选池内分位后加权。
    # 板块涨幅由**全部成分股等权平均**得出，补上 amt 漏掉的「接近涨停的大涨股」：
    # 实测 09-14 培育钻石只算涨停股成交额 3.56 亿（实际近百亿），容量分位仅 0.119，
    # 并入涨幅后 → 0.554，排名从第 17 名升到第 7 名（配比标定见 _CAP_AMT_W）。
    amt_r = _rank_pct(raw["capacity"])
    pct_r = _rank_pct(pct_raw)
    _cap = {k: round(_CAP_AMT_W * amt_r.get(k, 0.0)
                     + (1.0 - _CAP_AMT_W) * pct_r.get(k, 0.0), 4) for k in amt_r}

    # 聚焦度与容量取候选池内分位；持续性与高度本身已是 0~1 的绝对分
    dim = {
        "focus": _rank_pct(raw["focus"]),
        "persist": dict(raw["persist"]),
        "height": dict(raw["height"]),
        "capacity": _cap,
    }
    vals = {k: [dim[k].get(c["code"], 0.0) for c in cands] for k in dim}
    w = dict(_THEME_W)
    dead = {k for k, v in vals.items() if _std(v) < _THEME_ADAPT_STD}
    if dead and len(dead) < len(w):
        lost = sum(w[k] for k in dead)
        alive = [k for k in w if k not in dead]
        base = sum(w[k] for k in alive)
        for k in dead:
            w[k] = 0.0
        if base > 0:
            # 保 6 位：4 位会让"权重和 = 1"出现 1e-4 级误差，前端求和显示不干净
            for k in alive:
                w[k] = round(w[k] + lost * w[k] / base, 6)
            # 再把舍入残差补给权重最大的存活维度，使 sum 精确回到 1
            _d = round(1.0 - sum(w.values()), 6)
            if _d:
                _top = max(alive, key=lambda k: w[k])
                w[_top] = round(w[_top] + _d, 6)

    scored = []
    for c in cands:
        code = c["code"]
        d = {k: round(dim[k].get(code, 0.0), 4) for k in dim}
        score = sum(d[k] * w[k] for k in d)
        r = dict(c)
        r.update({
            "score": round(score, 4),
            "dims": d,
            "weights": w,
            "persist_days": meta[code]["persist_days"],
            "focus_raw": meta[code]["focus"],
            "height_raw": meta[code]["height"],
            "capacity_raw": meta[code]["capacity"],
        })
        scored.append(r)

    # 排序：总分 → 持续性 → 涨停家数（同分时"一直是主线"的优先）
    scored.sort(key=lambda x: (-x["score"], -x["persist_days"], -int(x.get("count") or 0)))
    # 档位**只用绝对阈值**，不做「前 3 名自动升核心」。
    #
    # 为什么去掉 TOP3 兜底（实测结论）：原来的 `score >= _THEME_CORE or (i < 3 and
    # score >= _THEME_SUB)` 里，前 3 名几乎必然 >= 0.40，于是 n_core 恒 >= 3，
    # market_state 退化成一句常量 —— 63 个交易日里 62 天都是"主线明确"，
    # 而它本该回答的核心问题是"今天到底有没有主线"。去掉兜底后分布才是可用的
    # 信号：达 0.66 说明该题材在近 5 日里有 4~5 天进过涨停家数前 15 且今日仍强，
    # 达不到就是真的没有能扛旗的题材 —— 空仓是合法输出。
    # 展示不受影响：main_lines 仍按总分取前 _THEME_MAX1 名。
    for r in scored:
        if r["score"] >= _THEME_CORE:
            r["tier"] = "core"
        elif r["score"] >= _THEME_SUB:
            r["tier"] = "secondary"
        else:
            r["tier"] = "edge"
    return scored, w


def _market_state(n_core: int) -> tuple:
    """核心主线家数 → (市场状态, 说明)

    「今天到底有没有主线」本身就是最有价值的信号之一：
    0 个说明空仓是合法输出；>=6 个在情绪周期里往往是普涨末期，反而该警惕。
    """
    if n_core <= 0:
        return "无主线", "当日无板块达到核心主线标准 —— 空仓是合法输出"
    if n_core <= 2:
        return "结构性行情", "核心主线 %d 个，集中火力" % n_core
    if n_core <= 5:
        return "主线明确", "核心主线 %d 个，正常参与" % n_core
    return "全面开花", "核心主线 %d 个 —— 情绪周期里常是普涨末期，反而该警惕" % n_core


def concept_emotion(boards: list[dict], zt_stocks: list[dict] | None = None,
                    hist: dict | None = None, top_n: int = 15,
                    date_str: str | None = None,
                    roles_ctx: dict | None = None,
                    roles_members: dict | None = None,
                    concept_codes: set | None = None,
                    index_pct: dict | None = None) -> dict:
    """概念板块每日情绪周期分析（V1.009.1 核心）

    boards: 概念板块全量（当日实时 / 历史重建结果均可）
    zt_stocks: 当日涨停个股（实时口径）；留空则用 board 行自带的涨停家数（历史重建口径）
    hist: 可选 {板块代码: [{date,pct,zt,up,size}, ...]} 近 N 日序列，用于周期阶段与主线持续性
    date_str: 当日日期（用于把当日并入历史序列）
    roles_ctx / roles_members: 个股角色分层（龙头/中军/跟风/补涨）所需的两个数据源 ——
      roles_ctx     = {"pct","lim","extra"}，历史走 roles_ctx_build、实时走 roles_ctx_live
      roles_members = {板块代码: {"name","members":[{code,name,mktcap}]}}
    只对进入「主线题材」的板块计算（控制快照体积）；缺任一者则该字段为空。
    concept_codes: 概念全集（实时路径必须传，见 _zt_concept_contrib）
    index_pct: {指数名: {日期: 涨跌幅%}} —— 持续性条件 a「板块涨幅 > 上证指数」的基准。
      缺失时条件 a 退化为「涨幅 > 0」（_onboard 内处理），不判 False、不静默归零。
    """
    if not boards:
        return {"available": False, "total": 0,
                "note": "概念板块数据不可用"}

    vals = [b["pct"] for b in boards if isinstance(b.get("pct"), (int, float))]
    vals_sorted = sorted(vals)
    median = vals_sorted[len(vals_sorted) // 2] if vals_sorted else None
    up = sum(1 for v in vals if v > 0)
    down = sum(1 for v in vals if v < 0)
    strong = sum(1 for v in vals if v > 2)
    weak = sum(1 for v in vals if v < -2)

    ranked = [b for b in boards if isinstance(b.get("pct"), (int, float))]
    # 涨跌幅榜均剔除伪板块（"昨日/最近/地域统计"这类交易标签无复盘价值）
    top_up = sorted([b for b in ranked if not _is_noise_board(b.get("name"))],
                    key=lambda x: -x["pct"])[:top_n]
    top_down = sorted([b for b in ranked if not _is_noise_board(b.get("name"))],
                      key=lambda x: x["pct"])[:10]
    bmap = {b["code"]: b for b in boards}
    # 板块成分数 → 用于"涨停占板块比"，衡量资金聚焦度
    size_map: dict = {}
    for b in boards:
        sz = (b.get("up") or 0) + (b.get("down") or 0) + (b.get("flat") or 0)
        size_map[b["code"]] = int(sz or b.get("count") or 0)

    # 题材情绪温度（0~100）：板块涨幅中位数 + 强弱板块占比综合
    temp = 50.0
    if vals:
        temp = round(max(0.0, min(100.0,
                                  50.0 + (median or 0) * 8.0
                                  + (strong - weak) / max(1, len(vals)) * 45.0)), 1)

    # 涨停贡献榜：实时走"涨停股 → 所属概念"映射；历史重建直接用板块行自带的涨停家数
    if zt_stocks:
        contrib, _sm = _zt_concept_contrib(zt_stocks, size_map, concept_codes)
        src = "live"
    else:
        contrib = [
            {"code": b["code"], "name": b["name"], "pct": b.get("pct"),
             "count": int(b.get("zt") or 0),
             # V1.009.5 修 bug：此处原为硬编码 "lbc_max": 0，导致历史日期
             # 前端的红色「N板」标签永不显示（实时正常、重建恒 0）。
             # 现在 rebuild_board_daily 已输出 lbc_max / lbc2 / amt。
             "lbc_max": int(b.get("lbc_max") or 0),
             "lbc2": int(b.get("lbc2") or 0),
             "amt": b.get("amt") or 0,
             "codes": [],
             "size": size_map.get(b["code"]) or 0,
             "ratio": (round(int(b.get("zt") or 0) / size_map[b["code"]] * 100, 1)
                       if size_map.get(b["code"]) else None)}
            for b in boards
            if int(b.get("zt") or 0) > 0 and not _is_noise_board(b.get("name"))
        ]
        contrib.sort(key=lambda x: (-x["count"],
                                    -(x["pct"] if isinstance(x["pct"], (int, float)) else -99)))
        src = "rebuild"

    # {code: 涨停家数} —— **实时板块行（东财 clist）没有涨停家数字段**，涨停家数一律
    # 由涨停贡献榜给出。下面凡是要用当日涨停家数的地方（当日日截面、今日启动信号）
    # 都必须过这个映射，否则实时日会静默取到 0：条件 b「有涨停」恒假 → 实时日持续性
    # 恒为 0，而历史重建日正常，形成一条只在实时路径出现、不报错的分叉。
    _zt_cnt = {c["code"]: int(c.get("count") or 0) for c in contrib}

    # ---- 主线题材：资格线筛池 → 四维打分 → 分级（V1.009.5 取代旧的"取前 N 名"）----
    # 涨幅 >= 全市场概念的 95 分位（资格线条件③用）
    _pct_all = sorted([b["pct"] for b in boards if isinstance(b.get("pct"), (int, float))])
    pct_q = (_pct_all[min(len(_pct_all) - 1, int(len(_pct_all) * _THEME_QUAL_PCT_Q))]
             if _pct_all else None)

    # 候选池 = 涨停贡献榜（含实时映射 / 重建板块行）+ 涨幅榜里未出现的板块
    cand_all: list[dict] = []
    seen_c: set[str] = set()
    for c in contrib:
        if c["code"] not in seen_c:
            seen_c.add(c["code"])
            cand_all.append(c)
    for b in top_up:
        if b["code"] in seen_c:
            continue
        seen_c.add(b["code"])
        cand_all.append({
            "code": b["code"], "name": b["name"], "pct": b["pct"],
            "count": int(_zt_cnt.get(b["code"], b.get("zt")) or 0),
            "lbc_max": int(b.get("lbc_max") or 0),
            "lbc2": int(b.get("lbc2") or 0),
            "amt": b.get("amt") or 0,
            "size": size_map.get(b["code"]) or 0, "ratio": None, "codes": [],
        })

    cand = [c for c in cand_all
            if _theme_qualified(c, pct_q) and not _is_noise_board(c.get("name"))]
    # 兜底：极端冰点日可能一个都过不了资格线（这本身是信号），但仍退回涨幅榜前列
    # 保证模块不空白；此时 tier 全为 edge，前端会显示"无主线"。
    if not cand:
        cand = [c for c in cand_all if not _is_noise_board(c.get("name"))][:3]

    # 历史日截面一次取够 —— 「首日启动」标注要看近 _FIRSTDAY_LOOKBACK 日，
    # 持续性只看末 _THEME_WIN 日；共用同一份索引，避免重复遍历 504 个板块的序列。
    _hist_days = _hist_dates(hist, date_str, _FIRSTDAY_LOOKBACK)
    day_idx = _day_index(hist, _hist_days)
    if date_str:
        # 当日用实盘 boards 覆写（hist 通常不含今天，见 _today_day_rows 注释）
        day_idx[date_str] = _today_day_rows(boards, size_map, _zt_cnt)
    sh_map = (index_pct or {}).get("上证指数") or {}
    dates_use = _hist_days[-_THEME_WIN:]
    scored, theme_w = _theme_score(cand, day_idx, dates_use, sh_map)
    main: list[dict] = scored[:_THEME_MAX1]

    # 涨幅榜标注「首日启动」（V1.009.6）—— 只看此前的在榜历史，当日不计入
    # ⚠️ 同时把当日**真实涨停家数**写进行里：涨幅榜行来自原始实时板块行，而实时行
    # 没有 zt 字段，前端若自己取 b.zt 会恒为 0，「首日/延续/无涨停」三态就永远
    # 落到「无涨停」。这里用 _zt_cnt 统一补齐（与当日日截面同一份映射）。
    _tu_src, top_up = top_up, []
    for b in _tu_src:
        _z = int(_zt_cnt.get(b["code"], b.get("zt")) or 0)
        _r = dict(b, zt=_z)
        _r.update(_firstday_info(day_idx, b["code"], _hist_days, sh_map, date_str,
                                 zt_today=_z))
        top_up.append(_r)

    # 今日启动（V1.009.6，方案 A）：涨幅进全市场概念前 _EMERGING_TOP 且涨停占比
    # >= _EMERGING_RATIO 的板块**单列**。它不参与持续性/高度排序、不进主线表 ——
    # 目的是补上「启动首日 / 重启首日」这个四维打分的结构性盲区（历史组占 60%，
    # 新启动题材结构上拿不到高分），同时不让一日游题材污染主线判断。
    # in_main 标记它是否已同时进主线表，前端据此区分展示。
    # ⚠️ 当日涨停家数走 _zt_cnt（实时板块行没有 zt 字段，直接取会恒为 0）
    _top_by_pct = sorted([b for b in ranked if not _is_noise_board(b.get("name"))],
                         key=lambda x: -x["pct"])[:_EMERGING_TOP]
    _main_codes = {m["code"] for m in main}
    emerging = []
    for b in _top_by_pct:
        _esz = size_map.get(b["code"]) or 0
        _ezt = int(_zt_cnt.get(b["code"], b.get("zt")) or 0)
        _eratio = (_ezt / _esz * 100) if _esz else 0.0
        if _eratio < _EMERGING_RATIO:
            continue
        emerging.append({
            "code": b["code"], "name": b["name"], "pct": b.get("pct"),
            "zt": _ezt, "ratio": round(_eratio, 1), "size": _esz,
            "in_main": b["code"] in _main_codes,
        })

    main_lines = []
    for m in main:
        zt_cnt = int(m.get("count") or 0)
        # 当日也要带上四维用的字段并进序列 —— 否则当日退化成"只有 pct/zt"的残缺行，
        # 明天回看今天时算不出持续性/高度/容量，打分口径就会分叉。
        seq_use = _merge_today_seq(
            hist, m["code"], date_str, m.get("pct"), zt_cnt,
            {"name": m.get("name"),
             "ratio": m.get("ratio"), "size": m.get("size"),
             "lbc_max": int(m.get("lbc_max") or 0),
             "lbc2": int(m.get("lbc2") or 0),
             "amt": m.get("amt") or 0})
        if len(seq_use) >= 3:   # 有历史序列 → 真实周期阶段；否则退化为"当日强度"口径
            stage, reason = _stage_of(m.get("pct"), zt_cnt, seq_use)
        else:
            stage, reason = _stage_today(m.get("pct"), zt_cnt, m.get("ratio"))
        bd = bmap.get(m["code"]) or {}
        # 角色分层：只对进入主线题材的板块算（每板块一次成分股扫描，成本可控）
        _roles = None
        if roles_ctx and roles_members and date_str:
            _mem = (roles_members.get(m["code"]) or {}).get("members") or []
            if _mem:
                try:
                    _roles = board_roles(_mem, roles_ctx, date_str, m.get("pct"))
                except Exception as _re:  # noqa: BLE001
                    _roles = {"leader": [], "main_force": [], "follower": [], "catchup": [],
                              "pool": 0,
                              "note": "角色判定异常：%s: %s" % (type(_re).__name__, _re)}
        # 大类赛道（V1.009.5）：把「芯片概念 / 半导体概念 / 光刻胶 / 存储芯片」这类同赛道
        # 细分收敛到一个标签，避免一眼看去像 4 条互不相干的独立主线。
        # 横切属性（央国企改革等）同样会返回，前端靠 track_cross 区分展示样式。
        # 读缓存的历史快照由 theme_taxonomy.attach_tracks() 在 cache_get 里补齐。
        _track = _tax.track_of(m["code"], m.get("name"))
        main_lines.append({
            "code": m["code"],
            "name": m["name"],
            "track": _track,
            "track_cross": bool(_track) and not _tax.is_track(_track),
            "pct": m.get("pct"),
            "zt_count": zt_cnt,
            "max_lbc": int(m.get("lbc_max") or 0),
            "lbc2": int(m.get("lbc2") or 0),
            "amt": m.get("amt") or 0,
            # ---- 四维打分结果（V1.009.5）----
            "score": m.get("score"),
            "tier": m.get("tier"),
            "dims": m.get("dims"),
            "weights": m.get("weights"),
            "focus_raw": m.get("focus_raw"),
            "height_raw": m.get("height_raw"),
            "capacity_raw": m.get("capacity_raw"),
            "persist_days": m.get("persist_days"),
            "ratio": m.get("ratio"),
            "size": m.get("size"),
            "up": bd.get("up"),
            "down": bd.get("down"),
            "lead_name": bd.get("lead_name"),
            "lead_pct": bd.get("lead_pct"),
            "mainflow": bd.get("mainflow"),
            "stage": stage,
            "reason": reason,
            "zt_codes": (m.get("codes") or [])[:12],
            "seq": seq_use[-10:],
            "roles": _roles,
        })

    stage_count: dict[str, int] = {}
    for line in main_lines:
        stage_count[line["stage"]] = stage_count.get(line["stage"], 0) + 1

    # ---- 今日主线数 / 市场状态 / 主线切换（V1.009.5 新增信号）----
    core_codes = {x["code"] for x in main_lines if x.get("tier") == "core"}
    n_core = len(core_codes)
    mstate, mstate_note = _market_state(n_core)

    def _nm_of(c):
        return (bmap.get(c) or {}).get("name") or c

    # 「在榜」= 持续性三条件当日成立（a 涨幅>上证 ∧ b 有涨停 ∧ c 上涨家数>成分股×60%）
    # —— 必须与持续性维度用**同一把尺子**，否则「进/出名单」与「持续性得分」互相矛盾。
    # 注意：新口径的在榜集合明显大于旧的「涨停家数前 15」（实测 09-14 全市场 86 个
    # vs 旧口径最多 15 个），所以 drop 靠下面的 [:8] 截断控制展示量。
    prev_codes: set = set()
    _prev_z: dict = {}
    prev_date = dates_use[-2] if len(dates_use) >= 2 else None
    if prev_date:
        prev_codes = _onboard_set(day_idx, prev_date, sh_map)
        _prev_slot = day_idx.get(prev_date) or {}
        _prev_z = {c: int((_prev_slot.get(c) or {}).get("zt") or 0) for c in prev_codes}
    # 今日「在榜」集合（drop 的对照基准，见下）
    today_on = _onboard_set(day_idx, dates_use[-1], sh_map) if dates_use else set()

    # 进出两个名单**必须用同一把尺子**，否则名单长度会悬殊到没法看。
    # 原来 new 取"今日核心「减去」昨日在榜"（严进），drop 取"昨日在榜「减去」今日核心"
    # （宽出）—— 两边基准不同，于是 new 常常是 0 条，drop 却是十几条（实测 09-11 为
    # 14 条），把"掉出"变成了"昨日热门里今天没当上核心的几乎所有板块"，等于没说。
    # 现在把 drop 收紧到「昨日在榜 且 今日掉出在榜」，与 new 形成严进严出的一对：
    #   new  = 今日核心主线中，昨日还不在榜的（抬头信号，最该看）
    #   drop = 昨日在榜的题材中，今日已掉出在榜的（退潮信号）
    # 按昨日涨停家数降序并限 8 条 —— 前端是一排标签，十几条会糊成一片。
    drop_codes = sorted(prev_codes - today_on, key=lambda c: -_prev_z.get(c, 0))[:8]
    main_switch = {
        "prev_date": prev_date,
        "keep": [{"code": c, "name": _nm_of(c)} for c in sorted(core_codes & prev_codes)],
        "new": [{"code": c, "name": _nm_of(c)} for c in sorted(core_codes - prev_codes)],
        "drop": [{"code": c, "name": _nm_of(c)} for c in drop_codes],
    }

    return {
        "available": True,
        "source": src,
        # ---- V1.009.5：主线打分口径元信息 ----
        "theme_weights": theme_w,
        "theme_candidates": len(cand),
        "theme_qualified_total": len(scored),
        "pct_q95": (round(pct_q, 2) if isinstance(pct_q, (int, float)) else None),
        "theme_win": len(dates_use),
        # 资金容量的内部配比（V1.009.6）：涨停股成交额 : 板块涨幅 = w : 1-w
        "theme_cap_amt_w": _CAP_AMT_W,
        # 在榜三条件的阈值（V1.009.6）—— 下发到前端，避免 TIP 文案里写死数字后
        # 与后端常量脱钩（改口径只改实现、忘了改文案是最容易漏的一处）
        "theme_persist_up_ratio": _PERSIST_UP_RATIO,
        "theme_firstday_lookback": _FIRSTDAY_LOOKBACK,
        "theme_emerging_top": _EMERGING_TOP,
        "theme_emerging_ratio": _EMERGING_RATIO,
        # 今日启动信号（V1.009.6）：与主线表互相独立，不做排序过滤
        "emerging": emerging,
        "mains_count": n_core,
        "market_state": mstate,
        "market_state_note": mstate_note,
        "main_switch": main_switch,
        "temperature": temp,
        "total": len(boards),
        "up": up,
        "down": down,
        "flat": len(boards) - up - down,
        "strong": strong,
        "weak": weak,
        "median_pct": round(median, 2) if median is not None else None,
        "top_up": top_up,
        "top_down": top_down,
        "zt_contrib": contrib[:20],
        "main_lines": main_lines,
        "stage_count": stage_count,
        "hist_used": bool(hist),
        "note": "" if hist else
                "尚未建立板块历史序列：周期阶段为「当日强度」口径；"
                "执行「历史数据重建」后可获得启动 / 发酵 / 高潮 / 退潮等真实周期阶段",
    }


# ---------------------------------------------------------------------------
# 历史回溯重建（V1.009.1 新增）
#   东财的"实时快照"类接口（涨跌家数分布、概念板块排行）无法按历史日期查询，
#   涨停池回溯窗口也只有约 15 个交易日。这里用全市场个股日K（新浪源，单只约
#   0.05s，5900 只并发 10 约 25s）回溯重建历史每日的市场宽度与板块表现。
# ---------------------------------------------------------------------------

_SINA_KLINE = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
_TX_FQKLINE = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"
_THS_LINE = "http://d.10jqka.com.cn/v6/line/hs_%s/%s/last.js"  # (code, 复权flag)
_TX_HEADERS = {"User-Agent": md.UA, "Referer": "https://gu.qq.com/"}
_THS_HEADERS = {"User-Agent": md.UA, "Referer": "http://q.10jqka.com.cn/"}
_KLINE_WORKERS = 6
_UNIVERSE_FIELDS = "f12,f13,f14"
_UNIVERSE_PAGE = 100
_UNIVERSE_MAX_PAGES = 70


def _sina_symbol(code: str) -> str:
    """6 位代码 → 新浪 symbol（sh / sz / bj）"""
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    return "bj" + code


def _norm_symbol(code: str) -> str:
    """6 位代码或已带 sh/sz/bj 前缀的 symbol → 统一带前缀 symbol

    指数（sh000001 / sz399001 / bj899050）本身已带前缀，不能再次加前缀
    （否则 "sh000001" 会被拼成 "bjsh000001"，数据源必然返回空）。
    """
    c = (code or "").strip()
    if c[:2].lower() in ("sh", "sz", "bj"):
        return c
    if len(c) == 6 and c.isdigit():
        return _sina_symbol(c)
    return c


def _limit_rates(code: str, name: str = "") -> list[float]:
    """当日可能的涨跌幅限制比例（升序候选）

    为什么给候选而不是单一比例：主板 ST/*ST 常态为 5%，但「撤销风险警示当日」
    「重大事项复牌」等情形实际按 10% 执行 —— 实测近 60 个交易日里 323 只 ST 中
    26% 出现过约 10% 的单日涨幅（如 *ST美芝 连续 +10.00% / +6.63% / +8.24%），
    而东财涨停池本身**不收录 ST 股**（实测当日 35 只涨停池内 ST 为 0），
    无法用它交叉验证。日K又不含「名称随时间变化」的信息，静态按名称判 5%
    会同时造成涨停漏算与炸板误判，故给出候选，再由当日价格反推
    真正适用的那个（见 _limit_rate_for）。

    顺序注意：先判板块 —— 创业板/科创板/北交所的 ST 股限制与普通股相同
    （20% / 30%），不能因为名称含 ST 就压到 5%。
    """
    if code.startswith(("30", "688", "689")):
        return [0.20]
    if code.startswith(("8", "4", "920")):
        return [0.30]
    if _is_st_name(name):
        return [0.05, 0.10]
    return [0.10]


def _limit_rate(code: str, name: str = "") -> float:
    """首选涨跌幅限制比例（兼容旧调用方）—— ST 取常态 5%"""
    return _limit_rates(code, name)[0]


def _limit_rate_for(prev: float, code: str, name: str, px: float,
                    tol: float = 0.005) -> float | None:
    """由当日出现的价格反推「当日实际适用」的涨跌幅限制比例

    原理：**价格不可能超过涨停价**。因此任何使 px 超出的候选比例都不成立，
    取剩下最小的候选即为该日真实比例。

    tol 是「基数本身的不确定度」（普通日 0.005、除权日 0.015，见 limit_base），
    必须用**绝对容差**而非 `lp*1.002` 这类相对项 —— 后者对高价股会松到 0.4 元
    （lp=200 时），足以把「超出涨停价 1 分」误判成同一比例，实测踩到：
    002306 ST云网 前收 2.37、5% 涨停价 2.49，而当日最高价 2.50 已越界 →
    说明当日实际比例为 10%（涨停价 2.61），该股并未触板，原实现却按 5% 记成炸板。

    例（主板 ST，前收 5.01，候选 {5%,10%} → 涨停价 {5.26, 5.51}）：
      px = 5.10（涨 1.8%）→ 两候选都成立 → 取 5%（常态）
      px = 5.46（涨 9.0%）→ 5% 被否（超出 5% 涨停价）→ 取 10%（摘帽等特殊日）
    """
    if not prev or prev <= 0 or px is None:
        return None
    for r in _limit_rates(code, name):
        lp = _limit_price(prev, code, name, True, rate=r)
        if not lp:
            continue
        if px <= lp + tol:
            return r
    return None


def _limit_price(prev: float, code: str, name: str = "", up: bool = True,
                 rate: float | None = None) -> float:
    """涨停（或跌停）价：前收 × (1 ± 限制比例)，取至分（交易所口径）

    三个必须注意的点（每个都曾造成涨停漏判）：
    1. 必须用十进制取整 —— Python 内置 round() 是「银行家舍入」；
    2. 必须用 **str() 构造 Decimal** —— Decimal(45.05) 会取二进制精确值
       45.049999…，乘 1.1 后得 49.5549…，被舍成 49.55（正确应为 49.56）；
    3. **取整方向随市场不同**（实测标定，见下）——
       沪深（主板/创业板/科创板）：ROUND_HALF_UP，允许略微越界。
         例 剑桥科技 188.37×1.1 = 207.207 → 207.21（涨幅 10.0005% > 10%）；
       北交所：不越界取整 —— 涨停向下取整、跌停向上取整。
         例 花溪科技 16.25×1.3 = 21.125 → 21.12（21.13 会到 30.03% > 30%）。
       标定样本：北交所有区分度的 40 例中 39 例符合「不越界」，沪深 547 例涨停中
       544 例符合「四舍五入」；混用会让北交所涨停系统性漏判（实测 12 个交易日
       754 只涨停漏 9 只，其中 8 只全是北交所）。

    rate 显式给定时用它，否则按名称取首选比例。
    """
    if not prev or prev <= 0:
        return 0.0
    r = rate if rate is not None else _limit_rate(code, name)
    one = _DEC("1")
    factor = one + _DEC(str(r)) if up else one - _DEC(str(r))
    v = _DEC(str(prev)) * factor
    if code.startswith(("8", "4", "920")):
        mode = ROUND_DOWN if up else ROUND_CEILING
    else:
        mode = ROUND_HALF_UP
    return float(v.quantize(_DEC("0.01"), rounding=mode))


def _is_limit_up(close: float, prev: float, code: str, name: str = "",
                 high: float | None = None) -> bool:
    """是否收盘涨停（收盘价恰好等于涨停价）

    high 给定时先用当日最高价反推当日实际适用的限制比例（覆盖 ST 摘帽等
    特殊日从 5% 切到 10% 的情形），再判定收盘是否封住。
    """
    r = _limit_rate_for(prev, code, name, high) if high is not None else None
    lp = _limit_price(prev, code, name, True, rate=r)
    return bool(lp and abs(float(close) - lp) < 0.005)


def _is_limit_down(close: float, prev: float, code: str, name: str = "",
                   high: float | None = None) -> bool:
    """是否收盘跌停（收盘价恰好等于跌停价）

    涨跌幅度对称，故同样用当日最高价反推比例：若某日 high 超过了 5% 涨停价，
    说明当日并不适用 5%，跌停价也应按 10% 计算。
    """
    r = _limit_rate_for(prev, code, name, high) if high is not None else None
    lp = _limit_price(prev, code, name, False, rate=r)
    return bool(lp and abs(float(close) - lp) < 0.005)


_LIMIT_TOL = 0.005          # 普通日：涨跌停价精确到分，半分即判定余量
_LIMIT_TOL_EXDIV = 0.015    # 除权日：基数含 ±1 分的不可消除不确定性（见 limit_base）


def limit_base(prev_bar, bar, code: str = "", name: str = "") -> tuple:
    """当日涨跌停价的基数 = **交易所公布的「除权参考价」** → (base, tol)

    为什么不能用单一口径（实测 547 个非除权日涨停 + 2 个除权日涨停逐一比对）：

      A 前复权前收 —— 除权日**之前**的日期会被整体缩水。前复权是对**全部历史**乘以
        同一个最新因子，故只要窗口内后面发生过除权，之前每一天的前收都会被下调。
        实测 547 例中 A 只命中 426 例；极端如 300920 会把 29.92 当成前收，配错比例后
        还能伪造出涨停（假阳性）。
      B 不复权前收 —— 非除权日 **544/547 精确命中**（含 A 漏掉的 118 例）；但除权日
        当天会算高：天赐材料 2026-04-29 前收 54.23 而除权参考价 53.93，
        B 给出涨停价 59.65 而实际是 59.32，整段涨停被判丢。
      C 除权参考价 = 前复权前收 × 当日不复权收 / 当日前复权收
         推导：除权日 D 有 f(D-1) = g_D·f(D)（g_D = 除权参考价/实际前收），
               除权参考价 = 实际(D-1)·g_D = [前复权(D-1)/f(D-1)]·g_D = 前复权(D-1)/f(D)，
               而 1/f(D) = 实际(D)/前复权(D)，代入即得。
         非除权日 f(D-1)=f(D) → C 恒等于 B；除权日 C 才启用。
         实测 2 例除权日涨停 C 均只差 1 分（59.31 vs 59.32 / 55.87 vs 55.86）——
         因为 前复权前收 本身只精确到分（±0.005），乘 1/f 后这一误差无法消除，
         **是数据精度上限，不是算法缺陷**，故除权日放宽判定余量到 1.5 分。

    除权日的判定同样是精确的：前复权因子**只在除权日跳变**，故
        当日 pct(不复权) ≠ pct(前复权)  ⟺  当日是除权日
    阈值按价格缩放 —— 两个价各带半分舍入，pct 误差上界 ≈ 2.1/前收 个百分点。

    无真不复权价的源（腾讯 bfq 参数无效、新浪本就不复权）退化为 A（改造前行为）。
    """
    qp = bar_parts(prev_bar)[1]                 # 前复权前收
    qc = bar_parts(bar)[1]                      # 前复权当日收
    rp = bar_close_raw(prev_bar)                # 不复权前收
    rc = bar_close_raw(bar)                     # 不复权当日收
    if not qp:
        return None, _LIMIT_TOL
    if rp is None or rc is None or not qc:
        return float(qp), _LIMIT_TOL            # 无复权价 → 退化
    pct_q = (qc - qp) / qp * 100.0
    pct_r = (rc - rp) / rp * 100.0
    if abs(pct_r - pct_q) > (2.5 / rp + 0.02):
        cand = float(qp * rc / qc)
        # 交叉校验：**价格不可能超过涨停价**。若当日最高价已越出候选基数对应的涨停价，
        # 说明候选偏低 —— 前复权因子存在假跳变（实测 301151 冠龙节能 2026-08-31：
        # 同花顺 17.08/20.54 给出的基数 17.2465 会推出涨停价 20.70，而当日实际成交
        # 20.74，交易所公布的涨跌幅 20.02% 也证明当天并未除权）。此时以不复权前收为准。
        # 用**最大**候选比例做校验，避免把 ST 摘帽（5%→10%）误判成假跳变。
        hi = bar_high_raw(bar)
        if hi is None:
            hi = bar_high(bar)
        if hi is not None:
            _r0 = max(_limit_rates(code, name))
            if cand > 0:
                lp = _limit_price(cand, code, name, True, rate=_r0)
                if lp and hi > lp + _LIMIT_TOL_EXDIV:
                    return float(rp), _LIMIT_TOL
        return cand, _LIMIT_TOL_EXDIV        # 除权日 → 除权参考价
    return float(rp), _LIMIT_TOL             # 非除权日 → 不复权前收（精确）


def limit_state(prev_bar, bar, code: str, name: str = "") -> tuple:
    """单根K线的涨跌停/炸板状态 → (flag, zb_high, zt_price)

    flag：1 = 收盘涨停、-1 = 收盘跌停、0 = 都不是
    zb_high / zt_price：仅当**炸板**（盘中最高价触及涨停价但收盘未封住）时非 None

    四个调用点（板块重建、全市场重建、昨涨停今表现、炸板回溯）共用同一判定，
    避免各处口径漂移。涨跌停一律用**不复权**价比较（除权参考价口径见 limit_base）。
    """
    base, tol = limit_base(prev_bar, bar, code, name)
    if not base or base <= 0:
        return 0, None, None
    close = bar_close_raw(bar)
    if close is None:
        close = bar_parts(bar)[1]
    high = bar_high_raw(bar)
    if high is None:
        high = bar_high(bar)
    if close is None:
        return 0, None, None
    r = _limit_rate_for(base, code, name, high, tol) if high is not None else None
    ulp = _limit_price(base, code, name, True, rate=r)
    dlp = _limit_price(base, code, name, False, rate=r)
    if ulp and abs(float(close) - ulp) < tol:
        return 1, None, None
    if dlp and abs(float(close) - dlp) < tol:
        return -1, None, None
    if high is not None and ulp and high >= ulp - tol:
        return 0, float(high), ulp
    return 0, None, None


def _limit_threshold(code: str, name: str = "") -> float:
    """涨跌停判定阈值（%）—— 保留给旧调用方，新代码优先用 _is_limit_up 精确判定"""
    return round(_limit_rate(code, name) * 100 - 0.4, 2)


def _norm_date(s) -> str:
    """'20260911' / '2026-09-11' → '2026-09-11'"""
    t = str(s or "").strip()
    if len(t) == 8 and t.isdigit():
        return "%s-%s-%s" % (t[:4], t[4:6], t[6:8])
    return t[:10]


def _sina_kline(code: str, datalen: int):
    """新浪日K（对高频批量请求会临时封 IP，作为末位兜底）

    新浪给的是**不复权**价，故末两位（不复权收/高）与前面的价一致；
    也正因如此，它的涨跌幅在除权日会失真 —— 仅作兜底用。
    返回 [(date, close, volume股, amount元|None, high, close_raw, high_raw)]。
    """
    url = "%s?symbol=%s&scale=240&ma=no&datalen=%d" % (
        _SINA_KLINE, _norm_symbol(code), max(5, int(datalen)))
    j = md._fetch_json(url, headers=md.SINA_HEADERS, timeout=8, retries=1)
    if not isinstance(j, list):
        return None
    out = []
    for x in j:
        try:
            hi = x.get("high")
            cl = float(x["close"])
            h = float(hi) if hi not in (None, "") else None
            out.append((_norm_date(x["day"]), cl, float(x.get("volume") or 0),
                        None, h, cl, h))
        except Exception:  # noqa: BLE001
            continue
    return out or None


def _tx_volume_unit(code: str) -> float:
    """腾讯 fqkline 成交量的单位系数 → 统一换算成「股」

    实测（各 8~24 只样本逐一比对同花顺口径）：
      科创板 688/689 → 返回单位已是「股」，系数 1
      主板 / 创业板 → 返回单位是「手」，系数 100
    两段完全互斥且稳定；不做区分会让科创板成交额虚增 100 倍（曾使全市场
    成交额从 2.0 万亿虚增到 27 万亿）。
    """
    return 1.0 if code.startswith(("688", "689")) else 100.0


def _tx_kline(code: str, datalen: int):
    """腾讯日K（proxy.finance 域名，支持沪深与指数；不支持北交所）

    腾讯 bar 列序：[日期, 开, 收, 高, 低, 量, ...] → 取 b[2]=收、b[3]=高、b[5]=量。
    **腾讯的 bfq 参数实测无效**（bfq 与 qfq 返回同一组价格，是前复权口径），
    因此末两位（不复权收/高）只能填同值 —— 由调用方退化为前复权判定，
    这也是同花顺优先的原因。
    返回 [(date, close, volume股, None, high, close_raw, high_raw)]。
    """
    sym = _norm_symbol(code)
    if sym.startswith("bj"):
        return None
    n = min(640, max(int(datalen), 20))
    j = md._fetch_json("%s?param=%s,day,,,%d,qfq" % (_TX_FQKLINE, sym, n),
                       headers=_TX_HEADERS, timeout=10, retries=1)
    try:
        d = ((j or {}).get("data") or {}).get(sym) or {}
        bars = d.get("qfqday") or d.get("day") or []
    except Exception:  # noqa: BLE001
        return None
    unit = _tx_volume_unit(code)
    out = []
    for b in bars:
        try:
            cl = float(b[2])
            h = float(b[3]) if len(b) > 3 and b[3] not in (None, "") else None
            out.append((_norm_date(b[0]), cl, float(b[5] or 0) * unit, None, h, cl, h))
        except Exception:  # noqa: BLE001
            continue
    return out or None


def _ths_parse(sym: str, flag: str) -> dict:
    """同花顺某一复权口径的日K → {日期: (高, 收, 量股, 额元|None)}

    flag：`01` 前复权 / `00` 不复权。价格与成交量都随口径变化，成交额不变。
    """
    r = md._fetch(_THS_LINE % (sym, flag), headers=_THS_HEADERS, timeout=10, retries=1)
    if not r:
        return {}
    try:
        data = json.loads(r[r.index("(") + 1: r.rindex(")")]).get("data") or ""
    except Exception:  # noqa: BLE001
        return {}
    d: dict = {}
    for line in data.split(";"):
        p = line.split(",")
        if len(p) < 7:
            continue
        try:
            amt = float(p[6])
            d[_norm_date(p[0])] = (float(p[2]), float(p[4]), float(p[5]),
                                   amt if amt > 0 else None)
        except Exception:  # noqa: BLE001
            continue
    return d


def _ths_kline(code: str, datalen: int):
    """同花顺日K（JSONP，近 140 个交易日；覆盖沪/深/北全部市场）

    data 列序：p[0]日期 p[1]开 p[2]高 p[3]低 p[4]收 p[5]成交量(股) p[6]成交额(元) p[7]换手率

    **同时取两个口径**（两次请求）—— 本项目里唯一既能给精确成交额、又能同时提供
    不复权价的源，而这两件事都不可省：
      flag=01 前复权 —— 跨除权日的涨跌幅才连续可比。分红季里约 66% 的个股在 60 日
                 窗口内发生除权除息（实测抽样 362 只中 240 只），若直接用不复权价
                 算涨跌幅，除息除权的跳空会被算成暴跌；
      flag=00 不复权 —— **交易所涨跌停价必须基于不复权前收**。前复权会下调除权日
                 「之前」的价格，使除权日当日的「前一日收盘」失真。实例：剑桥科技
                 09-04 前复权 188.28 / 不复权 188.37，导致 09-07 涨停价算成 207.11
                 而实际为 207.21，收盘涨停被误判成炸板。

    返回 [(date, close_qfq, volume, amount, high_qfq, close_raw, high_raw)]。
    不复权请求失败时末两位为 None，调用方退化为用前复权判定（即改造前行为）。
    """
    sym = _norm_symbol(code).lstrip("shzbj")
    q = _ths_parse(sym, "01")
    if not q:
        return None
    raw = _ths_parse(sym, "00")
    out = []
    for date, (hi, cl, vol, amt) in q.items():
        rh, rc = (raw.get(date) or (None, None))[:2] if raw else (None, None)
        out.append((date, cl, vol, amt, hi, rc, rh))
    out.sort(key=lambda x: x[0])
    return out or None


def bar_parts(b) -> tuple:
    """K线元素 → (date, close, volume股, amount元|None)

    兼容 3 元组（旧缓存 / 无成交额的源）、4 元组与 5 元组
    （第 5 位为最高价，见 bar_high）。
    """
    a = b[3] if len(b) > 3 else None
    return b[0], b[1], b[2], a


def bar_high(b) -> float | None:
    """单根K线的最高价（前复权口径）

    3/4 元组（早期缓存、或未升级的源）没有该位 → 返回 None，
    调用方（炸板回溯）需自行跳过。
    """
    if len(b) <= 4:
        return None
    v = b[4]
    try:
        return float(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def bar_close_raw(b) -> float | None:
    """单根K线的不复权收盘价

    **涨跌停价判定必须用它**：交易所的涨跌停价由不复权前收算出，而前复权会下调
    除权日之前的价格，使除权日当日的「前一日收盘」失真。缺失（<6 元组）返回 None，
    调用方退化为用前复权收盘价。
    """
    if len(b) <= 5:
        return None
    v = b[5]
    try:
        return float(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def bar_high_raw(b) -> float | None:
    """单根K线的不复权最高价（炸板判定用）；缺失返回 None"""
    if len(b) <= 6:
        return None
    v = b[6]
    try:
        return float(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def bar_amount(b) -> float:
    """单根K线的成交额（元）：优先用数据源给的精确值，缺失则用 收盘价×成交量 估算"""
    d, close, vol, amt = bar_parts(b)
    if isinstance(amt, (int, float)) and amt > 0:
        return float(amt)
    return float(close) * float(vol or 0)


def broken_from_klines(klines: dict, date_str: str, names: dict | None = None) -> dict:
    """由个股日K自建「炸板」池 —— 东财炸板池仅最近约 15 个交易日可查，更早无源

    口径：当日最高价触及涨停价，但收盘价未封住涨停。
        touched = high ≥ 涨停价
        sealed  = close ≥ 涨停价
        炸板    = touched and not sealed

    与东财口径的差异（已在报告与页面上标注）：
      东财炸板池 = 盘中封过板且开过板，含尾盘又封回去的「回封板」（其 zbc 字段记录开板次数）；
      日K只有最高价，无法判断盘中是否真的封住过，因此
        ① 会少计「回封板」（实测占比约 1~2%）；
        ② 会把「只是瞬时摸到涨停价、全天未封」计入（东财可能不计）。
      实测（与东财池逐日比对）：最近 3 个交易日 18/22/27 只**全部命中**，
      扩到 8 个交易日合计召回 99.4%；以涨停池作反例零误判。

    返回 {"count", "amount", "stocks": [...]}
    """
    names = names or {}
    stocks = []
    for code, bars in (klines or {}).items():
        if not bars:
            continue
        idx = next((i for i, b in enumerate(bars) if str(b[0]) == date_str), None)
        if idx is None or idx < 1:
            continue
        name = names.get(code, "")
        # 与全市场重建同源同口径（含除权参考价基数、ST 比例反推），避免两处漂移
        _fl, _zbh, _zbl = limit_state(bars[idx - 1], bars[idx], code, name)
        if _fl or _zbl is None:
            continue                      # 收盘封板 或 未触及涨停价 → 都不是炸板
        _, close, _v, _a = bar_parts(bars[idx])
        _, prev, _pv, _pa = bar_parts(bars[idx - 1])
        cur_raw = bar_close_raw(bars[idx])
        hi_raw = bar_high_raw(bars[idx])
        stocks.append({
            "code": code,
            "name": name,
            "close": round(float(cur_raw if cur_raw is not None else close), 2),
            "high": round(float(hi_raw if hi_raw is not None else _zbh), 2),
            "zt_price": round(float(_zbl), 2),
            # 涨跌幅用前复权（= 官方口径，跨除权日连续），价格用不复权（= 盘面所见）
            "pct": round((float(close) - float(prev)) / float(prev) * 100, 2)
                   if prev else None,
            "amount": bar_amount(bars[idx]),
            "zbc": None,  # 开板次数需分时/tick 数据，日K源无法提供
            "is_st": _is_st(name),
        })
    stocks.sort(key=lambda x: -x["amount"])
    return {
        "count": len(stocks),
        "amount": sum(s["amount"] for s in stocks),
        "stocks": stocks,
    }


def fetch_kline(code: str, datalen: int = 90):
    """个股日K → [(date, close, volume股, amount元|None), ...] 升序；多源自动降级

    源顺序按窗口长度自适应：
      datalen ≤ 130 —— 同花顺优先（唯一给出精确成交额、且覆盖北交所）；
      datalen > 130 —— 腾讯优先（同花顺只提供约 140 个交易日，长窗口不够）。
    腾讯与新浪都不给成交额，调用方用 bar_amount() 兜底估算。
    """
    order = (_ths_kline, _tx_kline, _sina_kline) if datalen <= 130 else \
            (_tx_kline, _ths_kline, _sina_kline)
    for fn in order:
        try:
            rows = fn(code, datalen)
        except Exception:  # noqa: BLE001
            rows = None
        if rows:
            return rows[-int(datalen):] if len(rows) > datalen else rows
    return None


def fetch_klines_multi(codes: list[str], datalen: int = 90, workers: int = _KLINE_WORKERS,
                       on_progress=None, batch: int = 800, pause: float = 1.2) -> dict:
    """并发拉取多只个股日K → {code: [(date, close, volume), ...]}

    分批拉取（每批 batch 只、批间暂停 pause 秒），降低被数据源 WAF 封禁的概率。
    on_progress: 可选回调 (已完成, 总数)
    """
    out: dict = {}
    total = len(codes)
    done = 0
    if not total:
        return out
    for i in range(0, total, max(50, batch)):
        chunk = codes[i:i + batch]
        with _cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(fetch_kline, c, datalen): c for c in chunk}
            for fu in _cf.as_completed(futs):
                c = futs[fu]
                done += 1
                try:
                    r = fu.result()
                except Exception:  # noqa: BLE001
                    r = None
                if r:
                    out[c] = r
                if on_progress and (done % 300 == 0 or done == total):
                    on_progress(done, total)
        if i + batch < total:
            time.sleep(pause)
    return out


def stock_universe() -> list[dict]:
    """全市场股票列表（沪深京 A 股，约 5900 只）→ [{code,name,market}]"""
    first, total = _clist(_FS_ALL_A, _UNIVERSE_FIELDS, 1, pz=_UNIVERSE_PAGE, fid="f12")
    if not first:
        return []
    rows = list(first)
    pages = min(_UNIVERSE_MAX_PAGES, max(1, (total + _UNIVERSE_PAGE - 1) // _UNIVERSE_PAGE))
    if pages > 1:
        def one(pn):
            r, _t = _clist(_FS_ALL_A, _UNIVERSE_FIELDS, pn, pz=_UNIVERSE_PAGE, fid="f12")
            return r

        with _cf.ThreadPoolExecutor(max_workers=5) as ex:
            for part in ex.map(one, range(2, pages + 1)):
                rows.extend(part)
    out, seen = [], set()
    for r in rows:
        c = r.get("f12")
        if not c or c in seen:
            continue
        seen.add(c)
        out.append({
            "code": c,
            "name": (r.get("f14") or "").replace(" ", ""),
            "market": int(r.get("f13") or 0),
        })
    return out


def _blank_breadth() -> dict:
    return {
        "up_gt7": 0, "up_5_7": 0, "up_3_5": 0, "up_0_3": 0,
        "down_0_3": 0, "down_3_5": 0, "down_5_7": 0, "down_gt7": 0,
        "flat": 0, "up_count": 0, "down_count": 0, "total": 0,
        "total_amount": 0.0, "limit_up": 0, "limit_down": 0, "limit_up_ex_st": 0,
        # 主口径 = 剔 ST（limit_up / limit_down 均已剔 ST）；下面两个是同日的含 ST 口径
        "limit_up_inc_st": 0, "limit_down_inc_st": 0,
    }


def board_member_map(boards: list[dict], workers: int = 6, max_pages: int = 10,
                     on_progress=None, cached: dict | None = None,
                     on_fetched=None) -> dict:
    """板块 → 成分股映射 → {board_code: {"name","members":[{code,name,mktcap}]}}

    东财 clist 单次最多返回 100 行，成分股多的板块需分页。上限取 10 页（1000 只）——
    实测大部分板块 ≤6 页即可取全，只有"华为概念"这类超大板块需要 10 页；
    若只取 3 页（300 只），被截断的恰是最热门的题材（通信技术 / 军工 / 光伏概念 /
    低空经济 / 5G概念 …），板块涨跌幅会系统性偏高，且连板与涨停家数漏计。

    cached: {board_code: {"name","members":[...]}} 本地缓存，命中的板块不再发请求；
    on_fetched(code, name, members): 新抓取到成分股时的回调（用于写缓存）。
    """
    cached = cached or {}
    out: dict = {}
    todo: list[dict] = []
    for b in boards:
        hit = cached.get(b["code"])
        if hit and (hit.get("members") or []):
            out[b["code"]] = {"name": hit.get("name") or b.get("name") or b["code"],
                              "members": hit["members"]}
        else:
            todo.append(b)

    def one(bk: dict) -> tuple:
        code = bk["code"]
        members: list[dict] = []
        for pn in range(1, max_pages + 1):
            q = urllib.parse.urlencode({
                "pn": pn, "pz": _BOARD_PAGE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                "fid": "f20", "fs": "b:" + code, "fields": "f12,f14,f20",
            })
            j = md._fetch_json(_API_CLIST + q, headers=md.EM_HEADERS, timeout=12, retries=1)
            d = (j or {}).get("data") or {}
            rows = _diff_list(d.get("diff"))
            if not rows:
                break
            for r in rows:
                c = r.get("f12")
                if not c:
                    continue
                members.append({
                    "code": c,
                    "name": (r.get("f14") or "").replace(" ", ""),
                    "mktcap": r.get("f20") if isinstance(r.get("f20"), (int, float)) else 0.0,
                })
            if len(rows) < _BOARD_PAGE:
                break
        return code, {"name": bk.get("name") or code, "members": members}

    hit_n, total, done = len(out), len(todo), 0
    if on_progress and hit_n:
        on_progress(0, total)
    with _cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = [ex.submit(one, b) for b in todo]
        for fu in _cf.as_completed(futs):
            done += 1
            try:
                code, info = fu.result()
                if info["members"]:
                    out[code] = info
                    if on_fetched:
                        try:
                            on_fetched(code, info["name"], info["members"])
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                pass
            if on_progress and (done % 50 == 0 or done == total):
                on_progress(done, total)
    return out


def rebuild_board_daily(members_map: dict, klines: dict, dates: list[str],
                        on_progress=None, ctx_out: dict | None = None) -> dict:
    """由成分股日K聚合概念板块每日表现 → {date: [{code,name,pct,zt,up,down,lead}]}

    板块涨跌幅 = 成分股涨跌幅的**等权平均**。

    口径经过实测标定：用当日 503 个概念板块与东财实时板块涨跌幅逐一比对，
    等权平均的平均绝对误差 0.028pct（RMSE 0.095），而市值加权为 0.625pct
    （RMSE 0.807，且系统性偏高 +0.43pct）。东财概念板块指数的编制口径即等权，
    此处必须一致，否则同一天的"今天（实时）"与"昨天（重建）"会出现明显跳变。

    涨停家数 = 成分股当日收盘涨停数（收盘价 = 交易所涨停价，见 limit_state）。

    ctx_out: 可选输出参数，回填个股指标与名称映射 —— 角色分层（board_roles）需要
    同一份指标，借此避免重复遍历 5900 只个股日K，也保证两者口径完全同源。
    """
    name_map = {}
    for info in members_map.values():
        for m in (info.get("members") or []):
            if m.get("code") and m.get("name"):
                name_map.setdefault(m["code"], m["name"])
    # 个股每日涨跌幅 / 涨跌停标记 / 成交额 / 连板 / 近N日累计涨幅（全站唯一入口）
    ctx = roles_ctx_build(klines, name_map)
    code_pct: dict[str, dict[str, float]] = ctx["pct"]
    code_limit: dict[str, dict[str, int]] = ctx["lim"]
    # extra: {code: {date: [成交额(元), 连板数|None, 近N日累计涨幅|None]}}
    #   板块维度的"空间高度"（涨停股最高连板）与"资金容量"（涨停股成交额合计）
    #   都取自这里，不再另跑一遍全市场日K。
    code_extra: dict = ctx["extra"]
    if ctx_out is not None:
        ctx_out["ctx"] = ctx
        ctx_out["names"] = name_map

    out: dict[str, list] = {d: [] for d in dates}
    total = len(members_map)
    done = 0
    for bk, info in members_map.items():
        done += 1
        mems = info.get("members") or []
        if not mems:
            continue
        cps = []
        for m in mems:
            c = m["code"]
            p = code_pct.get(c)
            if not p:
                continue
            cps.append((c, m.get("name") or name_map.get(c, ""), p))
        if not cps:
            continue
        for d in dates:
            vals = []
            zt = 0
            up = 0
            down = 0
            lbc_max = 0          # 当日涨停股里的最高连板（空间高度）
            lbc2 = 0             # 当日二板及以上家数（梯队厚度）
            amt = 0.0            # 当日涨停股成交额合计（资金容量）
            lead_n, lead_p = "", None
            for (c, nm, p) in cps:
                pv = p.get(d)
                if pv is None:
                    continue
                vals.append(pv)
                if (code_limit.get(c) or {}).get(d) == 1:
                    zt += 1
                    _e = (code_extra.get(c) or {}).get(d) or []
                    _amt = _e[0] if len(_e) > 0 else None
                    _lbc = _e[1] if len(_e) > 1 else None
                    if isinstance(_amt, (int, float)):
                        amt += float(_amt)
                    _lb = int(_lbc or 1)
                    if _lb > lbc_max:
                        lbc_max = _lb
                    if _lb >= 2:
                        lbc2 += 1
                if pv > 0:
                    up += 1
                elif pv < 0:
                    down += 1
                if lead_p is None or pv > lead_p:
                    lead_p, lead_n = pv, nm
            if not vals:
                continue
            # 等权平均（对齐东财概念板块指数口径，见函数文档）
            pct = round(sum(vals) / len(vals), 2)
            out[d].append({
                "code": bk,
                "name": info.get("name") or bk,
                "pct": pct,
                "zt": zt,
                "up": up,
                "down": down,
                "count": len(vals),
                "lead_name": lead_n,
                "lead_pct": round(lead_p, 2) if lead_p is not None else None,
                # V1.009.5：板块维度的空间高度与资金容量
                "lbc_max": lbc_max,
                "lbc2": lbc2,
                "amt": round(amt, 0) if amt else 0,
            })
        if on_progress and (done % 50 == 0 or done == total):
            on_progress(done, total)
    for d in out:
        out[d].sort(key=lambda x: -(x["pct"] if x["pct"] is not None else -999))
    return out


def board_history_from_daily(board_daily: dict, dates: list[str],
                             codes: list[str]) -> dict:
    """从每日板块聚合结果提取指定板块的近 N 日序列

    → {code: [{date,pct,zt,up,size,ratio,lbc_max,lbc2,amt}, ...]}

    V1.009.5 起带上聚焦度 / 空间高度 / 资金容量：主线题材的四维打分在**历史日期**
    必须与实时同口径，否则回看过去的主线排名会发生跳变（涨跌停口径刚统一过，
    这里不该再留一个跨口径不一致）。

    V1.009.6 起补 `up`（上涨家数）：持续性新口径的条件 c「上涨家数 > 成分股×60%」
    要用它。`rebuild_board_daily` 一直在算 up，只是**没落库** —— 故本版必须重建历史
    序列才能让历史日期也满足条件 c（只换代码不重建 → 历史日在榜天数偏少，且不报错）。

    ratio 用**当日有效成分股数**（count，停牌股会改变当日基数）算，不用静态板块规模。
    """
    out: dict[str, list] = {c: [] for c in codes}
    cset = set(codes)
    for d in dates:
        bucket = {b["code"]: b for b in (board_daily.get(d) or [])}
        for c in cset:
            b = bucket.get(c) or {}
            _zt = int(b.get("zt") or 0)
            _sz = int(b.get("count") or 0)
            out[c].append({
                "date": d,
                # name 只为「剔伪板块」服务（hist 只有 code，没有名字，
                # 而 _is_noise_board 是按名字判定的）。
                "name": b.get("name"),
                "pct": b.get("pct"),
                "zt": _zt,
                # 上涨家数（V1.009.6）：持续性条件 c 用。
                "up": int(b.get("up") or 0),
                "size": _sz,
                "ratio": (round(_zt / _sz * 100, 1) if _sz else None),
                "lbc_max": int(b.get("lbc_max") or 0),
                "lbc2": int(b.get("lbc2") or 0),
                "amt": b.get("amt") or 0,
            })
    return out


def _ladder_from(stocks: list[dict]) -> list[dict]:
    """涨停股列表 → 连板梯队（按连板数降序）"""
    m: dict[int, list] = {}
    for s in stocks:
        m.setdefault(int(s.get("lbc") or 1), []).append(s)
    return [{"lbc": k, "count": len(v),
             "amount": sum(x.get("amount") or 0 for x in v), "stocks": v}
            for k, v in sorted(m.items(), key=lambda kv: -kv[0])]


def _index_series(beg: str, end: str) -> dict:
    """多指数日K序列 → {指数名: {date: {"close","pct","volume"}}}（腾讯→新浪）"""
    out: dict = {}

    def one(item):
        code, name = item
        bars = _tx_kline(code, 400) or _sina_kline(code, 400) or []
        m: dict = {}
        prev = None
        for b in bars:
            d, close, vol, _amt = bar_parts(b)
            pct = round((close - prev) / prev * 100, 2) if prev else None
            if beg <= d <= end:
                m[d] = {"close": round(close, 2), "pct": pct, "volume": vol}
            prev = close
        return name, m

    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        for name, m in ex.map(one, INDEX_LIST):
            out[name] = m
    return out


def _rebuild_prev_perf(prev_zt: list, pct_close: dict, today: str,
                       limit_flags: dict | None = None) -> dict:
    """由日K口径重建"昨日涨停股今日表现"

    口径：prev_zt 必须只含非 ST 股（调用方负责过滤），与 limit_up.count 主口径一致。
    limit_flags: {code: {date: 1/-1}}，为本文件的精确涨停价判定结果；缺失时回退阈值判定。
    """
    flags = limit_flags or {}
    items = []
    for s in (prev_zt or []):
        v = (pct_close.get(s["code"]) or {}).get(today)
        p = v[0] if v else None
        again = (flags.get(s["code"], {}).get(today) == 1) if flags else bool(
            v and p is not None and p >= _limit_threshold(s["code"], s["name"]))
        items.append({
            "code": s["code"], "name": s["name"],
            "prev_lbc": int(s.get("lbc") or 1), "prev_stat": "",
            "pct": round(p, 2) if isinstance(p, (int, float)) else None,
            "again_limit_up": bool(again),
        })
    vals = [x["pct"] for x in items if isinstance(x["pct"], (int, float))]
    items.sort(key=lambda x: (-(x["pct"] if isinstance(x["pct"], (int, float)) else -999)))
    return {
        "prev_date": None, "count": len(items), "valid": len(vals),
        "avg_pct": round(sum(vals) / len(vals), 2) if vals else None,
        "up_count": sum(1 for v in vals if v > 0),
        "down_count": sum(1 for v in vals if v < 0),
        "flat_count": sum(1 for v in vals if v == 0),
        "limit_up_again": sum(1 for x in items if x["again_limit_up"]),
        "items": items, "source": "rebuild",
        "scope": "ex_st",       # 主口径：已剔除 ST/*ST
    }


def build_history_snapshots(days: int = 60, include_boards: bool = True,
                            on_progress=None, kline_loader=None,
                            kline_saver=None, member_loader=None,
                            member_saver=None, include_extra: bool = True,
                            hot_loader=None,
                            hot_concepts: dict | None = None) -> dict:
    """一次性回溯重建最近 N 个交易日的完整快照（历史回看用）

    数据流：
      全市场个股日K（腾讯/同花顺/新浪多源）→ 每日涨跌分布 / 涨停跌停个股 / 连板梯队 / 昨日涨停表现
      指数日K → 每日指数表现
      概念板块 + 成分股映射（东财）→ 每日板块表现与情绪周期
      龙虎榜席位（东财）→ 每日游资动向；个股日K + 指数日K → 每日严重异动提醒
      东财人气榜个股序列（hot_series）→ 每日股票热度榜

    kline_loader(dates) / kline_saver(klines, names)：由调用方注入的个股日K缓存读写，
    用于断点续传（拉取失败的个股下次自动补齐）。
    member_loader() / member_saver(code, name, members)：板块成分股缓存（7 天 TTL），
    避免每次重建都重复请求约 2000 次成分股接口。
    include_extra：是否重建游资动向（每日 3 次请求）。
    hot_loader(klines, dates) → {代码: {日期: 人气排名}}：人气排名本地缓存。由调用方
    （daily_cache._run_rebuild）负责「算候选集 → 增量抓取 → 返回缓存」，这里只做逐日
    组装；Loader 返回空则该章降级。

    返回 {"ok", "dates", "snapshots": {date: snap}, "board_hist": {...}}
    """

    def step(msg: str, pct: int):
        if on_progress:
            try:
                on_progress(msg, pct)
            except Exception:  # noqa: BLE001
                pass

    today_str = _dt.date.today().strftime("%Y-%m-%d")
    step("获取交易日历", 2)
    dates = _recent_trade_days(today_str, days)
    if not dates:
        return {"ok": False, "error": "交易日历获取失败"}

    step("获取全市场股票列表", 4)
    uni = stock_universe()
    if not uni:
        return {"ok": False, "error": "全市场股票列表获取失败（东财 clist 不可用）"}
    name_map = {u["code"]: u["name"] for u in uni}

    # 个股日K：本地缓存（断点续传）→ 补拉缺失部分
    klines: dict = {}
    if kline_loader:
        try:
            klines = dict(kline_loader(dates[0], dates[-1]))
        except Exception:  # noqa: BLE001
            klines = {}
    need = [u["code"] for u in uni if u["code"] not in klines]
    step("拉取个股日K（需 %d 只，本地已有 %d）" % (len(need), len(klines)), 6)
    if need:
        fresh = fetch_klines_multi(
            need, datalen=days + 15, workers=_KLINE_WORKERS,
            on_progress=lambda d, t: step(
                "个股日K %d/%d" % (d, t), 6 + int(d / max(1, t) * 44)),
        )
        klines.update(fresh)
        if kline_saver and fresh:
            try:
                kline_saver(fresh, name_map)
            except Exception:  # noqa: BLE001
                pass
    if not klines:
        return {"ok": False, "error": "个股日K全部拉取失败（腾讯/同花顺/新浪源均不可用）"}

    # ---- 股票热度榜历史：人气排名序列 ----
    # 榜单视角（getAllCurrentList）没有历史，但个股视角（getHisList）有逐日全市场名次。
    # 这里对「成交额大 或 单日波动大」的候选股各取一次序列落库，之后各历史日零网络。
    hot_series: dict = {}
    if hot_loader:
        step("抓取个股历史人气排名（热度榜回溯）", 51)
        try:
            hot_series = hot_loader(klines, dates) or {}
        except Exception:  # noqa: BLE001
            hot_series = {}

    # ---- 每日涨跌分布 / 涨停跌停个股 / 连板数 ----
    step("聚合每日涨跌分布与涨停结构", 52)
    all_dates = sorted({r[0] for rows in klines.values() for r in rows})
    win = all_dates[-min(len(all_dates), days + 20):]
    idx = {d: i for i, d in enumerate(win)}

    breadth = {d: _blank_breadth() for d in dates}
    zt_items: dict[str, list] = {d: [] for d in dates}
    dt_items: dict[str, list] = {d: [] for d in dates}
    zb_items: dict[str, list] = {d: [] for d in dates}
    pct_close: dict[str, dict] = {}
    lbc_map: dict[str, dict] = {}
    limit_flags: dict[str, dict[str, int]] = {}
    zb_flags: dict[str, dict[str, tuple]] = {}

    for code, rows in klines.items():
        nm = name_map.get(code, "")
        prev = None          # 前复权前收 —— 算涨跌幅
        prev_bar = None
        prev_d = None
        m: dict = {}
        flags: dict[str, int] = {}
        zbs: dict[str, tuple] = {}
        zs: set = set()
        for _bar in rows:
            d, close = _bar[0], _bar[1]
            if prev and prev > 0:
                p = (close - prev) / prev * 100.0
                # 展示价用**不复权**收盘价（= 盘面所见），涨跌幅仍用前复权价算
                # （前复权 pct 恒等于交易所公布的官方涨跌幅）。除权日两者会不同，
                # 与炸板项的 high / zt_price（同属不复权）保持一致。
                _disp = bar_close_raw(_bar)
                m[d] = (p, _disp if _disp is not None else close, bar_amount(_bar))
                # 停牌复牌首日不设涨跌幅限制，且累计涨跌幅不可比，故要求K线连续
                if prev_bar is not None and prev_d in idx and d in idx \
                        and idx[d] - idx[prev_d] == 1:
                    # 涨跌停/炸板统一走 limit_state（基数 = 除权参考价）：
                    # 交易所涨跌停价基于不复权前收，但除权日当天要用除权参考价 —— 单一
                    # 口径各错一半，见 limit_base 的推导与实测。
                    _fl, _zbh, _zbl = limit_state(prev_bar, _bar, code, nm)
                    if _fl:
                        flags[d] = _fl
                        if _fl == 1:
                            zs.add(d)
                    elif _zbl is not None:
                        # 炸板：盘中最高价触及涨停价，但收盘未封住（东财炸板池仅近
                        # ~15 日可查，更早的历史由这里自建；口径差异见 broken_from_klines）
                        zbs[d] = (_zbh, _zbl)
            prev = close
            prev_bar = _bar
            prev_d = d
        pct_close[code] = m
        limit_flags[code] = flags
        zb_flags[code] = zbs
        lm: dict = {}
        for d in zs:
            i = idx.get(d)
            if i is None:
                continue
            n = 1
            while i - n >= 0 and win[i - n] in zs:
                n += 1
            lm[d] = n
        lbc_map[code] = lm

    for code, m in pct_close.items():
        nm = name_map.get(code, "")
        is_st = _is_st(nm)          # 统一规则，避免 "S佳通" 这类被 "ST" in name 误判
        lm = lbc_map.get(code) or {}
        flags = limit_flags.get(code) or {}
        for d in dates:
            v = m.get(d)
            if v is None:
                continue
            p, close, amt = v
            b = breadth[d]
            b["total_amount"] += amt
            f = flags.get(d, 0)
            # 主口径 = 剔除 ST/*ST（与东财涨停池一致）。ST 股涨跌停不计入「涨跌停家数」，
            # 但降级为普通涨跌进入下面的分布档位 —— live 路径（东财池剔 ST + 快照分档）
            # 就是这个行为，两条路径必须一致，否则同一张趋势图上相邻两天不可比。
            if f == 1:
                b["limit_up_inc_st"] += 1
                if is_st:
                    f = 0
            elif f == -1:
                b["limit_down_inc_st"] += 1
                if is_st:
                    f = 0
            if f == 1:
                b["limit_up"] += 1
                zt_items[d].append({
                    "code": code, "name": nm, "price": round(close, 3),
                    "pct": round(p, 2), "amount": round(amt, 0),
                    "ltsz": 0, "hs": 0.0, "lbc": int(lm.get(d, 1)),
                    "fbt": "", "lbt": "", "zbc": 0, "industry": "", "stat": "",
                    "is_st": is_st,
                })
            elif f == -1:
                b["limit_down"] += 1
                dt_items[d].append({
                    "code": code, "name": nm, "price": round(close, 3),
                    "pct": round(p, 2), "amount": round(amt, 0),
                    "ltsz": 0, "hs": 0.0, "lbc": 1, "fbt": "",
                    "industry": "", "is_st": is_st,
                })
            elif p > 7:
                b["up_gt7"] += 1
            elif p > 5:
                b["up_5_7"] += 1
            elif p > 3:
                b["up_3_5"] += 1
            elif p > 0:
                b["up_0_3"] += 1
            elif p == 0:
                b["flat"] += 1
            elif p >= -3:
                b["down_0_3"] += 1
            elif p >= -5:
                b["down_3_5"] += 1
            elif p >= -7:
                b["down_5_7"] += 1
            else:
                b["down_gt7"] += 1

            # 炸板：不计入涨跌停家数，但单列统计（本身仍计入涨跌家数分布）
            if f == 0 and (zb_flags.get(code) or {}).get(d):
                _hi, _lp = zb_flags[code][d]
                zb_items[d].append({
                    "code": code, "name": nm, "price": round(close, 3),
                    "zt_price": round(_lp, 3), "high": round(_hi, 3),
                    "pct": round(p, 2), "amount": round(amt, 0),
                    "zbc": None,  # 开板次数需分时数据，日K源无法提供
                    "is_st": is_st,
                })

    for b in breadth.values():
        b["limit_up_ex_st"] = b["limit_up"]   # 兼容旧字段：与 limit_up 同义（均已剔 ST）
        b["up_count"] = b["up_gt7"] + b["up_5_7"] + b["up_3_5"] + b["up_0_3"]
        b["down_count"] = b["down_0_3"] + b["down_3_5"] + b["down_5_7"] + b["down_gt7"]
        b["total"] = b["up_count"] + b["down_count"] + b["flat"]
        b["total_amount"] = round(b["total_amount"], 2)
        b["source"] = "rebuild"

    # ---- 指数序列 ----
    step("拉取指数日K序列", 60)
    beg = (_dt.datetime.strptime(dates[0], "%Y-%m-%d").date()
           - _dt.timedelta(days=15)).strftime("%Y-%m-%d")
    iser = _index_series(beg, today_str)

    # ---- 概念板块历史（成分股聚合） ----
    boards_by_date: dict = {}
    board_hist: dict = {}
    mem: dict = {}
    board_ctx: dict = {}          # rebuild_board_daily 回填的个股指标（角色分层复用）
    if include_boards:
        step("拉取概念板块与成分股", 65)
        bl = board_list(_FS_CONCEPT)
        if bl:
            cached_mem = {}
            if member_loader:
                try:
                    cached_mem = member_loader() or {}
                except Exception:  # noqa: BLE001
                    cached_mem = {}
            step("板块成分股（本地缓存 %d 个板块）" % len(cached_mem), 66)
            mem = board_member_map(
                bl, cached=cached_mem,
                on_progress=lambda d, t: step(
                    "板块成分股 %d/%d" % (d, t), 66 + int(d / max(1, t) * 21)),
                on_fetched=(lambda c, n, m: member_saver(c, n, m)) if member_saver else None)
            step("聚合板块每日表现", 88)
            boards_by_date = rebuild_board_daily(mem, klines, dates, ctx_out=board_ctx)
            board_hist = board_history_from_daily(boards_by_date, dates,
                                                  [b["code"] for b in bl])

    # ---- 组装每日快照 ----
    step("组装每日快照", 94)
    from app.services import daily_extra as _dx

    # 基准指数逐日涨跌幅（严重异动偏离值用）
    index_pct = {}
    for _nm, _mm in (iser or {}).items():
        index_pct[_nm] = {_d: _r.get("pct") for _d, _r in (_mm or {}).items()
                          if isinstance(_r.get("pct"), (int, float))}

    # 游资动向（龙虎榜历史可回溯；每日 3 次请求，并行拉取）
    # 龙虎榜日榜明细同时用于异动原因佐证，故与游资动向一起取，避免重复请求
    youzi_map: dict = {}
    lhb_maps: dict = {}
    if include_extra:
        def _fetch_day(d: str):
            lm: dict = {}
            try:
                lm = _dx.lhb_daily(d)
            except Exception:  # noqa: BLE001
                lm = {}
            try:
                yz = _dx.youzi_flow(d, daily=lm)
            except Exception:  # noqa: BLE001
                yz = {"available": False, "date": d, "note": "游资动向采集失败"}
            return d, yz, lm

        try:
            with _cf.ThreadPoolExecutor(max_workers=4) as ex:
                for dd, yz, lm in ex.map(_fetch_day, dates):
                    youzi_map[dd] = yz
                    lhb_maps[dd] = lm
        except Exception:  # noqa: BLE001
            youzi_map, lhb_maps = {}, {}

    snaps: dict = {}
    for i, d in enumerate(dates):
        pd_ = dates[i - 1] if i > 0 else None
        # 当日热度榜：由本地东财人气排名序列组装（榜单视角无历史源，个股视角有）
        if hot_series:
            try:
                hot_for_day = _dx.hot_list_history(
                    hot_series, d, name_map=name_map, klines=klines,
                    prev_date=pd_, concepts_map=hot_concepts, size=50)
            except Exception as ex:  # noqa: BLE001
                hot_for_day = {"available": False, "note": "历史热度榜组装异常：%s" % ex}
        else:
            hot_for_day = {"available": False,
                           "note": ("本地人气排名缓存为空（东财人气榜个股序列尚未抓取）；"
                                    "候选股抓取完成后即可离线组装该日热度榜")}
        # 统一口径 = 剔 ST：zt_items / dt_items 在上面的分档里已只收非 ST，
        # 故 count 即剔 ST 家数；count_inc_st 取广度里记录的含 ST 总数，供与东财首页对账。
        zt_list = sorted(zt_items[d], key=lambda x: (-x["lbc"], -(x["pct"] or 0)))
        dt_list = sorted(dt_items[d], key=lambda x: (x["pct"] or 0))
        max_lbc = max([s["lbc"] for s in zt_list] or [0])
        zt_inc = int(breadth[d].get("limit_up_inc_st") or 0)
        dt_inc = int(breadth[d].get("limit_down_inc_st") or 0)
        zt_struct = {
            "count": len(zt_list),                 # 主口径：剔 ST
            "count_inc_st": zt_inc,                # 含 ST（对账用）
            "count_ex_st": len(zt_list),           # 兼容旧字段，与 count 同义
            "st_count": max(0, zt_inc - len(zt_list)),
            "amount": sum(s["amount"] for s in zt_list),
            "max_lbc": max_lbc,
            "stocks": zt_list,
            "ladder": _ladder_from(zt_list),
            "industries": [],
            "source": "kline",   # 由个股日K自建（东财涨停池超窗）
        }
        dt_struct = {
            "count": len(dt_list),                 # 主口径：剔 ST
            "count_inc_st": dt_inc,
            "count_ex_st": len(dt_list),
            "st_count": max(0, dt_inc - len(dt_list)),
            "amount": sum(s["amount"] for s in dt_list),
            "stocks": dt_list,
            "source": "kline",
        }
        # 炸板：zb_items 收全部（含 ST），组装时再按主口径过滤
        zb_all = list(zb_items.get(d) or [])
        zb_list = sorted([x for x in zb_all if not x["is_st"]],
                         key=lambda x: -(x["amount"] or 0))
        zb_struct = {
            "count": len(zb_list),                 # 主口径：剔 ST
            "count_inc_st": len(zb_all),
            "count_ex_st": len(zb_list),
            "st_count": len(zb_all) - len(zb_list),
            "amount": sum(s["amount"] for s in zb_list),
            "stocks": zb_list,
            "source": "kline",  # 由个股日K自建（东财炸板池超窗）
        }
        # 昨日涨停名单必须是「剔 ST」口径：zt_items 现在已只收非 ST（见上方分档），
        # 这里再显式过滤一次，防止未来重构把 ST 放回来导致 prev_limit_up 与
        # limit_up.count 口径不一致（历史事故：09-10 的 49 vs limit_up.count 48）。
        _pzt = [x for x in (zt_items.get(pd_) or []) if not x.get("is_st")]
        pp = _rebuild_prev_perf(_pzt, pct_close, d, limit_flags)
        pp["prev_date"] = pd_
        indices = []
        for _c, nm in INDEX_LIST:
            rec = (iser.get(nm) or {}).get(d) or {}
            indices.append({"name": nm, "date": d, "close": rec.get("close"),
                            "pct": rec.get("pct"), "volume": rec.get("volume")})
        concept = concept_emotion(boards_by_date.get(d) or [], None, board_hist, date_str=d,
                                  roles_ctx=board_ctx.get("ctx"),
                                  roles_members=mem,
                                  index_pct=index_pct)
        cov = (breadth[d]["total"] + len(zt_list) + len(dt_list))
        note = ("历史重建口径：涨跌分布 / 涨停跌停 / 连板数 / 炸板均由全市场个股日K聚合，"
                "成交额取自日K成交额（同花顺源为精确成交额，腾讯源为 量×价 换算）。"
                "炸板口径：**当日最高价触及涨停价但收盘未封住**，与东财炸板池有两处已知差异"
                "（均无法在日K层面消除，已量化，15 个交易日实测）："
                "① 东财要求「盘中真的封过板」（池内每只都带首次封板时间），日K无分时数据、"
                "无法区分「封过后开板」与「瞬时摸板」，故本口径略多计 —— 剔 ST 后 377 vs 328（+15%）；"
                "② 东财两个池都不收录 ST，本口径含 ST（全部家数 440）。"
                "炸板率是比值、分子分母同向偏移，实测差别约 +3 个百分点，小于家数差。"
                "最近约 15 个交易日内，实盘日走东财池（口径一致）；超出窗口才用本口径。")
        if len(uni) and cov / len(uni) < 0.97:
            note += "当日个股日K覆盖 %d/%d 只（差额主要为停牌无成交个股）。" % (cov, len(uni))

        # 成交额环比（相邻两个交易日同为日K口径，可直接比较）
        amt_chg = {"available": False, "prev_date": pd_ or "", "today": None, "prev": None,
                   "delta": None, "delta_pct": None, "today_source": "", "prev_source": ""}
        if pd_:
            amt_chg = _dx.amount_change(breadth[d].get("total_amount"),
                                        breadth[pd_].get("total_amount"),
                                        pd_, "kline", "kline")
        if not amt_chg.get("available"):
            amt_chg["today"] = breadth[d].get("total_amount")
            amt_chg["today_source"] = "kline"

        abn = {"available": False, "ok": False, "note": "异动检测未执行"}
        try:
            abn = _dx.abnormal_watch(klines, d, name_map, index_pct,
                                     lhb_map=lhb_maps.get(d) or {})
        except Exception as ex:  # noqa: BLE001
            _tb.print_exc()
            abn = {"available": False, "ok": False,
                   "note": "异动检测失败：%s: %s" % (type(ex).__name__, ex)}

        snaps[d] = {
            "date": d,
            "is_latest": False,
            "partial": False,
            "kind": "rebuild",
            "collected_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "indices": indices,
            "breadth": breadth[d],
            "amount_chg": amt_chg,
            "coverage": {"stocks": cov, "universe": len(uni)},
            "emotion": {
                "limit_up": zt_struct["count"],              # 剔 ST（主口径）
                "limit_up_inc_st": zt_struct["count_inc_st"],
                "limit_up_ex_st": zt_struct["count"],        # 兼容旧字段
                "limit_down": dt_struct["count"],
                "limit_down_inc_st": dt_struct["count_inc_st"],
                "limit_down_ex_st": dt_struct["count"],
                "broken": zb_struct["count"],
                "broken_inc_st": zb_struct["count_inc_st"],
                "broken_rate": (
                    round(len(zb_list) / (len(zt_list) + len(zb_list)) * 100, 2)
                    if (len(zt_list) + len(zb_list)) else None),
                "broken_amount_rate": (
                    round(zb_struct["amount"] / (zt_struct["amount"] + zb_struct["amount"]) * 100, 2)
                    if (zt_struct["amount"] + zb_struct["amount"]) else None),
                "max_lbc": max_lbc,
                "prev_limit_up_avg": pp.get("avg_pct"),
            },
            "limit_up": zt_struct,
            "limit_down": dt_struct,
            "broken": zb_struct,
            "prev_limit_up": pp,
            "prev_trade_day": pd_,
            "concept": concept,
            "hot": hot_for_day,
            "abnormal": abn,
            "youzi": youzi_map.get(d) or {"available": False, "date": d,
                                          "note": "游资动向未采集"},
            "note": note,
        }

    step("完成", 100)
    return {"ok": True, "dates": dates, "snapshots": snaps,
            "boards": boards_by_date, "board_hist": board_hist,
            "universe": len(uni), "kline_ok": len(klines),
            "hot_stocks": len(hot_series)}


# ---------------------------------------------------------------------------
# 主采集入口
# ---------------------------------------------------------------------------

def collect_daily(date_str: str, hist_boards: dict | None = None,
                  rebuild_ctx: dict | None = None, klines: dict | None = None,
                  index_pct: dict | None = None, names: dict | None = None,
                  with_extra: bool = True, hot_series: dict | None = None,
                  hot_concepts: dict | None = None,
                  prev_trade_day: str | None = None,
                  member_loader=None) -> dict:
    """采集指定交易日的完整盘面快照

    参数
      hist_boards : 板块历史序列（概念情绪周期阶段判定用）
      rebuild_ctx : {"breadth": {date:{...}}, "boards": {date:[...]}} —— 历史重建结果，
                    非最近交易日时用于补齐涨跌分布与概念板块
      klines      : 个股日K（严重异动检测 / 昨日涨停表现回溯用，通常来自 stock_kline_cache）
      index_pct   : {指数名: {日期: 涨跌幅}} 基准指数序列（严重异动偏离值用）
      names       : {代码: 名称}，日K缓存附带
      hot_series  : {代码: {日期: 人气排名}} —— 本地缓存的东财人气排名序列，
                    历史日热度榜由它组装（见 daily_extra.hot_list_history）
      hot_concepts: {代码: [概念名]}，由板块成分股缓存反查，用于补历史榜单的热门概念
      prev_trade_day : 上一交易日（显式传入可省一次交易日历请求）
      member_loader  : () -> {板块代码: {"name","members":[{code,name,mktcap}]}}
                       板块成分股缓存读取（**只读，不抓取**）。提供后实时路径才能
                       对主线题材做「龙头/中军/跟风/补涨」角色分层。

    返回结构化 dict（含 note 说明数据缺失/降级情况）。
    """
    t0 = time.time()
    notes: list[str] = []
    rebuild_ctx = rebuild_ctx or {}
    ctx_boards = (rebuild_ctx.get("boards") or {}).get(date_str)
    names_map = names or {}

    # 是否交易日：**先看星期几再查日历** —— 周末/节假日不可能有盘面数据，报告里若只说
    # "未采集"会让人误以为是功能缺失。周末直接判定，工作日才发一次日历请求（零额外开销）。
    is_trade_day = True
    try:
        _wd = _dt.datetime.strptime(date_str, "%Y-%m-%d").weekday()
        if _wd >= 5:
            is_trade_day = False
        else:
            _r = _recent_trade_days(date_str, 1)
            is_trade_day = bool(_r) and _r[-1] == date_str
    except Exception:  # noqa: BLE001
        pass

    # 1) 三个池（历史可回溯）
    zt_tc, zt_pool = _pool(_API_ZT, date_str, "fbt:asc")
    zb_tc, zb_pool = _pool(_API_ZB, date_str, "fbt:asc")
    dt_tc, dt_pool = _pool(_API_DT, date_str, "fund:asc")
    if not zt_pool and not zb_pool and not dt_pool and not zt_tc:
        notes.append("涨停/炸板/跌停池均无数据（该日可能非交易日或数据源未更新）")

    zt = _zt_stocks(zt_tc, zt_pool)
    zb = _zb_stocks(zb_tc, zb_pool)
    dt = _dt_stocks(dt_tc, dt_pool)

    # 炸板兜底：东财炸板池仅回溯最近约 15 个交易日，更早日期返回空池。
    # 此时改由个股日K自建（口径：当日最高价触及涨停价但收盘未封，见 broken_from_klines）。
    if zb.get("count"):
        zb["source"] = "em"
    elif klines:
        _kb = broken_from_klines(klines, date_str, names_map)
        if _kb.get("count"):
            _kb["source"] = "kline"
            # 主口径统一为剔 ST：日K自建口径本身含 ST，这里剔掉并把含 ST 数另存
            _kb_all = list(_kb.get("stocks") or [])
            _kb["count_inc_st"] = len(_kb_all)
            _kb["stocks"] = [x for x in _kb_all if not x.get("is_st")]
            _kb["count"] = len(_kb["stocks"])
            _kb["count_ex_st"] = _kb["count"]
            _kb["st_count"] = len(_kb_all) - _kb["count"]
            _kb["amount"] = sum(x.get("amount") or 0 for x in _kb["stocks"])
            zb = _kb
            notes.append(
                "炸板数据：东财炸板池仅覆盖最近约 15 个交易日，该日已超出回溯窗口，"
                "改由个股日K自建 —— 口径为「当日最高价触及涨停价但收盘未封住」。"
                "与东财池有两处已知差异（日K层面无法消除，15 个交易日实测量化）："
                "① 东财要求「盘中真的封过板」，日K无分时数据、无法区分「封过后开板」与"
                "「瞬时摸板」，故本口径偏多 —— 剔 ST 后 377 vs 328（+15%）；"
                "② 东财两个池都不收录 ST，本口径含 ST（全部家数 440，+26%）。"
                "炸板率是比值、分子分母同向偏移，实测仅差约 +3 个百分点。"
            )

    # 全市场快照仅"当前最近交易日"可用；历史日期用当前快照会造成数据错配，必须跳过
    today_str = _dt.date.today().strftime("%Y-%m-%d")
    last_td = (_recent_trade_days(today_str, 1) or [today_str])[-1]
    is_latest = date_str == last_td
    ctx_breadth = (rebuild_ctx.get("breadth") or {}).get(date_str)
    rows: list[dict] = []
    partial = False
    if is_latest:
        rows, snote = _market_snapshot()
        if snote:
            notes.append(snote)
        # 主口径 = 剔 ST（东财池不收录 ST）。「含 ST」东财没有现成字段，只能从快照自算 ——
        # 快照本来就要拉（算涨跌家数分布），所以零额外开销。日K兜底的炸板口径本身含 ST，
        # 已在上面改成剔 ST，此处跳过。
        if rows:
            _slc = _limit_board_from_snapshot(rows)
            for _k, _stt in (("limit_up", zt), ("limit_down", dt)):
                _base = int(_stt.get("count") or 0)
                _stt["st_count"] = _slc[_k]
                _stt["count_inc_st"] = _base + _slc[_k]
            if zb.get("source") != "kline":
                zb["st_count"] = _slc["broken"]
                zb["count_inc_st"] = int(zb.get("count") or 0) + _slc["broken"]
    elif ctx_breadth:
        # 已做历史重建：涨跌分布 / 成交额来自全市场个股日K聚合，历史日期同样完整
        notes.append(
            "该日期为历史回溯（rebuild）：涨跌家数分布 / 两市成交额 / 概念板块 / 炸板由全市场"
            "个股日K聚合重建，成交额取日K精确成交额（同花顺源）；涨停池 / 跌停池 / 连板梯队 / "
            "指数为按日查询（东财池回溯窗口约 15 个交易日），超出窗口的日期以日K聚合结果为准。"
        )
    else:
        partial = True
        notes.append(
            "该日期不是最近交易日且尚未做历史重建：涨跌家数分布 / 两市成交额 / 昨日涨停今日表现"
            "依赖实时快照，暂不可得（涨停池、炸板池、跌停池、连板梯队、指数为按日查询，仍准确）；"
            "可在页面执行「重建历史数据」补齐。"
        )

    zt_codes = {s["code"] for s in zt["stocks"]}
    dt_codes = {s["code"] for s in dt["stocks"]}

    # 3) 涨跌分布
    if rows:
        breadth = _breadth(rows, zt_codes, dt_codes)
    elif ctx_breadth:
        breadth = dict(ctx_breadth)
    else:
        breadth = {
            "up_gt7": 0, "up_5_7": 0, "up_3_5": 0, "up_0_3": 0,
            "down_0_3": 0, "down_3_5": 0, "down_5_7": 0, "down_gt7": 0,
            "flat": 0, "up_count": 0, "down_count": 0, "total": 0, "total_amount": 0.0,
        }

    # 4) 昨日涨停今日表现
    prev_day = _prev_trade_day(date_str)
    if rows:
        prev_perf = (_prev_limit_up_perf(prev_day, rows, zt_codes) if prev_day else
                     {"prev_date": prev_day, "count": 0, "valid": 0, "items": [],
                      "avg_pct": None, "up_count": 0, "down_count": 0,
                      "flat_count": 0, "limit_up_again": 0, "source": ""})
    elif klines and prev_day:
        # 无实时快照时用个股日K回溯（同源同口径，避免历史日期该项为空）
        pct_close: dict = {}
        flags: dict = {}
        for c, bars in klines.items():
            nm = names_map.get(c, "")
            m: dict = {}
            fl: dict = {}
            prev_c = None        # 前复权前收 —— 算涨跌幅
            prev_b = None
            for _b in bars:
                d, close, _v, _a = bar_parts(_b)
                if prev_c and prev_c > 0:
                    m[d] = ((close - prev_c) / prev_c * 100.0, close, 0)
                    _fl, _z1, _z2 = limit_state(prev_b, _b, c, nm)
                    if _fl:
                        fl[d] = _fl
                prev_c = close
                prev_b = _b
            pct_close[c] = m
            flags[c] = fl
        ptc, ppool = _pool(_API_ZT, prev_day, "fbt:asc")
        prev_zt_items = _zt_stocks(ptc, ppool).get("stocks") or []
        prev_perf = _rebuild_prev_perf(prev_zt_items, pct_close, date_str, flags)
        prev_perf["prev_date"] = prev_day
    else:
        prev_perf = {"prev_date": prev_day, "count": 0, "valid": 0, "items": [],
                     "avg_pct": None, "up_count": 0, "down_count": 0,
                     "flat_count": 0, "limit_up_again": 0, "source": ""}
        if prev_day:
            notes.append("昨日涨停股今日表现需要当日全市场快照或个股日K缓存，历史日期不可回溯")

    # 5) 情绪指标
    zt_cnt = zt["count"]
    zb_cnt = zb["count"]
    emotion = {
        "limit_up": zt_cnt,                                   # 主口径：剔 ST
        "limit_up_inc_st": zt.get("count_inc_st"),            # 含 ST（对账用）
        "limit_up_ex_st": zt["count_ex_st"],                  # 兼容旧字段，同 limit_up
        "limit_down": dt["count"],
        "limit_down_inc_st": dt.get("count_inc_st"),
        "limit_down_ex_st": dt["count_ex_st"],
        "broken": zb_cnt,
        "broken_inc_st": zb.get("count_inc_st"),
        "broken_rate": round(zb_cnt / (zt_cnt + zb_cnt) * 100, 2) if (zt_cnt + zb_cnt) else None,
        "broken_amount_rate": (
            round(zb["amount"] / (zt["amount"] + zb["amount"]) * 100, 2)
            if (zt["amount"] + zb["amount"]) else None
        ),
        "max_lbc": zt["max_lbc"],
        "prev_limit_up_avg": prev_perf.get("avg_pct"),
    }

    # 6) 概念板块情绪周期（实时抓取 / 历史重建结果）
    if is_latest:
        try:
            boards = board_list(_FS_CONCEPT)
            # 概念全集（用于剔除个股板块接口带回来的行业/交易标签板块）
            _ccodes = {b["code"] for b in boards} or None
            # 角色分层（龙头/中军/跟风/补涨）：实时指标走全市场快照 + 涨停池 + 日K缓存
            _rctx, _rmem = None, None
            if member_loader and rows:
                try:
                    _rmem = member_loader() or {}
                    if _rmem:
                        _rctx = roles_ctx_live(rows, list(zt["stocks"]), klines, date_str)
                except Exception as _e2:  # noqa: BLE001
                    notes.append("个股角色分层降级：%s: %s" % (type(_e2).__name__, _e2))
                    _rctx, _rmem = None, None
            concept = concept_emotion(boards, list(zt["stocks"]), hist_boards,
                                      date_str=date_str, roles_ctx=_rctx,
                                      roles_members=_rmem,
                                      concept_codes=_ccodes,
                                      index_pct=index_pct)
        except Exception as e:  # noqa: BLE001
            concept = {"available": False, "note": "概念板块采集失败：%s" % e}
    elif ctx_boards:
        concept = concept_emotion(ctx_boards, None, hist_boards, date_str=date_str,
                                  roles_ctx=None, roles_members=None,
                                  index_pct=index_pct)
    else:
        concept = {"available": False, "note": "概念板块需先执行「历史数据重建」后查看"}

    # 7) 两市成交额环比（与昨日比较）
    #    优先"同一链路"比较：都用个股日K聚合，避免快照与日K两个口径混比
    from app.services import daily_extra as _dx
    amt_chg = {"available": False, "prev_date": prev_day or "", "today": None, "prev": None,
               "delta": None, "delta_pct": None, "today_source": "", "prev_source": ""}
    if klines and prev_day:
        t_k = _dx.turnover_from_klines(klines, date_str)
        p_k = _dx.turnover_from_klines(klines, prev_day)
        snap_amt = breadth.get("total_amount") or 0
        if t_k and p_k:
            # 用快照值做一致性校验：若日K当日覆盖明显不足（<70%），改用快照当日值
            if snap_amt and t_k < snap_amt * 0.7:
                amt_chg = _dx.amount_change(snap_amt, p_k, prev_day, "snapshot", "kline")
            else:
                amt_chg = _dx.amount_change(t_k, p_k, prev_day, "kline", "kline")
        elif p_k and snap_amt and is_trade_day:
            # 日K缓存里还没有「当日」的 bar（个股日K每晚刷新一次，盘后刚打开页面时常见）
            # → 当日退化为实时快照成交额，昨日沿用日K聚合值，保证环比不空。
            # 空值分支只在 t_k 缺失时走：一旦日K补齐当日 bar，自动回到上面的同口径比较。
            # 非交易日（is_trade_day=False）不算 —— 此时快照拿到的是上一交易日的收盘值，
            # today 与 prev 会指向同一天，算出来是个假环比。
            amt_chg = _dx.amount_change(snap_amt, p_k, prev_day, "snapshot", "kline")
            notes.append("成交额环比：日K缓存无 %s 的当日bar，当日侧退化用实时快照" % date_str)
    if not amt_chg.get("available") and breadth.get("total_amount"):
        # 前面连 prev 都拿不到（日K缓存缺上一交易日、或非交易日）→ 只把当日值挂上，
        # available 保持 False，前端显示 '-' 而不是半个数。
        amt_chg["today"] = breadth.get("total_amount")
        amt_chg["today_source"] = "snapshot"

    # 8) 扩展数据：股票热度榜 / 严重异动提醒 / 游资动向
    extra: dict = {}
    if with_extra:
        try:
            extra = _dx.collect_extra(date_str, klines=klines, names=names_map,
                                      index_pct=index_pct, is_latest=is_latest,
                                      with_hot=bool(is_latest))
        except Exception as ex:  # noqa: BLE001
            _tb.print_exc()
            notes.append("扩展数据采集异常：%s: %s" % (type(ex).__name__, ex))

    # 历史日热度榜：榜单视角无历史源，**个股视角有** ——
    # 由本地缓存的东财人气排名序列（每只股票约 120 天逐日名次）按当日名次排序组装。
    hot = extra.get("hot") if is_latest else None
    if not is_latest:
        if hot_series:
            try:
                hot = _dx.hot_list_history(
                    hot_series, date_str, name_map=names_map, klines=klines,
                    prev_date=prev_day, concepts_map=hot_concepts, size=50)
            except Exception as ex:  # noqa: BLE001
                hot = {"available": False, "note": "历史热度榜组装异常：%s" % ex}
        else:
            hot = {"available": False,
                   "note": ("本地人气排名缓存为空 —— 重建一次历史数据即可补齐候选股序列，"
                            "之后所有历史日热度榜均可离线组装")}

    snap = {
        "date": date_str,
        "is_latest": is_latest,
        "is_trade_day": is_trade_day,
        "partial": partial,
        "collected_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(time.time() - t0, 1),
        "indices": _index_quotes(date_str),
        "breadth": breadth,
        "amount_chg": amt_chg,
        "emotion": emotion,
        "limit_up": zt,
        "limit_down": dt,
        "broken": zb,
        "prev_limit_up": prev_perf,
        "prev_trade_day": prev_day,
        "concept": concept,
        "hot": hot or extra.get("hot") or {"available": False,
                                           "note": "热度榜未采集"},
        "abnormal": extra.get("abnormal") or {"available": False, "ok": False,
                                              "note": "异动检测未执行"},
        "youzi": extra.get("youzi") or {"available": False, "note": "游资动向未采集"},
        "note": "；".join(notes),
    }
    return snap


def collect_trend(end_str: str, days: int = 20) -> dict:
    """多日情绪趋势（用于趋势图）：逐日拉三个池，算涨停/炸板/跌停家数、炸板率、连板高度"""
    dlist = _recent_trade_days(end_str, days)

    def one(d: str) -> dict:
        zt_tc, zt_pool = _pool(_API_ZT, d, "fbt:asc")
        zb_tc, zb_pool = _pool(_API_ZB, d, "fbt:asc")
        dt_tc, dt_pool = _pool(_API_DT, d, "fund:asc")
        zt_amt = sum((i.get("amount") or 0) for i in zt_pool)
        zb_amt = sum((i.get("amount") or 0) for i in zb_pool)
        dt_amt = sum((i.get("amount") or 0) for i in dt_pool)
        lbc_list = [int(i.get("lbc") or 1) for i in zt_pool]
        max_lbc = max(lbc_list or [0])
        # 首板/连板分档（与 daily_cache._trend_item 同口径）
        zt_n = zt_tc or len(zt_pool)
        if zt_n == 0:
            first_board = multi_board = 0
        elif lbc_list:
            first_board = sum(1 for n in lbc_list if n <= 1)
            multi_board = sum(1 for n in lbc_list if n >= 2)
        else:
            first_board = multi_board = None
        return {
            "date": d,
            "limit_up": zt_n,
            "broken": zb_tc or len(zb_pool),
            "limit_down": dt_tc,
            "zt_amount": zt_amt,
            "zb_amount": zb_amt,
            "broken_rate": round((zb_tc) / (zt_tc + zb_tc) * 100, 2) if (zt_tc + zb_tc) else None,
            "broken_amount_rate": (
                round(zb_amt / (zt_amt + zb_amt) * 100, 2) if (zt_amt + zb_amt) else None
            ),
            "max_lbc": max_lbc,
            # --- 情绪结构：池子里能算的照算，依赖全市场快照的留 None（取不到 ≠ 为 0）---
            "first_board": first_board,
            "multi_board": multi_board,
            "prev_lu_avg": None,
            "prev_lu_again": None,
            "prev_lu_up": None,
            "prev_lu_rate": None,
            "prev_lu_valid": None,
            "up_count": None,
            "down_count": None,
            "up_ratio": None,
            "total_amount": None,
            "dt_amount": dt_amt if dt_tc else None,
        }

    items: list[dict] = []
    with _cf.ThreadPoolExecutor(max_workers=3) as ex:
        items = list(ex.map(one, dlist))
    return {"end": end_str, "days": len(items), "items": items}


# ---------------------------------------------------------------------------
# 文本报告（程序生成，供展示 / AI prompt）
# ---------------------------------------------------------------------------

def _yi(v) -> str:
    try:
        return "%.2f亿" % (float(v) / 1e8)
    except Exception:  # noqa: BLE001
        return "-"


def build_daily_markdown(snap: dict) -> str:
    """把快照渲染成 markdown 数据速览（程序生成，AI 只做点评不改数）"""
    lines: list[str] = []
    d = snap.get("date", "")
    b = snap.get("breadth") or {}
    e = snap.get("emotion") or {}
    zt = snap.get("limit_up") or {}
    zb = snap.get("broken") or {}
    dtp = snap.get("limit_down") or {}
    pp = snap.get("prev_limit_up") or {}

    lines.append("### 一、指数表现")
    lines.append("")
    lines.append("| 指数 | 收盘 | 涨跌幅 |")
    lines.append("| --- | --- | --- |")
    for i in snap.get("indices") or []:
        if i.get("close") is None:
            lines.append("| %s | - | - |" % i.get("name"))
            continue
        pct = i.get("pct")
        pct_s = "-" if pct is None else ("%+.2f%%" % pct)
        lines.append("| %s | %.2f | %s |" % (i.get("name"), i.get("close"), pct_s))
    lines.append("")

    lines.append("### 二、涨跌家数与情绪")
    lines.append("")
    if b.get("total"):
        lines.append("| 指标 | 数值 |")
        lines.append("| --- | --- |")
        lines.append("| 涨停 | %d 只（剔ST %d） |" % (e.get("limit_up") or 0, e.get("limit_up_ex_st") or 0))
        lines.append("| > 7%% | %d |" % b.get("up_gt7", 0))
        lines.append("| 5~7%% | %d |" % b.get("up_5_7", 0))
        lines.append("| 3~5%% | %d |" % b.get("up_3_5", 0))
        lines.append("| 0~3%% | %d |" % b.get("up_0_3", 0))
        lines.append("| 下跌 0~3%% | %d |" % b.get("down_0_3", 0))
        lines.append("| 下跌 3~5%% | %d |" % b.get("down_3_5", 0))
        lines.append("| 下跌 5~7%% | %d |" % b.get("down_5_7", 0))
        lines.append("| 下跌 > 7%% | %d |" % b.get("down_gt7", 0))
        lines.append("| 跌停 | %d 只（剔ST %d） |" % (e.get("limit_down") or 0, e.get("limit_down_ex_st") or 0))
        lines.append("| 上涨家数 | %d |" % b.get("up_count", 0))
        lines.append("| 下跌家数 | %d |" % b.get("down_count", 0))
        lines.append("| 平盘停牌 | %d |" % b.get("flat", 0))
        lines.append("| 总品种数 | %d |" % b.get("total", 0))
        lines.append("| 总成交额 | %s |" % _yi(b.get("total_amount")))
        ac = snap.get("amount_chg") or {}
        if ac.get("available"):
            lines.append("| 成交额较昨日 | %+.2f%%（%s → %s） |" % (
                ac["delta_pct"], _yi(ac.get("prev")), _yi(ac.get("today"))))
        elif ac.get("today") is not None:
            lines.append("| 成交额较昨日 | -（缺上一交易日成交额） |")
        if b.get("source") == "rebuild":
            lines.append("")
            lines.append("> 该日涨跌分布由全市场个股日K回溯重建；成交额优先取日K精确成交额（同花顺源），"
                         "仅该源缺失时才用 量×价 估算。")
    else:
        if snap.get("is_trade_day") is False:
            lines.append("- 该日非交易日（%s 为周末或休市日），无涨跌分布可采集"
                         % snap.get("date"))
        else:
            lines.append("- 该交易日尚未执行历史数据重建，涨跌分布暂缺"
                         "（可在页面执行「历史数据重建」补齐）")
    lines.append("")
    lines.append("- 炸板 %d 只，炸板率 %s%%，炸板金额率 %s%%" % (
        e.get("broken") or 0,
        "-" if e.get("broken_rate") is None else "%.2f" % e.get("broken_rate"),
        "-" if e.get("broken_amount_rate") is None else "%.2f" % e.get("broken_amount_rate"),
    ))
    lines.append("- 最高连板高度：%s 板" % (e.get("max_lbc") or 0))
    if pp.get("avg_pct") is not None:
        lines.append("- 昨日涨停股今日平均表现：%+.2f%%（%d 只中 %d 只上涨 / %d 只下跌 / %d 只再涨停）" % (
            pp["avg_pct"], pp.get("valid") or 0, pp.get("up_count") or 0,
            pp.get("down_count") or 0, pp.get("limit_up_again") or 0))
    lines.append("")

    lines.append("### 三、涨停梯队（连板结构）")
    lines.append("")
    for g in (zt.get("ladder") or [])[:8]:
        names = "、".join("%s(%s)" % (s["name"], s["code"]) for s in g["stocks"][:14])
        more = "" if len(g["stocks"]) <= 14 else " 等 %d 只" % len(g["stocks"])
        lines.append("- **%d 板**（%d 只，%s）：%s%s" % (g["lbc"], g["count"], _yi(g["amount"]), names, more))
    if not (zt.get("ladder") or []):
        lines.append("- 无涨停")
    lines.append("")

    lines.append("### 四、涨停行业分布（前 10）")
    lines.append("")
    inds = (zt.get("industries") or [])[:10]
    if inds:
        lines.append("、".join("%s %d只" % (i["name"], i["count"]) for i in inds))
    else:
        lines.append("- 无数据")
    lines.append("")

    lines.append("### 五、概念板块情绪周期")
    lines.append("")
    cp = snap.get("concept") or {}
    if cp.get("available"):
        lines.append("- 板块全景：上涨 %d 个 / 下跌 %d 个（涨幅 >2%% 的强势板块 %d 个，跌幅 >2%% 的弱势板块 %d 个），"
                     "板块涨幅中位数 %s%%，题材情绪温度 %s/100" % (
                         cp.get("up") or 0, cp.get("down") or 0, cp.get("strong") or 0,
                         cp.get("weak") or 0,
                         "-" if cp.get("median_pct") is None else "%.2f" % cp["median_pct"],
                         "-" if cp.get("temperature") is None else "%.1f" % cp["temperature"]))
        if cp.get("main_lines"):
            lines.append("")
            lines.append("**主线题材与周期阶段**")
            lines.append("")
            lines.append("| 概念板块 | 涨跌幅 | 涨停家数 | 占板块比 | 最高连板 | 周期阶段 | 判定依据 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for m in cp["main_lines"]:
                lines.append("| %s | %s%% | %d | %s | %s | %s | %s |" % (
                    m.get("name"),
                    "-" if m.get("pct") is None else "%+.2f" % m["pct"],
                    m.get("zt_count") or 0,
                    ("%.1f%%" % m["ratio"]) if m.get("ratio") is not None else "-",
                    ("%d板" % m["max_lbc"]) if m.get("max_lbc") else "-",
                    m.get("stage") or "-", m.get("reason") or "-"))

            # 板块角色分层（龙头 / 中军 / 跟风 / 补涨）—— 只对上面进入主线题材的板块输出
            _rb = [m for m in cp["main_lines"]
                   if isinstance(m.get("roles"), dict)
                   and any((m["roles"].get(k) or [])
                           for k in ("leader", "main_force", "follower", "catchup"))]
            if _rb:
                lines.append("")
                lines.append("**板块角色分层（龙头 / 中军 / 跟风 / 补涨）**")
                lines.append("")

                def _f(x: dict) -> str:
                    return "%s(%s) %+.2f%%%s" % (
                        x.get("name"), x.get("code"), x.get("pct") or 0,
                        (" %d板" % x["lbc"]) if x.get("lbc") else "")

                for m in _rb:
                    z = m["roles"]
                    lines.append("- **%s** %s" % (
                        m.get("name"),
                        "-" if m.get("pct") is None else "%+.2f%%" % m["pct"]))
                    if z.get("leader"):
                        L = z["leader"][0]
                        lines.append("  - 龙头：%s —— %s" % (_f(L), L.get("reason") or ""))
                    if z.get("main_force"):
                        lines.append("  - 中军：%s" % "；".join(
                            "%s —— %s" % (_f(x), x.get("reason") or "")
                            for x in z["main_force"]))
                    if z.get("follower"):
                        lines.append("  - 跟风：%s" % "、".join(_f(x) for x in z["follower"]))
                    if z.get("catchup"):
                        lines.append("  - 补涨：%s" % "、".join(
                            "%s(%s) %+.2f%%（近5日 %+.2f%%）"
                            % (x.get("name"), x.get("code"),
                               x.get("pct") or 0, x.get("cum") or 0)
                            for x in z["catchup"]))
                    if z.get("note"):
                        lines.append("  - 说明：%s" % z["note"])
                lines.append("")
        if cp.get("zt_contrib"):
            lines.append("")
            lines.append("**涨停贡献榜（题材热度）**：" + "、".join(
                "%s %d只" % (c["name"], c["count"]) for c in cp["zt_contrib"][:12]))
        up_txt = "；".join("%s %+.2f%%" % (b["name"], b["pct"])
                          for b in (cp.get("top_up") or [])[:10]
                          if b.get("pct") is not None)
        dn_txt = "；".join("%s %+.2f%%" % (b["name"], b["pct"])
                          for b in (cp.get("top_down") or [])[:8]
                          if b.get("pct") is not None)
        if up_txt:
            lines.append("")
            lines.append("**概念涨幅榜 TOP10**：%s" % up_txt)
        if dn_txt:
            lines.append("")
            lines.append("**概念跌幅榜 TOP8**：%s" % dn_txt)
        if cp.get("note"):
            lines.append("")
            lines.append("> %s" % cp["note"])
        if cp.get("source") == "rebuild":
            lines.append("")
            lines.append("> 概念板块为历史重建口径：板块涨跌幅由成分股涨跌幅等权平均聚合"
                         "（已实测对齐东财板块指数，503 个板块平均误差 0.03pct）。")
    else:
        lines.append("- %s" % (cp.get("note") or "概念板块数据不可用"))
    lines.append("")

    lines.append("### 六、严重异动提醒")
    lines.append("")
    abn = snap.get("abnormal") or {}
    if abn.get("available"):
        lines.append("共 %d 只（%s）" % (
            abn.get("total") or 0,
            "、".join("%s %d 只" % (k, v) for k, v in (abn.get("counts") or {}).items())))
        lines.append("")
        lines.append("| 个股 | 现价 | 当日 | 连板 | 级别 | 判定依据 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for x in (abn.get("items") or [])[:20]:
            lines.append("| %s(%s) | %.2f | %s%% | %s | %s | %s |" % (
                x.get("name"), x.get("code"), x.get("close") or 0,
                "-" if x.get("pct") is None else "%+.2f" % x["pct"],
                ("%d板" % x["lbc"]) if x.get("lbc") else "-",
                x.get("level") or "-", "；".join(x.get("reasons") or [])))
        if abn.get("note"):
            lines.append("")
            lines.append("> %s" % abn["note"])
    elif abn.get("ok"):
        # 检测执行成功但当日无标的：不要写成"数据缺失"，这是有效结论
        lines.append("- 检测已执行，当日无满足阈值或龙虎榜异动条件的标的（0 只）。")
        if abn.get("note"):
            lines.append("")
            lines.append("> %s" % abn["note"])
    else:
        lines.append("- %s" % (abn.get("note") or "异动检测不可用"))
    lines.append("")

    lines.append("### 七、游资动向（龙虎榜席位）")
    lines.append("")
    yz = snap.get("youzi") or {}
    if yz.get("available"):
        lines.append("- 龙虎榜个股 %d 只，席位记录 %d 条；游资营业部 %d 家，"
                     "买入合计 %s / 卖出合计 %s" % (
                         yz.get("lhb_count") or 0, yz.get("record_count") or 0,
                         yz.get("seat_count") or 0, _yi(yz.get("total_buy")),
                         _yi(yz.get("total_sell"))))
        if yz.get("hots"):
            lines.append("")
            lines.append("**知名游资席位动向**")
            lines.append("")
            lines.append("| 游资 | 席位 | 买入 | 净额 | 买入个股 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for h in yz["hots"][:12]:
                lines.append("| %s | %s | %s | %s | %s |" % (
                    h.get("alias"), h.get("name"),
                    _yi(h.get("buy")), _yi(h.get("net")),
                    "、".join("%s(%+.1f%%)" % (s["name"], s["pct"] if s["pct"] is not None else 0)
                              for s in (h.get("stocks") or [])[:5])))
        if yz.get("seats"):
            lines.append("")
            lines.append("**营业部买入榜 TOP10**")
            lines.append("")
            lines.append("| 营业部 | 买入 | 净额 | 个股数 | 代表个股 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for s in yz["seats"][:10]:
                lines.append("| %s%s | %s | %s | %d | %s |" % (
                    s.get("name"), ("（%s）" % s["alias"]) if s.get("alias") else "",
                    _yi(s.get("buy")), _yi(s.get("net")), s.get("count") or 0,
                    "、".join((x.get("name") or x.get("code")) for x in (s.get("stocks") or [])[:4])))
        if yz.get("inst"):
            lines.append("")
            lines.append("**机构 / 北向通道（非游资，单独列出）**：" + "；".join(
                "%s 买%s 净%s" % (s["name"], _yi(s.get("buy")), _yi(s.get("net")))
                for s in yz["inst"][:5]))
        if yz.get("note"):
            lines.append("")
            lines.append("> %s" % yz["note"])
    else:
        lines.append("- %s" % (yz.get("note") or "无游资动向数据"))
    lines.append("")

    lines.append("### 八、股票热度榜")
    lines.append("")
    ht = snap.get("hot") or {}
    if ht.get("available"):
        _hsrc = ht.get("source") or "热度榜"
        _hist = not ht.get("source_ths", True) and ht.get("source_em") and \
            "回溯" in str(_hsrc)
        lines.append("数据源：%s（共 %d 只）" % (_hsrc, ht.get("count") or 0))
        lines.append("")
        if _hist:
            # 历史日：只有人气名次与排名变化是真实历史数据，热度值/上榜原因无源
            lines.append("| 排名 | 个股 | 涨跌幅 | 排名变化 | 热门概念 |")
            lines.append("| --- | --- | --- | --- | --- |")
        else:
            lines.append("| 排名 | 个股 | 涨跌幅 | 热度值 | 排名变化 | 东财人气 | 标签 | 热门概念 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for x in (ht.get("items") or [])[:20]:
            chg = x.get("rank_chg")
            if chg is None:
                chg_s = "-"
            elif chg == 0:
                chg_s = "持平" if _hist else "新上榜"
            else:
                chg_s = "%+d" % chg
            if _hist:
                lines.append("| %s | %s(%s) | %s%% | %s | %s |" % (
                    x.get("rank") or "-", x.get("name"), x.get("code"),
                    "-" if x.get("pct") is None else "%+.2f" % x["pct"],
                    chg_s,
                    "、".join((x.get("concepts") or [])[:3]) or "-"))
            else:
                lines.append("| %s | %s(%s) | %s%% | %s | %s | %s | %s | %s |" % (
                    x.get("rank") or x.get("em_rank") or "-",
                    x.get("name"), x.get("code"),
                    "-" if x.get("pct") is None else "%+.2f" % x["pct"],
                    x.get("rate") if x.get("rate") is not None else "-",
                    chg_s,
                    ("#%s" % x["em_rank"]) if x.get("em_rank") else "-",
                    x.get("tag") or "-",
                    "、".join((x.get("concepts") or [])[:3]) or "-"))
        if ht.get("top_concepts"):
            lines.append("")
            lines.append("**热榜概念分布**：" + "、".join(
                "%s %d只" % (c["name"], c["count"]) for c in ht["top_concepts"][:10]))
        if ht.get("note"):
            lines.append("")
            lines.append("> %s" % ht["note"])
    else:
        if snap.get("is_trade_day") is False:
            lines.append("- 该日非交易日（%s 为周末或休市日），无热度榜数据"
                         % snap.get("date"))
        else:
            lines.append("- %s" % (ht.get("note") or "热度榜数据不可用"))
    lines.append("")

    lines.append("### 九、炸板个股（前 15，按成交额）")
    lines.append("")
    _zb_src = (zb.get("source") or "").lower()
    for s in (zb.get("stocks") or [])[:15]:
        _zbc = s.get("zbc")
        if _zbc:  # 东财池：带开板次数与所属行业
            _extra = "炸板%d次 [%s]" % (_zbc, s.get("industry") or "-")
        else:     # 日K自建：给出最高价与涨停价，说明是摸板后回落
            _hi = s.get("high")
            _lp = s.get("zt_price")
            _extra = "最高%s/涨停价%s [日K回溯]" % (
                "-" if _hi is None else ("%.2f" % _hi),
                "-" if _lp is None else ("%.2f" % _lp))
        lines.append("- %s(%s) %+.2f%% %s 成交%s" % (
            s["name"], s["code"], s["pct"], _extra, _yi(s["amount"])))
    if not (zb.get("stocks") or []):
        lines.append("- 无炸板")
    if _zb_src == "kline":
        lines.append("")
        lines.append("> 该日已超出东财炸板池的回溯窗口（约 15 个交易日），上表由个股日K自建，"
                     "口径为「当日最高价触及涨停价但收盘未封住」。日K无分时数据，"
                     "无法区分「封过后开板」与「瞬时摸板」，故家数通常略多于东财池"
                     "（实测剔 ST 后 +15%，含 ST 更多，因东财池不收录 ST）；"
                     "炸板率因分子分母同向偏移，差别小于家数（约 +3 个百分点）。")
    lines.append("")

    lines.append("### 十、跌停个股（前 15）")
    lines.append("")
    for s in (dtp.get("stocks") or [])[:15]:
        lines.append("- %s(%s) %+.2f%% [%s]" % (
            s["name"], s["code"], s["pct"], s.get("industry") or "-"))
    if not (dtp.get("stocks") or []):
        lines.append("- 无跌停")
    lines.append("")

    lines.append("> 数据源：东财涨停/炸板/跌停池 + 全市场快照 + 指数日K（直连）。"
                 "涨跌家数口径对齐通达信 880005（涨跌停单列，不计入上涨/下跌家数）。")
    # 数据可得性速览：显式列出各章节有无数据，避免 LLM 把个别缺失项泛化成"全部不可回溯"
    _yn = lambda ok: "✓" if ok else "✗"  # noqa: E731
    _src = lambda d: ("（日K回溯）" if (d.get("source") or "") == "kline"
                      else "（东财池）")  # noqa: E731
    # 池类字段：count=0 可能是「当日确实没有」也可能是「没有数据源」。
    # 日K自建口径下 count=0 就是「确实没有」，算可得 —— 否则会把「无炸板」误标成缺失。
    _has = lambda d: bool((d.get("count") or 0) > 0  # noqa: E731
                          or (d.get("source") or "") == "kline")
    _ab = snap.get("abnormal") or {}
    _avail = [
        "指数 %s" % _yn(any(i.get("pct") is not None for i in (snap.get("indices") or []))),
        "涨跌家数 %s" % _yn((b.get("total") or 0) > 0),
        "成交额环比 %s" % _yn(bool((snap.get("amount_chg") or {}).get("available"))),
        "涨停池 %s%s" % (_yn(_has(zt)), _src(zt)),
        "炸板 %s%s" % (_yn(_has(zb)), _src(zb)),
        "跌停池 %s%s" % (_yn(_has(dtp)), _src(dtp)),
        "连板梯队 %s" % _yn((zt.get("max_lbc") or 0) > 0),
        "概念板块 %s" % _yn(bool((snap.get("concept") or {}).get("available"))),
        # 异动看 ok（是否成功执行），不看 available（当日是否真有标的）
        "严重异动 %s" % _yn(bool(_ab.get("ok")) or bool(_ab.get("available"))),
        "游资动向 %s" % _yn(bool((snap.get("youzi") or {}).get("available"))),
        "热度榜 %s%s" % (_yn(bool((snap.get("hot") or {}).get("available"))),
                       "（人气排名回溯）" if str((snap.get("hot") or {}).get("source") or "")
                       .find("回溯") >= 0 else ""),
    ]
    lines.append("> **本章数据可得性**：%s。" % "｜".join(_avail))
    lines.append("> 上表中标 ✗ 的项才是不存在的，标 ✓ 的均可直接引用，不要描述为「不可回溯」或「缺失」。")
    lines.append("> 说明：涨跌分布、涨停/跌停/炸板、连板梯队、概念板块、严重异动、游资动向、"
                 "热度榜名次均可回溯；唯一不可回溯的是同花顺热度值与上榜原因这两列。")
    if snap.get("note"):
        lines.append("> " + str(snap["note"]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AI 点评
# ---------------------------------------------------------------------------

AI_PROMPT = """你是一位专业的 A 股短线交易教练，请根据以下【当日盘面数据】和【用户手写复盘】，
写一份每日复盘点评（1100~1700 字，markdown 格式，不要用一级标题）。

要求：
1. 市场情绪：用给定数据说明涨跌家数对比、涨停/跌停结构、炸板率、连板高度，
   以及**两市成交额较昨日的增减**（放量/缩量对情绪的含义）反映了什么；
2. 概念板块情绪周期（重点，结合"五、概念板块情绪周期"章节）：
   ① 当日题材活跃度——强势板块（涨幅>2%）与弱势板块（跌幅>2%）的数量对比、板块涨幅中位数；
   ② 主线题材是谁（涨停贡献榜 + 涨幅榜），各自处于什么周期阶段（冰点/启动/发酵/高潮/退潮/震荡），
      依据是涨停家数变化、板块涨跌幅、连板高度与持续时间；
   ③ 题材轮动与退潮迹象——哪些题材在退潮、哪些在启动、有无高低切换或主线扩散；
3. 严重异动与风险（结合"六、严重异动提醒"）：指出高偏离标的所处的梯队位置，
   提示追高风险与可能触发交易所核查的情形；
4. 资金与游资（结合"七、游资动向"）：说明知名游资席位在买什么方向、机构与北向是净买还是净卖，
   判断当日资金是接力还是出货；
5. 人气聚焦（结合"八、股票热度榜"）：热度榜前排集中在哪些概念，与主线题材是否一致，
   有无"股价走弱但热度居前"的分歧标的；
6. 对照用户的手写复盘：指出其判断与数据一致之处、以及可能被忽略的信号（若有明显偏差直接指出）；
7. 明日 2~3 条可执行关注要点（情绪周期位置、重点盯的题材/梯队、需要规避的风险）。
严格依据给定数据，不要编造任何数字；数据缺失的地方直接跳过，不要凭空推测。

【当日盘面数据】
{data_md}

【用户手写复盘】
{manual}
"""


def ai_comment(db, snap: dict, manual_content: str) -> str:
    """调用 LLM 生成每日复盘点评（失败抛 LLMError，由上层捕获）"""
    from .llm import chat

    data_md = build_daily_markdown(snap)
    manual = (manual_content or "").strip() or "（用户未填写手写复盘）"
    prompt = AI_PROMPT.format(data_md=data_md, manual=manual)
    return chat(
        [
            {"role": "system", "content": "你是严谨的 A 股短线交易教练，只依据给定数据分析，绝不编造数字。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5, max_tokens=5000, db=db,
    ).strip()


def serialize(r) -> dict:
    """DailyReview ORM → dict"""
    ai = {}
    if r.ai_result:
        try:
            ai = json.loads(r.ai_result)
        except Exception:  # noqa: BLE001
            ai = {"content": r.ai_result}
    return {
        "id": r.id,
        "trade_date": r.trade_date.isoformat(),
        "market_json": r.market_json,
        "manual_content": r.manual_content or "",
        "ai_content": ai.get("content", ""),
        "ai_created_at": ai.get("created_at", ""),
        "has_ai": bool(ai.get("content")),
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else "",
    }


def brief(r) -> dict:
    """列表用精简结构（不返回大 JSON）"""
    snap = {}
    if r.market_json:
        try:
            snap = json.loads(r.market_json)
        except Exception:  # noqa: BLE001
            snap = {}
    e = (snap.get("emotion") or {})
    return {
        "id": r.id,
        "trade_date": r.trade_date.isoformat(),
        "has_manual": bool((r.manual_content or "").strip()),
        "manual_len": len(r.manual_content or ""),
        "has_ai": bool(r.ai_result),
        "limit_up": e.get("limit_up"),
        "limit_down": e.get("limit_down"),
        "broken": e.get("broken"),
        "broken_rate": e.get("broken_rate"),
        "max_lbc": e.get("max_lbc"),
        "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else "",
    }
