# SCU2 CUF技术白皮书 v4.0

> **版本**: v4.0 — Agent自主执行能力
> **日期**: 2026-08-10
> **作者**: AI助手 + 用户协作
> **前一版本**: v3.0（本地大模型+自学习+代码自修改）

---

## 一、版本演进

| 版本 | 核心能力 | 状态 |
|------|---------|------|
| v1.0 | CUF对子理论、三子系统 | 已交付 |
| v2.0 | SCU2 v3架构落地、3+3+3验证、5漏洞修复 | 已交付 |
| v3.0 | 本地大模型接入 + 自学习闭环 + 代码自修改 | 已交付 |
| **v4.0** | **Agent自主执行（任务拆解+多步执行+脚本自清理+经验学习）** | **本版本** |

---

## 二、v4.0核心目标

让SCU2具备**完整的Agent自主执行能力**，对标AI助手的工作模式：

| AI助手能力 | SCU2 v3.0状态 | SCU2 v4.0目标 |
|-----------|--------------|--------------|
| 理解用户意图 | ✓ 感知层 | ✓ |
| 拆解任务为子步骤 | ✗ 单轮 | ✓ TaskPlanner |
| 按步骤循环执行 | ✗ 单轮 | ✓ TaskExecutor |
| 步骤间上下文传递 | ✗ | ✓ TaskContext |
| 代码生成 | ⚠️ LLM能写但不自动执行 | ✓ CodeGenerator |
| 沙箱执行 | ✓ 有沙箱 | ✓ 复用 |
| 执行结果验证 | ✗ | ✓ Verifier |
| 失败重试/换策略 | ✗ | ✓ RetryStrategy |
| 多工具组合调用 | ⚠️ 单工具 | ✓ ToolChain |
| 临时资源生命周期 | ✗ | ✓ TempManager |
| 汇总输出报告 | ⚠️ 单轮 | ✓ ReportGenerator |
| 自我反思/总结 | ⚠️ 元认知层 | ✓ ReflectionEngine |
| 记忆积累 | ✓ RAG | ✓ 复用+增强 |

---

## 三、架构设计

### 3.1 Agent完整闭环

```
用户目标
  ↓
① TaskPlanner 任务拆解器
  ↓ (生成有序步骤列表)
② TaskExecutor 多步执行循环
  ↓ ↻ 每步循环:
  │   ├─ 检查依赖步骤完成
  │   ├─ 上下文注入 (${step1_result}等)
  │   ├─ ActionLayer 工具调用
  │   ├─ CodeGenerator 代码生成(按需)
  │   ├─ RetryStrategy 失败重试
  │   └─ TempManager 临时资源注册
  ↓
③ ReflectionEngine 反思总结
  ↓ (分析成功/失败原因)
④ TempManager 清理临时资源
  ↓ (用完删除)
⑤ AgentLearning 经验沉淀到RAG
  ↓ (积累成功/失败模式)
⑥ TaskTemplate 模板积累复用
  ↓ (下次相似任务可直接套用)
最终报告输出
```

### 3.2 三批实施路线

**第一批·核心Agent闭环**（最小可用）
- TaskPlanner：任务拆解器
- TaskExecutor：多步执行循环
- TempManager：临时资源管理（用完删除）
- ReflectionEngine：执行后反思

**第二批·能力增强**
- CodeGenerator：代码生成器（自己写脚本）
- ToolChain：多工具链式调用
- RetryStrategy：失败重试/策略切换

**第三批·学习进化**
- AgentLearning：执行经验沉淀
- ToolPreference：工具使用偏好学习
- TaskTemplate：任务模板积累

### 3.3 安全约束

- **完全自主执行**模式（无需人工确认）
- 保留两条项目硬约束作为安全底线：
  - D层基础文件不可修改（axioms/firewall/engine等10个文件）
  - 代码自修改仍走阴阳双签（γ_yin≥0.75 × γ_yang≥0.65）
- 沙箱执行（AST预检+危险函数拦截+超时限制）
- 临时资源限定在sandbox目录
- API端点保留API Key认证

---

## 四、新增10个模块

