"""应用设置服务：读写 app_settings 表。

设计原则：
- 数据库中的值优先（用户在软件界面里配置的）；
- 没有则回退 .env / 环境变量里的默认值（老用户升级后无需重新配置）；
- 这样 .env 只作为"出厂默认值"，日常修改走界面，规避文本编码/格式问题。
"""
from sqlalchemy.orm import Session

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from ..models import AppSetting

# 配置键名（app_settings 表）
KEY_API_KEY = "llm_api_key"
KEY_BASE_URL = "llm_base_url"
KEY_MODEL = "llm_model"


def get_setting(db: Session, key: str) -> str | None:
    """读数据库中的配置；不存在返回 None"""
    row = db.get(AppSetting, key)
    return row.value if row else None


def set_setting(db: Session, key: str, value: str) -> None:
    """写入配置（upsert）"""
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def get_llm_config(db: Session) -> dict:
    """读取当前生效的大模型配置：数据库优先，回退 .env 默认值。

    返回: {"api_key": str, "base_url": str, "model": str,
           "source": "db" | "env"}  # source 仅当三项都来自 .env 时为 env
    """
    api_key = get_setting(db, KEY_API_KEY)
    base_url = get_setting(db, KEY_BASE_URL)
    model = get_setting(db, KEY_MODEL)

    # 数据库有非空值 → 优先；否则回退 .env（清空 = 恢复 .env 默认，与界面提示一致）
    api_key = api_key if api_key else DEEPSEEK_API_KEY
    base_url = base_url if base_url else DEEPSEEK_BASE_URL
    model = model if model else DEEPSEEK_MODEL

    from_db = any(
        v
        for v in (
            get_setting(db, KEY_API_KEY),
            get_setting(db, KEY_BASE_URL),
            get_setting(db, KEY_MODEL),
        )
    )
    return {
        "api_key": api_key or "",
        "base_url": base_url or "",
        "model": model or "",
        "source": "db" if from_db else "env",
    }


def set_llm_config(
    db: Session,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> None:
    """写入大模型配置。参数为 None 表示不修改该项；空字符串表示清空（回退 .env）。"""
    if api_key is not None:
        set_setting(db, KEY_API_KEY, api_key.strip())
    if base_url is not None:
        set_setting(db, KEY_BASE_URL, base_url.strip())
    if model is not None:
        set_setting(db, KEY_MODEL, model.strip())
    db.commit()
