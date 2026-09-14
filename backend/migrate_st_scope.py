# -*- coding: utf-8 -*-
"""把 daily_market_cache 里 rebuild 记录的涨跌停口径迁移为「剔 ST」。

背景：rebuild 路径原先 count 含 ST，live 路径 count 剔 ST（东财池固有），
同一字段两种语义，趋势图上相邻两天不可比。统一为剔 ST 后需要就地改写历史快照。

可行性：缓存里已存 limit_up/limit_down/broken 的完整 stocks 明细（含 is_st / pct / lbc / amount），
因此无需重新抓取，本地重算即可。

幂等：若 limit_up 已带 count_inc_st 键则跳过该日。

用法：python migrate_st_scope.py <db_path> [--dry]
"""
import io
import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services.daily_market import _ladder_from  # noqa: E402


def _bucket(b: dict, pct: float, sign: str) -> None:
    """按涨跌幅把个股放进 breadth 档位（与 daily_market 的分档阈值严格一致）"""
    if sign == "up":
        if pct > 7:
            b["up_gt7"] += 1
        elif pct > 5:
            b["up_5_7"] += 1
        elif pct > 3:
            b["up_3_5"] += 1
        elif pct > 0:
            b["up_0_3"] += 1
        else:
            b["flat"] += 1
    else:
        if pct >= -3:
            b["down_0_3"] += 1
        elif pct >= -5:
            b["down_3_5"] += 1
        elif pct >= -7:
            b["down_5_7"] += 1
        else:
            b["down_gt7"] += 1


def migrate(conn: sqlite3.Connection, dry: bool) -> None:
    rows = conn.execute(
        "SELECT trade_date, kind, snapshot_json FROM daily_market_cache ORDER BY trade_date"
    ).fetchall()
    stat = {"skip_live": 0, "skip_done": 0, "done": 0}
    for td, kind, sj in rows:
        snap = json.loads(sj)
        lu, ld, zb = snap.get("limit_up") or {}, snap.get("limit_down") or {}, snap.get("broken") or {}
        if not lu:
            stat["skip_live"] += 1
            continue
        if "count_inc_st" in lu:
            stat["skip_done"] += 1
            continue

        # 只处理 rebuild（日K自建 = 含 ST 口径）。live / partial 记录的 stocks 来自东财池
        # （本就剔 ST），没有 ST 明细可算 —— 该日含 ST 口径不可得，显式置 None 而不是
        # 冒充成与剔 ST 相同，否则前端会把「40/40」当成真的两个口径一致。
        if kind != "rebuild":
            stat["skip_live"] += 1
            for struct in (lu, ld, zb):
                if struct:
                    struct["count_inc_st"] = None
                    struct["st_count"] = None
            if not dry:
                conn.execute("UPDATE daily_market_cache SET snapshot_json=? WHERE trade_date=?",
                             (json.dumps(snap, ensure_ascii=False), td))
            print("  %s  %-8s 非 rebuild → 剔 ST 计数不变，含 ST 标注为不可得" % (td, kind or "live"))
            continue

        # 只处理 rebuild（日K自建 = 含 ST 口径）
        st_zt = [s for s in (lu.get("stocks") or []) if s.get("is_st")]
        st_dt = [s for s in (ld.get("stocks") or []) if s.get("is_st")]
        st_zb = [s for s in (zb.get("stocks") or []) if s.get("is_st")]
        old_lu, old_ld = int(lu.get("count") or 0), int(ld.get("count") or 0)

        for struct, stk in ((lu, st_zt), (ld, st_dt), (zb, st_zb)):
            all_stk = list(struct.get("stocks") or [])
            struct["count_inc_st"] = len(all_stk)
            kept = [x for x in all_stk if not x.get("is_st")]
            struct["stocks"] = kept
            struct["count"] = len(kept)
            struct["count_ex_st"] = len(kept)
            struct["st_count"] = len(all_stk) - len(kept)
            struct["amount"] = sum(x.get("amount") or 0 for x in kept)
        if "ladder" in lu:
            lu["ladder"] = _ladder_from(lu["stocks"])
        if "max_lbc" in lu:
            lu["max_lbc"] = max([x.get("lbc") or 1 for x in lu["stocks"]] or [0])

        # breadth：ST 涨跌停不再计入 limit_up/limit_down，改为落进涨幅档位
        bd = snap.get("breadth") or {}
        if bd:
            bd["limit_up"] = lu["count"]
            bd["limit_down"] = ld["count"]
            bd["limit_up_ex_st"] = lu["count"]
            bd["limit_up_inc_st"] = lu["count_inc_st"]
            bd["limit_down_inc_st"] = ld["count_inc_st"]
            for s in st_zt:
                _bucket(bd, float(s.get("pct") or 0), "up")
            for s in st_dt:
                _bucket(bd, float(s.get("pct") or 0), "down")
            bd["up_count"] = bd["up_gt7"] + bd["up_5_7"] + bd["up_3_5"] + bd["up_0_3"]
            bd["down_count"] = bd["down_0_3"] + bd["down_3_5"] + bd["down_5_7"] + bd["down_gt7"]
            bd["total"] = bd["up_count"] + bd["down_count"] + bd["flat"]

        em = snap.get("emotion") or {}
        if em:
            em["limit_up"] = lu["count"]
            em["limit_up_inc_st"] = lu.get("count_inc_st")
            em["limit_up_ex_st"] = lu["count"]
            em["limit_down"] = ld["count"]
            em["limit_down_inc_st"] = ld.get("count_inc_st")
            em["limit_down_ex_st"] = ld["count"]
            em["broken"] = zb["count"]
            _den = lu["count"] + zb["count"]
            em["broken_rate"] = round(zb["count"] / _den * 100, 2) if _den else None
            if "max_lbc" in lu:
                em["max_lbc"] = lu["max_lbc"]

        print("  %s  %-8s 涨停 %d→%d（含ST %d）｜跌停 %d→%d｜炸板 %d→%d"
              % (td, kind or "live", old_lu, lu["count"], lu["count_inc_st"],
                 old_ld, ld["count"], len(st_zb) + zb["count"], zb["count"]))
        if not dry:
            conn.execute(
                "UPDATE daily_market_cache SET snapshot_json=? WHERE trade_date=?",
                (json.dumps(snap, ensure_ascii=False), td))
        stat["done"] += 1
    print("\n迁移 %d 条（跳过 live %d / 已迁移 %d）" % (stat["done"], stat["skip_live"], stat["skip_done"]))


if __name__ == "__main__":
    db = sys.argv[1]
    dry = "--dry" in sys.argv
    bak = db + ".bak_st_scope"
    if not dry and not os.path.exists(bak):
        shutil.copy2(db, bak)
        print("已备份 → %s" % bak)
    conn = sqlite3.connect(db)
    try:
        migrate(conn, dry)
        if not dry:
            conn.commit()
            conn.execute("VACUUM")
            print("已提交 + VACUUM")
    finally:
        conn.close()
