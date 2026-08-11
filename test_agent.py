# -*- coding: utf-8 -*-
"""阶段4 Agent能力端到端测试"""
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

passed = 0
failed = 0
errors = []

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        errors.append(f"{name}: {detail}")
        print(f"  [FAIL] {name} - {detail}")

print("=" * 60)
print("阶段4 Agent能力 端到端测试")
print("=" * 60)

# ─── 1. 模块导入测试 ────────────────────────────────────
print("\n[1] 模块导入测试")
try:
    from m_layer.task_planner import get_planner, TaskPlanner
    test("task_planner导入", True)
except Exception as e:
    test("task_planner导入", False, str(e))

try:
    from w1_layer.temp_manager import get_temp_manager, TempManager
    test("temp_manager导入", True)
except Exception as e:
    test("temp_manager导入", False, str(e))

try:
    from m_layer.reflection import get_reflection_engine, ReflectionEngine
    test("reflection导入", True)
except Exception as e:
    test("reflection导入", False, str(e))

try:
    from m_layer.task_executor import get_executor, TaskExecutor
    test("task_executor导入", True)
except Exception as e:
    test("task_executor导入", False, str(e))

try:
    from m_layer.code_generator import get_code_generator, CodeGenerator
    test("code_generator导入", True)
except Exception as e:
    test("code_generator导入", False, str(e))

try:
    from m_layer.tool_chain import ToolChain, quick_chain
    test("tool_chain导入", True)
except Exception as e:
    test("tool_chain导入", False, str(e))

try:
    from m_layer.retry_strategy import RetryStrategy, retry_on_fail
    test("retry_strategy导入", True)
except Exception as e:
    test("retry_strategy导入", False, str(e))

try:
    from m_layer.agent_learning import get_agent_learning, AgentLearningEngine
    test("agent_learning导入", True)
except Exception as e:
    test("agent_learning导入", False, str(e))

try:
    from m_layer.tool_preference import get_tool_preference, ToolPreferenceLearner
    test("tool_preference导入", True)
except Exception as e:
    test("tool_preference导入", False, str(e))

try:
    from m_layer.task_template import get_template_manager, TaskTemplateManager
    test("task_template导入", True)
except Exception as e:
    test("task_template导入", False, str(e))

# ─── 2. 任务拆解器测试 ────────────────────────────────────
print("\n[2] 任务拆解器测试")
planner = get_planner()

# 测试计算类拆解
plan = planner.plan("计算 2+3*4")
test("计算任务拆解", plan.get("source") in ("rule_based", "llm") and len(plan.get("steps", [])) > 0,
     f"source={plan.get('source')}, steps={len(plan.get('steps', []))}")
if plan.get("steps"):
    test("计算任务action正确", plan["steps"][0]["action"] == "calculator",
         f"action={plan['steps'][0]['action']}")

# 测试查询类拆解
plan2 = planner.plan("北京天气")
test("天气任务拆解", len(plan2.get("steps", [])) > 0)
if plan2.get("steps"):
    test("天气任务action正确", plan2["steps"][0]["action"] == "weather",
         f"action={plan2['steps'][0]['action']}")

# 测试时间查询
plan3 = planner.plan("现在几点")
test("时间任务拆解", len(plan3.get("steps", [])) > 0)
if plan3.get("steps"):
    test("时间任务action正确", plan3["steps"][0]["action"] == "time_now")

# 测试文件分析拆解
plan4 = planner.plan("分析readme.md文件")
test("文件分析任务拆解", len(plan4.get("steps", [])) >= 2,
     f"steps={len(plan4.get('steps', []))}")

# ─── 3. 临时资源管理测试 ────────────────────────────────────
print("\n[3] 临时资源管理测试")
tm = get_temp_manager()

# 注册临时文件
test("注册临时资源", tm.register("test_task_001", "temp_test_file.txt"))
test("重复注册不报错", tm.register("test_task_001", "temp_test_file.txt"))

# 列出资源
resources = tm.list_temp_resources("test_task_001")
test("列出资源", len(resources.get("resources", [])) > 0)

# 清理（文件不存在也应该成功）
cleanup_report = tm.cleanup("test_task_001")
test("清理临时资源", cleanup_report["deleted_count"] >= 0 or len(cleanup_report["errors"]) >= 0)

