"""全局配置：通过环境变量或 .env 文件覆盖默认值"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录：
#  - 开发模式：backend/
#  - 打包后(frozen)：exe 所在目录（数据 app.db/uploads 与 exe 同目录，便于迁移）
#
# .env 加载优先级（打包后）：
#   1) exe 同目录 .env —— 兼容老版本用户手改过的 .env（升级后依然生效）
#   2) 程序内部(_internal) .env —— V1.005 起"出厂默认值"内置，用户目录不再需要 .env
# 开发模式固定加载 backend/.env
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    # 打包资源目录：onedir 标准结构为 exe 目录/_internal；
    # onefile 模式则回退到 sys._MEIPASS（临时解压目录）
    BUNDLE_DIR = BASE_DIR / "_internal"
    if not BUNDLE_DIR.is_dir():
        BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)).resolve()
    _env_candidates = [BASE_DIR / ".env", BUNDLE_DIR / ".env"]
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    _env_candidates = [BASE_DIR / ".env"]
for _env_path in _env_candidates:
    if _env_path.is_file():
        load_dotenv(_env_path)
        break

# 数据库
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'app.db'}")

# 截图存储目录
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads" / "screenshots"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 上传限制：默认 10MB
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", 10 * 1024 * 1024))
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 大模型配置（阶段二使用，先预留）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# OCR（Tesseract）可执行文件路径；留空则自动探测常见安装路径
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")

# 前端来源（CORS）
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")