### 4.1 task_planner.py（任务拆解器）

**位置**: m_layer/task_planner.py
**职责**: 接收用户自然语言目标，拆解为有序可执行步骤列表

**核心方法**:
- `plan(goal, context)` → 返回结构化执行计划
- `_plan_with_llm(goal, context)` → LLM智能拆解
- `_plan_with_rules(goal, context)` → 规则模板拆解（降级）

**支持的工具映射**:
```python
AVAILABLE_TOOLS = [
    "calculator", "weather", "time_now", "text_stats", "file_read",
    "exchange_rate", "crypto_price", "stock_price", "github_search",
    "datetime_calc", "unit_convert", "file_write", "code_run",
]
```

**降级策略**:
- LLM可用 → 智能拆解
- LLM不可用 → 规则模板（计算类/查询类/文件分析类/代码执行类）
- 目标过于模糊 → 返回澄清请求

**步骤结构**:
```python
{
    "step_id": 1,
    "action": "file_read",  # 工具名
    "description": "读取文件",
    "params": {"path": "readme.md"},
    "depends_on": [],  # 依赖的前序步骤
    "is_temporary": False,  # 是否产生可清理的临时资源
    "status": "pending",
}
```

### 4.2 task_executor.py（多步执行循环）

**位置**: m_layer/task_executor.py
**职责**: 按计划循环执行步骤，维护状态，传递上下文

**核心方法**:
- `run(goal, context, cleanup, reflect)` → 完整流程：拆解→执行→反思→清理
- `create_plan(goal)` → 仅创建计划
- `execute_plan(plan, task_id)` → 执行已有计划

**核心循环逻辑**:
1. 取出下一个待执行步骤
2. 检查依赖步骤是否完成
3. 上下文注入（`${step1_result}` → 上一步结果）
4. 调用ActionLayer工具执行
5. 记录结果到上下文
6. 临时资源注册到TempManager
7. 失败时记录但继续执行（完全自主模式）

**上下文传递**:
支持占位符替换，前序步骤的输出可自动注入到后续步骤参数中：
```python
# 步骤1的输出
report["step_context"]["step1_result"] = {"content": "文件内容"}

# 步骤2的参数中引用
{"code": "data = ${step1_result}\nprint(data['content'])"}
# → 自动替换为
{"code": "data = {'content': '文件内容'}\nprint(data['content'])"}
```

### 4.3 temp_manager.py（临时资源管理）

**位置**: w1_layer/temp_manager.py
**职责**: Agent执行任务时产生的临时文件/目录，用完自动删除

**核心方法**:
- `register(task_id, path, is_dir)` → 注册临时资源
- `preserve(task_id, path)` → 标记为保留（不删除）
- `cleanup(task_id, force)` → 清理指定任务的资源
- `cleanup_all(force)` → 紧急清理所有
- `list_temp_resources(task_id)` → 列出注册的资源
- `get_history(limit)` → 清理历史

**安全机制**:
- 只能清理sandbox目录内的文件
- 路径校验防目录遍历
- 使用`os.path.commonpath`防止前缀碰撞
- 删除前二次确认路径在允许范围内

### 4.4 reflection.py（执行后反思）

**位置**: m_layer/reflection.py
**职责**: 任务执行完成后，对过程和结果进行反思总结

**核心方法**:
- `reflect(report)` → 生成反思结果
- `_reflect_with_llm(report)` → LLM深度反思
- `_reflect_with_rules(report)` → 规则反思（降级）
- `_sink_knowledge(report, reflection)` → 沉淀到RAG

**反思输出**:
```python
{
    "summary": "任务整体评价",
    "successes": ["成功点1", ...],
    "failures": ["失败原因1", ...],
    "improvements": ["改进建议1", ...],
    "knowledge_sunk": bool,  # 是否沉淀到知识库
}
```

### 4.5 code_generator.py（代码生成器）

**位置**: m_layer/code_generator.py
**职责**: 让Agent能自己生成Python代码并安全执行

**核心方法**:
- `generate_and_run(requirement, context)` → 生成+审查+执行+验证
- `generate_only(requirement, context)` → 仅生成不执行
- `_audit_code(code)` → AST安全审查

