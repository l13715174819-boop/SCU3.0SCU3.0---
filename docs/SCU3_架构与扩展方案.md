# SCU3.0 架构与扩展方案

> 版本：SCU3.0 · 架构：v3 三维度分离
> 生成日期：2026-08-11
> 文档定位：当前架构说明 + 后续扩展蓝图

---

## 一、整体架构概览

SCU3.0 采用 **CUF（Compute Unit Fabric）三维度分离架构**，将以下三个正交维度彻底解耦：

| 维度 | 含义 | 说明 |
|------|------|------|
| **数据流** | 业务数据传递方向 | 感知(W2) → 记忆(W1) → 执行(W1) → 认知(M) → 元认知(M) → 输出 |
| **依赖方向** | 代码层级的单向依赖 | D ← M ← W1 ← W2（A4 公理：依赖方向不可反向） |
| **守卫横切** | 安全审计的切面 | 5 个守卫点横切在数据流管道上 |

### 分层结构

```
┌─────────────────────────────────────────────┐
│  W2 层 — 感知入口                            │
│  · 意图识别（12+ 种）                        │
│  · 领域识别（hotel/product/medical/general） │
└──────────────┬──────────────────────────────┘
               │ W2→W1 跨层审计（守卫①）
┌──────────────▼──────────────────────────────┐
│  W1 层 — 工作层（记忆 + 执行，同层免审）      │
│  · 三级记忆（L1工作/L2语义/L3情景）           │
│  · 14 种工具 + 沙箱执行                       │
│  · 熵税账本运行时实例                          │
│  · RAG 知识库（TF-IDF / FAISS 向量）          │
└──────────────┬──────────────────────────────┘
               │ W1→M 跨层审计（守卫②）
┌──────────────▼──────────────────────────────┐
│  M 层 — 元认知/认知层                         │
│  · 认知层：多策略 LLM 推理                    │
│  · 阴阳对子思考（阴DeepSeek + 阳Qwen）        │
│  · 插件市场闭环 + 自进化                      │
│  · 元认知层：CUF 路径汇合 + 周期审计          │
│  · 分布式执行器 + 代码自修改                  │
└──────────────┬──────────────────────────────┘
               │
┌──────────────▼──────────────────────────────┐
│  D 层 — 基线层（只读代码定义）                │
│  · 四公理（A1基线不可变/A2熵税/A3契约/A4层级）│
│  · 四契约规范 + 账本抽象基类                  │
│  · MANIFEST 哈希基线                          │
└─────────────────────────────────────────────┘
```

### 核心设计原则

1. **D 层只读**：代码定义在 D 层（只读），运行时状态在 W1 层，消除"审计 D 层账本需写 D 层"的自指死循环
2. **同层免审**：W1→W1、M→M 同层流动免审，只有跨 CUF 层才审计
3. **阴阳双签**：阴方 DeepSeek（批判）+ 阳方 Qwen（支持）+ 太极合一
4. **分布式 + 插件市场闭环**：能力缺失 → 自动下载加载 → 重试 → 经验沉淀 → 自进化

---

## 二、分层详解

### 2.1 D 层 — 基线层（只读）

路径：`scu2/d_layer/`

| 文件 | 职责 |
|------|------|
| [axioms.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/d_layer/axioms.py) | 四公理 + 四契约枚举 + 层级定义 + `Operation`/`TaxBreakdown` 数据类 + 基础税率表 + 经济常量 |
| [contracts.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/d_layer/contracts.py) | 四契约详细规范 + `validate_contracts()` |
| [ledger_base.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/d_layer/ledger_base.py) | 账本抽象基类 `LedgerBase`（接口签名，无运行时状态） |
| [MANIFEST.json](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/d_layer/MANIFEST.json) | 文件清单 + 固化 `expected_hashes` + 禁止项 + 完整性校验配置 |

