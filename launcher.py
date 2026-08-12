# -*- coding: utf-8 -*-
"""
SCU3 一键启动器（轻量版）
========================
功能：
  1. 自动查找系统 Python 环境
  2. 通过 subprocess 启动后端服务（uvicorn server:app）
  3. 等待服务就绪（端口检测 + HTTP 探测）
  4. 自动打开前端浏览器（http://localhost:8000/）
  5. 实时显示后端日志，支持优雅退出（Ctrl+C 或关闭窗口）

打包后（exe 模式）：查找系统 Python，通过 subprocess 启动后端
开发模式（python launcher.py）：直接使用当前 Python

打包：
  pyinstaller launcher.spec
"""
import os
import sys
import time
import socket
import shutil
import subprocess
import threading
import webbrowser
import urllib.request
from urllib.error import URLError
from pathlib import Path

# ─── 配置 ────────────────────────────────────
HOST = "127.0.0.1"
PORT = 8000
STARTUP_TIMEOUT = 30  # 服务启动超时（秒）
HEALTH_PATH = "/"     # 健康探测路径
BROWSER_URL = f"http://localhost:{PORT}/"

# 判断运行环境
FROZEN = getattr(sys, "frozen", False)

# 定位工作目录
if FROZEN:
    # exe 所在目录（启动器放在 SCU3 项目根目录下）
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def find_python() -> str:
    """查找系统 Python 可执行文件路径"""
    if not FROZEN:
        return sys.executable

    # 打包模式：查找系统 Python
    candidates = []

    # 1. 从 PATH 查找
    for name in ("python", "python3", "python.exe", "python3.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(found)

    # 2. 常见安装路径
    common_paths = [
        r"C:\Python310\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Python312\python.exe",
        r"C:\Python39\python.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python310\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python39\python.exe"),
        # conda
        os.path.expandvars(r"%USERPROFILE%\anaconda3\python.exe"),
        os.path.expandvars(r"%USERPROFILE%\miniconda3\python.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe"),
    ]
    for p in common_paths:
        if os.path.isfile(p):
            candidates.append(p)

    # 3. 同目录下的 Python（便携版）
    portable = os.path.join(BASE_DIR, "python.exe")
    if os.path.isfile(portable):
        candidates.append(portable)

    # 去重并验证
    seen = set()
    for candidate in candidates:
        norm = os.path.normpath(candidate)
        if norm in seen:
            continue
        seen.add(norm)
        try:
            result = subprocess.run(
                [candidate, "--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                print(f"[启动器] 找到 Python: {candidate} ({result.stdout.strip()})")
                return candidate
        except Exception:
            continue

    return ""


def find_SCU3_project(python_path: str) -> str:
    """查找 SCU3 项目目录（包含 server.py 的目录）"""
    # 1. exe 同目录
    if os.path.isfile(os.path.join(BASE_DIR, "server.py")):
        return BASE_DIR

    # 2. exe 同级下的 SCU3 子目录
    sub = os.path.join(BASE_DIR, "SCU3")
    if os.path.isfile(os.path.join(sub, "server.py")):
        return sub

    # 3. 通过 Python 查找 SCU3 包位置
    if python_path:
        try:
            result = subprocess.run(
                [python_path, "-c",
                 "import SCU3; import os; print(os.path.dirname(SCU3.__file__))"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                if os.path.isfile(os.path.join(path, "server.py")):
                    return path
        except Exception:
            pass

    # 4. 常见路径
    common = [
        os.path.expanduser(r"~\Desktop\SCU3"),
        os.path.expanduser(r"~\Desktop\scu_v5.2"),
        os.path.expanduser(r"~\AppData\SCU3"),
    ]
    for p in common:
        if os.path.isfile(os.path.join(p, "server.py")):
            return p

    return ""


def is_port_open(host: str = HOST, port: int = PORT, timeout: float = 0.5) -> bool:
    """检测端口是否可连接"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def is_http_ready(url: str = f"http://{HOST}:{PORT}{HEALTH_PATH}", timeout: float = 1.0) -> bool:
    """检测 HTTP 服务是否返回响应"""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, ConnectionError, OSError):
        return False


def wait_for_service() -> bool:
    """等待服务就绪"""
    print(f"[启动器] 等待后端服务就绪（最长 {STARTUP_TIMEOUT} 秒）...")
    start_time = time.time()

    while time.time() - start_time < STARTUP_TIMEOUT:
        if is_port_open():
            print(f"[启动器] 端口 {PORT} 已开放")
            break
        time.sleep(0.3)
    else:
        print(f"[启动器] 错误：端口 {PORT} 在 {STARTUP_TIMEOUT} 秒内未开放")
        return False

    # HTTP 探测（额外等待最多 10 秒）
    http_start = time.time()
    while time.time() - http_start < 10:
        if is_http_ready():
            print("[启动器] HTTP 服务已就绪")
            return True
        time.sleep(0.5)

    print("[启动器] 警告：HTTP 探测未确认，但端口已开放，尝试打开浏览器")
    return True


def open_browser():
    """打开前端浏览器"""
    print(f"[启动器] 正在打开浏览器：{BROWSER_URL}")
    try:
        webbrowser.open(BROWSER_URL, new=2)
    except Exception as e:
        print(f"[启动器] 打开浏览器失败：{e}")
        print(f"[启动器] 请手动访问：{BROWSER_URL}")


def stream_output(process: subprocess.Popen):
    """实时输出子进程的标准输出"""
    try:
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
    except Exception:
        pass


def main():
    print("=" * 60)
    print("  SCU3 一键启动器")
    print("  Smart Computing Unit 2 - One-Click Launcher")
    print("=" * 60)
    print(f"[启动器] 运行模式: {'打包版(exe)' if FROZEN else '开发版(python)'}")
    print(f"[启动器] 启动器目录: {BASE_DIR}")

    # 检查端口是否已被占用（避免重复启动）
    if is_port_open():
        print(f"[启动器] 端口 {PORT} 已被占用，服务可能已在运行")
        print("[启动器] 直接打开浏览器...")
        open_browser()
        input("按回车键退出...")
        return 0

    # 查找 Python
    python_path = find_python()
    if not python_path:
        print("[启动器] 错误：未找到系统 Python，请先安装 Python 3.8+")
        print("[启动器] 下载地址：https://www.python.org/downloads/")
        input("按回车键退出...")
        return 1

    # 查找 SCU3 项目目录
    project_dir = find_SCU3_project(python_path)
    if not project_dir:
        if FROZEN:
            print("[启动器] 错误：未找到 SCU3 项目（server.py）")
            print("[启动器] 请将本启动器放到 SCU3 项目根目录（包含 server.py 的目录）")
        else:
            print(f"[启动器] 错误：在 {BASE_DIR} 未找到 server.py")
        input("按回车键退出...")
        return 1

    print(f"[启动器] SCU3 项目目录: {project_dir}")
    print(f"[启动器] Python: {python_path}")

    # 检查 uvicorn 是否安装
    try:
        result = subprocess.run(
            [python_path, "-c", "import uvicorn; print(uvicorn.__version__)"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            print("[启动器] uvicorn 未安装，正在安装...")
            subprocess.run([python_path, "-m", "pip", "install", "uvicorn", "fastapi"],
                           check=True)
    except Exception as e:
        print(f"[启动器] 检查 uvicorn 失败：{e}")

    # 启动后端服务
    # 安全：默认监听127.0.0.1，避免局域网暴露；如需远程访问通过SCU3_HOST环境变量配置
    bind_host = os.environ.get("SCU3_HOST", "127.0.0.1")
    print(f"[启动器] 正在启动后端服务（uvicorn server:app @ {bind_host}:{PORT}）...")
    if bind_host in ("0.0.0.0", "::"):
        print(f"[启动器] ⚠️ 警告：监听 {bind_host} 将暴露至所有网卡，仅开发测试用！")
    backend_cmd = [
        python_path, "-m", "uvicorn",
        "server:app",
        "--host", bind_host,
        "--port", str(PORT),
    ]

    try:
        backend_process = subprocess.Popen(
            backend_cmd,
            cwd=project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except Exception as e:
        print(f"[启动器] 启动后端失败：{e}")
        input("按回车键退出...")
        return 1

    print(f"[启动器] 后端进程 PID: {backend_process.pid}")

    # 启动日志输出线程
    log_thread = threading.Thread(
        target=stream_output,
        args=(backend_process,),
        daemon=True,
    )
    log_thread.start()

    # 等待服务就绪
    if not wait_for_service():
        print("[启动器] 后端服务启动失败，正在终止进程...")
        backend_process.terminate()
        try:
            backend_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_process.kill()
        input("按回车键退出...")
        return 1

    # 打开前端浏览器
    open_browser()

    print("\n" + "=" * 60)
    print("  SCU3 服务已启动")
    print(f"  访问地址: {BROWSER_URL}")
    print("  按 Ctrl+C 或关闭此窗口可停止服务")
    print("=" * 60 + "\n")

    # 监控后端进程
    try:
        while backend_process.poll() is None:
            time.sleep(0.5)
        exit_code = backend_process.returncode
        print(f"\n[启动器] 后端服务已退出（返回码：{exit_code}）")
    except KeyboardInterrupt:
        print("\n[启动器] 收到退出信号，正在关闭后端服务...")
        backend_process.terminate()
        try:
            backend_process.wait(timeout=5)
            print("[启动器] 后端服务已优雅关闭")
        except subprocess.TimeoutExpired:
            print("[启动器] 后端未响应终止信号，强制结束")
            backend_process.kill()

    input("\n按回车键退出...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