**自动重试机制**:
- 最多3次重试
- 执行失败时反馈错误给LLM重新生成
- 安全审查失败直接拒绝

**安全审查规则**:
- 禁止import语句
- 禁止访问下划线属性（`__class__`等）
- 禁止调用危险函数（eval/exec/__import__/getattr/open等13种）
- 与沙箱安全规则对齐

### 4.6 tool_chain.py（多工具链式调用）

**位置**: m_layer/tool_chain.py
**职责**: 支持多工具按序链式执行，前一个的输出作为后一个的输入

**核心方法**:
- `add(tool, params, extract_field, input_field, transform, on_fail)` → 添加步骤
- `execute()` → 链式执行
- `describe()` → 描述工具链

**链式执行示例**:
```python
chain = ToolChain()
chain.add("file_read", {"path": "readme.md"}, extract_field="content")
chain.add("text_stats", input_field="text")  # content自动传入
chain.add("file_write", {"path": "stats.txt"})
result = chain.execute()
```

**失败策略**:
- `stop`: 失败停止（默认）
- `continue`: 继续但用上一步输出
- `skip`: 跳过当前步骤

### 4.7 retry_strategy.py（失败重试与策略切换）

**位置**: m_layer/retry_strategy.py
**职责**: 执行失败时自动重试或切换策略

**核心方法**:
- `retry(func, *args, **kwargs)` → 简单重试
- `try_strategies(strategies)` → 策略切换
- `with_fallback(primary, fallback, *args)` → 主策略+回退

**退避策略**:
- `exponential`: 指数退避（默认，0.1s→0.2s→0.4s）
- `linear`: 线性退避
- `fixed`: 固定延迟

### 4.8 agent_learning.py（执行经验沉淀）

**位置**: m_layer/agent_learning.py
**职责**: 从执行历史中学习，积累经验供下次复用

**核心方法**:
- `learn_from_history(history)` → 从历史学习
- `query_experience(goal)` → 查询类似任务经验
- `get_stats()` → 学习统计

**经验库结构**:
```python
{
    "goal_pattern": {
        "sample_goal": "分析文件词频",
        "success_count": 5,
        "fail_count": 1,
        "avg_time": 250.5,
        "last_seen": "2026-08-10T21:30:00",
    }
}
```

**持久化**: `scu2_data/agent_learning.json`

### 4.9 tool_preference.py（工具使用偏好学习）

**位置**: m_layer/tool_preference.py
**职责**: 学习工具使用偏好，优化工具选择

**核心方法**:
- `record(tool, success, elapsed_ms, scenario)` → 记录使用
- `recommend(scenario, top_k)` → 推荐最优工具
- `get_all_stats()` → 工具统计

**评分算法**:
```
score = 成功率 * 0.7 + 速度分 * 0.3
速度分 = max(0, 1 - elapsed_ms / 5000)
```

**滑动平均**: `new_score = old_score * 0.7 + current_score * 0.3`

### 4.10 task_template.py（任务模板积累）

**位置**: m_layer/task_template.py
**职责**: 积累成功任务的执行模板，相似目标可复用

**核心方法**:
- `save_template(goal, plan, execution_report)` → 保存模板
- `find_template(goal)` → 查找匹配模板
- `use_template(template_id)` → 使用模板（增加计数）
- `list_templates(limit)` → 列出模板

**模板匹配算法**:
1. 精确匹配（goal_pattern相同）
2. 模糊匹配（Jaccard相似度>0.3）

---

## 五、新增17个API端点

### 5.1 Agent核心端点

| 端点 | 方法 | 认证 | 功能 |
|------|------|------|------|
| `/agent/run` | POST | API Key | 完整Agent执行：目标→拆解→执行→反思→清理 |
| `/agent/plan` | POST | API Key | 仅生成执行计划（不执行） |
| `/agent/execute` | POST | API Key | 执行已有计划 |
| `/agent/history` | GET | API Key | Agent执行历史 |
| `/agent/status` | GET | API Key | Agent执行器状态 |
| `/agent/learn` | POST | Admin Key | 触发Agent经验学习 |
| `/agent/experience` | GET | API Key | 查询类似任务的执行经验 |

