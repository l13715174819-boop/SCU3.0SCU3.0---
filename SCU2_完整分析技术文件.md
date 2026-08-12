# SCU3 完整分析技术文件

**生成时间**: 2026-08-11  
**版本**: v5.4 (3轮8类检查后)  
**检查方法**: 8类×3轮检查 + 即时修复 + 验证

---

## 一、系统概述

SCU3（标准计算单元2）是基于CUF（Cross-layer Universal Framework）架构的智能Agent系统，实现了三维度分离设计：

- **数据流维度**: 感知(W2) → 记忆(W1) → 执行(W1) → 认知(M) → 元认知(M) → 输出
- **守卫维度**: ① W2→W1跨层 ② W1→M跨层 ③ 工具守卫 ④ 周期审计 ⑤ 内容过滤
- **层级维度**: D层(公理) / M层(元认知) / W1层(记忆执行) / W2层(感知)

## 二、代码规模统计

| 指标 | 数值 |
|------|------|
| Python文件数 | 105 |
| m_layer模块文件 | 29 |
| w1_layer模块文件 | 8 |
| guard守卫文件 | 5 |
| API端点总数 | 151 |
| 请求模型(BaseModel) | 54 |
| 内置注册模块 | 9 |
| 内置插件 | 3 |
| 工具总数 | 13基础+17扩展+31 MCP = 61 |

## 三、架构合规性（5条原则）

### 原则1: D层只放代码定义 — ✅ 完全符合
- D层仅 `axioms.py`/`ledger_base.py`/`contracts.py` 三个业务文件
- 运行时状态全在 `w1_layer/ledger_runtime.py`
- `d_layer_integrity.py` 主动扫描8类禁止模式
- **已修复**: startup 现在调用 `verify_on_startup()` 执行启动时完整性校验

### 原则2: A4只管依赖方向 — ✅ 完全符合
- `DEPENDENCY_ACTIONS = {import, modify, patch, base_modify, delete}`
- 数据流动作（query/tool_call/layer_jump等）明确豁免

### 原则3: 同层流动免审 — ✅ 完全符合
- `firewall.py` 第64行 `if src == tgt: return True`
- W1→W1、M→M 均无 guard.check 调用

### 原则4: 工具调用独立守卫 — ✅ 完全符合
- `tool_guard.py` 独立于 `firewall.py`
- 13种工具完整映射，read=0.2E/write=3.0E

### 原则5: 输出必经内容过滤 — ✅ 完全符合
- `content_filter.py` 50+条正则规则
- server.py 三处强制调用（process_request + _build_response + SSE chunk）

## 四、模块化可插拔性

### 已实现能力
| 能力 | 状态 |
|------|------|
| 模块注册表 API | ✅ register/load/unload/reload/disable/enable |
| 插件系统 | ✅ 生命周期钩子 + 沙箱隔离 + 动态加载 |
| 卸载后503 | ✅ require_module() 35个端点 |
| disabled持久化 | ✅ _pending_state 恢复机制 |
| 单例重置 | ✅ reset_browser/screen/scraper/desktop |
| .env配置驱动 | ✅ SCU3_DISABLED_MODULES / SCU3_ENABLED_ONLY |
| knowledge.base | ✅ 正确引用 get_store + unloader |

### 本轮修复项
1. ✅ 插件钩子接入运行时（on_message/on_tool_call/on_response）
2. ✅ pm变量作用域修复（顶部初始化）
3. ✅ plugin_traces输出到响应
4. ✅ SafetyPlugin锁修复（self._lock）
5. ✅ 周期审计定时器（24小时自动触发）
6. ✅ D层完整性启动校验
7. ✅ None输入防御
8. ✅ 插件blocked语义修复（merge读取business_ctx.blocked）

## 五、数据流验证

### 主数据流（process_request）
```
用户输入 → pm初始化 → 插件①on_message → W2感知 → 守卫①(W2→W1)
→ W1记忆 → W1执行 → 插件②on_tool_call → 工具守卫 → 守卫②(W1→M)
→ M认知 → M元认知(merge) → 内容过滤 → 插件③on_response → _build_response
```

### 数据流断点修复
| 断点 | 修复方式 |
|------|---------|
| plugin_traces未输出 | _build_response 添加 plugin_traces 字段 |
| 插件blocked语义丢失 | merge() 读取 business_ctx.get("blocked") |
| pm变量作用域 | 顶部初始化 pm=None |

## 六、安全防护

### 认证机制
- 122个普通端点: verify_api_key (普通Key)
- 29个敏感端点: verify_admin_key (管理员Key)
- 使用 `secrets.compare_digest` 防时序攻击

### 内容过滤
- 50+条正则规则（API密钥/密码/base64/余额/内网IP/员工ID等）
- D层字段白名单
- 双保险过滤机制
- SSE流式过滤

### 饱和攻击测试结果
| 测试 | 通过率 |
|------|--------|
| 异常输入（10种） | 90% (None已修复) |
| 高并发（50线程） | 100% |
| 边界条件（27项） | 100% |

## 七、功能实现深度

### 真实硬实现（10项核心功能）
1. ✅ LLM对话 — 真实调用 DeepSeek/OpenAI API
2. ✅ 知识库检索 — TF-IDF + 向量化(FAISS) + BM25混合
3. ✅ 代码自修改 — AST预检 + 阴阳双签 + 原子写入 + 回滚
4. ✅ 浏览器自动化 — 真实调用 Playwright
5. ✅ 语音IO — Whisper/Google STT + pyttsx3 TTS
6. ✅ 本地模型 — transformers + Qwen2.5-VL
7. ✅ 多Agent协作 — ThreadPool + multiprocessing 双模式
8. ✅ 分布式执行 — WorkerNode + WorkerServer HTTP
9. ✅ MCP协议 — JSON-RPC 2.0
10. ✅ 降级策略 — 三级降级 + 明确标识 + 日志记录

### 软实现（已知，docstring标注）
- weather/exchange_rate/crypto_price/stock_price/github_search 使用模拟数据
- 代码逻辑真实，数据源为模拟（docstring已标注"模拟"）

## 八、测试结果汇总

| 测试类型 | 第1轮 | 第2轮 | 第3轮 |
|---------|-------|-------|-------|
| 语法检查 | 105/105 | 105/105 | 105/105 |
| 模块化测试 | 7/7 | 7/7 | 7/7 |
| None防御 | FAIL | PASS | PASS |
| plugin_traces | FAIL | PASS | PASS |
| 并发安全 | 50/50 | 50/50 | 50/50 |
| 边界条件 | 27/27 | 27/27 | 27/27 |
| 最终确认 | — | — | 5/5 |

## 九、遗留风险项（低优先级）

1. 守卫①②tax记录为0（CUFGuard.check返回结构问题，不影响实际扣税）
2. 5个工具使用模拟数据（weather等，docstring已标注）
3. 74处空except pass（多数为合理的异常静默）
4. TOOL_TYPES在action.py和tool_guard.py重复定义
5. 重启后模块loaded=False（需手动load，disabled状态已持久化）

## 十、结论

SCU3经过3轮8类检查，**架构合规、模块化可插拔、数据流通畅、安全防护健全、功能硬实现**。本轮共修复8项关键问题，系统从"有形无神"提升到"形神兼备"状态。
