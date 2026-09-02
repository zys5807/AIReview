"""AI复盘APP 一键启动器（单服务模式）

用法：
    python launcher.py                   正常启动
    python launcher.py --reset-password  重置密码（忘记密码时，无需旧密码）

说明：
    - 若检测到本服务已在运行（比如上次关浏览器但后台进程没退出），
      再次双击 exe 会直接打开网页并退出，不会因端口被占用而报错。
    - 想彻底停止服务：关闭启动时的黑色窗口，或使用"停止服务.bat"。
"""
import getpass
import json
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

# 直接导入 app（确保 PyInstaller 打包时能收集整个 app 包；import 时会执行建表/迁移）
from app.main import app

PORT = 8000


def get_lan_ip():
    """获取局域网 IP（手机同 WiFi 可访问）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def _port_in_use(port=PORT) -> bool:
    """端口是否已有服务在监听（用 TCP 连接探测，比 bind 探测更可靠）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _is_our_service(port=PORT) -> bool:
    """端口上是否已经是本系统的服务（避免误复用其他程序）"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("service") == "AIReviewSystem"
    except Exception:
        return False


def _open_browser():
    time.sleep(3)
    webbrowser.open(f"http://localhost:{PORT}")


def reset_password():
    """重置用户密码（无需旧密码）。支持交互式或命令行参数方式。"""
    from app.database import SessionLocal
    from app.models import User
    from app.services.security import hash_password

    print("=" * 50)
    print("  Reset Password - 重置密码")
    print("  (先关闭正在运行的 AIReviewApp 服务再操作)")
    print("=" * 50)

    # 支持：--reset-password <username> <new_password>
    if len(sys.argv) >= 4 and sys.argv[2]:
        username, new_password = sys.argv[2], sys.argv[3]
    else:
        username = input("  用户名: ").strip()
        new_password = getpass.getpass("  新密码: ")
        confirm = getpass.getpass("  确认新密码: ")
        if new_password != confirm:
            print("  两次输入不一致，已取消")
            sys.exit(1)

    if len(new_password) < 6:
        print("  密码至少 6 位")
        sys.exit(1)

    db = SessionLocal()
    try:
        user = db.query(User).filter_by(username=username).first()
        if not user:
            print(f"  用户「{username}」不存在")
            sys.exit(1)
        user.password_hash = hash_password(new_password)
        db.commit()
        print(f"  用户「{username}」密码已重置，请用新密码登录")
    finally:
        db.close()
    sys.exit(0)


if __name__ == "__main__":
    if "--reset-password" in sys.argv:
        reset_password()

    # 端口被占用时：如果是本服务在跑，直接复用并打开网页；否则提示端口冲突
    if _port_in_use(PORT):
        if _is_our_service(PORT):
            print("=" * 50)
            print("  AIReviewSystem 已在运行，正在打开网页...")
            print("  (无需重复启动；想彻底停止服务请关闭旧窗口或使用 停止服务.bat)")
            print("=" * 50)
            webbrowser.open(f"http://localhost:{PORT}")
            sys.exit(0)
        print("=" * 50)
        print(f"  错误：端口 {PORT} 已被其他程序占用，无法启动。")
        print("  请先关闭占用该端口的程序，或修改配置中的端口后重试。")
        print("=" * 50)
        sys.exit(1)

    ip = get_lan_ip()
    print("=" * 50)
    print("  AIReviewSystem V1.0.8.2 - Starting...")
    print("=" * 50)
    print()
    print("  On this PC:  http://localhost:8000")
    if ip:
        print(f"  On phone:    http://{ip}:8000   (phone must be on same WiFi)")
    print()
    print("  Browser will open automatically...")
    print("  To stop: close this window or press Ctrl+C")
    print("=" * 50)
    print()
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
