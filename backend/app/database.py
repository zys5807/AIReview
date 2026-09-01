"""数据库连接与会话管理（SQLite 起步，预留 PostgreSQL）"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

# SQLite 需要 check_same_thread=False 以支持多线程 Web 请求
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


# 轻量迁移：确保旧库补上新列（SQLite 的 ALTER TABLE ADD COLUMN）
_MIGRATIONS = [
    ("trades", "psychology_notes", "TEXT DEFAULT ''"),
    ("trades", "timeframe_notes", "TEXT DEFAULT ''"),
    # 本笔实际周期（可偏离系统默认）
    ("trades", "trend_timeframe_used", "VARCHAR(20) DEFAULT ''"),
    ("trades", "direction_timeframe_used", "VARCHAR(20) DEFAULT ''"),
    ("trades", "entry_timeframe_used", "VARCHAR(20) DEFAULT ''"),
    ("trading_systems", "trend_timeframe", "VARCHAR(20) DEFAULT ''"),
    ("trading_systems", "direction_timeframe", "VARCHAR(20) DEFAULT ''"),
    ("trading_systems", "entry_timeframe", "VARCHAR(20) DEFAULT ''"),
    # 多用户
    ("users", "is_admin", "INTEGER DEFAULT 0"),
    ("users", "is_active", "INTEGER DEFAULT 1"),
    ("trades", "user_id", "INTEGER DEFAULT NULL"),
    # 加仓字段
    ("trades", "scale_in_time", "DATETIME DEFAULT NULL"),
    ("trades", "scale_in_price", "FLOAT DEFAULT NULL"),
    ("trades", "scale_in_volume", "FLOAT DEFAULT NULL"),
    ("trades", "fee", "FLOAT DEFAULT NULL"),
    ("trades", "remaining_volume", "FLOAT DEFAULT 0"),
    ("trades", "import_cost", "FLOAT DEFAULT 0"),
    ("trades", "import_revenue", "FLOAT DEFAULT 0"),
    ("trade_plans", "plan_date", "DATE DEFAULT NULL"),
    ("screenshots", "user_id", "INTEGER DEFAULT NULL"),
    ("trading_systems", "user_id", "INTEGER DEFAULT NULL"),
    ("review_reports", "user_id", "INTEGER DEFAULT NULL"),
]

# 迁移后把历史 NULL 统一补为空字符串
_NULL_FIXES = [
    ("trades", "psychology_notes"),
    ("trades", "timeframe_notes"),
    ("trading_systems", "trend_timeframe"),
    ("trading_systems", "direction_timeframe"),
    ("trading_systems", "entry_timeframe"),
]


def _table_exists(conn, name: str) -> bool:
    return (
        conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name}
        ).fetchone()
        is not None
    )


def _rebuild_trades_table(conn):
    """SQLite 不支持改列约束：把 trades.exit_time/exit_price 从 NOT NULL 重建为可空。

    同时重建引用 trades 的 trade_position_actions / trade_plans（它们的 FK 会随
    RENAME 指向旧表而悬空）。数据保留。
    """
    from sqlalchemy.schema import CreateTable

    from . import models

    # 仅当 exit_time 仍为 NOT NULL 时需要重建
    cols = conn.execute(text("PRAGMA table_info(trades)")).fetchall()
    exit_col = next((c for c in cols if c[1] == "exit_time"), None)
    if exit_col is None or exit_col[3] == 0:
        return

    conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        # 1. trades
        conn.execute(text("ALTER TABLE trades RENAME TO trades_old"))
        conn.execute(text(str(CreateTable(models.Trade.__table__).compile(engine))))
        old = [c[1] for c in conn.execute(text("PRAGMA table_info(trades_old)")).fetchall()]
        sql = ", ".join(f'"{c}"' for c in old)
        conn.execute(text(f'INSERT INTO trades ({sql}) SELECT {sql} FROM trades_old'))
        conn.execute(text("DROP TABLE trades_old"))

        # 2. trade_position_actions（FK → trades.id）
        if _table_exists(conn, "trade_position_actions"):
            conn.execute(text("ALTER TABLE trade_position_actions RENAME TO tpa_old"))
            conn.execute(
                text(str(CreateTable(models.TradePositionAction.__table__).compile(engine)))
            )
            old = [c[1] for c in conn.execute(text("PRAGMA table_info(tpa_old)")).fetchall()]
            sql = ", ".join(f'"{c}"' for c in old)
            conn.execute(text(f'INSERT INTO trade_position_actions ({sql}) SELECT {sql} FROM tpa_old'))
            conn.execute(text("DROP TABLE tpa_old"))

        # 3. trade_plans（linked_trade_id FK → trades.id）
        if _table_exists(conn, "trade_plans"):
            conn.execute(text("ALTER TABLE trade_plans RENAME TO tp_old"))
            conn.execute(text(str(CreateTable(models.TradePlan.__table__).compile(engine))))
            old = [c[1] for c in conn.execute(text("PRAGMA table_info(tp_old)")).fetchall()]
            sql = ", ".join(f'"{c}"' for c in old)
            conn.execute(text(f'INSERT INTO trade_plans ({sql}) SELECT {sql} FROM tp_old'))
            conn.execute(text("DROP TABLE tp_old"))
    finally:
        conn.execute(text("PRAGMA foreign_keys=ON"))


def ensure_schema():
    """创建缺失的表；已有表补上新列"""
    from . import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine)
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        _rebuild_trades_table(conn)
        for table, column, col_type in _MIGRATIONS:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            except Exception:
                pass  # 列已存在
        for table, column in _NULL_FIXES:
            try:
                conn.execute(text(f"UPDATE {table} SET {column}='' WHERE {column} IS NULL"))
            except Exception:
                pass
        # 回填计划日期（从创建时间取日期）
        try:
            conn.execute(
                text("UPDATE trade_plans SET plan_date = date(created_at) WHERE plan_date IS NULL")
            )
        except Exception:
            pass
        # 未平仓交易：清空出场时间/价格（完全平仓时才记录）
        try:
            conn.execute(
                text("UPDATE trades SET exit_time=NULL, exit_price=NULL WHERE remaining_volume > 0")
            )
        except Exception:
            pass
        # 一次性迁移：旧加仓字段(scale_in_*) → trade_position_actions（仅当该交易还没有操作记录）
        try:
            rows = conn.execute(
                text(
                    "SELECT id, scale_in_time, scale_in_price, scale_in_volume "
                    "FROM trades WHERE scale_in_time IS NOT NULL"
                )
            ).fetchall()
            migrated = 0
            for r in rows:
                has = conn.execute(
                    text("SELECT COUNT(*) FROM trade_position_actions WHERE trade_id = :tid"),
                    {"tid": r[0]},
                ).scalar()
                if has == 0:
                    conn.execute(
                        text(
                            "INSERT INTO trade_position_actions "
                            "(trade_id, action_time, price, volume, note, sort_order) "
                            "VALUES (:tid, :t, :p, :v, '原加仓记录', 0)"
                        ),
                        {"tid": r[0], "t": r[1], "p": r[2], "v": r[3] or 0},
                    )
                    migrated += 1
            if migrated:
                print(f"[migrate] 已迁移 {migrated} 条旧加仓记录 → 持仓操作表")
        except Exception:
            pass
            count = conn.execute(text("SELECT COUNT(*) FROM trade_strategies")).scalar()
            if count == 0:
                rows = conn.execute(
                    text(
                        "SELECT trading_system_id, name, rule, is_active, sort_order "
                        "FROM entry_strategies"
                    )
                ).fetchall()
                for r in rows:
                    conn.execute(
                        text(
                            "INSERT INTO trade_strategies "
                            "(trading_system_id, name, entry_rule, stop_loss_rule, take_profit_rule, is_active, sort_order, created_at) "
                            "VALUES (:ts, :name, :rule, '', '', :act, :sort, datetime('now'))"
                        ),
                        {
                            "ts": r[0],
                            "name": r[1] or "",
                            "rule": r[2] or "",
                            "act": r[3],
                            "sort": r[4],
                        },
                    )
                if rows:
                    print(f"[migrate] 已迁移 {len(rows)} 条旧入场策略 → 交易策略")
        except Exception:
            pass


def get_db():
    """FastAPI 依赖：提供数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
