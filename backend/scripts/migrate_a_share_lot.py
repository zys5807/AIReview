# -*- coding: utf-8 -*-
"""A股手数口径迁移脚本（V1.006 → 后续优化）

背景：旧口径下 A 股 volume 填"成交股数"（占用资金 = 价×股数）；
新口径下 volume 统一按"手"填写（占用资金 = 价×手数×100，1手=100股）。

迁移规则（启发式，输出清单供人工核对）：
- volume >= 100          → 判定旧值为"股数"（A股最小成交 1 手=100 股起）→ volume /= 100 转手数
- volume < 100           → 判定旧值已是"手数" → 保持
- invested_capital = 开仓价 × 手数 × 100（两种情形转换后占用资金口径一致）

用法：
  venv/Scripts/python.exe scripts/migrate_a_share_lot.py [--db 数据库路径] [--apply]
不带 --apply 时仅预览（只读，不写库）；加 --apply 才执行写入。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Trade
from app.services.investment import A_SHARE_LOT_SIZE, compute_invested_capital


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None, help="数据库路径（默认用配置的 DATABASE_URL）")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认仅预览）")
    args = parser.parse_args()

    db: Session = SessionLocal()
    trades = db.query(Trade).filter(Trade.instrument_type == "A股").all()
    if not trades:
        print("无 A 股交易，无需迁移")
        db.close()
        return

    print(f"共 {len(trades)} 条 A 股交易：")
    print(f"{'ID':<6}{'品种':<12}{'原volume':<10}{'原占用资金':<12}→{'新volume':<10}{'新占用资金':<12}")
    changed = 0
    for t in trades:
        old_vol = t.volume
        old_cap = t.invested_capital
        new_vol = old_vol / 100 if old_vol is not None and old_vol >= 100 else old_vol
        new_cap = compute_invested_capital(
            t.instrument_type, t.instrument_code, t.instrument_name,
            t.entry_price, new_vol,
        )
        name = (t.instrument_name or t.instrument_code or "")[:10]
        print(
            f"{t.id:<6}{name:<12}{str(old_vol):<10}{str(old_cap):<12}"
            f"→{str(new_vol):<10}{str(new_cap):<12}"
        )
        if new_vol != old_vol or (old_cap is not None and new_cap is not None and abs(new_cap - old_cap) > 0.01):
            changed += 1
        if args.apply:
            t.volume = new_vol
            t.invested_capital = new_cap

    if args.apply:
        db.commit()
        print(f"\n已写入 {changed} 条记录（volume 转手数 + 占用资金重算）")
    else:
        print(f"\n预览模式：{changed} 条记录会变更。确认无误后加 --apply 执行")

    db.close()


if __name__ == "__main__":
    main()