**四公理约束**：
- **A1 基线不可变性**：禁止 W2/M 修改 D 层代码
- **A2 熵税经济性**：跨层操作必须支付熵税
- **A3 契约闭环性**：高危动作必须携带四契约
- **A4 层级单向性**：依赖方向 D←M←W1←W2 单向（数据流不受 A4 约束）

**经济常量**（`axioms.py`）：
- `INITIAL_BUDGET = 1000.0`（初始预算）
- `MIN_BALANCE = 10.0`（保底余额）
- `MAX_SINGLE_TRANSACTION = 1000.0`（单笔上限）
- `MAX_TRANSACTION_PER_SECOND = 50`（限频）

### 2.2 M 层 — 元认知/认知层

路径：`scu2/m_layer/`

| 文件 | 职责 |
|------|------|
| [cognition.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/cognition.py) | 认知层核心：多策略综合注入 LLM、阴阳对子思考、兜底联网搜索、插件市场闭环 |
| [metacognition.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/metacognition.py) | 元认知层：业务路径与 CUF 路径汇合、周期审计、补偿退款 |
| [cognition_endorser.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/cognition_endorser.py) | 阴阳双签实现（继承 `YinYangEndorser`），按批判/支持词汇+分点论证+因果论证+字数评分 |
| [distributed_executor.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/distributed_executor.py) | 分布式执行器（Worker 节点管理、任务分片、负载均衡、结果合并） |
| [plugin_market.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/plugin_market.py) | 插件市场（能力匹配、自动安装加载、经验沉淀、TTL 卸载） |
| [code_self_modify.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/code_self_modify.py) | 代码自修改引擎（D 层保护清单、危险模式黑名单、阴阳双签+人工审批） |
| [self_evolution.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/self_evolution.py) | 自进化引擎（缺陷分析+提案生成，触发阈值 fail_count≥3） |
| [experience_store.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/experience_store.py) | 经验沉淀（衰减 30 天，成熟阈值 2 次） |
| [module_registry.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/module_registry.py) | 模块注册表（受保护模块不可卸载） |
| [llm_client.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/llm_client.py) | 多平台 LLM 客户端（DeepSeek/Qwen/本地 Qwen2.5） |

### 2.3 W1 层 — 工作层（记忆 + 执行）

路径：`scu2/w1_layer/`

| 文件 | 职责 |
|------|------|
| [ledger_runtime.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/w1_layer/ledger_runtime.py) | 熵税账本运行时（五维计税 base×depth×state×custom，保底余额限频 5次/小时，哈希链） |
| [action.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/w1_layer/action.py) | 执行层（14 种工具 + 降级链 + 沙箱 AST 预检 + 路径安全 `commonpath`） |
| [memory/](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/w1_layer/memory/) | 三级记忆（L1工作/L2语义/L3情景） |
| [knowledge_store.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/w1_layer/knowledge_store.py) | RAG 知识库（TF-IDF + 中文 2-gram 分词） |
| [vector_store.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/w1_layer/vector_store.py) | 向量版知识库（FAISS 索引 + 迁移工具） |

### 2.4 W2 层 — 感知入口

路径：`scu2/w2_layer/perception.py`

`PerceptionLayer.process()` 接收用户输入 → 意图识别：
- **12+ 种意图**：followup / analytical / calculate / weather / time / text_stats / document_read / translate / qrcode / image_process / md_render / greeting / knowledge_query / web_search
- **analytical 意图**：正则覆盖"分析/批判/反思/利弊/可行性"等，触发阴阳对子思考
- **领域识别**：hotel / product / medical / general

---

## 三、守护系统

### 3.1 守卫点清单（5 个）

| # | 守卫点 | 位置 | 触发条件 | 实现文件 |
|---|--------|------|----------|----------|
| ① | W2→W1 跨层审计 | 数据流管道 | 跨 CUF 层 | [firewall.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/guard/firewall.py) |
| ② | W1→M 跨层审计 | 数据流管道 | 跨 CUF 层 | [firewall.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/guard/firewall.py) |
| ③ | 工具守卫 | 工具调用前 | 无论同层与否 | [tool_guard.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/guard/tool_guard.py) |
| ④ | 周期审计 | 定时器 | M→W1 同层免审 | [metacognition.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/metacognition.py) |
| ⑤ | 内容过滤 | 输出脱敏 | 响应生成后 | [content_filter.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/guard/content_filter.py) |

