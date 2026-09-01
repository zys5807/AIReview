"""V1.006 占用资金回填脚本

对指定 SQLite 数据库执行：
1. 检查并添加 trades.invested_capital 列（缺失时 ALTER TABLE）
2. 按品种自动计算回填历史交易的占用资金（仅回填 NULL 的记录）

用法：
    python scripts/backfill_capital.py [--db 数据库路径]
    不带 --db 时默认 backend/app.db
"""
import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.investment import backfill_invested_capital


def main():
    parser = argparse.ArgumentParser(description="V1.006 占用资金回填")
    parser.add_argument("--db", default=str(BACKEND_DIR / "app.db"), help="SQLite 数据库路径")
    parser.add_argument("--force", action="store_true", help="重算所有记录（算法变更后使用）")
    args = parser.parse_args()

    db_file = Path(args.db)
    if not db_file.exists():
        print(f"错误：数据库不存在 {db_file}")
        sys.exit(1)

    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    # 1. 迁移列
    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(trades)")).fetchall()]
        if "invested_capital" not in cols:
            conn.execute(
                text("ALTER TABLE trades ADD COLUMN invested_capital FLOAT DEFAULT NULL")
            )
            print("已添加列 invested_capital")
        else:
            print("列 invested_capital 已存在，跳过")

    # 2. 回填
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = Session()
    try:
        filled, scanned = backfill_invested_capital(db, force=args.force)
        print(f"回填完成：扫描 {scanned} 条，实际回填 {filled} 条")
        rows = db.execute(
            text(
                "SELECT id, instrument_type, instrument_code, instrument_name, "
                "entry_price, volume, invested_capital, pnl FROM trades ORDER BY id"
            )
        ).fetchall()
        for r in rows:
            print(
                f"  #{r[0]} {r[1]} {r[2]} {r[3]} 价{r[4]} 量{r[5]} "
                f"-> 占用 {r[6]} 盈亏 {r[7]}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
