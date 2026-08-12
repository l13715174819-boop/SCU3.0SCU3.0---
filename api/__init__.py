# -*- coding: utf-8 -*-
"""api/ — HTTP 路由按域拆分

每个域文件定义一个 APIRouter，由 server.py 统一 include_router 装配。
依赖注入：通过 deps.py 提供的 Depends 闭包访问全局单例（ledger/guard/...）。
"""