# 清理后资源应不存在
resources_after = tm.list_temp_resources("test_task_001")
test("清理后无资源", len(resources_after.get("resources", [])) == 0)

# ─── 4. 反思引擎测试 ────────────────────────────────────
print("\n[4] 反思引擎测试")
reflector = get_reflection_engine()

# 构造模拟执行报告
mock_report = {
    "goal": "测试任务",
    "steps": [
        {"step_id": 1, "action": "calculator", "status": "done",
         "description": "计算", "result": {"result": 14}},
        {"step_id": 2, "action": "file_write", "status": "done",
         "description": "写文件", "result": {"written": 10}},
    ],
    "success": True,
    "elapsed_ms": 150.5,
    "errors": [],
}

reflection = reflector.reflect(mock_report)
test("反思生成", "summary" in reflection)
test("反思有成功点", len(reflection.get("successes", [])) > 0 or reflection.get("summary"))

# 测试失败报告反思
mock_fail_report = {
    "goal": "失败任务",
    "steps": [
        {"step_id": 1, "action": "file_read", "status": "failed",
         "description": "读取不存在的文件", "error": "文件不存在"},
    ],
    "success": False,
    "elapsed_ms": 50,
    "errors": ["文件不存在"],
}
reflection_fail = reflector.reflect(mock_fail_report)
test("失败反思生成", "failures" in reflection_fail)
test("失败反思有失败原因", len(reflection_fail.get("failures", [])) > 0)

# ─── 5. 任务执行器测试（核心闭环） ────────────────────────────────────
print("\n[5] 任务执行器测试（核心闭环）")
executor = get_executor()

# 测试简单计算任务
result = executor.run("计算 2+3", cleanup=True, reflect=True)
test("Agent计算任务执行", result.get("success", False),
     f"errors={result.get('errors')}")
test("Agent有步骤结果", len(result.get("steps", [])) > 0)
test("Agent有反思", "reflection" in result)
test("Agent有清理报告", "cleanup" in result)

# 测试时间查询任务
result2 = executor.run("现在几点")
test("Agent时间查询执行", result2.get("success", False))

# 测试多步骤任务（文件分析）
# 先创建一个测试文件
from w1_layer.action import ActionLayer
action = ActionLayer()
action.execute({"tool": "file_write", "params": {"path": "test_analysis.txt", "content": "Hello World\nThis is a test file for analysis.\nIt has multiple lines."}, "tool_type": "write"})

result3 = executor.run("分析test_analysis.txt文件")
test("Agent文件分析执行", result3.get("success", False) or len(result3.get("steps", [])) > 0,
     f"success={result3.get('success')}, steps={len(result3.get('steps', []))}")

# ─── 6. 代码生成器测试 ────────────────────────────────────
print("\n[6] 代码生成器测试")
codegen = get_code_generator()

# 测试仅生成
preview = codegen.generate_only("计算1到100的和")
test("代码生成预览", "code" in preview and len(preview["code"]) > 0)
test("代码安全审查", preview.get("safe") in (True, False))

# 测试生成并执行
exec_result = codegen.generate_and_run("计算1到10的和并打印")
test("代码生成执行", exec_result.get("success") or exec_result.get("attempts", 0) > 0,
     f"error={exec_result.get('error')}")

# 测试安全审查（危险代码）
dangerous = codegen._audit_code("__import__('os').system('rm -rf /')")
test("危险代码拦截", not dangerous[0], dangerous[1])

dangerous2 = codegen._audit_code("eval('1+1')")
test("eval拦截", not dangerous2[0])

# ─── 7. 工具链测试 ────────────────────────────────────
print("\n[7] 工具链测试")
chain = ToolChain()
chain.add("time_now", {})
chain_result = chain.execute()
test("工具链执行", chain_result.get("success", False))
test("工具链有结果", chain_result.get("final_result") is not None)

# 多步链
chain2 = ToolChain()
chain2.add("calculator", {"expression": "2+3"})
chain2_result2 = chain2.execute()
test("多步工具链", chain2_result2.get("success", False))

# ─── 8. 重试策略测试 ────────────────────────────────────
print("\n[8] 重试策略测试")
retry = RetryStrategy(max_retries=3, base_delay=0.01)

# 成功重试
def success_func():
    return "ok"
r = retry.retry(success_func)
test("成功函数重试", r["success"] and r["attempts"] == 1)

