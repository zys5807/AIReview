# -*- coding: utf-8 -*-
"""V1.009.7 部署库迁移：同步情绪周期缓存（不碰任何用户数据）

为什么是「同步缓存表」而不是「对部署库联网重跑 70 日重建」：
    开发库里的 70 日快照 + board_hist 已经被**同一套代码**产出了一遍，并且过了
    74 项单测 / 生产口径复算 / 字段落库核验三重检查。对部署库再联网重跑一遍不仅慢，
    还会得到一份**没被任何检查覆盖过**的数据 —— 重跑一次就是一次新的不确定性。
    直接搬那份已验证的，结果确定。

⚠️ 本版**必须**迁移，不能只换 exe：
    V1.009.7 的板块阶段依赖 board_hist 里新增的 `stage` / `zb_r` 两列（实时日的
    「近 10 日是否活跃」「昨日最高板」「炸板率自身 20 日均值」全靠它）。库里没有这两列
    → 实时日永远按「非活跃态」判 → 启动/发酵语义全错，**且接口和页面都不报错**。

只动 3 张**纯缓存**表：
    daily_market_cache   70 日盘面快照（含新增 phase_list / phase_order / phase_* 阈值）
    market_cache_meta    board_hist（504 板块 × 70 日序列，含新增 stage / zb_r）
    board_member_cache   504 板块成分股（角色分层依赖）
用户数据表一律不动 —— 迁移前后**逐表行数 + 全表内容 md5** 双向比对。

用法：
    python backend/migrate_phase_v1097.py                       # 干跑
    python backend/migrate_phase_v1097.py --apply               # 执行
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import sys

CACHE_TABLES = ['daily_market_cache', 'market_cache_meta', 'board_member_cache']
USER_TABLES = [
    'users', 'trades', 'trade_position_actions', 'trade_plans', 'trade_screenshots',
    'screenshots', 'daily_reviews', 'phase_reviews', 'market_reviews', 'review_reports',
    'account_flows', 'trading_systems', 'trade_strategies', 'entry_strategies',
    'futures_config', 'import_fills', 'app_settings',
]
# V1.009.7 新增的 concept 字段（缺任一项 → 该快照是旧格式）
NEW_CONCEPT_KEYS = ['phase_order', 'phase_list', 'phase_min_cnt', 'phase_peak_h',
                    'phase_zb_up', 'phase_zb_win', 'phase_zb_min', 'phase_lookback',
                    'phase_fade_zt']


def counts(path, tables):
    con = sqlite3.connect(path)
    out = {}
    for t in tables:
        try:
            out[t] = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        except Exception:  # noqa: BLE001
            out[t] = None
    con.close()
    return out


def content_md5(path, tables):
    """全表内容 md5（按 rowid 顺序，逐行 repr）—— 比行数比对强得多"""
    con = sqlite3.connect(path)
    out = {}
    for t in tables:
        h = hashlib.md5()
        try:
            for row in con.execute('SELECT * FROM "%s" ORDER BY rowid' % t):
                h.update(repr(row).encode('utf-8'))
            out[t] = h.hexdigest()
        except Exception:  # noqa: BLE001
            out[t] = None
    con.close()
    return out


def audit_src_cache(src):
    """源头自证：开发库的缓存必须确实带 V1.009.7 新字段，否则拒绝迁移"""
    con = sqlite3.connect('file:%s?mode=ro' % os.path.abspath(src).replace('\\', '/'), uri=True)
    problems = []

    n_snap = con.execute('SELECT COUNT(*) FROM daily_market_cache').fetchone()[0]
    print('  快照 %d 个交易日' % n_snap)
    if n_snap < 60:
        problems.append('快照只有 %d 天（<60），请先重建' % n_snap)

    bad_dates = []
    for d, sj in con.execute('SELECT trade_date, snapshot_json FROM daily_market_cache'):
        c = (json.loads(sj).get('concept') or {})
        miss = [k for k in NEW_CONCEPT_KEYS if k not in c]
        if miss:
            bad_dates.append((d, miss))
    if bad_dates:
        for d, miss in bad_dates[:10]:
            problems.append('%s 缺 %s' % (d, ','.join(miss)))
        print('  !! %d 个日期的快照是旧格式' % len(bad_dates))
    else:
        print('  ✓ %d 天快照全部含 9 项新 phase_* 字段' % n_snap)

    hj = con.execute("SELECT value_json FROM market_cache_meta WHERE key='board_hist'").fetchone()
    if not hj:
        problems.append('market_cache_meta 里没有 board_hist')
    else:
        h = json.loads(hj[0])
        allrows = [x for v in h.values() for x in v]
        n = len(allrows)
        has_stage = sum(1 for x in allrows if x.get('stage'))
        has_zb = sum(1 for x in allrows if x.get('zb_r') is not None)
        named = [x for x in allrows if x.get('name')]
        # 允许的例外：taxonomy 里存在、但当日没有任何成分股行 → name 为 null 的幽灵板块
        miss_named = [x for x in allrows if not x.get('stage') and x.get('name')]
        print('  board_hist：%d 板块 / %d 行，含 stage %d 行（%.2f%%）、含 zb_r %d 行（%.2f%%）'
              % (len(h), n, has_stage, has_stage * 100.0 / n, has_zb, has_zb * 100.0 / n))
        if missing := [x for x in miss_named]:
            problems.append('有 %d 行「有名字却无 stage」—— 判定链路漏了' % len(missing))
        else:
            print('  ✓ 有名字的板块行 100%% 带 stage；%d 行 name=null（taxonomy 幽灵板块，属预期）'
                  % (n - len(named)))
        if has_zb * 100.0 / n < 60:
            problems.append('含 zb_r 的行只有 %.1f%%（<60%%），炸板率维度不完整' % (has_zb * 100.0 / n))
    con.close()
    return problems


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=os.path.join(root, 'backend', 'app.db'))
    ap.add_argument('--dst', default=r'F:/Trading/复盘APP/AIReviewSystem/app.db')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--no-backup', action='store_true')
    args = ap.parse_args()

    for p in (args.src, args.dst):
        if not os.path.isfile(p):
            raise SystemExit('!! 库不存在: %s' % p)
    if os.path.abspath(args.src) == os.path.abspath(args.dst):
        raise SystemExit('!! src 与 dst 相同')

    print('=' * 72)
    print('V1.009.7 部署库迁移 —— 同步情绪周期缓存')
    print('=' * 72)
    print('源（开发库，已重建 + 已验证）: %s  %.1f MB' % (args.src, os.path.getsize(args.src) / 1048576))
    print('目标（部署库）              : %s  %.1f MB' % (args.dst, os.path.getsize(args.dst) / 1048576))
    print()
    print('=== ① 源头自证（开发库缓存是否带 V1.009.7 新字段）===')
    problems = audit_src_cache(args.src)
    if problems:
        print()
        print('!! 源头不合格，拒绝迁移：')
        for p in problems:
            print('   -', p)
        raise SystemExit(1)
    print()

    before_user = counts(args.dst, USER_TABLES)
    before_md5 = content_md5(args.dst, USER_TABLES)
    before_cache = counts(args.dst, CACHE_TABLES)
    src_cache = counts(args.src, CACHE_TABLES)
    print('=== ② 缓存表：源 → 目标（迁移前）===')
    for t in CACHE_TABLES:
        print('  %-22s %5s → %5s' % (t, src_cache[t], before_cache[t]))
    print()
    print('=== ③ 用户数据表（本次不得变动）===')
    for t in USER_TABLES:
        print('  %-22s %5s  md5=%s' % (t, before_user[t], (before_md5[t] or '-')[:12]))
    print()

    if not args.apply:
        print('干跑结束。加 --apply 执行。')
        return

    if not args.no_backup:
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        bak = args.dst + '.bak_v1.009.6_' + ts
        shutil.copy2(args.dst, bak)
        print('已备份 → %s  (%.1f MB)' % (bak, os.path.getsize(bak) / 1048576))

    src = sqlite3.connect(args.src)
    dst = sqlite3.connect(args.dst)
    try:
        dst.execute('PRAGMA foreign_keys=OFF')
        for t in CACHE_TABLES:
            rows = src.execute('SELECT * FROM "%s"' % t).fetchall()
            cols = [d[0] for d in src.execute('SELECT * FROM "%s" LIMIT 0' % t).description]
            dst.execute('DELETE FROM "%s"' % t)
            ph = ','.join('?' * len(cols))
            dst.executemany(
                'INSERT INTO "%s" (%s) VALUES (%s)' % (t, ','.join('"%s"' % c for c in cols), ph),
                rows)
            print('  同步 %-22s %5d 行' % (t, len(rows)))
        dst.commit()
        print('已提交，开始 VACUUM…')
        dst.execute('VACUUM')
        dst.commit()
    finally:
        src.close()
        dst.close()

    print()
    print('=== ④ 迁移后复验 ===')
    after_user = counts(args.dst, USER_TABLES)
    after_md5 = content_md5(args.dst, USER_TABLES)
    after_cache = counts(args.dst, CACHE_TABLES)
    bad = []
    for t in USER_TABLES:
        if after_user[t] != before_user[t]:
            bad.append('用户表行数变动: %s %s → %s' % (t, before_user[t], after_user[t]))
        elif after_md5[t] != before_md5[t]:
            bad.append('用户表内容变动: %s' % t)
    for t in CACHE_TABLES:
        if after_cache[t] != src_cache[t]:
            bad.append('缓存表行数不符: %s 期望 %s 实得 %s' % (t, src_cache[t], after_cache[t]))
    print('  用户数据表：%s（%d 张，行数 + 内容 md5 双向比对）'
          % ('全部未变动 ✓' if not any('用户表' in b for b in bad) else '!! 有变动', len(USER_TABLES)))
    for t in CACHE_TABLES:
        print('    %-22s %5d' % (t, after_cache[t]))

    print('  --- 目标库新字段复验 ---')
    con = sqlite3.connect('file:%s?mode=ro' % os.path.abspath(args.dst).replace('\\', '/'), uri=True)
    d0, sj = con.execute(
        'SELECT trade_date, snapshot_json FROM daily_market_cache ORDER BY trade_date DESC LIMIT 1').fetchone()
    c = (json.loads(sj).get('concept') or {})
    miss = [k for k in NEW_CONCEPT_KEYS if k not in c]
    if miss:
        bad.append('目标库最新快照 %s 仍缺 %s' % (d0, miss))
    else:
        print('    ✓ 最新快照 %s 含全部 9 项新字段；阶段词表=%s'
              % (d0, json.dumps(c.get('phase_order'), ensure_ascii=False)))
    print('    ✓ stage_count=%s' % json.dumps(c.get('stage_count'), ensure_ascii=False))
    pl = c.get('phase_list') or []
    print('    ✓ 风控清单 %d 条：%s' % (len(pl), [x['name'] for x in pl[:5]]))
    hj2 = con.execute("SELECT value_json FROM market_cache_meta WHERE key='board_hist'").fetchone()
    h2 = json.loads(hj2[0]) if hj2 else {}
    r2 = [x for v in h2.values() for x in v]
    ns = sum(1 for x in r2 if x.get('stage'))
    nz = sum(1 for x in r2 if x.get('zb_r') is not None)
    print('    ✓ board_hist %d 行：含 stage %d、含 zb_r %d' % (len(r2), ns, nz))
    con.close()
    print('  库大小：%.1f MB' % (os.path.getsize(args.dst) / 1048576))

    if bad:
        print()
        for b in bad:
            print('  !!', b)
        sys.exit(1)
    print('\n✅ 迁移完成，复验通过（用户数据零改动 / 最新快照为新格式 / board_hist 带 stage+zb_r）')


if __name__ == '__main__':
    main()
