"""A股每日复盘接口（V1.009 新模块 / V1.009.1 增加概念板块与历史缓存）

流程：抓取当日盘面（/fetch，缓存优先）→ 用户写复盘 → 保存（POST ""）/ AI 点评（/ai）。
一个交易日一条记录（user_id + trade_date 唯一），重复保存即覆盖。

历史数据：东财实时快照类接口无法按历史日期查询，故引入本地缓存
（services/daily_cache.py）：当日实时抓取（live），历史由全市场个股日K回溯重建（rebuild）。
POST /rebuild 触发后台重建，GET /rebuild/status 轮询进度。
"""
import datetime as _dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DailyReview, User
from ..routers.auth import get_current_user
from ..services import daily_cache as dc
from ..services import daily_market as dm
from ..services.llm import LLMError

router = APIRouter(prefix="/api/daily-reviews", tags=["A股每日复盘"])


class SaveIn(BaseModel):
    trade_date: _dt.date
    manual_content: str = ""
    market_json: str = ""  # 前端回传抓取到的快照（保证"所见即所存"；空则不覆盖）


class AiIn(BaseModel):
    trade_date: _dt.date
    manual_content: str = ""
    market_json: str = ""


def _parse_date(s: str) -> _dt.date:
    try:
        return _dt.datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from e


# 快照「扩展字段」——V1.009 起才有；缺任一即说明是早期版本遗留的残缺快照。
# 注意判据必须落在**键是否存在**上，不能看 available 是否为 True：
# 热度榜的数据源不提供历史榜单，历史日期 available 本就是 False（键在、值为假），
# 若按 available 判残缺会导致缓存被反复重抓。
_SNAP_EXT_KEYS = ("concept", "amount_chg", "abnormal", "youzi", "hot")


def _snap_stale(snap) -> bool:
    """快照是否为早期版本遗留的残缺数据（需回退到本地缓存）。

    命中任一即视为残缺：
      1) 不是 dict；
      2) 涨跌家数缺失（total 为 0/None）—— 早期历史快照会是这样；
      3) 缺 _SNAP_EXT_KEYS 中任一键 —— 早期版本没有成交额环比与四个扩展模块。
    """
    if not isinstance(snap, dict):
        return True
    if ((snap.get("breadth") or {}).get("total") or 0) == 0:
        return True
    return any(k not in snap for k in _SNAP_EXT_KEYS)


# ---------------------------------------------------------------------------
# 抓取（不落库）
# ---------------------------------------------------------------------------

