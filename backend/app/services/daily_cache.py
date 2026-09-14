"""A股每日复盘的本地历史缓存与回溯重建（V1.009.1 新增）

职责：
1. daily_market_cache 读写 —— 按交易日缓存完整快照（kind=live / rebuild）
2. board_member_cache 读写 —— 板块成分股映射（准静态，7 天过期）
3. 查询编排 get_snapshot() —— 缓存优先 → 缺失则回补（实时抓取 / 历史回溯）→ 落库
4. 后台重建任务 —— 一次性回溯最近 N 个交易日，带进度状态

设计动机：东财的"实时快照"类接口（全市场涨跌家数分布、两市成交额、概念板块实时排行）
无法按历史日期查询，涨停池/炸板池/跌停池回溯窗口也只有约 15 个交易日。要真正"能查历史"，
必须把每个交易日的数据落到本地：当日实时抓取（live），历史由全市场个股日K回溯（rebuild）。
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import traceback

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import (BoardMemberCache, DailyMarketCache, MarketCacheMeta,
                      StockHotRankCache, StockKlineCache)
from . import daily_extra as _dxe
from . import daily_market as dm

_MEMBER_TTL_DAYS = 7
_HIST_KEY = "board_hist"

# 快照数据质量等级（数值越大越可信）——见 cache_put 的说明。
# 东财的涨停/炸板/跌停池回溯窗口只有约 15 个交易日，超出窗口的历史日一旦走
# 「实时抓取」就只会得到 partial（涨跌家数 0、无概念板块），必须让它无法挤掉 rebuild。
_KIND_RANK = {"partial": 1, "rebuild": 2, "live": 3}


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------

def _d(date_str: str) -> _dt.date:
    return _dt.datetime.strptime(date_str, "%Y-%m-%d").date()


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def latest_trade_day() -> str:
    """最近交易日（以上证指数日K为日历）"""
    t = today_str()
    return (dm._recent_trade_days(t, 1) or [t])[-1]


# ---------------------------------------------------------------------------
# 快照缓存
# ---------------------------------------------------------------------------

def cache_get(db: Session, date_str: str) -> dict | None:
    r = (db.query(DailyMarketCache)
         .filter(DailyMarketCache.trade_date == _d(date_str)).first())
    if not r or not r.snapshot_json:
        return None
    try:
        snap = json.loads(r.snapshot_json)
    except Exception:  # noqa: BLE001
        return None
    snap["cache_kind"] = r.kind
    snap["cache_updated_at"] = r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else ""
    return snap


def cache_put(db: Session, date_str: str, snap: dict, kind: str = "live",
              force: bool = False) -> None:
    """写入/更新某交易日快照（按数据质量分级，低质量不得覆盖高质量）

    质量等级 live(3) > rebuild(2) > partial(1)：
      live    当日实时抓取（全市场快照在，字段最全）
      rebuild 历史全市场日K重建（涨跌家数/成交额/概念/炸板齐全）
      partial 历史日**尚未重建**时抓到的残缺快照（涨跌家数为 0、无概念板块）

    为什么必须分级：partial 是「实时接口查不了历史日期」的必然降级产物，
    它一旦覆盖 rebuild，用户辛苦重建的 60 天数据就没了（且要再花两分钟重建），
    界面上表现为「重建完又点了一次抓取，数据全空」。
    实盘日 live 同理不可被回溯/残缺数据挤掉（除非 force）。
    """
    payload = json.dumps(snap, ensure_ascii=False)
    r = (db.query(DailyMarketCache)
         .filter(DailyMarketCache.trade_date == _d(date_str)).first())
    if r:
        if not force and _KIND_RANK.get(kind, 0) < _KIND_RANK.get(r.kind or "", 0):
            return
        if r.kind == kind and r.snapshot_json == payload:
            return
        r.snapshot_json = payload
        r.kind = kind
    else:
        db.add(DailyMarketCache(trade_date=_d(date_str), kind=kind, snapshot_json=payload))
    db.commit()


def cached_dates(db: Session, limit: int = 400) -> list[dict]:
    rows = (db.query(DailyMarketCache)
            .order_by(DailyMarketCache.trade_date.desc()).limit(limit).all())
    return [{"date": r.trade_date.isoformat(), "kind": r.kind,
             "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M:%S") if r.updated_at else ""}
            for r in rows]


def cache_delete(db: Session, date_str: str) -> bool:
    r = (db.query(DailyMarketCache)
         .filter(DailyMarketCache.trade_date == _d(date_str)).first())
    if not r:
        return False
    db.delete(r)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# 板块成分股映射缓存
# ---------------------------------------------------------------------------

def members_get(db: Session, board_code: str) -> list[dict] | None:
    r = (db.query(BoardMemberCache)
         .filter(BoardMemberCache.board_code == board_code).first())
    if not r or not r.members_json:
        return None
    if r.updated_at and (_dt.datetime.now() - r.updated_at).days >= _MEMBER_TTL_DAYS:
        return None
    try:
        return json.loads(r.members_json)
    except Exception:  # noqa: BLE001
        return None


def members_all(db: Session) -> dict:
    """一次取出全部未过期的板块成分股缓存 → {board_code: {"name","members"}}"""
    out: dict = {}
    fresh_after = _dt.datetime.now() - _dt.timedelta(days=_MEMBER_TTL_DAYS)
    for r in db.query(BoardMemberCache).all():
        if not r.members_json or not r.updated_at or r.updated_at < fresh_after:
            continue
        try:
            m = json.loads(r.members_json)
        except Exception:  # noqa: BLE001
            continue
        if m:
            out[r.board_code] = {"name": r.board_name or r.board_code, "members": m}
    return out


def members_put(db: Session, board_code: str, name: str, members: list[dict],
                commit: bool = True) -> None:
    r = (db.query(BoardMemberCache)
         .filter(BoardMemberCache.board_code == board_code).first())
    payload = json.dumps(members, ensure_ascii=False)
    if r:
        r.board_name = name or r.board_name
        r.members_json = payload
    else:
        db.add(BoardMemberCache(board_code=board_code, board_name=name or "",
                                members_json=payload))
    if commit:
        db.commit()


# ---------------------------------------------------------------------------
# 板块历史序列（KV）
# ---------------------------------------------------------------------------

def hist_get(db: Session) -> dict:
    r = db.query(MarketCacheMeta).filter(MarketCacheMeta.key == _HIST_KEY).first()
    if not r or not r.value_json:
        return {}
    try:
        return json.loads(r.value_json)
    except Exception:  # noqa: BLE001
        return {}


def hist_put(db: Session, hist: dict) -> None:
    payload = json.dumps(hist, ensure_ascii=False)
    r = db.query(MarketCacheMeta).filter(MarketCacheMeta.key == _HIST_KEY).first()
    if r:
        r.value_json = payload
    else:
        db.add(MarketCacheMeta(key=_HIST_KEY, value_json=payload))
    db.commit()


# ---------------------------------------------------------------------------
# 个股日K缓存（重建断点续传）
# ---------------------------------------------------------------------------

def _norm_bar(b) -> tuple:
    """缓存行 → 7 元组 (date, close, vol, amt, high, close_raw, high_raw)

    兼容早期只有 3/4/5 位的缓存：缺的位补 None。前复权价（close/high）用于涨跌幅，
    不复权价（close_raw/high_raw）用于涨跌停与炸板判定。
    """
    def f(i):
        if len(b) <= i or b[i] is None:
            return None
        try:
            return float(b[i])
        except Exception:  # noqa: BLE001
            return None

    return (b[0], f(1), f(2), f(3), f(4), f(5), f(6))


def _pad7(b: list) -> list:
    return b + [None] * (7 - len(b)) if len(b) < 7 else b


def klines_load(db: Session, need_from: str, min_last: str,
                required: int | None = None, need_high: bool = False) -> dict:
    """加载可复用的个股日K缓存

    判据：最后一根不早于 min_last（数据新鲜）。
    覆盖度方面，满足任一条即视为可用：
      a) 最早一根不晚于 need_from（缓存窗口够长）；
      b) required 给定且缓存根数 ≥ required（例如重建 N 日本身只需要 N 根）。

    为什么需要 (b)：次新股（如上市不足 60 个交易日的个股）永远无法满足 (a)，
    若只用 (a) 会被**永久排除**在重建之外，其涨停/成交额全部遗漏。

    为什么需要 need_high：最高价（第 5 位）与不复权价（第 6/7 位）都是后加的字段，
    早期缓存没有。需要回溯「炸板」时若沿用旧缓存会静默算不出任何炸板，
    故此时**三位（4/5/6）都要有** —— 只查到最高价的 5 元组仍是不可用的：
    涨跌停基数要按「除权参考价」取（= 前复权前收 × 不复权当日收 / 前复权当日收），
    缺了不复权价就只能退化成前复权前收，除权日之前的日子会被整体缩水而漏判涨停。
    不满足即视为不可用，由调用方重抓升级。
    """
    out: dict = {}
    for r in db.query(StockKlineCache).all():
        if not r.bars_json:
            continue
        try:
            bars = json.loads(r.bars_json)
        except Exception:  # noqa: BLE001
            continue
        if not bars or len(bars) < 3:
            continue
        if min_last and str(bars[-1][0]) < min_last:
            continue
        if need_high:
            _last = bars[-1]
            if len(_last) <= 6 or any(_last[i] is None for i in (4, 5, 6)):
                continue  # 旧格式缓存（无最高价 / 无不复权价）→ 重抓
        if str(bars[0][0]) > need_from:
            if not required or len(bars) < int(required):
                continue
        try:
            out[r.code] = [_norm_bar(b) for b in bars]
        except Exception:  # noqa: BLE001
            continue
    return out


def _bar_amt(b) -> float | None:
    try:
        v = b[3] if len(b) > 3 else None
        return float(v) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


def _merge_bars(old: list, new: list) -> list:
    """按日期合并两段日K → 升序列表（缓存只增不减）

    必要性：重建窗口长度决定每次拉取的根数（days+15）。若直接覆盖，
    一次 20 日重建会把 60 日重建留下的 75 根截成 35 根，导致下次 60 日
    重建的缓存命中条件（首根日期 ≤ need_from）失效而全量重拉。

    同日冲突时：
      ① 优先保留带**精确成交额**的那根（同花顺源给精确额，腾讯源只有 量×价
         估算，两者混用会让成交额口径不一致）；
      ② 价格附加位（最高价 4 / 不复权收 5 / 不复权高 6）在两值间取「有值的那个」，
         旧格式缓存升级时不能把新拉到的字段丢掉，反之亦然。
    """
    m: dict = {}
    for b in old:
        if b:
            m[str(b[0])] = _pad7(list(b))
    for b in new:
        if not b:
            continue
        k = str(b[0])
        nb = _pad7(list(b))
        pb = m.get(k)
        if pb is not None:
            for i in (4, 5, 6):
                if nb[i] is None and pb[i] is not None:
                    nb[i] = pb[i]  # 新值缺该位 → 沿用旧值
            if _bar_amt(nb) is None and _bar_amt(pb) is not None:
                # 新值缺精确额而旧值有 → 保留旧值成交额，附加位取有值者
                for i in (4, 5, 6):
                    if pb[i] is None:
                        pb[i] = nb[i]
                m[k] = pb
                continue
        m[k] = nb
    return [m[k] for k in sorted(m.keys())]


def klines_save(db: Session, klines: dict, names: dict | None = None) -> int:
    """批量写入个股日K缓存（合并式，只写发生变化的）

    每根存 [日期, 收盘, 成交量(股), 成交额(元|None), 最高价, 不复权收, 不复权高]：
      成交额为空表示该源未提供，读取方用 收盘价×成交量 估算；
      最高价供炸板回溯使用；
      末两位不复权价供**涨跌停基数**使用（除权参考价 = 前复权前收 × 不复权当日收 /
      前复权当日收，见 daily_market.limit_base），缺了它除权日之前的日子会漏判涨停。
    价格一律 round 到 3 位（源数据本就 2 位，3 位是无损上限），不可截断到 2 位以外。
    与已有缓存按日期合并，保证缓存窗口单调变宽（见 _merge_bars），使重复重建的增量拉取生效。
    """
    names = names or {}

    def _g(b, i):
        return round(float(b[i]), 3) if len(b) > i and b[i] is not None else None

    n = 0
    for code, bars in klines.items():
        if not bars:
            continue
        r = db.query(StockKlineCache).filter(StockKlineCache.code == code).first()
        old: list = []
        if r and r.bars_json:
            try:
                old = json.loads(r.bars_json) or []
            except Exception:  # noqa: BLE001
                old = []
        merged = _merge_bars(old, bars) if old else bars
        payload = json.dumps(
            [[b[0], _g(b, 1), _g(b, 2),
              (round(float(b[3]), 0) if len(b) > 3 and b[3] is not None else None),
              _g(b, 4), _g(b, 5), _g(b, 6)]
             for b in merged],
            ensure_ascii=False)
        if r:
            if r.bars_json == payload:
                continue
            r.bars_json = payload
            if names.get(code):
                r.name = names[code]
        else:
            db.add(StockKlineCache(code=code, name=names.get(code) or "", bars_json=payload))
        n += 1
    if n:
        db.commit()
    return n


# ---------------------------------------------------------------------------
# 查询编排
# ---------------------------------------------------------------------------

def klines_names(db: Session) -> dict:
    """{代码: 名称}（日K缓存附带，用于涨停判定与异动提醒显示）"""
    out: dict = {}
    for c, n in db.query(StockKlineCache.code, StockKlineCache.name).all():
        if c and n:
            out[c] = n
    return out


# --- 个股历史人气排名（热度榜回溯的数据底座） -------------------------------

def hot_series_load(db: Session, codes: list[str] | None = None) -> dict:
    """读个股人气排名序列 → {代码: {日期: 名次}}

    `codes` 为空则读全表（历史日热度榜需要在整个候选集里排序，必须全读）。
    典型规模：2500 只 × 120 天，JSON 约 7 MB，解码约 0.3 秒 —— 可接受。
    """
    q = db.query(StockHotRankCache.code, StockHotRankCache.series_json)
    if codes:
        q = q.filter(StockHotRankCache.code.in_(list(codes)))
    out: dict = {}
    for c, sj in q.all():
        if not c or not sj:
            continue
        try:
            rows = json.loads(sj)
        except Exception:  # noqa: BLE001
            continue
        m = {}
        for it in (rows or []):
            if isinstance(it, (list, tuple)) and len(it) >= 2 and it[0]:
                m[str(it[0])[:10]] = it[1]
        if m:
            out[c] = m
    return out


def hot_series_sync(db: Session, candidates: list[str], ttl_hours: int = 6,
                    workers: int = 8, on_progress=None) -> dict:
    """增量抓取候选股的人气排名序列并落库

    只抓「缓存里没有」或「updated_at 早于 ttl_hours 前」的股票，因此第二次重建近乎零成本。
    实测 0.016 秒/只（8 并发）。
    """
    from ..models import StockHotRankCache as _M
    fresh_after = _dt.datetime.now() - _dt.timedelta(hours=ttl_hours)
    have = set()
    for c, u in db.query(_M.code, _M.updated_at).all():
        if c and u and u >= fresh_after:
            have.add(c)
    todo = [c for c in (candidates or []) if c and c not in have]
    if not todo:
        return {"fetched": 0, "skipped": len(candidates or []), "hit": 0}

    got = _dxe.hot_rank_hist(todo, workers=workers, on_progress=on_progress)
    now = _dt.datetime.now()
    saved = 0
    for c, m in (got or {}).items():
        if not m:
            continue
        rows = sorted((str(k)[:10], int(v)) for k, v in m.items() if v)
        payload = json.dumps(rows, ensure_ascii=False)
        r = db.query(_M).filter(_M.code == c).first()
        if r:
            r.series_json = payload
            r.day_count = len(rows)
            r.updated_at = now
        else:
            db.add(_M(code=c, series_json=payload, day_count=len(rows),
                      updated_at=now))
        saved += 1
    db.commit()
    return {"fetched": len(todo), "skipped": len(candidates or []) - len(todo),
            "hit": saved}


def hot_concepts_map(db: Session) -> dict:
    """{代码: [概念板块名]} —— 由板块成分股缓存反查，用于补历史热度榜的「热门概念」

    成分股映射是**当前**口径（准静态），用于历史日会有一点点失真：概念标签本身是慢变量
    （板块归属调整不频繁），可接受。噪音板块（融资融券/深股通之类的"伪概念"）已剔除。
    """
    out: dict = {}
    try:
        allb = members_all(db)
    except Exception:  # noqa: BLE001
        return out
    for _bc, v in (allb or {}).items():
        name = v.get("name") or ""
        if not name or dm._is_noise_board(name):
            continue
        for m in (v.get("members") or []):
            c = m.get("code") if isinstance(m, dict) else None
            if not c:
                continue
            lst = out.setdefault(c, [])
            if name not in lst:
                lst.append(name)
    return out


def _trend_item(date_str: str, snap: dict, kind: str = "") -> dict:
    """快照 → 趋势图的一个数据点

    字段与 `daily_market.collect_trend` 的返回严格一一对应（前端直接消费），
    炸板率/炸板金额率的分母都含炸板自身，与当日看板口径一致。

    V1.009.3 起补充「情绪结构」字段（首板/连板家数、昨日涨停股今日表现、
    上涨家数占比、两市成交额），供页面上的近期情绪趋势小图使用。
    ⚠️ 取不到与真为 0 必须区分：字段缺失时返回 None（图上断点），不要填 0，
    否则历史残缺快照（partial）会在图里画出一根假的下探线。
    """
    zt = snap.get("limit_up") or {}
    zb = snap.get("broken") or {}
    dtp = snap.get("limit_down") or {}
    zt_c = int(zt.get("count") or 0)
    zb_c = int(zb.get("count") or 0)
    zt_a = float(zt.get("amount") or 0)
    zb_a = float(zb.get("amount") or 0)

    # ---- 首板 / 连板家数：由涨停池个股的 lbc（连板数）分档统计 ----
    lbc_list = [int(x.get("lbc") or 1) for x in (zt.get("stocks") or []) if isinstance(x, dict)]
    if zt_c == 0:
        first_board = multi_board = 0
    elif lbc_list:
        first_board = sum(1 for n in lbc_list if n <= 1)
        multi_board = sum(1 for n in lbc_list if n >= 2)
    else:
        # 有涨停家数却没有个股明细 —— 残缺快照，标为不可得
        first_board = multi_board = None

    # ---- 昨日涨停股今日表现：情绪承接的核心指标 ----
    pl = snap.get("prev_limit_up") or {}
    pl_valid = int(pl.get("valid") or 0)
    if pl_valid > 0:
        prev_lu_avg = pl.get("avg_pct")
        prev_lu_again = int(pl.get("limit_up_again") or 0)
        prev_lu_up = int(pl.get("up_count") or 0)
        prev_lu_rate = round(prev_lu_again / pl_valid * 100, 2)
    else:
        prev_lu_avg = prev_lu_again = prev_lu_up = prev_lu_rate = None

    # ---- 市场宽度：上涨家数占比 ----
    # ⚠️ breadth 的上涨/下跌家数是「四档区间家数之和」，**不含涨跌停**（涨跌停单列），
    #    平盘/停牌也单列。所以占比必须把涨停并进分子、把涨跌停与平盘都并进分母，
    #    才是「全部有行情品种里红盘的比例」，也才能与「涨跌家数分布」表对上账。
    b = snap.get("breadth") or {}
    has_breadth = bool(b.get("total"))
    up_c = int(b.get("up_count") or 0)
    down_c = int(b.get("down_count") or 0)
    flat_c = int(b.get("flat") or 0)
    dt_c = int(dtp.get("count") or 0)
    denom = up_c + down_c + flat_c + zt_c + dt_c
    if has_breadth and denom:
        up_ratio = round((up_c + zt_c) / denom * 100, 2)
    else:
        up_ratio = None

    return {
        "date": date_str,
        "limit_up": zt_c,
        "broken": zb_c,
        "limit_down": int(dtp.get("count") or 0),
        "zt_amount": zt_a,
        "zb_amount": zb_a,
        "broken_rate": round(zb_c / (zt_c + zb_c) * 100, 2) if (zt_c + zb_c) else None,
        "broken_amount_rate": (round(zb_a / (zt_a + zb_a) * 100, 2) if (zt_a + zb_a) else None),
        "max_lbc": int(zt.get("max_lbc") or 0),
        # --- 情绪结构（V1.009.3）---
        "first_board": first_board,
        "multi_board": multi_board,
        "prev_lu_avg": prev_lu_avg,
        "prev_lu_again": prev_lu_again,
        "prev_lu_up": prev_lu_up,
        "prev_lu_rate": prev_lu_rate,
        "prev_lu_valid": pl_valid or None,
        "up_count": up_c if has_breadth else None,
        "down_count": down_c if has_breadth else None,
        "up_ratio": up_ratio,
        "total_amount": (
            float(b.get("total_amount") or 0) if has_breadth else None
        ),
        "dt_amount": float(dtp.get("amount") or 0) if dtp.get("count") else None,
        "source": kind or "cache",
    }


def trend_from_cache(db: Session, end_str: str, days: int) -> tuple[list[dict], list[str]]:
    """从本地快照缓存拼出「情绪趋势」序列 → (命中项, 缺失的交易日)

    为什么要走缓存：东财涨/炸/跌停池的回溯窗口只有约 15 个交易日，逐日实时抓取时
    趋势图最多只能画 15 个点（更早日期全空）——这正是「重建之后情绪分析仍只有 15 日」
    的原因。重建已把每个交易日的涨停/炸板/跌停结构与连板高度写进 daily_market_cache，
    这里直接复用：既能把趋势画满 N 日，又与当日看板同源同口径（不会出现"趋势图与
    某日详情对不上"）。

    缺失日期交调用方决定是否用东财池回补（见 routers/daily_reviews.trend）。
    """
    n = max(int(days), 1)
    end = _d(end_str)
    rows = (db.query(DailyMarketCache)
            .filter(DailyMarketCache.trade_date <= end)
            .order_by(DailyMarketCache.trade_date.desc())
            .limit(n + 20).all())
    items: list[dict] = []
    have: set[str] = set()
    for r in rows:
        if len(items) >= n:
            break
        if not r.snapshot_json:
            continue
        try:
            s = json.loads(r.snapshot_json)
        except Exception:  # noqa: BLE001
            continue
        # 周末/节假日被查过留下的空壳不属于交易日，不能进趋势图（否则画出假的数据点）
        if s.get("is_trade_day") is False:
            continue
        ds = r.trade_date.isoformat()
        items.append(_trend_item(ds, s, r.kind or ""))
        have.add(ds)
    items.sort(key=lambda x: x["date"])
    try:
        want = dm._recent_trade_days(end_str, n)
    except Exception:  # noqa: BLE001
        want = [x["date"] for x in items]
    missing = [d for d in want if d not in have]
    return items, missing


def _index_pct_map(date_str: str, back_days: int = 70) -> dict:
    """基准指数逐日涨跌幅 → {指数名: {日期: pct}}（严重异动偏离值用）"""
    try:
        beg = (_d(date_str) - _dt.timedelta(days=back_days)).strftime("%Y-%m-%d")
        iser = dm._index_series(beg, date_str)
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for nm, mm in (iser or {}).items():
        out[nm] = {d: r.get("pct") for d, r in (mm or {}).items()
                   if isinstance(r.get("pct"), (int, float))}
    return out


def get_snapshot(db: Session, date_str: str, force: bool = False) -> tuple[dict, str]:
    """取某交易日的盘面快照（缓存优先 → 回补 → 落库）

    返回 (snapshot, source)；source ∈ "cache" / "fresh"
    """
    is_latest = date_str == latest_trade_day()
    if not is_latest:
        # 历史日：缓存优先是**无条件**的（force 也一样）。
        # 实时接口按历史日期只能取到涨停/炸板/跌停池（且仅约 15 个交易日），
        # 涨跌家数、两市成交额、概念板块一律取不到 —— 强行「重新抓取」只会得到一份
        # partial 残缺快照。对历史日而言，「重建」才是唯一的刷新手段。
        # 命中缓存时回带 force_ignored，前端据此提示用户改走重建而非重抓。
        cached = cache_get(db, date_str)
        if cached:
            if force:
                cached["force_ignored"] = True
            return cached, "cache"
    elif not force:
        cached = cache_get(db, date_str)
        # 最近交易日：只认实盘缓存，避免早上重建数据"锁死"当日
        if cached and cached.get("cache_kind") == "live":
            return cached, "cache"

    # 个股日K（严重异动 / 昨日涨停表现回溯）+ 基准指数序列
    klines: dict = {}
    names: dict = {}
    index_pct: dict = {}
    try:
        # required=5：异动/昨日涨停回溯只需近几日数据，次新股也应纳入。
        # 这里**刻意不传 need_high**：本函数是只读路径、不做抓取，若强制要求最高价与
        # 不复权价（7 元组的第 4/5/6 位），在缓存被重建升级之前会让整段失效；
        # 缺这些位时炸板兜底自然返回空池，属于可接受的降级（重建一次即补齐）。
        klines = klines_load(
            db, (_d(date_str) - _dt.timedelta(days=75)).strftime("%Y-%m-%d"), "",
            required=5) or {}
        if klines:
            names = klines_names(db)
            index_pct = _index_pct_map(date_str)
    except Exception:  # noqa: BLE001
        klines, names, index_pct = {}, {}, {}

    hist = hist_get(db)
    # 历史日热度榜：读本地人气排名序列（纯本地读，约 0.3 秒），不在此处发起网络抓取
    hot_series, hot_concepts = {}, {}
    if not is_latest:
        try:
            hot_series = hot_series_load(db)
            if hot_series:
                hot_concepts = hot_concepts_map(db)
        except Exception:  # noqa: BLE001
            hot_series, hot_concepts = {}, {}
    snap = dm.collect_daily(date_str, hist_boards=hist, klines=klines or None,
                            index_pct=index_pct or None, names=names or None,
                            hot_series=hot_series or None,
                            hot_concepts=hot_concepts or None,
                            # 板块成分股缓存（只读，缺则角色分层降级为不显示）
                            member_loader=(lambda: members_all(db)) if is_latest else None)
    if is_latest:
        kind = "live"
    else:
        src = (snap.get("breadth") or {}).get("source")
        kind = "rebuild" if src == "rebuild" else "partial"
    cache_put(db, date_str, snap, kind)
    return snap, "fresh"


# ---------------------------------------------------------------------------
# 后台重建任务
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_state: dict = {
    "status": "idle",       # idle / running / done / error
    "message": "",
    "progress": 0,
    "days": 0,
    "saved": 0,
    "started_at": "",
    "finished_at": "",
    "error": "",
    "universe": 0,
    "kline_ok": 0,
    "hot_stocks": 0,
    "dates": [],
}


def _progress(msg: str, pct: int) -> None:
    _state["message"] = msg
    _state["progress"] = max(0, min(100, int(pct)))


def rebuild_status() -> dict:
    return dict(_state)


def start_rebuild(days: int = 60) -> dict:
    """启动后台重建任务（同一时刻只允许一个）"""
    with _lock:
        if _state["status"] == "running":
            return {"ok": False, "error": "重建任务正在进行中",
                    "progress": _state["progress"], "message": _state["message"]}
        _state.update({
            "status": "running", "message": "准备中…", "progress": 0,
            "days": days, "saved": 0, "started_at": _now(), "finished_at": "",
            "error": "", "universe": 0, "kline_ok": 0, "hot_stocks": 0, "dates": [],
        })
    threading.Thread(target=_run_rebuild, args=(days,), daemon=True).start()
    return {"ok": True, "status": "running", "days": days}


def _run_rebuild(days: int) -> None:
    def _loader(need_from: str, min_last: str) -> dict:
        db = SessionLocal()
        try:
            # required 是「根数下限」，低于它的缓存行会被判为不可用。
            # **不要传 days+2**：重建 N 日并不要求每只个股都有 N 根 —— 次新股（上市不足
            # 窗口长度）会被整只排除，后果有三：① 每次重建都重抓它（浪费网络）；
            # ② 它的涨停/成交额/板块聚合全部漏计（涨跌家数系统性偏少）；
            # ③ 实时路径用全市场快照（含次新股）算角色，历史路径却没有它 →
            #    同一天「今天」与「昨天」的龙头/跟风名单对不上。
            # 取 cum_win+2：角色判定的「近 5 日累计涨幅」需要 6 根，+1 根用于预热算涨跌幅。
            # need_high=True：重建要回溯炸板，必须拿到最高价 —— 旧格式缓存会被判为不可用
            # 并重抓升级（这也是既有缓存一次性补上不复权价/最高价的唯一入口）。
            return klines_load(db, need_from, min_last,
                               required=dm._ROLE_CUM_WIN + 2, need_high=True)
        finally:
            db.close()

    def _saver(klines: dict, names: dict) -> int:
        db = SessionLocal()
        try:
            return klines_save(db, klines, names)
        finally:
            db.close()

    def _member_loader() -> dict:
        db = SessionLocal()
        try:
            return members_all(db)
        finally:
            db.close()

    _mem_buf: list[tuple] = []

    def _flush_members() -> None:
        if not _mem_buf:
            return
        db = SessionLocal()
        try:
            for (c, n, m) in _mem_buf:
                members_put(db, c, n, m, commit=False)
            db.commit()
        finally:
            db.close()
        _mem_buf.clear()

    def _member_saver(code: str, name: str, members: list) -> None:
        # 成分股是并发回调，这里只入缓冲，批量落库（约 500 个板块逐个开会话太慢）
        _mem_buf.append((code, name, members))
        if len(_mem_buf) >= 60:
            _flush_members()

    def _hot_loader(klines: dict, dates: list[str]) -> dict:
        """人气排名序列的「算候选集 → 增量抓取 → 读回」全流程

        候选集 = 各日成交额前 N ∪ 单日涨跌幅绝对值 ≥ 7%（见 daily_extra.hot_candidates）。
        只抓缓存里没有或过期的股票，因此二次重建近乎零成本（只补当日新增的排名）。
        """
        db = SessionLocal()
        try:
            cands = _dxe.hot_candidates(klines, dates)
            # 兜底：并入「全市场清单里没有日K」的股票。这些多是新股/次新股，
            # 恰恰是人气榜常客，但没有日K就算不出成交额与波动、进不了候选集
            # （实测漏掉 688836：东财人气榜 top30，而本地日K为 0 根）。
            # 数量通常在几十只量级，成本可忽略。
            try:
                _uni = dm.stock_universe() or []
                _extra = [u["code"] for u in _uni
                          if u.get("code") and u["code"] not in klines]
                if _extra:
                    cands = sorted(set(cands) | set(_extra))
            except Exception:  # noqa: BLE001
                pass
            _progress("抓取个股历史人气排名（候选 %d 只）" % len(cands), 51)
            st = hot_series_sync(
                db, cands, ttl_hours=6, workers=8,
                on_progress=lambda d, t: _progress(
                    "个股人气排名 %d/%d" % (d, t), 51 + int(d / max(1, t) * 1)))
            if st.get("hit"):
                _state["hot_stocks"] = st["hit"]
            return hot_series_load(db)
        finally:
            db.close()

    try:
        res = dm.build_history_snapshots(days=days, on_progress=_progress,
                                         kline_loader=_loader, kline_saver=_saver,
                                         member_loader=_member_loader,
                                         member_saver=_member_saver,
                                         hot_loader=_hot_loader)
        _flush_members()
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "重建失败")

        # 补挂热门概念：概念映射来自本轮刚刷新的板块成分股缓存，必须等重建跑完才有
        try:
            _cdb = SessionLocal()
            try:
                cmap = hot_concepts_map(_cdb)
            finally:
                _cdb.close()
            if cmap:
                for _snap in (res.get("snapshots") or {}).values():
                    h = _snap.get("hot")
                    if isinstance(h, dict) and h.get("available"):
                        _dxe.hot_fill_concepts(h, cmap)
        except Exception:  # noqa: BLE001
            pass

        db = SessionLocal()
        try:
            saved = 0
            for d, snap in (res.get("snapshots") or {}).items():
                cache_put(db, d, snap, "rebuild")
                saved += 1
            if res.get("board_hist"):
                hist_put(db, res["board_hist"])
        finally:
            db.close()
        _state.update({
            "status": "done", "progress": 100, "saved": saved,
            "finished_at": _now(), "message": "重建完成（%d 个交易日）" % saved,
            "universe": res.get("universe") or 0, "kline_ok": res.get("kline_ok") or 0,
            "hot_stocks": res.get("hot_stocks") or _state.get("hot_stocks") or 0,
            "dates": res.get("dates") or [],
        })
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _state.update({
            "status": "error", "error": str(e), "finished_at": _now(),
            "message": "重建失败：%s" % e,
        })
