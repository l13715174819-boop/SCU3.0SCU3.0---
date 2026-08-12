# -*- coding: utf-8 -*-
"""HTTP 级验证：测试迁移到 api/{system,mcp,modules,memory,distributed}.py 的路由。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server
from fastapi.testclient import TestClient

# 注入全局单例（绕过 startup 事件）
from api.deps import set_globals
set_globals(
    ledger=server.ledger,
    whitelist=server.whitelist,
    guard=server.guard,
    metacog=server.metacog,
    memory=server.memory,
    api_key=server._get_configured_api_key(),
    admin_key=server._get_configured_admin_key(),
)

client = TestClient(server.app)
api_key = server._get_configured_api_key()
admin_key = server._get_configured_admin_key()
headers = {"X-API-Key": api_key}
admin_headers = {"X-API-Key": admin_key}

passed = 0
failed = 0


def test(name, method, path, expected_status=200, **kwargs):
    global passed, failed
    resp = getattr(client, method)(path, **kwargs)
    ok = resp.status_code == expected_status
    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"[{status}] {name}: {method.upper()} {path} -> {resp.status_code}")
    if not ok:
        print(f"  expected {expected_status}: {resp.text[:200]}")


# ─── system.py ────────────────────────────────
test("health", "get", "/health", 200)
test("index", "get", "/", 200)
test("help", "get", "/help", 200)
test("favicon", "get", "/favicon.ico", 200)
test("status_admin", "get", "/status", 200, headers=admin_headers)
test("status_no_auth_403", "get", "/status", 403)  # 管理员端点返回 403
test("self_check_admin", "get", "/self-check", 200, headers=admin_headers)

# ─── mcp.py ────────────────────────────────
test("mcp_tools", "get", "/mcp/tools", 200, headers=headers)
test("mcp_health", "get", "/mcp/health", 200, headers=headers)

# ─── modules.py ────────────────────────────────
test("modules_list", "get", "/modules", 200, headers=headers)
test("modules_status", "get", "/modules/status", 200, headers=headers)

# ─── memory.py ────────────────────────────────
test("memory_stats", "get", "/memory/stats", 200, headers=headers)
test("memory_health", "get", "/memory/health", 200, headers=headers)
test("memory_search", "get", "/memory/search?query=test", 200, headers=headers)

# ─── distributed.py ────────────────────────────────
test("distributed_workers", "get", "/distributed/workers", 200, headers=headers)
test("distributed_health", "get", "/distributed/health", 200, headers=headers)
test("distributed_status", "get", "/distributed/status", 200, headers=headers)

# ─── ledger.py (回归)────────────────────────────────
test("ledger_balance", "get", "/ledger/balance", 200, headers=headers)

# ─── 未迁移路由回归（确认未受影响）─────────────────────
test("pair_status", "get", "/pair/status", 200)  # 仍在 server.py
test("agent_status", "get", "/agent/status", 200, headers=headers)  # 仍在 server.py

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
sys.exit(1 if failed > 0 else 0)
