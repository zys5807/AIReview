"""AI复盘APP 后端入口"""
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import CORS_ORIGINS, BASE_DIR
from .database import ensure_schema
from .routers import accounts, analysis, auth, backup, futures, importer, reviews, screenshots, settings, trade_plans, trades, trading_systems
from .services import futures_sync

# 创建数据表 + 轻量迁移（补新列）
ensure_schema()

# 后台同步期货保证金率（启动立即同步 + 每日 16:30，失败静默）
futures_sync.start_background_sync()

app = FastAPI(
    title="AIReviewSystem",
    description="AI交易复盘系统 - 单机/局域网多用户版",
    version="1.0.7",
)

# CORS：允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件：提供截图访问 /uploads/...
app.mount("/uploads", StaticFiles(directory=BASE_DIR / "uploads"), name="uploads")

# 注册路由
app.include_router(auth.router)
app.include_router(screenshots.router)
app.include_router(trades.router)
app.include_router(trading_systems.router)
app.include_router(reviews.router)
app.include_router(analysis.router)
app.include_router(trade_plans.router)
app.include_router(importer.router)
app.include_router(backup.router)
app.include_router(settings.router)
app.include_router(accounts.router)
app.include_router(futures.router)


@app.get("/api/health", tags=["系统"])
def health_check():
    return {
        "status": "ok",
        "service": "AIReviewSystem",
        "version": "1.0.7",
        "frontend_built": _HAS_DIST,
    }


# ---------- 单服务模式：托管前端构建产物 ----------
# 开发：../frontend/dist；打包后：PyInstaller data 解压目录(dist)
if getattr(sys, "frozen", False):
    DIST_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "dist"
else:
    DIST_DIR = BASE_DIR.parent / "frontend" / "dist"
_HAS_DIST = (DIST_DIR / "index.html").is_file()

if _HAS_DIST:
    # 静态资源（JS/CSS/图片）
    if (DIST_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def serve_index():
        return FileResponse(DIST_DIR / "index.html")

    # SPA 路由回退：非 API 路径一律返回 index.html
    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str):
        if path.startswith("api") or path.startswith("uploads"):
            raise HTTPException(status_code=404)
        target = DIST_DIR / path
        if target.is_file():
            return FileResponse(target)
        return FileResponse(DIST_DIR / "index.html")
