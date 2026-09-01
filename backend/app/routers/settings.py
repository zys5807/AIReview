"""应用设置接口：API 配置（软件内管理，替代手改 .env）"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.settings_service import get_llm_config, set_llm_config
from .auth import get_current_user

router = APIRouter(prefix="/api/settings", tags=["设置"])


class LlmSettingsIn(BaseModel):
    api_key: str = Field(default="", max_length=500, description="留空表示清空（回退 .env 默认值）")
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)


@router.get("/llm", response_model=dict)
def get_llm_settings(
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """读取当前生效的大模型配置（数据库优先，回退 .env 默认值）"""
    cfg = get_llm_config(db)
    return {
        "api_key": cfg["api_key"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "source": cfg["source"],
        "has_key": bool(cfg["api_key"]),
    }


@router.put("/llm", response_model=dict)
def update_llm_settings(
    data: LlmSettingsIn,
    db: Session = Depends(get_db),
    _: object = Depends(get_current_user),
):
    """保存大模型配置到数据库（软件内管理，无需手改 .env）"""
    set_llm_config(
        db,
        api_key=data.api_key,
        base_url=data.base_url,
        model=data.model,
    )
    cfg = get_llm_config(db)
    return {
        "message": "API 配置已保存",
        "api_key": cfg["api_key"],
        "base_url": cfg["base_url"],
        "model": cfg["model"],
        "source": cfg["source"],
        "has_key": bool(cfg["api_key"]),
    }
