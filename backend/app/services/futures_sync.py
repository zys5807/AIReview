"""东方财富期货保证金率同步服务（V1.007）

数据源：https://qhweb.eastmoney.com/bzj/allexchange
- HTML 内嵌数据（非 JS 渲染），stdlib urllib + 正则即可解析，零新依赖
- 提供的是期货公司实际收取保证金率（比交易所基础比例高 2~5 个点），更贴合真实资金占用

同步机制：
- 应用启动时后台线程立即同步一次 + 每日 16:30 后自动同步
- 设置页可手动触发（POST /api/futures/sync）
- 失败静默：保留上次成功数据，不影响其他功能

数据写入 futures_config（level=variety 品种级，margin_source=eastmoney）。
合约级覆盖（level=contract）仅由用户手动维护，同步不触碰。
新品种检测：页面品种不在内置乘数表(FUTURES_MULTIPLIERS)时，记录到 app_settings
（key=futures_new_varieties），设置页黄条提示"检测到新品种，乘数未配置"。
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from datetime import datetime

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import AppSetting, FuturesConfig
from .investment import FUTURES_MULTIPLIERS

_EASTMONEY_URL = "https://qhweb.eastmoney.com/bzj/allexchange"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# 合约行格式：<td>品种名</td><td>合约代码</td><td>¥保证金金额</td><td>比例%</td>
_ROW_RE = re.compile(
    r"<td>([^<]+)</td>\s*<td>([a-zA-Z]{1,3}\d{3,4})</td>\s*<td>¥[\d.]+</td>\s*<td>(\d+)%</td>"
)

# 品种代码前缀 → 交易所（用于展示；NR 20号胶归属能源中心）
_VARIETY_EXCHANGE = {
    "RB": "上期所", "HC": "上期所", "AL": "上期所", "CU": "上期所", "ZN": "上期所",
    "PB": "上期所", "NI": "上期所", "SN": "上期所", "AU": "上期所", "AG": "上期所",
    "SS": "上期所", "AO": "上期所", "FU": "上期所", "BU": "上期所", "RU": "上期所",
    "SP": "上期所", "BR": "上期所", "WR": "上期所", "AD": "上期所", "OP": "上期所",
    "JM": "大商所", "J": "大商所", "I": "大商所", "L": "大商所", "PP": "大商所",
    "V": "大商所", "EG": "大商所", "EB": "大商所", "PG": "大商所", "M": "大商所",
    "Y": "大商所", "A": "大商所", "B": "大商所", "C": "大商所", "CS": "大商所",
    "JD": "大商所", "LH": "大商所", "P": "大商所", "RR": "大商所", "FB": "大商所",
    "BB": "大商所", "BZ": "大商所", "LG": "大商所",
    "MA": "郑商所", "TA": "郑商所", "PF": "郑商所", "SA": "郑商所", "FG": "郑商所",
    "UR": "郑商所", "AP": "郑商所", "CJ": "郑商所", "CF": "郑商所", "SR": "郑商所",
    "OI": "郑商所", "RM": "郑商所", "ZC": "郑商所", "WH": "郑商所", "PM": "郑商所",
    "RS": "郑商所", "SF": "郑商所", "SM": "郑商所", "PK": "郑商所", "SH": "郑商所",
    "PX": "郑商所", "CY": "郑商所", "PR": "郑商所", "PL": "郑商所",
    "RI": "郑商所", "JR": "郑商所", "LR": "郑商所",
    "SI": "广期所", "LC": "广期所", "PS": "广期所", "PT": "广期所", "PD": "广期所",
    "SC": "能源中心", "LU": "能源中心", "NR": "能源中心", "BC": "能源中心", "EC": "能源中心",
    "IF": "中金所", "IH": "中金所", "IC": "中金所", "IM": "中金所",
    "T": "中金所", "TF": "中金所", "TL": "中金所", "TS": "中金所",
}


def _extract_variety_code(contract_code: str) -> str:
    """AL2609 -> AL；TA609 -> TA；A2609 -> A。取字母前缀（去重保序场景由调用方处理）"""
    return "".join(ch for ch in contract_code.upper() if ch.isalpha())


def fetch_margin_data(timeout: int = 10) -> dict[str, dict]:
    """抓取东财保证金页，返回 {品种代码: {"name": 品种名, "margin_rate": 0.17}}

    同一品种多合约时取第一行（页面按最近月份/主力排序）。
    解析失败抛异常，由调用方捕获静默降级。
    """
    req = urllib.request.Request(_EASTMONEY_URL, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    rows = _ROW_RE.findall(html)
    if not rows:
        raise RuntimeError("东财保证金页解析为空，可能页面结构已改版")
    result: dict[str, dict] = {}
    for name, contract_code, ratio_str in rows:
        code = _extract_variety_code(contract_code)
        if not code:
            continue
        if code not in result:  # 取该品种第一个合约（最近月份）
            result[code] = {
                "name": name.strip(),
                "margin_rate": int(ratio_str) / 100.0,
                "contract": contract_code.upper(),
            }
    return result


def sync_futures_config(db: Session | None = None) -> dict:
    """执行一次完整同步：抓取 → upsert 品种级 → 新品种检测。返回统计信息。"""
    close_db = db is None
    if db is None:
        db = SessionLocal()
    try:
        data = fetch_margin_data()
        now = datetime.now()
        upserted = 0
        new_varieties: list[dict] = []
        for code, info in data.items():
            cfg = (
                db.query(FuturesConfig)
                .filter(FuturesConfig.level == "variety", FuturesConfig.code == code)
                .first()
            )
            if cfg is None:
                cfg = FuturesConfig(level="variety", code=code)
                db.add(cfg)
            cfg.exchange = _VARIETY_EXCHANGE.get(code, "")
            cfg.name = info["name"]
            cfg.margin_rate = info["margin_rate"]
            cfg.margin_source = "eastmoney"
            cfg.updated_at = now
            upserted += 1
            # 新品种检测：不在内置乘数表 → 提示补录乘数
            if code not in FUTURES_MULTIPLIERS:
                new_varieties.append(
                    {"code": code, "name": info["name"], "margin_rate": info["margin_rate"]}
                )
        # 同步状态持久化（app_settings）
        _set_setting(db, "futures_last_sync", now.strftime("%Y-%m-%d %H:%M:%S"))
        _set_setting(db, "futures_new_varieties", json.dumps(new_varieties, ensure_ascii=False))
        db.commit()
        return {
            "ok": True,
            "synced": upserted,
            "new_varieties": new_varieties,
            "synced_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:  # 失败静默：保留旧数据
        db.rollback()
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "synced": 0,
            "new_varieties": [],
        }
    finally:
        if close_db:
            db.close()


def get_sync_status(db: Session) -> dict:
    """返回同步状态：上次同步时间 + 待补录新品种列表"""
    last = _get_setting(db, "futures_last_sync", "")
    raw = _get_setting(db, "futures_new_varieties", "[]")
    try:
        new_varieties = json.loads(raw)
    except Exception:
        new_varieties = []
    return {"last_sync": last, "new_varieties": new_varieties}


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else default


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = datetime.now()


# ---------- 后台调度 ----------

def start_background_sync() -> threading.Thread:
    """启动后台同步线程（daemon）：立即同步一次 + 每日 16:30 自动同步

    失败静默（不抛出，不影响主进程）；间隔 60s 轮询判断是否到点。
    """
    t = threading.Thread(target=_scheduler, daemon=True, name="futures-margin-sync")
    t.start()
    return t


def _scheduler() -> None:
    try:
        sync_futures_config()  # 启动立即同步一次
    except Exception:
        pass
    last_sync_date: str = ""
    while True:
        time.sleep(60)
        now = datetime.now()
        # 每日 16:30 后同步一次（每个自然日只同步一次）
        if now.hour >= 16 and now.minute >= 30:
            today = now.strftime("%Y-%m-%d")
            if today != last_sync_date:
                last_sync_date = today
                try:
                    sync_futures_config()
                except Exception:
                    pass