### 3.2 CUF 逻辑防火墙（CUFGuard）

审计顺序：
1. 层标识符校验（`.upper().strip()` + 白名单 D/M/W1/W2）
2. 同层免审短路
3. 白名单短路（只读操作 + code_hash 校验）
4. **A1 基线不可变性**：拒绝写 D 层 + 实时完整性校验
5. **A4 层级单向性**：只校验依赖方向反向，数据流动作不受约束
6. **A3 契约闭环性**：高危动作必须携带四契约
7. **A2 熵税经济性**：调用 `ledger.pay_tax()`，登记待补偿

补偿机制：业务失败时反向退款 `refund_on_failure()`。

### 3.3 熵税账本

- **D 层**：只定义接口签名（`ledger_base.py` 抽象基类）
- **W1 层**：实际运行时实例 `LedgerRuntime`（同层免审，消除自指死循环）
- **五维计税**：base × depth × state × custom
- **保底补充**：余额低于 10E 时自动补 100E，限频 5次/小时
- **哈希链**：账本历史防篡改

### 3.4 D 层完整性校验

- **启动校验**：失败时**熔断拒绝启动**
- **定期校验**：每小时一次，失败时进入**只读降级模式**
- **基线 hash**：从 `MANIFEST.expected_hashes` 加载（非首运计算）
- **禁止运行时状态**：扫描 `_balance/_history/threading.Lock()` 等模式

### 3.5 阴阳双签基类

文件：[yin_yang_base.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/guard/yin_yang_base.py)

- `YIN_THRESHOLD = 0.75`（阴方通过阈值）
- `YANG_THRESHOLD = 0.65`（阳方通过阈值）
- `endorsed = yin_passed and yang_passed`
- **软约束**：认知思考（非高风险，不触发 Pair 硬约束）
- **硬约束**：代码自修改（必触发，需双通过 + 人工审批）

---

## 四、认知层处理流程

`CognitionLayer.process()` 采用 **AND 关系非互斥**的多策略综合注入：

```
用户输入 + 意图
      │
      ├─ analytical 意图 ──→ 阴阳对子思考（阴DeepSeek + 阳Qwen + 合一）
      │                        │
      │                        └─ 成功 → 返回（跳过后续流程）
      │                        └─ 失败 → 降级到原流程
      │
      ├─ web_search 成功 ──→ 搜索结果 + 深度爬取 + RAG 综合注入 LLM
      │
      ├─ web_crawl 成功 ──→ 爬取结果 + RAG 综合注入 LLM
      │
      ├─ 其他工具成功 ──→ 格式化结果（信息查询类额外注入 RAG）
      │
      ├─ 工具全失败 ──→ 插件市场闭环 ──→ 仍失败 → RAG + LLM 常规对话
      │
      ├─ web_search 意图但无工具 ──→ 兜底联网搜索
      │
      └─ 无工具调用 ──→ RAG 上下文 + LLM 生成回复（闲聊也注入 RAG）
```

### 阴阳对子思考（方案 C）

| 角色 | LLM | 视角 | Prompt 要点 |
|------|-----|------|-------------|
| 阴方 | DeepSeek-Chat | 批判 | 找漏洞、风险、反对理由，至少 3 条 |
| 阳方 | Qwen-Plus | 支持 | 找优势、机会、支持理由（失败回退 DeepSeek） |
| 合一 | DeepSeek-Chat | 综合 | 不复读素材、不提及阴方阳方、独立观点、500-800 字 |

**评分维度**（满分 1.0）：
- 基础分 0.3
- 批判/支持性词汇 +0.2
- 分点论证 +0.15/+0.05/+0.05
- 因果论证 +0.15
- 字数充分（>200 +0.2，>100 +0.1）