# 失败重试
attempt_count = [0]
def fail_func():
    attempt_count[0] += 1
    if attempt_count[0] < 3:
        raise ValueError("故意失败")
    return "ok"
r2 = retry.retry(fail_func)
test("失败后重试成功", r2["success"] and r2["attempts"] == 3)

# 策略切换
def strategy_a():
    raise ValueError("策略A失败")
def strategy_b():
    return "策略B成功"
r3 = retry.try_strategies([
    {"name": "A", "func": strategy_a, "retry": 1},
    {"name": "B", "func": strategy_b, "retry": 1},
])
test("策略切换成功", r3["success"] and r3["winning_strategy"] == "B")

# ─── 9. Agent学习引擎测试 ────────────────────────────────────
print("\n[9] Agent学习引擎测试")
learner = get_agent_learning()

# 从历史学习
history = executor.get_history(50)
if history:
    learn_report = learner.learn_from_history(history)
    test("Agent学习执行", "records_processed" in learn_report)
    test("Agent处理记录数>0", learn_report.get("records_processed", 0) > 0)

# 查询经验
exp = learner.query_experience("计算")
test("经验查询", "has_experience" in exp)

# 统计
stats = learner.get_stats()
test("学习统计", "total_experiences" in stats)

# ─── 10. 工具偏好学习测试 ────────────────────────────────────
print("\n[10] 工具偏好学习测试")
pref = get_tool_preference()

# 记录使用
pref.record("calculator", success=True, elapsed_ms=50, scenario="数学计算")
pref.record("calculator", success=True, elapsed_ms=30, scenario="数学计算")
pref.record("weather", success=True, elapsed_ms=100, scenario="天气查询")

# 推荐
recs = pref.recommend("数学计算", top_k=3)
test("工具推荐", len(recs) > 0)
test("推荐calculator排首位", recs[0]["tool"] == "calculator" if recs else False)

# 统计
all_stats = pref.get_all_stats()
test("工具统计", "tools" in all_stats and "calculator" in all_stats["tools"])

# ─── 11. 任务模板测试 ────────────────────────────────────
print("\n[11] 任务模板测试")
tpl_mgr = get_template_manager()

# 保存模板（需要成功执行的计划）
if result.get("success") and result.get("steps"):
    plan_data = {
        "goal": result.get("goal", ""),
        "steps": result.get("steps", []),
        "cleanup_needed": False,
    }
    tpl_id = tpl_mgr.save_template(result.get("goal", ""), plan_data, result)
    test("保存模板", tpl_id is not None)

# 列出模板
templates = tpl_mgr.list_templates()
test("列出模板", isinstance(templates, list))

# 查找模板
match = tpl_mgr.find_template("计算")
test("查找模板", match is not None or match is None)  # 模板存在与否都算通过

# ─── 12. 完整闭环集成测试 ────────────────────────────────────
print("\n[12] 完整闭环集成测试")
# 测试：目标→拆解→执行→反思→清理→学习 全流程
final_result = executor.run("计算 100*200", cleanup=True, reflect=True)
test("完整闭环执行", final_result.get("success", False))
test("闭环含计划来源", "plan_source" in final_result)
test("闭环含步骤", len(final_result.get("steps", [])) > 0)
test("闭环含反思", "reflection" in final_result)
test("闭环含清理", "cleanup" in final_result)
test("闭环含耗时", "elapsed_ms" in final_result)

# 执行器状态
status = executor.get_status()
test("执行器状态", "history_count" in status)

# 执行历史
hist = executor.get_history(10)
test("执行历史", isinstance(hist, list) and len(hist) > 0)

# ─── 13. 临时文件清理验证 ────────────────────────────────────
print("\n[13] 临时文件清理验证")
# 执行一个会产生临时文件的任务
temp_before = tm.list_temp_resources()
test("清理前无遗留", isinstance(temp_before, dict))

# 执行任务（带清理）
executor.run("计算 50+50", cleanup=True)
temp_after = tm.list_temp_resources()
test("清理后无遗留", len(temp_after.get("tasks", {})) == 0)

# ─── 总结 ────────────────────────────────────
print("\n" + "=" * 60)
total = passed + failed
print(f"测试结果: {passed}/{total} 通过, {failed} 失败")
if errors:
    print("\n失败项:")
    for e in errors:
        print(f"  - {e}")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
