"""期货品种参数配置接口（V1.007）

- 读操作（列表/状态）：所有登录用户可读（交易录入/编辑时需展示识别信息）
- 写操作（补录/覆盖/同步）：仅管理员（get_current_admin）

futures_config 两级：
- variety 品种级：乘数（管理员补录覆盖内置表）+ 保证金率（东财每日同步 / 手动配置）
- contract 合约级：个别合约单独保证金率（手动覆盖，同步不触碰）
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FuturesConfig, User
from ..routers.auth import get_current_admin, get_current_user
from ..services import futures_sync
from ..services.investment import FUTURES_DEFAULT_MARGIN, FUTURES_MULTIPLIERS

router = APIRouter(prefix="/api/futures", tags=["期货参数"])


class VarietyIn(BaseModel):
    """品种级配置（补录/修改）"""
    code: str = ""  # 品种代码 AL（补录必填，修改可省略）
    name: str = ""  # 品种名 沪铝
    exchange: str = ""  # 交易所
    multiplier: float | None = None  # 合约乘数（None=沿用内置表）
    margin_rate: float | None = None  # 保证金率 0.17（None=沿用现有/内置默认）


class ContractIn(BaseModel):
    """合约级覆盖（个别合约单独保证金率）"""
    code: str  # 完整合约代码 AL2609
    name: str = ""  # 品种名（展示用）
    margin_rate: float  # 保证金率 0.17


@router.get("/config", response_model=list[dict])
def list_config(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出全部期货参数配置（品种级+合约级，合并内置乘数展示）"""
    rows = db.query(FuturesConfig).order_by(FuturesConfig.level, FuturesConfig.code).all()
    result = []
    for r in rows:
        builtin = FUTURES_MULTIPLIERS.get(r.code)
        result.append(
            {
                "id": r.id,
                "level": r.level,
                "exchange": r.exchange,
                "code": r.code,
                "name": r.name,
                "multiplier": r.multiplier if r.multiplier is not None else (builtin[1] if builtin else None),
                "multiplier_builtin": builtin[1] if builtin else None,
                "multiplier_source": "manual" if r.multiplier is not None else ("builtin" if builtin else "missing"),
                "margin_rate": r.margin_rate,
                "margin_source": r.margin_source or "builtin",
                "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else "",
            }
        )
    return result


@router.get("/status", response_model=dict)
def sync_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """同步状态：上次同步时间 + 待补录乘数的新品种列表"""
    return futures_sync.get_sync_status(db)


@router.post("/sync", response_model=dict)
def manual_sync(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """手动触发一次东财保证金率同步（管理员）"""
    result = futures_sync.sync_futures_config(db)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=result.get("error", "同步失败"))
    return result


@router.post("/varieties", response_model=dict, status_code=201)
def create_variety(
    data: VarietyIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """补录品种（管理员）：code 必填，乘数/保证金率可选"""
    code = data.code.strip().upper()
    if not code:
        raise HTTPException(status_code=422, detail="品种代码不能为空")
    exist = (
        db.query(FuturesConfig)
        .filter(FuturesConfig.level == "variety", FuturesConfig.code == code)
        .first()
    )
    if exist:
        raise HTTPException(status_code=409, detail=f"品种 {code} 已存在，请用修改接口")
    cfg = FuturesConfig(
        level="variety",
        code=code,
        name=data.name,
        exchange=data.exchange,
        multiplier=data.multiplier,
        margin_rate=data.margin_rate,
        margin_source="manual" if data.margin_rate is not None else "",
    )
    db.add(cfg)
    db.commit()
    return {"ok": True, "id": cfg.id, "code": code}


@router.put("/varieties/{code}", response_model=dict)
def update_variety(
    code: str,
    data: VarietyIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """修改品种配置（管理员）：乘数/保证金率可部分更新（None 不修改）"""
    cfg = (
        db.query(FuturesConfig)
        .filter(FuturesConfig.level == "variety", FuturesConfig.code == code.upper())
        .first()
    )
    if not cfg:
        raise HTTPException(status_code=404, detail=f"品种 {code.upper()} 不存在")
    if data.name:
        cfg.name = data.name
    if data.exchange:
        cfg.exchange = data.exchange
    if data.multiplier is not None:
        cfg.multiplier = data.multiplier
    if data.margin_rate is not None:
        cfg.margin_rate = data.margin_rate
        cfg.margin_source = "manual"
    cfg.updated_at = datetime.now()
    db.commit()
    return {"ok": True, "code": cfg.code}


@router.delete("/varieties/{code}", response_model=dict)
def delete_variety(
    code: str,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """删除品种配置（管理员）：删除后该品种回退到内置表/内置默认保证金率"""
    cfg = (
        db.query(FuturesConfig)
        .filter(FuturesConfig.level == "variety", FuturesConfig.code == code.upper())
        .first()
    )
    if not cfg:
        raise HTTPException(status_code=404, detail=f"品种 {code.upper()} 不存在")
    db.delete(cfg)
    db.commit()
    return {"ok": True, "code": cfg.code}


@router.post("/contracts", response_model=dict, status_code=201)
def create_contract(
    data: ContractIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """合约级保证金率覆盖（管理员）：完整合约代码如 AL2609"""
    code = data.code.strip().upper()
    if not code or not any(ch.isdigit() for ch in code):
        raise HTTPException(status_code=422, detail="合约代码需包含月份数字，如 AL2609")
    exist = (
        db.query(FuturesConfig)
        .filter(FuturesConfig.level == "contract", FuturesConfig.code == code)
        .first()
    )
    if exist:
        raise HTTPException(status_code=409, detail=f"合约 {code} 已有覆盖配置")
    db.add(
        FuturesConfig(
            level="contract",
            code=code,
            name=data.name,
            margin_rate=data.margin_rate,
            margin_source="manual",
        )
    )
    db.commit()
    return {"ok": True, "code": code}


@router.delete("/contracts/{code}", response_model=dict)
def delete_contract(
    code: str,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """删除合约级覆盖（管理员）：删除后回退到品种级保证金率"""
    cfg = (
        db.query(FuturesConfig)
        .filter(FuturesConfig.level == "contract", FuturesConfig.code == code.upper())
        .first()
    )
    if not cfg:
        raise HTTPException(status_code=404, detail=f"合约 {code.upper()} 无覆盖配置")
    db.delete(cfg)
    db.commit()
    return {"ok": True, "code": cfg.code}
