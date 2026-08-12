# -*- coding: utf-8 -*-
"""
w1_layer/extended_tools.py — 扩展工具集（W1层）
================================================
v5.0第一批：扩展工具种类 + 真实网络搜索

新增工具:
  read类:
    - web_search: 真实网络搜索（DuckDuckGo无Key）
    - web_fetch: 抓取网页内容
    - git_status/git_log/git_diff: Git操作
    - pdf_read: PDF文本提取
    - image_info: 图片信息
    - json_query: JSON数据查询
    - regex_match: 正则匹配
    - hash_calc: 哈希计算
    - base64_codec: Base64编解码
    - url_codec: URL编解码
  write类:
    - shell_exec: Shell命令执行（沙箱）
    - file_copy: 文件复制
    - file_move: 文件移动
    - file_delete: 文件删除
    - dir_create: 创建目录
    - dir_list: 列出目录

架构归属：W1层（执行层扩展）
"""
import os
import re
import json
import base64
import hashlib
import urllib.parse
import subprocess
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger("SCU3.w1.ext_tools")

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "SCU3_data")
SANDBOX_DIR = os.path.join(DATA_DIR, "sandbox")
os.makedirs(SANDBOX_DIR, exist_ok=True)


class ExtendedTools:
    """扩展工具集"""

    # 工具类型映射
    TOOL_TYPES = {
        "web_search": "read",
        "web_fetch": "read",
        "git_status": "read",
        "git_log": "read",
        "git_diff": "read",
        "pdf_read": "read",
        "image_info": "read",
        "json_query": "read",
        "regex_match": "read",
        "hash_calc": "read",
        "base64_codec": "read",
        "url_codec": "read",
        "shell_exec": "write",
        "file_copy": "write",
        "file_move": "write",
        "file_delete": "write",
        "dir_create": "write",
        "dir_list": "read",
    }

    # 安全的shell命令白名单（防止危险操作）
    SAFE_SHELL_COMMANDS = {
        "dir", "ls", "type", "cat", "echo", "find", "where", "which",
        "python --version", "git --version", "node --version",
        "pip list", "pip show",
    }

    def __init__(self):
        self._tools = {
            "web_search": self._tool_web_search,
            "web_fetch": self._tool_web_fetch,
            "git_status": self._tool_git_status,
            "git_log": self._tool_git_log,
            "git_diff": self._tool_git_diff,
            "pdf_read": self._tool_pdf_read,
            "image_info": self._tool_image_info,
            "json_query": self._tool_json_query,
            "regex_match": self._tool_regex_match,
            "hash_calc": self._tool_hash_calc,
            "base64_codec": self._tool_base64,
            "url_codec": self._tool_url_codec,
            "shell_exec": self._tool_shell_exec,
            "file_copy": self._tool_file_copy,
            "file_move": self._tool_file_move,
            "file_delete": self._tool_file_delete,
            "dir_create": self._tool_dir_create,
            "dir_list": self._tool_dir_list,
        }

    def execute(self, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行扩展工具"""
        if tool not in self._tools:
            return {"success": False, "error": f"未知工具: {tool}"}
        try:
            result = self._tools[tool](**params)
            return {"success": True, "tool": tool, "result": result,
                    "tool_type": self.TOOL_TYPES.get(tool, "read")}
        except Exception as e:
            return {"success": False, "tool": tool, "error": str(e)}

    # ─── 网络工具 ────────────────────────────────────

    def _tool_web_search(self, query: str, max_results: int = 5) -> Dict:
        """真实网络搜索（DuckDuckGo HTML解析，无需API Key）"""
        import urllib.request
        import urllib.parse as urlparse

        encoded_query = urlparse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode("utf-8", errors="ignore")

            # 解析搜索结果
            results = []
            # DuckDuckGo HTML结果链接
            links = re.findall(r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.S)
            for link, title in links[:max_results]:
                # 清理HTML标签
                clean_title = re.sub(r'<[^>]+>', '', title).strip()
                # DuckDuckGo的链接是重定向
                if "uddg=" in link:
                    actual_url = urlparse.unquote(
                        re.search(r'uddg=([^&]+)', link).group(1)
                    )
                else:
                    actual_url = link
                results.append({"title": clean_title, "url": actual_url})

            # 提取摘要
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>', html, re.S)
            for i, snippet in enumerate(min(len(results), len(snippets))):
                if i < len(snippets):
                    results[i]["snippet"] = re.sub(r'<[^>]+>', '', snippets[i]).strip()

            if not results:
                return {"query": query, "results": [], "note": "无搜索结果或网络不可用"}

            return {"query": query, "results": results, "count": len(results)}
        except Exception as e:
            logger.warning(f"网络搜索失败: {e}")
            return {"query": query, "results": [], "error": f"搜索失败: {e}"}

    def _tool_web_fetch(self, url: str, max_length: int = 5000) -> Dict:
        """抓取网页内容"""
        import urllib.request

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                content_type = response.headers.get("Content-Type", "")
                raw = response.read()

                # 检测编码
                charset = "utf-8"
                if "charset=" in content_type:
                    charset = content_type.split("charset=")[-1].strip()

                text = raw.decode(charset, errors="ignore")

                # 如果是HTML，简单清理
                if "html" in content_type.lower():
                    # 移除script和style
                    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.S)
                    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.S)
                    # 移除标签
                    text = re.sub(r'<[^>]+>', ' ', text)
                    # 压缩空白
                    text = re.sub(r'\s+', ' ', text).strip()

                return {
                    "url": url,
                    "content": text[:max_length],
                    "length": len(text),
                    "truncated": len(text) > max_length,
                    "content_type": content_type,
                }
        except Exception as e:
            return {"url": url, "content": "", "error": f"抓取失败: {e}"}

    # ─── Git工具 ────────────────────────────────────

    def _tool_git_status(self, repo_path: str = ".") -> Dict:
        """Git状态查询"""
        return self._git_command(["status", "--porcelain"], repo_path)

    def _tool_git_log(self, repo_path: str = ".", limit: int = 10) -> Dict:
        """Git日志查询"""
        return self._git_command(
            ["log", f"--max-count={limit}", "--oneline", "--format=%H|%an|%ad|%s"],
            repo_path
        )

    def _tool_git_diff(self, repo_path: str = ".", file: str = "") -> Dict:
        """Git差异查询"""
        cmd = ["diff"]
        if file:
            cmd.append(file)
        return self._git_command(cmd, repo_path)

    def _git_command(self, args: List[str], repo_path: str) -> Dict:
        """执行git命令"""
        try:
            full_path = os.path.join(BASE_DIR, repo_path) if not os.path.isabs(repo_path) else repo_path
            result = subprocess.run(
                ["git"] + args,
                cwd=full_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return {"error": result.stderr.strip(), "output": ""}
            return {"output": result.stdout.strip(), "repo": repo_path}
        except FileNotFoundError:
            return {"error": "git未安装", "output": ""}
        except subprocess.TimeoutExpired:
            return {"error": "git命令超时", "output": ""}
        except Exception as e:
            return {"error": str(e), "output": ""}

    # ─── 文件处理工具 ────────────────────────────────────

    def _tool_pdf_read(self, path: str, max_pages: int = 50) -> Dict:
        """PDF文本提取"""
        full_path = self._safe_path(path)
        if not full_path or not os.path.exists(full_path):
            return {"path": path, "text": "", "error": "文件不存在"}

        try:
            # 尝试用PyPDF2
            from PyPDF2 import PdfReader
            reader = PdfReader(full_path)
            texts = []
            for i, page in enumerate(reader.pages[:max_pages]):
                texts.append(page.extract_text() or "")
            return {
                "path": path,
                "text": "\n".join(texts),
                "pages": len(reader.pages),
                "extracted_pages": min(max_pages, len(reader.pages)),
            }
        except ImportError:
            # 尝试pdfplumber
            try:
                import pdfplumber
                with pdfplumber.open(full_path) as pdf:
                    texts = []
                    for i, page in enumerate(pdf.pages[:max_pages]):
                        texts.append(page.extract_text() or "")
                    return {
                        "path": path,
                        "text": "\n".join(texts),
                        "pages": len(pdf.pages),
                    }
            except ImportError:
                return {"path": path, "text": "", "error": "需要安装PyPDF2或pdfplumber"}
        except Exception as e:
            return {"path": path, "text": "", "error": str(e)}

    def _tool_image_info(self, path: str) -> Dict:
        """图片信息"""
        full_path = self._safe_path(path)
        if not full_path or not os.path.exists(full_path):
            return {"path": path, "error": "文件不存在"}

        try:
            from PIL import Image
            img = Image.open(full_path)
            return {
                "path": path,
                "format": img.format,
                "size": img.size,
                "mode": img.mode,
                "file_size": os.path.getsize(full_path),
            }
        except ImportError:
            # 无PIL时返回基本信息
            return {
                "path": path,
                "file_size": os.path.getsize(full_path),
                "note": "安装Pillow获取更多信息",
            }
        except Exception as e:
            return {"path": path, "error": str(e)}

    # ─── 数据处理工具 ────────────────────────────────────

    def _tool_json_query(self, json_str: str, query: str = "") -> Dict:
        """JSON数据查询（支持点号路径）"""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return {"error": f"JSON解析失败: {e}"}

        if not query:
            return {"data": data}

        # 点号路径查询: a.b.c
        parts = query.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return {"error": f"数组索引无效: {part}"}
            else:
                return {"error": f"路径无效: {part}"}
            if current is None:
                return {"error": f"路径不存在: {part}"}

        return {"query": query, "result": current}

    def _tool_regex_match(self, text: str, pattern: str, flags: str = "") -> Dict:
        """正则匹配"""
        flag_map = {
            "i": re.I, "m": re.M, "s": re.S, "x": re.X,
        }
        re_flags = 0
        for f in flags.lower():
            re_flags |= flag_map.get(f, 0)

        matches = re.findall(pattern, text, re_flags)
        return {
            "pattern": pattern,
            "match_count": len(matches),
            "matches": matches[:50],  # 最多返回50个
        }

    def _tool_hash_calc(self, text: str, algorithm: str = "md5") -> Dict:
        """哈希计算"""
        algo = algorithm.lower()
        if algo not in hashlib.algorithms_available:
            return {"error": f"不支持的算法: {algo}"}
        h = hashlib.new(algo)
        h.update(text.encode("utf-8"))
        return {"algorithm": algo, "hash": h.hexdigest(), "input_length": len(text)}

    def _tool_base64(self, text: str, mode: str = "encode") -> Dict:
        """Base64编解码"""
        if mode == "encode":
            encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
            return {"mode": "encode", "result": encoded}
        else:
            try:
                decoded = base64.b64decode(text).decode("utf-8")
                return {"mode": "decode", "result": decoded}
            except Exception as e:
                return {"mode": "decode", "error": str(e)}

    def _tool_url_codec(self, text: str, mode: str = "encode") -> Dict:
        """URL编解码"""
        if mode == "encode":
            return {"mode": "encode", "result": urllib.parse.quote(text, safe="")}
        else:
            return {"mode": "decode", "result": urllib.parse.unquote(text)}

    # ─── Shell执行（沙箱） ────────────────────────────────────

    def _tool_shell_exec(self, command: str, timeout: float = 10.0) -> Dict:
        """Shell命令执行（白名单+超时）"""
        # 安全检查：命令白名单
        cmd_lower = command.strip().lower()
        is_safe = False
        for safe_cmd in self.SAFE_SHELL_COMMANDS:
            if cmd_lower.startswith(safe_cmd):
                is_safe = True
                break

        # 允许在sandbox目录执行的命令
        if not is_safe:
            # 检查是否有危险操作
            dangerous = ["rm", "del", "rmdir", "format", "shutdown", "reboot",
                        "mkfs", "dd", ">", ">>", "|", "&"]
            if any(d in cmd_lower for d in dangerous):
                return {"command": command, "output": "", "error": "危险命令被拦截"}

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=SANDBOX_DIR,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"command": command, "output": "", "error": f"执行超时(>{timeout}s)"}
        except Exception as e:
            return {"command": command, "output": "", "error": str(e)}

    # ─── 文件操作工具 ────────────────────────────────────

    def _tool_file_copy(self, src: str, dst: str) -> Dict:
        """文件复制"""
        import shutil
        src_path = self._safe_path(src)
        dst_path = self._safe_path(dst)
        if not src_path or not dst_path:
            return {"error": "路径越界"}
        if not os.path.exists(src_path):
            return {"error": f"源文件不存在: {src}"}
        shutil.copy2(src_path, dst_path)
        return {"src": src, "dst": dst, "size": os.path.getsize(dst_path)}

    def _tool_file_move(self, src: str, dst: str) -> Dict:
        """文件移动"""
        import shutil
        src_path = self._safe_path(src)
        dst_path = self._safe_path(dst)
        if not src_path or not dst_path:
            return {"error": "路径越界"}
        if not os.path.exists(src_path):
            return {"error": f"源文件不存在: {src}"}
        shutil.move(src_path, dst_path)
        return {"src": src, "dst": dst}

    def _tool_file_delete(self, path: str) -> Dict:
        """文件删除（限sandbox）"""
        full_path = self._safe_path(path)
        if not full_path:
            return {"error": "路径越界"}
        if not os.path.exists(full_path):
            return {"error": f"文件不存在: {path}"}
        os.remove(full_path)
        return {"deleted": path}

    def _tool_dir_create(self, path: str) -> Dict:
        """创建目录"""
        full_path = self._safe_path(path)
        if not full_path:
            return {"error": "路径越界"}
        os.makedirs(full_path, exist_ok=True)
        return {"created": path}

    def _tool_dir_list(self, path: str = ".") -> Dict:
        """列出目录"""
        full_path = self._safe_path(path) or SANDBOX_DIR
        if not os.path.exists(full_path):
            return {"error": f"目录不存在: {path}"}
        entries = []
        for entry in os.listdir(full_path):
            entry_path = os.path.join(full_path, entry)
            entries.append({
                "name": entry,
                "type": "dir" if os.path.isdir(entry_path) else "file",
                "size": os.path.getsize(entry_path) if os.path.isfile(entry_path) else None,
            })
        return {"path": path, "entries": entries, "count": len(entries)}

    # ─── 路径安全 ────────────────────────────────────

    def _safe_path(self, path: str) -> Optional[str]:
        """安全路径检查"""
        if os.path.isabs(path):
            full = os.path.abspath(path)
        else:
            full = os.path.abspath(os.path.join(SANDBOX_DIR, path))
        try:
            common = os.path.commonpath([full, SANDBOX_DIR])
            if common != SANDBOX_DIR:
                return None
        except ValueError:
            return None
        return full


# ─── 单例 ────────────────────────────────────
_ext_tools_instance: Optional[ExtendedTools] = None


def get_extended_tools() -> ExtendedTools:
    """获取扩展工具集单例"""
    global _ext_tools_instance
    if _ext_tools_instance is None:
        _ext_tools_instance = ExtendedTools()
    return _ext_tools_instance
