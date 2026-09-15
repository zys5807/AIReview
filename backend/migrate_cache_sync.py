# -*- coding: utf-8 -*-
"""V1.009.5 部署库迁移：同步缓存表（不碰用户数据）

为什么用「同步缓存表」而不是「联网重跑 65 日重建」：
    开发库里的 daily_market_cache 已经是新口径、且被 58 项单测 + 验收脚本 + 页面截图
    三重验证过。重跑一遍联网重建（1.5 分钟、要拉 400+ 只个股日K）不仅慢，
    还会产生一份**没被验证过**的数据。直接搬已验证的那份，结果确定。

只动 3 张**纯缓存**表：
    daily_market_cache   65 日盘面快照（新口径主线）
    market_cache_meta    board_hist（504 板块 × 65 日序列，持续性维度依赖）
    board_member_cache   504 板块成分股（角色分层依赖）
用户数据表（trades / daily_reviews / users / phase_reviews / account_flows …）**一律不动**，
迁移前后逐表比对行数。
"""
import argparse
import datetime
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='backend/app.db')
    ap.add_argument('--dst', default=r'F:/Trading/复盘APP/AIReviewSystem/app.db')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--no-backup', action='store_true')
    args = ap.parse_args()

    for p in (args.src, args.dst):
        if not os.path.isfile(p):
            raise SystemExit('!! 库不存在: %s' % p)
    if os.path.abspath(args.src) == os.path.abspath(args.dst):
        raise SystemExit('!! src 与 dst 相同')

    print('源（开发库，已验证）: %s  %.1f MB' % (args.src, os.path.getsize(args.src) / 1048576))
    print('目标（部署库）      : %s  %.1f MB' % (args.dst, os.path.getsize(args.dst) / 1048576))
    print()

    before_user = counts(args.dst, USER_TABLES)
    before_cache = counts(args.dst, CACHE_TABLES)
    src_cache = counts(args.src, CACHE_TABLES)
    print('=== 缓存表：源 → 目标（当前）===')
    for t in CACHE_TABLES:
        print('  %-22s %5s → %5s' % (t, src_cache[t], before_cache[t]))
    print()
    print('=== 用户数据表（本次不得变动）===')
    for t in USER_TABLES:
        print('  %-22s %5s' % (t, before_user[t]))
    print()

    if not args.apply:
        print('干跑结束。加 --apply 执行。')
        return

    # 备份
    if not args.no_backup:
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        bak = args.dst + '.bak_v1.009.4_' + ts
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
    print('=== 迁移后复验 ===')
    after_user = counts(args.dst, USER_TABLES)
    after_cache = counts(args.dst, CACHE_TABLES)
    bad = []
    for t in USER_TABLES:
        if after_user[t] != before_user[t]:
            bad.append('用户表变动: %s %s → %s' % (t, before_user[t], after_user[t]))
    for t in CACHE_TABLES:
        if after_cache[t] != src_cache[t]:
            bad.append('缓存表行数不符: %s 期望 %s 实得 %s' % (t, src_cache[t], after_cache[t]))
    print('  用户数据表：%s' % ('全部未变动 ✓' if not bad or all('用户表' not in b for b in bad) else '!! 有变动'))
    for t in CACHE_TABLES:
        print('    %-22s %5d' % (t, after_cache[t]))
    print('  库大小：%.1f MB' % (os.path.getsize(args.dst) / 1048576))
    if bad:
        print()
        for b in bad:
            print('  !!', b)
        sys.exit(1)
    print('\n迁移完成，复验通过 ✓')


if __name__ == '__main__':
    main()
