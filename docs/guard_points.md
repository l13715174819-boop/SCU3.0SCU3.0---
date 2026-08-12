# 守卫点文档（v3架构落地）

> **原则三落地**：同层流动免审，只有跨CUF层才审计
> **更新日期**: 2026-08-10

## 守卫点清单

SCU3 共有 3 类守卫点，覆盖所有跨层操作：

### 守卫点 ① W2→W1（感知→记忆）

| 项 | 内容 |
|----|------|
| **触发条件** | source="W2" AND target="W1" AND source≠target |
| **守卫类型** | CUFGuard.check() |
| **审计内容** | A1（D层不可变）+ A2（熵税）+ A3（契约，高危）+ A4（依赖方向） |
| **典型场景** | 用户输入从感知层进入记忆层查询上下文 |
| **税率** | 按 `query` 操作计税（约 0.5-1.0E） |
| **日志字段** | `same_layer_bypass=False`, `guard_point="W2→W1"` |

### 守卫点 ② W1→M（执行→认知）

| 项 | 内容 |
|----|------|
| **触发条件** | source="W1" AND target="M" AND source≠target |
| **守卫类型** | CUFGuard.check() |
| **审计内容** | A1 + A2 + A3（高危）+ A4 |
| **典型场景** | 工具调用结果从执行层送入认知层生成回复 |
| **税率** | 按 `layer_jump` 操作计税（约 1.0-2.0E） |
| **日志字段** | `same_layer_bypass=False`, `guard_point="W1→M"` |

### 守卫点 ③ 工具守卫（ToolGuard）

| 项 | 内容 |
|----|------|
| **触发条件** | 任何工具调用（无论同层与否） |
| **守卫类型** | ToolGuard.check_tool() |
| **审计内容** | 按 tool_type（read/write）定税 + 白名单检查 |
| **典型场景** | calculator/weather/file_read/file_write 等工具调用 |
| **税率** | read: 0.2E / write: 3.0E |
| **日志字段** | `guard="tool"`, `tool_type="read|write"` |

### 守卫点 ④ 周期审计（M→W1，同层免审）

| 项 | 内容 |
|----|------|
| **触发条件** | 每日定时触发（非实时） |
| **守卫类型** | Metacognition.daily_audit() |
| **审计内容** | 汇总反馈 → 写 W1 层覆写表 |
| **税率** | 同层免审（same_layer_bypass=True） |
| **日志字段** | `same_layer_bypass=True`, `guard_point="daily_audit"` |

### 守卫点 ⑤ 内容过滤（ContentFilter）

| 项 | 内容 |
|----|------|
| **触发条件** | 系统输出前（_build_response） |
| **守卫类型** | ContentFilter.filter() |
| **审计内容** | 敏感模式匹配 + D层字段白名单 |
| **税率** | 非A4审计，是数据安全检查 |
| **日志字段** | `filter_warnings=[...]` |

## 同层免审规则

| 流动 | source | target | 是否审计 | 日志 |
|------|--------|--------|---------|------|
| 感知→记忆 | W2 | W1 | ✅ 审计 | guard_point="W2→W1" |
| 记忆→执行 | W1 | W1 | ❌ 免审 | same_layer_bypass=True |
| 执行→认知 | W1 | M | ✅ 审计 | guard_point="W1→M" |
| 认知→元认知 | M | M | ❌ 免审 | same_layer_bypass=True |
| 元认知→输出 | M | (输出) | ❌ 免审 | 非跨层 |
| 周期审计 | M | W1 | ❌ 免审 | same_layer_bypass=True |

## 日志格式

```json
{
  "op_id": "op_xxx",
  "timestamp": "2026-08-10T12:00:00",
  "source": "W2",
  "target": "W1",
  "action": "query",
  "same_layer_bypass": false,
  "guard_point": "W2→W1",
  "axioms_checked": [
    {"axiom": "A1", "passed": true, "msg": "A1 通过"},
    {"axiom": "A4", "passed": true, "msg": "A4 跳过（数据流动作 query 不受 A4 约束）"},
    {"axiom": "A2", "passed": true, "msg": "A2 通过: 已支付 0.5E"}
  ]
}
```
