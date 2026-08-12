#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCU3 桌面版入口（无黑框）
关闭窗口时同步终止后端进程
"""

import os
import sys
import ctypes
import subprocess
import time
import atexit
import traceback
from pathlib import Path

# 获取应用目录
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    BASE_DIR = Path(__file__).parent.resolve()

SERVER_URL = "http://127.0.0.1:8300"
LOG_FILE = BASE_DIR / "scu3_launcher.log"
_server_process = None


def _log(msg):
    """写日志"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")
        f.flush()


def _kill_server():
    """彻底终止后端进程及其子进程"""
    global _server_process
    if _server_process:
        pid = _server_process.pid
        try:
            # taskkill /F /T 强制终止整个进程树
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=5
            )
            _log(f"后端进程树已终止 (PID: {pid})")
        except Exception as e:
            _log(f"终止后端进程失败: {e}")
        finally:
            _server_process = None


def _start_server():
    """用子进程启动后端"""
    global _server_process
    try:
        python_exe = sys.executable
        python_dir = os.path.dirname(python_exe)
        python_exe_candidate = os.path.join(python_dir, "python.exe")
        if os.path.exists(python_exe_candidate):
            python_exe = python_exe_candidate

        # 将后端输出重定向到日志文件，便于排查
        server_log = open(BASE_DIR / "server_output.log", "w", encoding="utf-8")

        _log(f"启动后端子进程，Python: {python_exe}")
        # 设置 PYTHONUTF8=1 强制子进程使用 UTF-8，避免 GBK 编码崩溃
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        _server_process = subprocess.Popen(
            [python_exe, str(BASE_DIR / "server.py")],
            cwd=str(BASE_DIR),
            stdout=server_log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        _log(f"后端子进程 PID: {_server_process.pid}")

        # 注册退出钩子，确保任何方式退出都会终止后端
        atexit.register(_kill_server)
    except Exception as e:
        _log(f"后端启动失败: {e}")
        _log(traceback.format_exc())


def _wait_for_server(timeout=30):
    """等待后端就绪"""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.urlopen(f"{SERVER_URL}/", timeout=2)
            if req.status == 200:
                _log("后端服务就绪")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    _log("等待后端服务超时")
    return False


def main():
    """主入口"""
    _log("=" * 50)
    _log("SCU3 桌面版启动")
    _log(f"Python: {sys.executable}")

    # 1. 启动后端
    _start_server()

    # 2. 等待就绪
    if not _wait_for_server():
        _log("后端启动超时，退出")
        _kill_server()
        sys.exit(1)

    # 3. 创建桌面窗口
    try:
        _log("创建桌面窗口...")
        import webview

        webview.create_window(
            title="标准计算单元 SCU3",
            url=SERVER_URL,
            width=1280,
            height=860,
            min_size=(900, 600),
            resizable=True,
            text_select=True,
            easy_drag=False
        )

        _log("启动 WebView...")
        webview.start(debug=False)
        _log("WebView 已退出")
    except Exception as e:
        _log(f"窗口创建失败: {e}")
        _log(traceback.format_exc())
    finally:
        # 4. 关闭窗口后同步终止后端
        _kill_server()


if __name__ == "__main__":
    main()