@router.get("/fetch")
def fetch_snapshot(
    date_str: str = Query(..., alias="date"),
    force: bool = Query(False, description="忽略缓存强制重新抓取"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取指定交易日的盘面数据快照（缓存优先，未命中才联网抓取）。

    最近交易日走实时抓取（含涨跌分布、概念板块实时排行）；
    历史日期优先读本地缓存，未命中则尝试回溯（涨停池等按日查询，
    涨跌分布/概念板块需先执行「历史数据重建」）。
    """
    d = _parse_date(date_str)
    today = _dt.date.today()
    if d > today:
        raise HTTPException(status_code=400, detail="不能抓取未来日期")
    if (today - d).days > 400:
        raise HTTPException(status_code=400, detail="仅支持查询近一年内的交易日")
    try:
        snap, source = dc.get_snapshot(db, d.isoformat(), force=force)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"盘面数据获取失败: {e}") from e
    if not snap.get("is_latest") and not (snap.get("breadth") or {}).get("source"):
        snap["need_rebuild"] = True
    return {"snapshot": snap, "markdown": dm.build_daily_markdown(snap), "source": source}


@router.post("/rebuild")
def start_rebuild(
    days: int = Query(60, ge=5, le=250),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """启动后台「历史数据重建」（全市场个股日K回溯，约 1~3 分钟）

    重建完成后，所选区间内每个交易日的涨跌分布、涨停跌停结构、连板梯队、
    概念板块表现都会写入本地缓存，历史复盘即可随时快速查看。
    """
    return dc.start_rebuild(days)


@router.get("/rebuild/status")
def rebuild_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """重建任务进度"""
    return dc.rebuild_status()


@router.get("/cache/dates")
def cache_dates(
    limit: int = Query(400, ge=1, le=2000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """本地已缓存的交易日（含来源 live/rebuild）"""
    return dc.cached_dates(db, limit)


@router.get("/concept/boards")
def concept_boards(
    kind: str = Query("concept", description="concept=概念板块 / industry=行业板块"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """概念板块实时排行（东财全量，约 504 个）"""
    fs = dm._FS_INDUSTRY if kind == "industry" else dm._FS_CONCEPT
    rows = dm.board_list(fs)
    return {"kind": kind, "total": len(rows), "boards": rows}


@router.get("/concept/history")
def concept_history(
    code: str = Query("", description="板块代码，如 BK0976；留空返回涨停贡献最高的板块"),
    days: int = Query(20, ge=5, le=120),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """概念板块历史序列（来自本地重建数据）：近 N 日涨跌幅与涨停家数"""
    hist = dc.hist_get(db)
    if not hist:
        return {"code": code, "seq": [], "note": "暂无重建数据，请先执行历史数据重建"}
    if code:
        seq = (hist.get(code) or [])
        return {"code": code, "seq": seq[-days:]}
    # 留空：按"近 N 日涨停总数"排序返回 TOP10 板块
    ranked = sorted(
        ({"code": k, "name": (v[-1] or {}).get("name", k),
          "zt_sum": sum(int(x.get("zt") or 0) for x in v[-days:]),
          "seq": v[-days:]}
         for k, v in hist.items() if v),
        key=lambda x: -x["zt_sum"],
    )[:10]
    return {"code": "", "boards": ranked, "note": ""}


@router.get("/trend")
def trend(
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    days: int = Query(20, ge=2, le=60),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """多日情绪趋势（涨停/炸板/跌停家数、炸板率、连板高度），用于趋势图"""
    d = _parse_date(end)
    try:
        return dm.collect_trend(d.isoformat(), days)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"趋势数据抓取失败: {e}") from e


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------

@router.get("")
def list_reviews(
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """历史每日复盘列表（日期倒序，精简字段）"""
    q = db.query(DailyReview).filter(DailyReview.user_id == user.id)
    if start:
        q = q.filter(DailyReview.trade_date >= _parse_date(start))
    if end:
        q = q.filter(DailyReview.trade_date <= _parse_date(end))
    rows = q.order_by(DailyReview.trade_date.desc()).limit(min(max(limit, 1), 400)).all()
    return [dm.brief(r) for r in rows]


@router.get("/{review_id}")
def get_review(
    review_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.query(DailyReview).filter(DailyReview.id == review_id).first()
    if not r or r.user_id != user.id:
        raise HTTPException(status_code=404, detail="每日复盘不存在")
    return dm.serialize(r)


@router.get("/by-date/{trade_date}")
def get_review_by_date(
    trade_date: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按交易日取已保存的复盘（不存在返回 404，前端据此判断是新建还是回看）"""
    d = _parse_date(trade_date)
    r = (db.query(DailyReview)
         .filter(DailyReview.user_id == user.id, DailyReview.trade_date == d)
         .first())
    if not r:
        raise HTTPException(status_code=404, detail="该交易日暂无复盘记录")
    return dm.serialize(r)


# ---------------------------------------------------------------------------
# 保存 / AI 点评 / 删除
# ---------------------------------------------------------------------------

@router.post("")
def save_review(
    data: SaveIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保存（或更新）某交易日的复盘：手写正文 + 盘面快照"""
    cur = (db.query(DailyReview)
           .filter(DailyReview.user_id == user.id,
                   DailyReview.trade_date == data.trade_date)
           .first())
    if cur:
        if data.market_json:
            cur.market_json = data.market_json
        cur.manual_content = data.manual_content or ""
    else:
        cur = DailyReview(
            user_id=user.id,
            trade_date=data.trade_date,
            market_json=data.market_json or "",
            manual_content=data.manual_content or "",
        )
        db.add(cur)
    db.commit()
    db.refresh(cur)
    return dm.serialize(cur)


@router.post("/ai")
def ai_analyze(
    data: AiIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """AI 点评（结合当日盘面数据 + 用户手写复盘），结果落库到该交易日记录。

    约 20~60 秒；市场数据优先用传入快照，其次读库，都没有则实时抓取。
    """
    snap = None
    refresh_record = False
    if data.market_json:
        try:
            snap = json.loads(data.market_json)
        except Exception:  # noqa: BLE001
            snap = None

    cur = (db.query(DailyReview)
           .filter(DailyReview.user_id == user.id,
                   DailyReview.trade_date == data.trade_date)
           .first())
    if snap is None and cur and cur.market_json:
        try:
            snap = json.loads(cur.market_json)
        except Exception:  # noqa: BLE001
            snap = None
    # 记录里的快照可能是早期版本遗留的（涨跌家数正常、但缺成交额环比与四个扩展模块），
    # 而缓存里已有完整结果 —— 这种情况必须以缓存为准，否则 AI 会拿着残缺数据说"不可回溯"。
    if snap is None or _snap_stale(snap):
        try:
            snap2, _src = dc.get_snapshot(db, data.trade_date.isoformat())
        except Exception:  # noqa: BLE001
            snap2 = None
        if snap2:
            snap = snap2
            refresh_record = True
    if snap is None:
        raise HTTPException(status_code=502, detail="盘面数据获取失败：该日期既无缓存也无本地记录")

    manual = data.manual_content if data.manual_content else ((cur.manual_content if cur else "") or "")
    try:
        text = dm.ai_comment(db, snap, manual)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"AI 点评失败：{e}（请检查 API 设置）") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"AI 点评失败：{e}") from e

    payload = json.dumps({
        "content": text,
        "created_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False)

    if cur:
        cur.ai_result = payload
        if data.manual_content:
            cur.manual_content = data.manual_content
        if not cur.market_json or refresh_record:
            cur.market_json = json.dumps(snap, ensure_ascii=False)
    else:
        cur = DailyReview(
            user_id=user.id,
            trade_date=data.trade_date,
            market_json=json.dumps(snap, ensure_ascii=False),
            manual_content=data.manual_content or "",
            ai_result=payload,
        )
        db.add(cur)
    db.commit()
    db.refresh(cur)
    return dm.serialize(cur)


@router.delete("/{review_id}")
def delete_review(
    review_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    r = db.query(DailyReview).filter(DailyReview.id == review_id).first()
    if not r or r.user_id != user.id:
        raise HTTPException(status_code=404, detail="每日复盘不存在")
    db.delete(r)
    db.commit()
    return {"ok": True}