**请求示例**:
```bash
# 完整Agent执行
curl -X POST http://127.0.0.1:8300/agent/run \
  -H "X-API-Key: scu2_dev_key_2026" \
  -H "Content-Type: application/json" \
  -d '{"goal": "分析readme.md文件并生成报告", "cleanup": true, "reflect": true}'

# 仅生成计划
curl -X POST http://127.0.0.1:8300/agent/plan \
  -H "X-API-Key: scu2_dev_key_2026" \
  -H "Content-Type: application/json" \
  -d '{"goal": "计算 2+3*4"}'
```

### 5.2 代码生成端点

| 端点 | 方法 | 认证 | 功能 |
|------|------|------|------|
| `/codegen/generate` | POST | API Key | 代码生成（可选自动执行） |

**请求示例**:
```bash
curl -X POST http://127.0.0.1:8300/codegen/generate \
  -H "X-API-Key: scu2_dev_key_2026" \
  -H "Content-Type: application/json" \
  -d '{"requirement": "计算1到100的和", "execute": true}'
```

### 5.3 工具链端点

| 端点 | 方法 | 认证 | 功能 |
|------|------|------|------|
| `/toolchain/execute` | POST | API Key | 多工具链式执行 |

### 5.4 任务模板端点

| 端点 | 方法 | 认证 | 功能 |
|------|------|------|------|
| `/templates` | GET | API Key | 列出任务模板 |
| `/templates/stats` | GET | API Key | 模板统计 |
| `/templates/{id}` | DELETE | Admin Key | 删除模板 |

### 5.5 工具偏好端点

| 端点 | 方法 | 认证 | 功能 |
|------|------|------|------|
| `/tools/stats` | GET | API Key | 工具使用统计 |
| `/tools/recommend` | GET | API Key | 推荐最优工具 |

### 5.6 临时资源端点

| 端点 | 方法 | 认证 | 功能 |
|------|------|------|------|
| `/temp/resources` | GET | API Key | 列出临时资源 |
| `/temp/cleanup` | POST | API Key | 清理临时资源 |
| `/temp/history` | GET | API Key | 清理历史 |

---

## 六、元认知层集成

在 metacognition.py 的 `daily_audit` 中新增Agent学习触发：

```python
# 阶段4：触发Agent经验学习（从执行历史中积累经验）
agent_learning_report = None
try:
    from m_layer.task_executor import get_executor
    from m_layer.agent_learning import get_agent_learning
    history = get_executor().get_history(50)
    if history:
        agent_learning_report = get_agent_learning().learn_from_history(history)
        logger.info(f"✅ Agent经验学习: 处理{agent_learning_report.get('records_processed', 0)}条记录")
except Exception as e:
    logger.warning(f"Agent经验学习失败: {e}")
    agent_learning_report = {"error": str(e)}
```

周期审计现在包含三层学习：
1. **反馈学习**（v2.0）：反馈→提示词优化
2. **自学习闭环**（v3.0）：RAG沉淀+提示词权重
3. **Agent经验学习**（v4.0）：执行历史→经验库

---

## 七、端到端测试报告

### 7.1 测试范围

13个测试类别，63个测试点：

| 类别 | 测试点数 | 状态 |
|------|---------|------|
| 模块导入测试 | 10 | ✓ 全通过 |
| 任务拆解器测试 | 7 | ✓ 全通过 |
| 临时资源管理测试 | 5 | ✓ 全通过 |
| 反思引擎测试 | 4 | ✓ 全通过 |
| 任务执行器测试 | 6 | ✓ 全通过 |
| 代码生成器测试 | 5 | ✓ 全通过 |
| 工具链测试 | 3 | ✓ 全通过 |
| 重试策略测试 | 3 | ✓ 全通过 |
| Agent学习引擎测试 | 4 | ✓ 全通过 |
| 工具偏好学习测试 | 3 | ✓ 全通过 |
| 任务模板测试 | 3 | ✓ 全通过 |
| 完整闭环集成测试 | 7 | ✓ 全通过 |
| 临时文件清理验证 | 3 | ✓ 全通过 |
| **总计** | **63** | **63/63 通过** |

