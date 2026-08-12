# -*- coding: utf-8 -*-
"""临时验证脚本：导入 server.py，递归遍历路由（含 _IncludedRouter.original_router）。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _collect_routes(app):
    """递归收集所有路由

    FastAPI 0.141+ 使用 _IncludedRouter 包装 include_router 的路由，
    其子路由位于 .original_router.routes 而非 .routes。
    """
    result = []
    for route in app.routes:
        cls = type(route).__name__
        # _IncludedRouter: 子路由在 original_router.routes
        if cls == "_IncludedRouter":
            orig = getattr(route, "original_router", None)
            if orig is not None:
                for sub in orig.routes:
                    path = getattr(sub, "path", None)
                    if path:
                        result.append(path)
        # Mount: 子路由在 routes
        elif hasattr(route, "routes") and getattr(route, "routes", None):
            for sub in route.routes:
                path = getattr(sub, "path", None)
                if path:
                    result.append(path)
        else:
            path = getattr(route, "path", None)
            if path:
                result.append(path)
    return result


try:
    import server
    routes = _collect_routes(server.app)
    print(f"[OK] server.py imported successfully")
    print(f"[INFO] Total routes (recursive): {len(routes)}")

    # 关键路由核对
    must_have = [
        "/cuf/check",
        "/ledger/balance",
        "/ledger/replenish",
        "/cuf/activity",
        "/chat",
        "/chat/stream",
        "/health",
        "/status",
        "/agent/run",
        "/agent/plan",
        "/agent/execute",
        "/plugins",
        "/modules",
        "/memory/stats",
        "/mcp/tools",
    ]
    missing = [p for p in must_have if p not in routes]
    if missing:
        print(f"[FAIL] Missing routes: {missing}")
        sys.exit(1)
    else:
        print(f"[OK] All {len(must_have)} key routes present")

    # 重复路由检测
    from collections import Counter
    dupes = [p for p, c in Counter(routes).items() if c > 1]
    if dupes:
        print(f"[WARN] Duplicate routes ({len(dupes)}): {dupes[:10]}")
    else:
        print(f"[OK] No duplicate routes")

    # 域分布
    domains = {}
    for p in routes:
        if p in ("/", "/favicon.ico", "/@vite/client"):
            continue
        parts = p.strip("/").split("/")
        domain = parts[0] if parts and parts[0] else "root"
        domains[domain] = domains.get(domain, 0) + 1
    print(f"\n[INFO] Domain distribution:")
    for d in sorted(domains, key=lambda x: -domains[x])[:20]:
        print(f"  {d}: {domains[d]}")

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"[FAIL] Import error: {e}")
    sys.exit(1)
