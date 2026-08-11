# -*- coding: utf-8 -*-
"""
tools/dependency_scanner.py — A4依赖关系扫描器
==============================================
原则二落地：A4只管依赖方向（D←M←W1←W2）。

用途：
  - CI/CD集成，构建时扫描import关系
  - 发现"底层import顶层"的A4违规
  - 发现"非白名单反向调用"的A4违规

使用：
  python tools/dependency_scanner.py [--root scu2/]
"""
import os
import sys
import ast
import json
import logging
from typing import List, Dict, Tuple

logger = logging.getLogger("scu2.tools.dep_scanner")

# 层级优先级（D=0最低，W2=3最高）
# A4规则：低层不得import高层（依赖方向反向）
LAYER_PRIORITY = {
    "d_layer": 0,
    "m_layer": 1,
    "w1_layer": 2,
    "w2_layer": 3,
}

# A4白名单动作（允许的反向调用）
A4_WHITELIST = {"self_modify", "tool_call", "check", "inspect"}

# 依赖类动作（触发A4校验）
DEPENDENCY_ACTIONS = {"import", "modify", "patch", "base_modify", "delete"}


def detect_layer(filepath: str) -> str:
    """从文件路径推断所属层"""
    parts = filepath.replace("\\", "/").split("/")
    for part in parts:
        if part in LAYER_PRIORITY:
            return part
    return ""


def detect_layer_from_module(module: str) -> str:
    """从import模块名推断层"""
    if not module:
        return ""
    # 如 "d_layer.axioms" → "d_layer"
    first = module.split(".")[0]
    return first if first in LAYER_PRIORITY else ""


def scan_file_imports(filepath: str) -> List[Dict]:
    """扫描单个文件的import关系"""
    violations = []
    src_layer = detect_layer(filepath)
    if not src_layer:
        return violations

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=filepath)
    except Exception as e:
        logger.warning(f"解析失败 {filepath}: {e}")
        return violations

    for node in ast.walk(tree):
        # import xxx
        if isinstance(node, ast.Import):
            for alias in node.names:
                tgt_layer = detect_layer_from_module(alias.name)
                if tgt_layer and tgt_layer != src_layer:
                    violations.append({
                        "type": "import",
                        "file": filepath,
                        "src_layer": src_layer,
                        "tgt_layer": tgt_layer,
                        "module": alias.name,
                        "line": node.lineno,
                    })
        # from xxx import yyy
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tgt_layer = detect_layer_from_module(node.module)
                if tgt_layer and tgt_layer != src_layer:
                    violations.append({
                        "type": "from_import",
                        "file": filepath,
                        "src_layer": src_layer,
                        "tgt_layer": tgt_layer,
                        "module": node.module,
                        "line": node.lineno,
                    })
    return violations


def check_a4_violation(violation: Dict) -> Tuple[bool, str]:
    """判断单个import是否违反A4

    Returns:
        (is_violation, reason)
    """
    src = violation["src_layer"]
    tgt = violation["tgt_layer"]
    src_pri = LAYER_PRIORITY.get(src, 0)
    tgt_pri = LAYER_PRIORITY.get(tgt, 0)

    # 低层import高层 = 依赖方向反向 = A4违规
    if src_pri < tgt_pri:
        return True, (
            f"A4违规: {src}(priority={src_pri}) → {tgt}(priority={tgt_pri}) "
            f"依赖方向反向 ({violation['file']}:{violation['line']} "
            f"import {violation['module']})"
        )
    return False, ""


def scan_directory(root_dir: str) -> Dict:
    """扫描整个目录的A4违规

    Returns:
        {
            "total_files": int,
            "total_imports": int,
            "violations": List[Dict],
            "cross_layer_imports": List[Dict],
            "passed": bool,
        }
    """
    results = {
        "total_files": 0,
        "total_imports": 0,
        "violations": [],
        "cross_layer_imports": [],
        "passed": True,
    }

    for dirpath, _, filenames in os.walk(root_dir):
        # 跳过非代码目录
        if "__pycache__" in dirpath or ".git" in dirpath:
            continue
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            results["total_files"] += 1

            imports = scan_file_imports(fpath)
            for imp in imports:
                results["total_imports"] += 1
                results["cross_layer_imports"].append(imp)

                is_violation, reason = check_a4_violation(imp)
                if is_violation:
                    imp["reason"] = reason
                    results["violations"].append(imp)
                    results["passed"] = False
                    logger.error(f"🚨 {reason}")

    return results


def generate_report(results: Dict, output_path: str = "") -> str:
    """生成扫描报告"""
    report = []
    report.append("=" * 60)
    report.append("A4 依赖关系扫描报告")
    report.append("=" * 60)
    report.append(f"扫描文件数: {results['total_files']}")
    report.append(f"跨层import数: {len(results['cross_layer_imports'])}")
    report.append(f"A4违规数: {len(results['violations'])}")
    report.append(f"扫描结果: {'✓ 通过' if results['passed'] else '✗ 有违规'}")
    report.append("")

    if results["violations"]:
        report.append("违规详情:")
        for v in results["violations"]:
            report.append(f"  - {v['reason']}")
        report.append("")

    if results["cross_layer_imports"]:
        report.append("跨层import清单（含合规的）:")
        for imp in results["cross_layer_imports"]:
            status = "✗违规" if "reason" in imp else "✓合规"
            report.append(
                f"  {status} {imp['src_layer']}→{imp['tgt_layer']} "
                f"{imp['file']}:{imp['line']} ({imp['module']})"
            )

    report_text = "\n".join(report)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_text)
    return report_text


def main():
    """命令行入口"""
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    root = os.path.abspath(root)
    print(f"扫描目录: {root}")
    results = scan_directory(root)
    report = generate_report(results)
    print(report)
    sys.exit(0 if results["passed"] else 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