### 7.2 关键测试用例

**用例1：完整Agent闭环**
```python
result = executor.run("计算 100*200", cleanup=True, reflect=True)
# 验证项:
# ✓ result.success == True
# ✓ result.plan_source in ("rule_based", "llm")
# ✓ len(result.steps) > 0
# ✓ "reflection" in result
# ✓ "cleanup" in result
# ✓ "elapsed_ms" in result
```

**用例2：危险代码拦截**
```python
dangerous = codegen._audit_code("__import__('os').system('rm -rf /')")
# ✓ not dangerous[0]  → 拒绝执行

dangerous2 = codegen._audit_code("eval('1+1')")
# ✓ not dangerous2[0]  → 拒绝执行
```

**用例3：失败重试+策略切换**
```python
def strategy_a(): raise ValueError("策略A失败")
def strategy_b(): return "策略B成功"

r3 = retry.try_strategies([
    {"name": "A", "func": strategy_a, "retry": 1},
    {"name": "B", "func": strategy_b, "retry": 1},
])
# ✓ r3.success == True
# ✓ r3.winning_strategy == "B"
```

**用例4：临时文件清理**
```python
# 执行任务（带清理）
executor.run("计算 50+50", cleanup=True)
temp_after = tm.list_temp_resources()
# ✓ len(temp_after.get("tasks", {})) == 0  → 无遗留
```

### 7.3 测试输出

```
============================================================
阶段4 Agent能力 端到端测试
============================================================

[1] 模块导入测试          10/10 通过
[2] 任务拆解器测试         7/7 通过
[3] 临时资源管理测试       5/5 通过
[4] 反思引擎测试           4/4 通过
[5] 任务执行器测试         6/6 通过
[6] 代码生成器测试         5/5 通过
[7] 工具链测试             3/3 通过
[8] 重试策略测试           3/3 通过
[9] Agent学习引擎测试      4/4 通过
[10] 工具偏好学习测试      3/3 通过
[11] 任务模板测试          3/3 通过
[12] 完整闭环集成测试      7/7 通过
[13] 临时文件清理验证      3/3 通过

测试结果: 63/63 通过, 0 失败
============================================================
```

---

## 八、源码统计

### 8.1 新增文件

| 文件 | 行数 | 职责 |
|------|------|------|
| m_layer/task_planner.py | ~330 | 任务拆解器 |
| m_layer/task_executor.py | ~360 | 多步执行循环 |
| m_layer/reflection.py | ~230 | 执行后反思 |
| m_layer/code_generator.py | ~250 | 代码生成器 |
| m_layer/tool_chain.py | ~180 | 多工具链式调用 |
| m_layer/retry_strategy.py | ~200 | 失败重试/策略切换 |
| m_layer/agent_learning.py | ~260 | 执行经验沉淀 |
| m_layer/tool_preference.py | ~180 | 工具使用偏好学习 |
| m_layer/task_template.py | ~230 | 任务模板积累 |
| w1_layer/temp_manager.py | ~230 | 临时资源管理 |
| **新增总计** | **~2450行** | **10个模块** |

### 8.2 修改文件

| 文件 | 修改内容 |
|------|---------|
| server.py | 新增17个API端点 |
| m_layer/metacognition.py | 挂载Agent学习到周期审计 |

---

## 九、能力对标总结

### SCU2 v4.0 vs AI助手能力对照

| 能力维度 | AI助手 | SCU2 v4.0 | 实现方式 |
|---------|--------|-----------|---------|
| 理解用户意图 | ✓ | ✓ | 感知层 + TaskPlanner |
| 任务拆解 | ✓ | ✓ | TaskPlanner (LLM+规则双模式) |
| 按步骤执行 | ✓ | ✓ | TaskExecutor循环 |
| 上下文传递 | ✓ | ✓ | ${stepN_result}占位符 |
| 代码生成 | ✓ | ✓ | CodeGenerator (AST审查+沙箱) |
| 代码执行 | ✓ | ✓ | 复用沙箱 |
| 失败重试 | ✓ | ✓ | RetryStrategy (指数退避) |
| 策略切换 | ✓ | ✓ | try_strategies |
| 多工具组合 | ✓ | ✓ | ToolChain |
| 临时资源管理 | ✓ | ✓ | TempManager (用完删除) |
| 执行后反思 | ✓ | ✓ | ReflectionEngine |
| 经验积累 | ✓ | ✓ | AgentLearning → RAG |
| 工具偏好 | ✓ | ✓ | ToolPreferenceLearner |
| 模板复用 | ✓ | ✓ | TaskTemplateManager |