---

## 五、分布式执行

文件：[distributed_executor.py](file:///C:/Users/若水/AppData/Roaming/TRAE%20SOLO%20CN/ModularData/ai-agent/work-mode-projects/6a77f42497bc426f3121fb5d/scu2/m_layer/distributed_executor.py)

### 5.1 核心组件

| 组件 | 职责 |
|------|------|
| `WorkerNode` | 工作节点（三状态：IDLE/BUSY/OFFLINE，能力声明 cpu/memory/gpu/special_tools） |
| `WorkerRegistry` | 节点注册表（三种负载均衡策略：轮询/最少忙碌/能力匹配） |
| `TaskDispatcher` | 任务分发器（七种任务状态，幂等性缓存，重试+超时迁移） |
| `WorkerServer` | 工作节点服务端（5 个 HTTP 端点：register/heartbeat/execute/health/status） |
| `LocalMultiProcessExecutor` | 本地多进程降级（`multiprocessing.Pool`，默认 cpu_count-1） |
| `DistributedExecutor` | 主类（自动选择分布式/本地模式，状态持久化） |

### 5.2 任务分片与合并

**分片策略** `split_task()`：
- list payload：按元素均分
- dict payload：按 key 分组
- 不可分片（str/int）：复制为 n 份（用于冗余/对比）

**合并策略** `merge_results()`：
- concat / sum / avg / max / min / dict_merge / first

### 5.3 负载均衡策略

| 策略 | 说明 |
|------|------|
| `ROUND_ROBIN` | 轮询 |
| `LEAST_BUSY` | 最少失败 + 最少任务 |
| `CAPABILITY_MATCH` | 能力最贴近（选资源最少占用的，留大节点给重任务） |

### 5.4 故障处理

- 心跳超时 30s → 标记 OFFLINE → 任务迁移 `retry()`
- 幂等性：`_results` 缓存，重复 task_id 跳过分发
- 状态持久化：`scu2_data/distributed_state.json`，重启后恢复（标记 offline 待心跳确认）

---

## 六、插件系统

### 6.1 能力匹配（四级优先级）

1. 文件扩展名匹配（`.pdf→pdf_reader`、`.docx→docx_reader`、`.xlsx→excel_reader`）
2. 触发词匹配（市场清单 `triggers` 字段）
3. 能力关键词匹配（市场清单 `capabilities` 字段）
4. 失败工具 → 能力映射（`tool_capability_map`）

### 6.2 自动安装加载

- 支持 `pip` / `git` 两种安装方式
- pip 多源回退：清华 → 阿里云 → 官方 PyPI
- git 安装：GitHub 白名单校验 + `git clone --depth 1`
- 动态 `importlib.import_module` + 内置工具工厂创建工具函数
- 自动注册到 `ActionLayer._tools` 和 `tool_guard.TOOL_TYPE_MAP`

### 6.3 生命周期

- 默认 TTL 600s，后台线程每 30s 检查
- `unload_after_use()`：注销工具 + 卸载 Python 模块
- `extend_ttl()`：延长 300s
- `keep_alive()`：标记持久模式

### 6.4 经验沉淀与自进化

- **经验沉淀**：成功路径记录，下次直接预加载跳过 all_failed
- **衰减机制**：30 天未用降权
- **成熟阈值**：成功 2 次以上视为成熟方案
- **自进化触发**：失败经验 `fail_count ≥ 3` 且 `success_count == 0` → 异步触发扫描
- **闭环流程**：缺陷分析 → 提案生成 → 安全审查 + 阴阳双签 → 用户审批 → 自动应用 + 备份

---

## 七、多单元支持

### 7.1 当前状态

后端 `/units` 接口当前返回**单个单元** `scu2-default`（SCU2 标准单元），无认证。

前端 `chatUnit` 下拉框已预留多单元选择能力：
- 默认选项："自动选择"（value 为空）
- 启动时自动调用 `loadUnits()` 渲染

### 7.2 单元与 LLM 的关系

| 概念 | 数量 | 说明 |
|------|------|------|
| SCU 单元 | 1 个（当前） | 系统部署的处理单元，`scu2-default` |
| LLM 调用 | 2 个 API（阴阳对子时） | DeepSeek(阴) + Qwen(阳)，认知层内部双签 |

阴阳对子是**单单元内部**调用了两个 LLM API，不是两个独立 SCU 单元并行运行。

---

## 八、扩展方案

### 8.1 横向扩展：添加 Worker 节点

#### 方式 A：添加远程 Worker（独立机器部署）

1. **远程机器启动 WorkerServer**：
   ```python
   from m_layer.distributed_executor import WorkerServer
   server = WorkerServer(port=9700, handler=my_task_handler, host="0.0.0.0")
   server.start(background=False)
   ```

2. **主节点注册远程 Worker**：
   ```python
   executor = get_distributed_executor()
   executor.add_remote_worker(url="http://remote-host:9700", capabilities={"cpu": 8, "memory": 16384, "gpu": 1})
   ```
   或通过 API：`POST /distributed/workers/add`（需 admin key）

3. **任务分发**：`POST /distributed/execute`，自动选择分布式/本地模式

#### 方式 B：本地多进程模拟（单机多核）

无需远程节点，`LocalMultiProcessExecutor` 使用 `multiprocessing.Pool`，默认 worker 数 = `cpu_count - 1`。

#### 关键扩展点

- 负载均衡策略：`round_robin` / `least_busy` / `capability_match`
- 能力声明：`cpu/memory/gpu/special_tools`
- 故障处理：心跳超时 30s → OFFLINE → 任务迁移
- 幂等性：重复 task_id 跳过分发
- 状态持久化：`scu2_data/distributed_state.json`

### 8.2 多单元：修改 /units 返回多个单元配置

当前 `/units` 返回单个 `scu2-default`。扩展方式：

1. **修改 `/units` 端点**返回多个单元：
   ```python
   @app.get("/units")
   async def list_units():
       return JSONResponse({
           "success": True,
           "data": {
               "units": [
                   {"uid": "scu2-default", "system_prompt_style": "SCU2 标准单元"},
                   {"uid": "scu2-coding", "system_prompt_style": "coding", "model": "deepseek-coder"},
                   {"uid": "scu2-analytical", "system_prompt_style": "analytical", "force_yin_yang": True},
                   {"uid": "scu2-medical", "system_prompt_style": "medical", "domain": "medical"},
               ],
           },
       })
   ```

2. **前端已就绪**：`chatUnit` 下拉框 + `loadUnits()` 函数已支持渲染多选项

3. **后端联动扩展**（需新增）：
   - `/chat` 请求体增加 `uid` 字段，按 uid 选择 system_prompt/平台/领域
   - 不同单元可绑定不同 LLM 平台、不同领域插件配置、不同税率覆写
   - 单元配置持久化到 `scu2_data/units.json`

4. **隔离级别**：
   - 软隔离（当前）：共享 ledger/记忆/知识库
   - 硬隔离：每个单元独立 DATA_DIR + 独立 ledger 实例

### 8.3 阴阳对子叠加：每个 Worker 内部都可跑阴阳对子

阴阳对子思考与分布式执行是**正交能力**，可叠加：

1. **Worker 内嵌阴阳对子**：
   - Worker 节点的 handler 回调内调用 `CognitionLayer._yin_yang_think()`
   - 每个 Worker 可独立配置 LLM 平台（阴方/阳方路由）

2. **分布式阴阳对子模式**：
   - 主节点 `split_task()` 将 analytical 任务拆为 3 个子任务：阴方/阳方/合一
   - 三个子任务分发到不同 Worker 并行执行
   - `merge_results(strategy="dict_merge")` 合并 `{yin_view, yang_view, synthesis}`

3. **双签判定位置**：
   - 主节点统一双签（推荐）：子任务结果回传后由 `CognitionEndorser.endorse()` 统一判定
   - Worker 端本地双签：只回传 `endorsed` 状态

4. **安全约束不变**：阴阳对子是软双签，不触发 Pair 硬约束，不跨层，不修改 D 层。Worker 端的 LLM 调用仍受 `ContentFilter` 脱敏。

---

## 九、安全约束

### 9.1 API Key 认证

- **双 Key 体系**：普通 `SCU2_API_KEY` + 管理员 `SCU2_ADMIN_API_KEY`
- **时序攻击防护**：`secrets.compare_digest()` 防时序侧信道
- **开发模式默认 Key**：启动时显著告警
- **敏感端点**：`/whitelist/add`、`/whitelist/list`、`/audit/daily`、`/status`、`/history`、`/knowledge/import`、`/knowledge/delete`

### 9.2 文件操作限制

- **SANDBOX_DIR 隔离**：`scu2_data/sandbox/`
- **`_safe_path()`**：绝对路径必须在项目目录内，用 `commonpath` 防前缀碰撞
- **沙箱执行**：AST 预检（拒绝属性访问/dunder/危险函数/import）+ 安全内置函数白名单 + 5 秒超时

### 9.3 代码自修改保护

- **D 层保护清单**：axioms/firewall/entropy_ledger/ledger_base/engine/meta_guard/baseline + 自修改模块自身 + content_filter
- **危险模式黑名单**：eval/exec/compile/globals/`__import__` 等
- **文件扩展名白名单**：`{".py"}`
- **阴阳双签 + 人工审批**：高风险操作必须 Yin(γ≥0.75) + Yang(γ≥0.65) 双通过
- **备份回滚**：修改前自动备份到 `scu2_data/backups/`

### 9.4 内容过滤

50+ 条正则规则覆盖：
- API Key / OpenAI / DeepSeek / AWS / GitHub PAT / Slack Token
- 密码凭证 / 长 base64 / PEM 私钥块
- 余额 / 账本数据 / 哈希链
- 内网 IP / 员工 ID / 内部 API 路径
- 数据库连接串 / JWT / 手机号 / 身份证 / 邮箱

### 9.5 模块保护

`PROTECTED_MODULES` 不可卸载：`cuf.firewall` / `cuf.entropy_ledger` / `cuf.axioms` / `engine` / `meta_guard` / `baseline` / `code_self_modify` / `module_registry`

### 9.6 网络与监听

- **默认监听 127.0.0.1**（生产环境建议用反向代理）
- **0.0.0.0 告警**：监听 `0.0.0.0` 或 `::` 时显著告警
- **默认端口 8300**（`SCU2_PORT` 环境变量可覆盖）

---

## 十、API 接口分类清单

### 10.1 对话与会话（8 个）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/` | GET | 无 | 首页 HTML |
| `/chat` | POST | api_key | 主对话入口 |
| `/chat/stream` | POST | api_key | SSE 流式对话 |
| `/chat/image` | POST | api_key | 图片对话（VL 模型） |
| `/conversation/start` | POST | - | 创建会话 |
| `/conversation/{id}/message` | POST | - | 发送会话消息 |
| `/conversation/{id}/history` | GET | - | 会话历史 |
| `/conversation/{id}` | DELETE | - | 删除会话 |

### 10.2 CUF 守卫与状态（10 个）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/health` | GET | 无 | 健康探活 |
| `/status` | GET | admin | 系统状态 |
| `/history` | GET | admin | 账本历史 |
| `/pair/status` | GET | 无 | 阴阳对子（Pair）状态 |
| `/cognition/yin-yang` | GET | 无 | 阴阳对子思考状态（方案 C） |
| `/cuf/activity` | GET | api_key | CUF 守卫活动记录 |
| `/cuf/check` | GET | api_key | CUF 守卫状态检查 |
| `/self-check/quick` | GET | admin | 快速自检 |
| `/self-check` | GET | admin | 完整自检 |
| `/audit/daily` | POST | admin | 触发周期审计 |

### 10.3 分布式执行（7 个）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/distributed/execute` | POST | api_key | 分布式执行任务 |
| `/distributed/split` | POST | api_key | 任务分片 |
| `/distributed/merge` | POST | api_key | 结果合并 |
| `/distributed/workers` | GET | api_key | 列出工作节点 |
| `/distributed/workers/add` | POST | admin | 添加节点 |
| `/distributed/workers/{id}/remove` | POST | admin | 移除节点 |
| `/distributed/health` | GET | api_key | 健康检查 |

### 10.4 自修改与自进化（15 个）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/self-modify/propose` | POST | - | 提交修改提议 |
| `/self-modify/pending` | GET | - | 待审批列表 |
| `/self-modify/approve` | POST | - | 批准 |
| `/self-modify/reject` | POST | - | 拒绝 |
| `/self-modify/rollback` | POST | - | 回滚 |
| `/self-modify/history` | GET | - | 修改历史 |
| `/code/proposals` | GET | admin | 提案列表 |
| `/evolution/status` | GET | api_key | 自进化状态 |
| `/evolution/scan` | POST | admin | 手动触发扫描 |
| `/evolution/history` | GET | api_key | 扫描历史 |
| `/evolution/defects` | GET | api_key | 当前缺陷列表 |
| `/learning/run` | POST | admin | 触发自学习 |
| `/learning/status` | GET | - | 自学习状态 |
| `/learning/history` | GET | - | 自学习历史 |

### 10.5 插件与市场（15 个）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/plugins` | GET | api_key | 列出所有插件 |
| `/plugins/{name}/enable` | POST | admin | 启用 |
| `/plugins/{name}/disable` | POST | admin | 禁用 |
| `/plugins/load` | POST | admin | 从目录加载 |
| `/plugins/metrics` | GET | api_key | MetricsPlugin 数据 |
| `/plugins/market/list` | GET | api_key | 市场清单 |
| `/plugins/market/install` | POST | api_key | 安装加载 |
| `/plugins/market/unload` | POST | api_key | 卸载 |
| `/plugins/market/uninstall` | POST | admin | 完全卸载 |
| `/plugins/market/loaded` | GET | api_key | 已加载列表（含 TTL） |
| `/plugins/market/keep-alive` | POST | api_key | 标记持久模式 |
| `/plugins/market/match` | POST | api_key | 测试能力匹配 |

### 10.6 其他分类

- **知识库与向量**（8 个）：`/knowledge/*`、`/vector/*`
- **三级记忆**（7 个）：`/memory/*`
- **LLM 与本地模型**（10+ 个）：`/llm/*`、`/models`、`/units`、`/local-model/*`、`/vision/*`
- **自动化与浏览器**（15+ 个）：`/automation/*`、`/browser/*`
- **多模态与语音**（7 个）：`/multimodal/*`、`/voice/*`
- **MCP 协议**（6 个）：`/mcp/*`
- **模块注册表**（5 个）：`/modules/*`

---

## 十一、总结

### 当前架构亮点

1. **D 层只读**：代码定义在 D 层（只读），运行时状态在 W1 层，消除自指死循环
2. **同层免审**：W1→W1、M→M 同层流动免审，只有跨 CUF 层才审计
3. **阴阳双签**：阴方 DeepSeek（批判）+ 阳方 Qwen（支持）+ 太极合一，γ_yin≥0.75 / γ_yang≥0.65
4. **分布式 + 插件市场闭环**：能力缺失 → 自动下载加载 → 重试 → 经验沉淀 → 自进化
5. **多重安全**：API Key 时序防护、SANDBOX 沙箱、AST 预检、D 层完整性熔断、50+ 内容过滤规则

### 扩展能力已就绪

- **横向扩展**：添加 Worker 节点（远程/本地多进程）
- **纵向扩展**：多单元配置（修改 `/units` 返回值）
- **能力叠加**：阴阳对子可叠加在 Worker 内（分布式 + 双签）

所有扩展点都有对应的 API 端点和单例管理器，架构弹性充足。
