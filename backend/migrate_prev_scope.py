# -*- coding: utf-8 -*-
"""把历史缓存里的 prev_limit_up（昨日涨停股今日表现）统一到「剔 ST」口径

背景
----
主口径已统一为「剔除 ST/*ST」（对账东财涨停池）。`limit_up.count` 等字段已迁移，
但 `prev_limit_up` 漏了 —— 它的 items 来自 rebuild 当年自己算的昨日涨停名单，
那时 `zt_items` 还是含 ST 的。最直观的表现：
    2026-09-10 的 prev_limit_up.count = 49（含 ST），而 2026-09-09 的 limit_up.count = 48
直接导致情绪指标卡左下角文案与同一张卡上的「涨停（剔ST）」互相矛盾。

做法
----
缓存里 `prev_limit_up.items[i]` 都带 `name`，用与后端同一套 `_is_st()` 规则筛掉 ST，
再重算所有派生字段（count / valid / avg_pct / up / down / flat / limit_up_again），
并同步 `emotion.prev_limit_up_avg`。天然幂等（筛过一次后不再有 ST）。

不重抓任何网络数据，就地改写，先自动备份。
"""
import io
import json
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本就在 backend/ 内
sys.path.insert(0, HERE)

from app.services.daily_market import _is_st  # noqa: E402

# 默认就地迁移源码库；发版时可用 argv[1] 指向部署库（如 F:/Trading/复盘APP/AIReviewSystem/app.db）
DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "app.db")
BAK = DB + ".bak_prev_scope"


def recount(pp: dict) -> tuple:
    """就地重算 prev_limit_up 的派生字段，返回 (原条数, 新条数, 移除的 ST 名)"""
    items = list(pp.get("items") or [])
    before = len(items)
    kept, removed = [], []
    for x in items:
        nm = (x.get("name") or "").replace(" ", "")
        if _is_st(nm):
            removed.append(nm)
        else:
            kept.append(x)
    vals = [x["pct"] for x in kept if isinstance(x.get("pct"), (int, float))]
    pp["items"] = kept
    pp["count"] = len(kept)
    pp["valid"] = len(vals)
    pp["avg_pct"] = round(sum(vals) / len(vals), 2) if vals else None
    pp["up_count"] = sum(1 for v in vals if v > 0)
    pp["down_count"] = sum(1 for v in vals if v < 0)
    pp["flat_count"] = sum(1 for v in vals if v == 0)
    pp["limit_up_again"] = sum(1 for x in kept if x.get("again_limit_up"))
    pp["scope"] = "ex_st"
    return before, len(kept), removed


def main() -> int:
    if not os.path.exists(DB):
        print("!! 找不到 %s" % DB)
        return 1
    if not os.path.exists(BAK):
        shutil.copy2(DB, BAK)
        print("备份 -> %s (%.1f MB)" % (os.path.basename(BAK),
                                        os.path.getsize(BAK) / 1024.0 / 1024.0))
    else:
        print("备份已存在，跳过 -> %s" % os.path.basename(BAK))

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("select id, trade_date, kind, snapshot_json from daily_market_cache "
                "order by trade_date")
    rows = cur.fetchall()

    changed, touched, total_removed = [], 0, 0
    for r in rows:
        snap = json.loads(r["snapshot_json"])
        pp = snap.get("prev_limit_up") or {}
        if not pp or int(pp.get("count") or 0) == 0 and not (pp.get("items") or []):
            snap.setdefault("prev_limit_up", {})["scope"] = "ex_st"
            cur.execute("update daily_market_cache set snapshot_json=? where id=?",
                        (json.dumps(snap, ensure_ascii=False), r["id"]))
            touched += 1
            continue
        before, after, removed = recount(pp)
        # 同步 emotion 里的均值
        em = snap.get("emotion") or {}
        if em:
            em["prev_limit_up_avg"] = pp.get("avg_pct")
            snap["emotion"] = em
        snap["prev_limit_up"] = pp
        cur.execute("update daily_market_cache set snapshot_json=? where id=?",
                    (json.dumps(snap, ensure_ascii=False), r["id"]))
        touched += 1
        if removed:
            changed.append((r["trade_date"], pp.get("prev_date"), before, after, removed))
            total_removed += len(removed)

    con.commit()
    print("\n扫描 %d 条，改写 %d 条，剔除 ST 记录 %d 条" % (len(rows), touched, total_removed))
    print("\n受影响交易日（前 20）：")
    print("  %-12s %-12s %5s -> %-5s  移除" % ("快照日", "昨涨停日", "原", "新"))
    for d, pd_, b, a, rm in changed[:20]:
        print("  %-12s %-12s %5d -> %-5d  %s" % (d, pd_, b, a, ",".join(rm[:5])))
    if len(changed) > 20:
        print("  ... 另有 %d 天" % (len(changed) - 20))

    # 一致性自证：rebuild 日的 prev_limit_up.count 必须等于昨日 limit_up.count
    cur.execute("select trade_date, kind, snapshot_json from daily_market_cache "
                "order by trade_date")
    allrows = [(x["trade_date"], x["kind"], json.loads(x["snapshot_json"]))
               for x in cur.fetchall()]
    lut = {d: (s.get("limit_up") or {}).get("count") for d, k, s in allrows}
    bad = []
    for d, k, s in allrows:
        pp = s.get("prev_limit_up") or {}
        pdt = pp.get("prev_date")
        if k != "rebuild" or not pdt or pdt not in lut:
            continue
        if pp.get("count") != lut[pdt]:
            bad.append((d, pdt, pp.get("count"), lut[pdt]))
    print("\n口径自证（rebuild 日：prev_limit_up.count == 昨日 limit_up.count）：")
    if bad:
        for d, pdt, a, b in bad:
            print("  x %s prev=%s   %s != %s" % (d, pdt, a, b))
    else:
        print("  全部一致")

    print("\nVACUUM ...")
    con.execute("VACUUM")
    con.close()
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
