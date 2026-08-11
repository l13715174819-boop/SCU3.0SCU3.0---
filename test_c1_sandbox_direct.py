# -*- coding: utf-8 -*-
"""C1沙箱AST预检直接验证（不经过HTTP，直接调用）"""
import sys
sys.path.insert(0, '.')
from w1_layer.action import ActionLayer

action = ActionLayer()
print("=" * 60)
print("C1沙箱AST预检直接验证")
print("=" * 60)

# 测试逃逸代码（应被AST预检拦截）
escape_codes = [
    ("().__class__.__bases__[0].__subclasses__()", "subclass逃逸"),
    ("''.__class__.__mro__[-1].__subclasses__()", "mro逃逸"),
    ("type(1).__subclasses__()", "type逃逸"),
    ("__import__('os')", "__import__逃逸"),
    ("getattr(__builtins__, 'eval')", "getattr逃逸"),
    ("eval('1+1')", "eval调用"),
    ("exec(\"import os\")", "exec调用"),
    ("import os", "import语句"),
    ("x = 1; x.__class__", "变量属性访问"),
]

print("\n── 逃逸代码拦截测试 ──")
for code, desc in escape_codes:
    result = action._sandbox_exec(code)
    blocked = "安全拦截" in result.get("error", "") or "禁止" in result.get("error", "")
    status = "PASS" if blocked else "FAIL"
    print(f"  [{status}] {desc}: error={result.get('error', '')[:50]}")

# 正常代码应可执行
print("\n── 正常代码执行测试 ──")
normal_codes = [
    ("1+1", "简单算术"),
    ("print('hello')", "print输出"),
    ("sum([1,2,3])", "sum函数"),
    ("[x*2 for x in range(5)]", "列表推导"),
    ("len('hello')", "len函数"),
]
for code, desc in normal_codes:
    result = action._sandbox_exec(code)
    ok = not result.get("error") or "安全拦截" not in result.get("error", "")
    status = "PASS" if ok else "FAIL"
    output = result.get("output", "") or str(result.get("result", ""))
    print(f"  [{status}] {desc}: output={output[:30]} error={result.get('error','')[:30]}")

print("\n" + "=" * 60)
