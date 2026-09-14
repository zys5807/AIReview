"""AI复盘APP 一键启动器（单服务模式）

用法：
    AIReviewSystem.exe                      正常启动
    AIReviewSystem.exe --port 8080          指定端口启动
    AIReviewSystem.exe --reset-password     重置密码（无需旧密码，须先关闭服务）
    AIReviewSystem.exe --diagnose           生成 diagnose_report.txt（启动异常时排障用）

说明：
    - 若检测到本服务已在运行（上次关浏览器但后台进程没退出），
      再次双击 exe 会直接打开网页并退出，不会报错。
    - 任何启动失败都会写进 startup_log.txt，并把窗口停住，
      避免"闪退"看不到任何提示。日志写不了时落到系统 TEMP 目录。
    - 设置环境变量 AIRES_NO_PAUSE=1 可跳过"按回车关闭"（自动化测试用）。
"""
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request

PORT = 8000
LOG_NAME = "startup_log.txt"
REPORT_NAME = "diagnose_report.txt"
LOG_MAX_BYTES = 512 * 1024


def _setup_stdout():
    """控制台/重定向都强制 UTF-8，避免在英文系统上 print 中文直接抛异常"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _base_dir():
    from pathlib import Path

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()


def _log_targets():
    from pathlib import Path

    out = [BASE_DIR / LOG_NAME]
    tmp = os.environ.get("TEMP") or os.environ.get("TMP")
    if tmp:
        out.append(Path(tmp) / "AIReviewSystem_startup_log.txt")
    return out


def _log(msg: str, echo: bool = True):
    """写启动日志（程序目录优先，不可写时退到 TEMP），同时回显到窗口"""
    line = "[{}] {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if echo:
        try:
            print(line, flush=True)
        except Exception:
            pass
    for p in _log_targets():
        try:
            if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
                p.unlink()
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return
        except Exception:
            continue


def _hold(seconds: int = None):
    """停住窗口，避免错误信息一闪而过（用户看到的"闪退"）"""
    if os.environ.get("AIRES_NO_PAUSE") == "1":
        return
    if seconds is None:
        try:
            print()
            input("  按回车键关闭本窗口...")
            return
        except Exception:
            seconds = 30
    print("\n  （{} 秒后自动关闭）".format(seconds))
    try:
        time.sleep(seconds)
    except Exception:
        pass


# ---------------------------------------------------------------- 环境体检
def _check_env():
    """返回 (fatal, warnings, info)：fatal 为无法启动的硬问题"""
    import platform
    import sqlite3

    from pathlib import Path

    fatal, warnings, info = [], [], []
    frozen = bool(getattr(sys, "frozen", False))

    info.append("程序目录        : {}".format(BASE_DIR))
    info.append("程序文件        : {}".format(Path(sys.executable).resolve()))
    info.append("当前工作目录    : {}".format(os.getcwd()))
    info.append("Windows         : {} {} (build {})".format(
        platform.system(), platform.release(), platform.version()))
    info.append("系统架构        : {} / {}".format(platform.machine(), platform.architecture()[0]))
    info.append("Python          : {} ({})".format(sys.version.split()[0], sys.executable))
    info.append("打包运行        : {}".format(frozen))
    info.append("控制台代码页    : {}".format(getattr(sys.stdout, "encoding", "?")))

    bundle = BASE_DIR / "_internal"
    if frozen:
        if bundle.is_dir():
            info.append("依赖目录        : {} (存在)".format(bundle))
        else:
            fatal.append("缺少依赖文件夹 _internal —— 压缩包没有完整解压，"
                         "请用压缩软件「解压到当前文件夹」后重试")
    else:
        info.append("依赖目录        : 开发模式，无需 _internal")

    # 前端页面
    dist_html = (bundle / "dist" / "index.html") if frozen else (
        BASE_DIR.parent / "frontend" / "dist" / "index.html")
    if dist_html.is_file():
        info.append("前端页面        : 存在")
    else:
        fatal.append("找不到前端页面 {} —— 解压不完整，请重新完整解压压缩包".format(dist_html))

    # 目录可写
    try:
        probe = BASE_DIR / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        info.append("程序目录可写    : 是")
    except Exception as e:
        warnings.append("程序目录不可写（{}: {}）—— 记录与截图保存可能失败，"
                        "建议把文件夹放到 D 盘或桌面，不要放 Program Files".format(
                            type(e).__name__, e))

    # 数据库可读写
    db_path = BASE_DIR / "app.db"
    if db_path.is_file():
        info.append("数据库文件      : {} ({:.2f} MB)".format(db_path, db_path.stat().st_size / 1048576))
    else:
        info.append("数据库文件      : 不存在（首次启动会自动创建）")
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _wtest(x INTEGER)")
            conn.execute("DROP TABLE _wtest")
            conn.commit()
            tabs = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            info.append("数据库可读写    : 是（{} 张表）".format(len(tabs)))
            info.append("数据库表        : {}".format(", ".join(sorted(tabs))[:400]))
            try:
                integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if integ != "ok":
                    fatal.append("数据库文件损坏（integrity_check={}）—— "
                                 "请用备份恢复或删除 app.db 重新初始化".format(integ))
            except Exception:
                pass
        finally:
            conn.close()
    except Exception as e:
        fatal.append("数据库无法读写（{}: {}）—— 常见原因：文件夹只读 / 被其他程序占用 / "
                     "被安全软件拦截".format(type(e).__name__, e))

    return fatal, warnings, info


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


def _port_in_use(port=PORT):
    """端口是否已有服务在监听（用 TCP 连接探测，比 bind 探测更可靠）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _is_our_service(port=PORT):
    """端口上是否已经是本系统的服务（避免误复用其他程序）"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:{}/api/health".format(port), timeout=2) as r:
            import json

            data = json.loads(r.read().decode("utf-8"))
            return data.get("service") == "AIReviewSystem"
    except Exception:
        return False


def _open_browser(port=PORT):
    time.sleep(3)
    try:
        import webbrowser

        webbrowser.open("http://localhost:{}".format(port))
    except Exception as e:
        _log("打开浏览器失败（请手动访问 http://localhost:{}）：{}".format(port, e))


def _port_owner_hint(port):
    """给出查占用进程的具体命令"""
    return ("请在「命令提示符」里执行下面这行，看是哪个程序占着 {} 端口：\n"
            "     netstat -ano | findstr :{}\n"
            "  记下最后一列的 PID，再到「任务管理器 → 详细信息」按 PID 找到并结束它；\n"
            "  或者双击本目录下的「换个端口启动.bat」用其他端口启动。").format(port, port)


# ---------------------------------------------------------------- 诊断模式
def diagnose(port=PORT):
    import datetime

    print("=" * 60)
    print("  AIReviewSystem 启动诊断")
    print("=" * 60)
    fatal, warnings, info = _check_env()

    print("\n【环境信息】")
    for line in info:
        print("  " + line)

    print("\n【端口检查】{}".format(port))
    if _port_in_use(port):
        if _is_our_service(port):
            print("  端口 {} 已被「本程序」占用（服务已在运行，不是故障）".format(port))
        else:
            print("  ✗ 端口 {} 被其他程序占用 —— 这就是双击没反应/闪退的原因".format(port))
            print("  " + _port_owner_hint(port).replace("\n", "\n  "))
    else:
        print("  端口 {} 空闲".format(port))

    print("\n【结果】")
    if fatal:
        for f in fatal:
            print("  ✗ " + f)
    for w in warnings:
        print("  ! " + w)
    if not fatal and not warnings:
        print("  ✓ 未发现环境问题，程序应可正常启动")

    report = BASE_DIR / REPORT_NAME
    body = []
    body.append("AIReviewSystem 启动诊断报告")
    body.append("生成时间: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    body.append("")
    body.append("== 环境信息 ==")
    body.extend(info)
    body.append("")
    body.append("== 端口检查 ({}) ==".format(port))
    if _port_in_use(port):
        body.append("占用中；是本程序服务: {}".format(_is_our_service(port)))
    else:
        body.append("空闲")
    body.append("")
    body.append("== 硬性问题 ==")
    body.extend(fatal or ["无"])
    body.append("")
    body.append("== 警告 ==")
    body.extend(warnings or ["无"])
    body.append("")
    body.append("== 目录清单 ==")
    try:
        for f in sorted(BASE_DIR.iterdir()):
            body.append("  {:<32} {}".format(
                f.name + ("/" if f.is_dir() else ""),
                "<DIR>" if f.is_dir() else "{:.2f} MB".format(f.stat().st_size / 1048576)))
    except Exception as e:
        body.append("  列目录失败: {}".format(e))
    body.append("")
    body.append("== 启动日志末尾 ==")
    for p in _log_targets():
        try:
            body.append("--- {} ---".format(p))
            body.append(p.read_text(encoding="utf-8", errors="replace")[-4000:])
            break
        except Exception:
            continue
    try:
        report.write_text("\n".join(body), encoding="utf-8")
        print("\n诊断报告已保存（请把这个文件发回给我）：\n  {}".format(report))
        _log("诊断报告已生成: {}".format(report))
    except Exception as e:
        print("\n诊断报告写入失败: {}".format(e))
    _hold()


# ---------------------------------------------------------------- 重置密码
def reset_password():
    """重置用户密码（无需旧密码）。支持交互式或命令行参数方式。"""
    from app.database import SessionLocal
    from app.models import User
    from app.services.security import hash_password

    print("=" * 50)
    print("  Reset Password - 重置密码")
    print("  (先关闭正在运行的 AIReviewSystem 服务再操作)")
    print("=" * 50)

    # 支持：--reset-password <username> <new_password>
    if len(sys.argv) >= 4 and sys.argv[2]:
        username, new_password = sys.argv[2], sys.argv[3]
    else:
        username = input("  用户名: ").strip()
        new_password = getpass_getpass("  新密码: ")
        confirm = getpass_getpass("  确认新密码: ")
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
            print("  用户「{}」不存在".format(username))
            sys.exit(1)
        user.password_hash = hash_password(new_password)
        db.commit()
        print("  用户「{}」密码已重置，请用新密码登录".format(username))
    finally:
        db.close()
    sys.exit(0)


# ---------------------------------------------------------------- 启动
def run(port=PORT):
    """返回进程退出码；所有失败路径都会先 _hold() 再返回"""
    fatal, warnings, info = _check_env()
    for w in warnings:
        _log("警告: " + w)

    if fatal:
        print("=" * 60)
        print("  启动失败：环境检查未通过")
        print("=" * 60)
        for f in fatal:
            print("  ✗ " + f)
        _log("环境检查未通过: " + " | ".join(fatal))
        print("\n  可双击本目录下的「诊断.bat」生成诊断报告。")
        _hold()
        return 1

    if _port_in_use(port):
        if _is_our_service(port):
            print("=" * 60)
            print("  AIReviewSystem 已在运行，正在打开网页...")
            print("  (无需重复启动；想彻底停止服务请关闭旧窗口或使用「停止服务.bat」)")
            print("=" * 60)
            _log("检测到服务已在 {} 端口运行，打开网页后退出".format(port))
            _open_browser(port)
            _hold(8)
            return 0
        print("=" * 60)
        print("  错误：端口 {} 已被其他程序占用，无法启动。".format(port))
        print("=" * 60)
        print("  " + _port_owner_hint(port).replace("\n", "\n  "))
        _log("端口 {} 被其他程序占用".format(port))
        _hold()
        return 1

    import uvicorn

    from app.main import app  # 导入即执行建表/迁移

    ip = get_lan_ip()
    print("=" * 60)
    print("  AIReviewSystem V1.0.9.4 - Starting...")
    print("=" * 60)
    print()
    print("  On this PC:  http://localhost:{}".format(port))
    if ip:
        print("  On phone:    http://{}:{}   (phone must be on same WiFi)".format(ip, port))
    print()
    print("  Browser will open automatically...")
    print("  To stop: close this window or press Ctrl+C")
    print("=" * 60)
    print()
    _log("服务启动: http://127.0.0.1:{}  局域网: {}".format(port, ip))
    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()
    try:
        uvicorn.run(app, host="0.0.0.0", port=port)
    except KeyboardInterrupt:
        pass
    finally:
        _log("服务已停止")
    return 0


def main():
    _setup_stdout()
    argv = sys.argv[1:]
    port = PORT
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except Exception:
            print("  --port 后需要跟一个端口号，例如：AIReviewSystem.exe --port 8080")
            _hold()
            return 1

    if "--diagnose" in argv:
        diagnose(port)
        return 0

    if "--reset-password" in argv:
        reset_password()

    try:
        return run(port)
    except KeyboardInterrupt:
        return 0
    except SystemExit:
        raise
    except BaseException:
        tb = traceback.format_exc()
        print("=" * 60)
        print("  启动异常，程序即将退出")
        print("=" * 60)
        print(tb)
        _log("启动异常:\n" + tb)
        print("异常详情已写入「{}」，可双击「诊断.bat」生成完整报告。".format(LOG_NAME))
        _hold()
        return 1


if __name__ == "__main__":
    sys.exit(main())
