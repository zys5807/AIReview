"""截图文件存储服务"""
import uuid
from pathlib import Path

from fastapi import UploadFile

from ..config import ALLOWED_EXTENSIONS, MAX_UPLOAD_SIZE, UPLOAD_DIR


async def save_screenshot(file: UploadFile) -> dict:
    """保存截图文件，返回存储元信息"""
    filename = file.filename or "unknown.png"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {suffix}")

    content = await file.read()
    if not content:
        raise ValueError("文件内容为空")
    if len(content) > MAX_UPLOAD_SIZE:
        raise ValueError("文件过大，最大支持 10MB")

    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = UPLOAD_DIR / stored_name
    stored_path.write_bytes(content)

    return {
        "filename": filename,
        "stored_path": f"uploads/screenshots/{stored_name}",
        "file_size": len(content),
        "content_type": file.content_type or "",
    }
