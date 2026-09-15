# -*- coding: utf-8 -*-
"""V1.009.5 就地迁移：给旧格式快照补齐「主线题材四维打分」字段

用法
----
    venv/Scripts/python.exe migrate_theme_score.py                    # 干跑（默认），只报告
    venv/Scripts/python.exe migrate_theme_score.py --apply            # 执行并写入
    venv/Scripts/python.exe migrate_theme_score.py --dates 2026-09-11,2026-09-14 --apply
    venv/Scripts/python.exe migrate_theme_score.py D:/x/app.db --apply   # 指定库

为什么要这个脚本
----------------
V1.009.5 把主线题材的挑选从「绝对涨停家数前 N 名」改成「资格线筛池 → 四维打分 →
分级」，并新增 theme_weights / mains_count / market_state / main_switch 等字段。

daily_market_cache 里有若干日期是**旧代码**写入的 kind=live 快照。它们无法由
「历史数据重建」覆盖 —— daily_cache.cache_put 的质量分级是 live(3) > rebuild(2)，
重建数据不得挤掉实盘数据（这条保护本身是对的）。

也不能删掉重抓：东财的「全市场实时快照」只能取当天，对历史日期重抓只会得到
涨跌家数全 0 的 partial 残缺快照；而改走历史重建，会把这几天的**东财实时口径**
（涨停池 / 板块实时涨幅 / 涨跌家数分布，精度都高于日K近似）整体降级。
实盘日的口径正是全站对账的基准，不能为了一个新字段把它换掉。

于是就地补算。快照自身already带着重算所需的一切：
    limit_up.stocks          当日全部涨停股（含连板数 lbc、成交额 amount）
    concept.zt_contrib       各板块涨停家数 / 实时涨跌幅 / 成分数
    concept.top_up,temp...   板块实时涨跌幅与分布统计
再配上本地已有的：
    board_member_cache       概念全集（约 504 个）
    market_cache_meta.board_hist   板块每日序列（60+ 个交易日）
    stock_kline_cache        全市场日K（角色分层用）

即可**离线**重跑 concept_emotion，产出与实时路径同口径的新字段。

口径要点（照抄实时路径，不许自创）
----------------------------------
1. 候选池只含概念全集里的板块。个股板块接口 spt=3 实际返回「概念 + 行业 + 交易
   标签」的混合列表，其中的行业板块（电子 / 元件 / 印制电路板…）不在概念全集里、
   拿不到成分数，会以 size=0 / ratio=None 混进主线（实测 09-11 的 12 个里占 3 个）。
2. size（成分数）优先取快照里已知的实时值（zt_contrib.size / top_up 的涨跌平家数
   之和），缺了才退到 board_hist 的当日 size。两条来源必须显式记录，否则 ratio
   会与快照原本的值对不上，而它是表格里的「聚焦度」主指标。
3. top_up / top_down / temperature / total / up / down / strong / weak / median_pct
   这些展示层统计量**保留快照原值**：它们是对全部 504 个概念板块实时涨幅的聚合，
   日K重建不出来的部分不许降级覆盖（top_up 还带「领涨股」名，前端有渲染）。
4. 不碰 kind（live 仍是 live）。走 cache_put(force=True) 只为了让质量分级放行，
   不是要把数据降级成 rebuild。

安全措施
--------
· 默认干跑，只有 --apply 才写。
· 写前把受影响行整表备份到 backup/daily_market_cache_<时间戳>.json。
· 写后跑 8 项自证断言，任一不过立即抛错（此时可用备份还原）。
· 结束执行 VACUUM，避免已替换的旧 JSON 残留在文件空洞里。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import sqlite3
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


# ---------------------------------------------------------------------------

def _p(msg: str) -> None:
    print(msg, flush=True)


def _section(t: str) -> None:
    _p("\n" + "=" * 74)
    _p(t)
    _p("=" * 74)


def _load_rows(db_path: str) -> dict:
    """{date: {"kind":..., "snap": dict}}"""
    con = sqlite3.connect(db_path)
    try:
        cur = con.cursor()
        cur.execute("SELECT trade_date, kind, snapshot_json FROM daily_market_cache "
                    "ORDER BY trade_date")
        out = {}
        for d, k, sj in cur.fetchall():
            try:
                out[str(d)[:10]] = {"kind": k, "snap": json.loads(sj), "raw": sj}
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        return out
    finally:
        con.close()


def _needs_migration(snap: dict) -> bool:
    cj = snap.get("concept") or {}
    return cj.get("available") and "theme_weights" not in cj


# ---------------------------------------------------------------------------
# 板块行重建
# ---------------------------------------------------------------------------

def _best_size_map(snap: dict, hrow: dict) -> dict:
    """每个概念板块的当日成分数 → (size, 来源)

    优先级：快照自带的实时值 > board_hist 的当日 size。
    实时值来源有两处，都是东财当日的真实口径：
      zt_contrib[].size      板块行自带的成分数
      top_up[].up+down+flat  涨跌平家数之和（有交易的家数，动态口径）
    """
    out: dict = {}
    for b in (snap.get("concept") or {}).get("zt_contrib") or []:
        s = b.get("size")
        if isinstance(s, (int, float)) and s > 0:
            out[b["code"]] = (int(s), "live")
    for b in (snap.get("concept") or {}).get("top_up") or []:
        s = (b.get("up") or 0) + (b.get("down") or 0) + (b.get("flat") or 0)
        if s > 0:
            out[b["code"]] = (int(s), "live")
    for code, row in (hrow or {}).items():
        if code in out:
            continue
        s = row.get("size")
        if isinstance(s, (int, float)) and s > 0:
            out[code] = (int(s), "hist")
    return out


def _best_pct_map(snap: dict, hrow: dict) -> dict:
    """每个概念板块的当日涨跌幅 → (pct, 来源)：快照实时值 > board_hist 的当日 pct"""
    out: dict = {}
    cj = snap.get("concept") or {}
    for key in ("zt_contrib", "top_up", "top_down"):
        for b in cj.get(key) or []:
            p = b.get("pct")
            if isinstance(p, (int, float)) and b.get("code"):
                out[b["code"]] = (float(p), "live")
    for code, row in (hrow or {}).items():
        if code in out:
            continue
        p = row.get("pct")
        if isinstance(p, (int, float)):
            out[code] = (float(p), "hist")
    return out


def build_boards(mem: dict, sz: dict, pc: dict) -> tuple[list[dict], dict]:
    """概念全集 → 板块行（供 concept_emotion 的 size_map / pct_q 使用）

    刻意不填 up / down / flat，只把成分数塞进 count：
    size_map 的取值是 `int(up+down+flat or count)`，于是上面三个为空时会自动退到
    count —— 这样 size_map 拿到的是**当日真实成分数**，而 up/down 保持 None 而不是
    编一个假值（前端主线表格本就不渲染它们）。
    """
    boards, used = [], {"size_live": 0, "size_hist": 0, "size_none": 0,
                        "pct_live": 0, "pct_hist": 0, "pct_none": 0}
    for code in sorted(mem.keys()):
        s, _ssrc = sz.get(code, (0, "none"))
        p, _psrc = pc.get(code, (None, "none"))
        used["size_" + ("live" if _ssrc == "live" else
                        "hist" if _ssrc == "hist" else "none")] += 1
        used["pct_" + ("live" if _psrc == "live" else
                       "hist" if _psrc == "hist" else "none")] += 1
        boards.append({
            "code": code, "name": (mem.get(code) or {}).get("name") or code,
            "pct": p, "up": None, "down": None, "flat": None, "count": int(s or 0),
        })
    return boards, used


# ---------------------------------------------------------------------------
# 单日迁移
# ---------------------------------------------------------------------------

# 展示层统计量：保留快照原值（实时口径的全市场聚合，重建不出来的不许降级）
_KEEP_OLD = ("top_up", "top_down", "temperature", "total", "up", "down", "flat",
             "strong", "weak", "median_pct", "source", "available")


def migrate_one(db, dm, dc, date_str: str, snap: dict, hist: dict, mem: dict,
                klines: dict, names: dict, verbose: bool = True) -> tuple[dict, dict]:
    """→ (新 concept, 诊断报告)"""
    from app.services import daily_market as _dm

    cj_old = snap.get("concept") or {}
    hrow = {}
    for code, seq in (hist or {}).items():
        for row in seq:
            if row.get("date") == date_str:
                hrow[code] = row
                break
    sz = _best_size_map(snap, hrow)
    pc = _best_pct_map(snap, hrow)
    boards, used = build_boards(mem, sz, pc)

    zt_stocks = (snap.get("limit_up") or {}).get("stocks") or []
    rctx = None
    if klines:
        try:
            rctx = dm.roles_ctx_build(klines, names)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            rctx = None

    concept = dm.concept_emotion(
        boards, list(zt_stocks), hist, date_str=date_str,
        roles_ctx=rctx, roles_members=mem,
        concept_codes=set(mem.keys()))

    # 展示层字段回填原值
    for k in _KEEP_OLD:
        if k in cj_old:
            concept[k] = cj_old[k]
    # zt_contrib 也保留原值，但剔掉非概念板块（与新池口径一致）
    if cj_old.get("zt_contrib") is not None:
        concept["zt_contrib"] = [b for b in cj_old["zt_contrib"]
                                 if b.get("code") in mem][:20]

    rep = {
        "used": used, "boards": len(boards),
        "zt_stocks": len(zt_stocks), "roles_ctx": bool(rctx),
        "old_main": [(x["code"], x["name"], x["zt_count"]) for x in cj_old.get("main_lines") or []],
        "new_main": [(x["code"], x["name"], x["zt_count"], x.get("tier"),
                      x.get("score"), x.get("persist_days")) for x in concept.get("main_lines") or []],
        "weights": concept.get("theme_weights"),
        "state": concept.get("market_state"), "mains": concept.get("mains_count"),
        "switch": concept.get("main_switch"),
        "cand": concept.get("theme_candidates"), "win": concept.get("theme_win"),
        "q95": concept.get("pct_q95"),
    }
    if verbose:
        _p("  板块行 %d  |  size: 实时 %d / hist %d / 缺 %d  |  pct: 实时 %d / hist %d / 缺 %d"
           % (rep["boards"], used["size_live"], used["size_hist"], used["size_none"],
              used["pct_live"], used["pct_hist"], used["pct_none"]))
        _p("  涨停股 %d 只 → 概念映射重算；角色分层 %s"
           % (rep["zt_stocks"], "已算" if rctx else "缺（roles 置空）"))
        _p("  权重 %s" % json.dumps(rep["weights"], ensure_ascii=False))
        _p("  候选池 %s 个，窗口 %s 日，95 分位涨幅 %s，市场状态 %s（核心 %s 个）"
           % (rep["cand"], rep["win"], rep["q95"], rep["state"], rep["mains"]))
        _p("  主线切换 prev=%s 新进=%s 掉出=%s"
           % ((rep["switch"] or {}).get("prev_date"),
              [x["name"] for x in (rep["switch"] or {}).get("new") or []],
              [x["name"] for x in (rep["switch"] or {}).get("drop") or []]))
        _p("  --- 旧主线（%d）---" % len(rep["old_main"]))
        for c, n, z in rep["old_main"]:
            _p("      %-9s %-14s 涨停%s" % (c, n, z))
        _p("  --- 新主线（%d）---" % len(rep["new_main"]))
        for c, n, z, t, s, p_ in rep["new_main"]:
            _p("      %-9s %-14s 涨停%-3s %-9s score=%-7s 持续%s/5" % (c, n, z, t, s, p_))
    return concept, rep


# ---------------------------------------------------------------------------
# 自证断言
# ---------------------------------------------------------------------------

def assert_ok(date_str: str, old: dict, new: dict, hist: dict, mem: dict) -> list[str]:
    errs: list[str] = []

    if not new.get("theme_weights"):
        errs.append("theme_weights 为空")
    else:
        tot = round(sum(new["theme_weights"].values()), 6)
        if abs(tot - 1.0) > 1e-6:
            errs.append("权重和 = %s（应精确为 1）" % tot)

    ml = new.get("main_lines") or []
    if not ml:
        errs.append("main_lines 为空")
    for x in ml:
        if x.get("score") is None or not x.get("tier"):
            errs.append("主线 %s 缺 score/tier" % x.get("code"))
        d = x.get("dims") or {}
        if set(d.keys()) != {"focus", "persist", "height", "capacity"}:
            errs.append("主线 %s 四维不齐：%s" % (x.get("code"), sorted(d.keys())))
        if x.get("code") not in mem:
            errs.append("主线 %s 不在概念全集里（行业板块泄漏）" % x.get("code"))
        if not (0.0 <= float(x.get("score") or 0) <= 1.0):
            errs.append("主线 %s score 越界 %s" % (x.get("code"), x.get("score")))
        if x.get("persist_days") is None:
            errs.append("主线 %s 缺 persist_days" % x.get("code"))

    if new.get("market_state") in (None, ""):
        errs.append("缺 market_state")
    if new.get("mains_count") is None:
        errs.append("缺 mains_count")
    n_core = sum(1 for x in ml if x.get("tier") == "core")
    if n_core != new.get("mains_count"):
        errs.append("mains_count=%s 与 core 档实际 %s 个不符" % (new.get("mains_count"), n_core))

    ms = new.get("main_switch") or {}
    if not ms:
        errs.append("缺 main_switch")
    else:
        pd_ = ms.get("prev_date")
        if not pd_:
            errs.append("main_switch.prev_date 为空")
        elif pd_ >= date_str:
            errs.append("main_switch.prev_date=%s 不早于当日 %s（_hist_dates 上界失守）" % (pd_, date_str))
        else:
            # 必须真的是「上一交易日」，不能在窗口里乱跳
            all_d = set()
            for seq in (hist or {}).values():
                for r in seq:
                    if r.get("date"):
                        all_d.add(r["date"])
            prevs = sorted(d for d in all_d if d < date_str)
            if prevs and pd_ != prevs[-1]:
                errs.append("main_switch.prev_date=%s，但上一交易日应为 %s" % (pd_, prevs[-1]))

    if new.get("theme_win") != 5:
        errs.append("theme_win=%s（应为 5）" % new.get("theme_win"))

    # 口径保真：两边都出现的板块，涨停家数必须一致（同一份涨停股名单，不该变）。
    # ⚠️ 只对旧口径里 zt_count > 0 的板块比 —— 旧口径是 `contrib[:10] + top_up[:8]`，
    # top_up 那部分是按涨幅榜补进来的、涨停家数本来就是 0（如 09-11 的「被动元件概念」
    # 旧记 0 家、新记 3 家）。那不是口径漂移，是新口径把它的真实涨停家数算出来了；
    # 拿 0 去比会误判成漂移而中止迁移。
    old_z = {c: z for c, _n, z in
             [(x["code"], x["name"], x["zt_count"]) for x in old.get("main_lines") or []]
             if int(z or 0) > 0}
    for x in ml:
        c = x["code"]
        if c in old_z and int(x.get("zt_count") or 0) != int(old_z[c]):
            errs.append("主线 %s 涨停家数 %s ≠ 原快照 %s（口径漂移）"
                        % (c, x.get("zt_count"), old_z[c]))

    # 展示层原值必须保留
    for k in _KEEP_OLD:
        if k in old and old.get(k) is not None and new.get(k) is None:
            errs.append("展示层字段 %s 被清空" % k)
    return errs


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", nargs="?", default=os.path.join(_HERE, "app.db"))
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只干跑）")
    ap.add_argument("--dates", default="", help="只迁这些日期，逗号分隔；默认自动挑旧格式行")
    ap.add_argument("--no-vacuum", action="store_true")
    args = ap.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        _p("!! 找不到数据库：%s" % db_path)
        return 2
    _p("库：%s（%.1f MB）" % (db_path, os.path.getsize(db_path) / 1048576.0))
    _p("模式：%s" % ("APPLY 写入" if args.apply else "DRY-RUN 只报告（加 --apply 才写）"))

    from app.database import SessionLocal
    from app.services import daily_cache as dc
    from app.services import daily_market as dm

    rows = _load_rows(db_path)
    _section("一、扫描旧格式行")
    if args.dates:
        want = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        want = [d for d, v in sorted(rows.items()) if _needs_migration(v["snap"])]
    _p("daily_market_cache 共 %d 行；待迁移 %d 行：%s" % (len(rows), len(want), want or "（无）"))
    if not want:
        _p("没有需要迁移的行，退出。")
        return 0
    for d in want:
        if d not in rows:
            _p("!! 库中没有 %s" % d)
            return 2

    # ---- 备份 ----
    bk_dir = os.path.join(_HERE, "backup")
    os.makedirs(bk_dir, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = os.path.join(bk_dir, "daily_market_cache_%s.json" % ts)
    with io.open(bk, "w", encoding="utf-8") as f:
        json.dump({d: {"kind": rows[d]["kind"], "snapshot_json": rows[d]["raw"]} for d in want},
                  f, ensure_ascii=False)
    _p("已备份 %d 行 → %s（%.1f MB）"
       % (len(want), bk, os.path.getsize(bk) / 1048576.0))

    db = SessionLocal()
    try:
        hist = dc.hist_get(db)
        mem = dc.members_all(db)
        names = dc.klines_names(db)
        dates_in_hist = set()
        for seq in (hist or {}).values():
            for r in seq:
                if r.get("date"):
                    dates_in_hist.add(r["date"])
        _section("二、本地数据底座")
        _p("board_hist：%d 个板块 × %d 个交易日（%s ~ %s）"
           % (len(hist), len(dates_in_hist),
              min(dates_in_hist) if dates_in_hist else "-",
              max(dates_in_hist) if dates_in_hist else "-"))
        _p("board_member_cache（概念全集）：%d 个板块，未过期" % len(mem))
        _p("stock_kline_cache 名称表：%d 只" % len(names))

        new_concepts: dict = {}
        reports: dict = {}
        for d in want:
            _section("三、%s（kind=%s）" % (d, rows[d]["kind"]))
            klines = {}
            try:
                _from = (_dt.datetime.strptime(d, "%Y-%m-%d").date()
                         - _dt.timedelta(days=95)).strftime("%Y-%m-%d")
                klines = dc.klines_load(db, _from, d, required=dm._ROLE_CUM_WIN + 2,
                                        need_high=False) or {}
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            _p("  个股日K：%d 只（角色分层用）" % len(klines))
            concept, rep = migrate_one(db, dm, dc, d, rows[d]["snap"], hist, mem,
                                       klines, names, verbose=True)
            errs = assert_ok(d, rows[d]["snap"].get("concept") or {}, concept, hist, mem)
            if errs:
                _section("!! 自证断言未通过：%s" % d)
                for e in errs:
                    _p("   ✗ %s" % e)
                _p("\n未写入任何数据。备份在 %s" % bk)
                return 3
            _p("  ✓ 自证断言 全部通过")
            new_concepts[d] = concept
            reports[d] = rep

        if not args.apply:
            _section("四、DRY-RUN 结束（未写入）")
            _p("加 --apply 才真正写库。备份：%s" % bk)
            return 0

        _section("四、写入")
        for d, concept in new_concepts.items():
            snap = dict(rows[d]["snap"])
            snap["concept"] = concept
            dc.cache_put(db, d, snap, rows[d]["kind"], force=True)
            _p("  已写 %s（kind=%s）" % (d, rows[d]["kind"]))

        # ---- 写后复验：从库里重新读出来断言 ----
        _section("五、写后复验（重新读库）")
        bad = 0
        for d in want:
            again = dc.cache_get(db, d)
            cj = (again or {}).get("concept") or {}
            ok = ("theme_weights" in cj and cj.get("main_lines")
                  and cj.get("market_state") and cj.get("main_switch"))
            _p("  %s  kind=%s  mains=%s  state=%s  prev=%s  %s"
               % (d, (again or {}).get("cache_kind"), cj.get("mains_count"),
                  cj.get("market_state"), (cj.get("main_switch") or {}).get("prev_date"),
                  "OK" if ok else "!! 失败"))
            if not ok:
                bad += 1
        # 全表复扫
        allrows = _load_rows(db_path)
        left = [d for d, v in sorted(allrows.items()) if _needs_migration(v["snap"])]
        _p("  全表 %d 行，仍为旧格式：%s" % (len(allrows), left or "无"))
        if bad or left:
            _p("\n!! 复验未通过，备份在 %s" % bk)
            return 4
    finally:
        db.close()

    if not args.no_vacuum:
        _section("六、VACUUM")
        _p("前 %.1f MB" % (os.path.getsize(db_path) / 1048576.0))
        con = sqlite3.connect(db_path)
        try:
            con.execute("VACUUM")
            con.commit()
        finally:
            con.close()
        _p("后 %.1f MB（旧 JSON 已不在文件空洞里）" % (os.path.getsize(db_path) / 1048576.0))

    _section("完成")
    _p("迁移 %d 个交易日：%s" % (len(want), ", ".join(want)))
    _p("备份：%s" % bk)
    return 0


if __name__ == "__main__":
    sys.exit(main())