### 完整工作流示例

**用户**: "分析readme.md的词频并生成报告"

**SCU2 v4.0执行流程**:
1. TaskPlanner拆解为3步：
   - 步骤1: file_read(path="readme.md")
   - 步骤2: code_run(词频统计代码) [依赖步骤1]
   - 步骤3: file_write(path="report.txt") [依赖步骤2, 临时]

2. TaskExecutor逐步执行：
   - 步骤1: 读取文件 → 内容保存到step_context
   - 步骤2: 执行统计代码（注入step1结果）→ 统计数据
   - 步骤3: 写入报告文件 → 注册到TempManager

3. ReflectionEngine反思：
   - 成功点: 3步全部完成
   - 改进建议: 无

4. TempManager清理：
   - 删除report.txt（标记为临时）

5. AgentLearning沉淀：
   - 记录"分析文件"模式成功经验

6. TaskTemplate积累：
   - 保存模板供下次复用

---

## 十、版本历史

### v4.0 (2026-08-10)
- 新增10个Agent能力模块
- 新增17个API端点
- 实现完整Agent闭环：拆解→执行→反思→清理→学习
- 63/63端到端测试通过
- 元认知层集成Agent学习

### v3.0 (2026-08-10)
- 本地大模型接入（LM Studio/Ollama）
- 自学习闭环（反馈分析→RAG沉淀→提示词优化）
- 代码自修改（阴阳双签+D层保护+自动回滚）
- 78/78端到端测试通过

### v2.0 (2026-08-10)
- SCU2 v3架构落地（三维度分离）
- 3+3+3验证（全功能+饱和攻击+代码检查）
- 5个严重漏洞修复
- RAG知识库 + 13种工具补全

### v1.0 (2026-08-08)
- CUF对子理论
- CUF三子系统（核心守护/网关缓存/熵税标定）
- 基础架构搭建

---

## 十一、后续规划

### v5.0候选特性
1. **多Agent协作**：多个Agent并行处理复杂任务
2. **Web搜索工具**：接入真实网络搜索能力
3. **文件系统工具扩展**：支持更多文件操作（移动/复制/压缩）
4. **可视化执行流**：前端展示Agent执行过程
5. **人机协作模式**：关键步骤可选人工确认（当前为完全自主）

### 已识别的优化点
1. TaskPlanner的LLM拆解可增加few-shot示例提升质量
2. CodeGenerator可支持更多语言（JavaScript/SQL等）
3. ToolChain可增加条件分支（if-else逻辑）
4. AgentLearning可引入更复杂的模式匹配算法

---

## 十二、安全声明

### 保留的安全约束
1. **D层基础文件保护**: 10个核心文件不可被任何操作修改
2. **代码自修改阴阳双签**: γ_yin≥0.75 × γ_yang≥0.65
3. **沙箱AST预检**: 禁止import/eval/exec/dunder访问
4. **API Key认证**: 所有端点需认证，敏感端点需Admin Key
5. **临时资源限制**: 只能操作sandbox目录内文件
6. **请求体大小限制**: ≤1MB防DoS

### v4.0新增安全措施
1. CodeGenerator的AST审查与沙箱规则对齐
2. TempManager的路径校验防目录遍历
3. TaskExecutor的上下文注入做JSON安全解析

---

**白皮书版本**: v4.0
**生成时间**: 2026-08-10 21:50:00
**测试通过率**: 63/63 (100%)
**新增代码量**: ~2450行 (10个模块)
**累计API端点**: 44个 (v3.0的27个 + v4.0的17个)
