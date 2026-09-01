"""大模型客户端封装（OpenAI 兼容协议，支持 DeepSeek / 通义千问 / Kimi / GLM 等）

配置读取优先级（V1.005 起）：
1. 软件界面保存的配置（app_settings 表，用户在"API 设置"里维护）；
2. .env 文件里的默认值（老用户升级后自动兜底，无需重新配置）。
"""
from openai import OpenAI
from sqlalchemy.orm import Session

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from .settings_service import get_llm_config


class LLMError(RuntimeError):
    """大模型调用异常"""


def get_client(db: Session | None = None) -> OpenAI:
    """创建 OpenAI 兼容客户端。传 db 时优先用界面保存的配置，否则用 .env 默认值。"""
    if db is not None:
        cfg = get_llm_config(db)
        api_key, base_url = cfg["api_key"], cfg["base_url"]
    else:
        api_key, base_url = DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

    if not api_key:
        raise LLMError(
            "未配置大模型 API Key：请点击右上角账号 →「API 设置」填写（或检查 .env 文件）"
        )
    return OpenAI(api_key=api_key, base_url=base_url or None)


def chat(
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 4096,
    db: Session | None = None,
) -> str:
    """调用大模型 Chat 完成接口"""
    try:
        client = get_client(db)
        model = DEEPSEEK_MODEL
        if db is not None:
            model = get_llm_config(db)["model"] or DEEPSEEK_MODEL
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"大模型调用失败: {e}") from e
