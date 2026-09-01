"""数据备份导出接口：打包数据库 + 截图目录为 zip"""
import io
import zipfile
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..config import BASE_DIR

router = APIRouter(prefix="/api/backup", tags=["备份"])


@router.get("/download")
def download_backup():
    """导出全部数据（app.db + uploads/）为 zip 文件"""
    db_path = BASE_DIR / "app.db"
    uploads_dir = BASE_DIR / "uploads"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        if db_path.exists():
            z.write(db_path, "app.db")
        if uploads_dir.is_dir():
            for f in sorted(uploads_dir.rglob("*")):
                if f.is_file():
                    z.write(f, f"uploads/{f.relative_to(uploads_dir)}")

    buffer.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=ai-review-backup_{stamp}.zip"
        },
    )
