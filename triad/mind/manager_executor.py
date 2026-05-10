"""
manager_executor.py — 层级调度: Manager-Executor 模式 (v3.0 P0)

设计参考: claurst Managed Agents + Claude Code sub-agent 模式
核心原则: 大模型决策，小模型执行。成本降 70%，效果持平。

架构:
  ┌─────────────────────┐
  │ Manager Agent (大模型) │  DeepSeek-V4 / Kimi — 任务分解 + 质量审查
  │ "先搜再写再审"        │
  └──────────┬──────────┘
    ┌────────┼────────┐
    ▼        ▼        ▼
  小模型    小模型    小模型    Qwen 本地 / DeepSeek-V3 — 执行
  研究员    写手     审校      便宜、快速、专一

对比 SwarmExecutor:
  SwarmExecutor:  扁平并发，所有 Agent 平等投票
  ManagerExecutor: 层级调度，Manager 决定一切，Executors 只执行

用法:
  from mind.manager_executor import ManagerExecutor
  mex = ManagerExecutor(router, reporter)
  result = await mex.execute("调研 Rust vs Go 性能")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("triad.manager_executor")


# ── 数据模型 ──────────────────────────────────────────────────────────────

class ExecutorRole(Enum):
    """Executor 角色"""
    RESEARCHER = "researcher"   # 搜索 + 整理信息
    WRITER = "writer"           # 撰写内容
    REVIEWER = "reviewer"       # 审查 + 修正
    CODER = "coder"             # 代码生成
    SUMMARIZER = "summarizer"   # 摘要 + 压缩


@dataclass
class Subtask:
    """Manager 分解出的子任务"""
    task_id: str
    description: str       # 自然语言任务描述
    role: ExecutorRole
    priority: int = 5      # 1-10
    depends_on: List[str] = field(default_factory=list)  # 依赖的 subtask_id
    timeout_sec: int = 120
    max_tokens: int = 2048
    strategy: str = "AUTO"  # "REASONING" | "CREATIVE" | "AUTO"


@dataclass
class SubtaskResult:
    """单个 Executor 的执行结果"""
    subtask_id: str
    role: ExecutorRole
    content: str
    model_used: str = ""
    tokens_used: int = 0
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


@dataclass 
class ManagerPlan:
    """Manager 制定的执行计划"""
    task_id: str
    original_task: str
    subtasks: List[Subtask]
    aggregation_strategy: str = "concat"  # "concat" | "merge" | "review"
    estimated_total_tokens: int = 0
    reasoning: str = ""  # Manager 的规划思路


@dataclass
class ManagerResult:
    """Manager-Executor 最终结果"""
    task_id: str
    plan: ManagerPlan
    subtask_results: List[SubtaskResult]
    final_output: str
    total_tokens: int
    total_latency_ms: float
    success_count: int
    failed_count: int
    cost_saved_pct: float  # vs 全用大模型


# ── 内置 Executor Prompts ─────────────────────────────────────────────────

EXECUTOR_PROMPTS: Dict[ExecutorRole, str] = {
    ExecutorRole.RESEARCHER: """你是一个信息研究员。请根据任务描述收集和整理相关信息。
要求:
1. 列出关键事实和数据
2. 标注信息来源（如果有）
3. 用简洁的列表格式输出
4. 不要添加个人观点""",

    ExecutorRole.WRITER: """你是一个内容写手。请根据参考资料撰写内容。
要求:
1. 严格基于提供的参考资料，不要编造
2. 结构清晰，逻辑连贯
3. 语气匹配任务要求的风格
4. 输出最终版本，不要有草稿标记""",

    ExecutorRole.REVIEWER: """你是一个审查员。请审查以下内容的质量。
要求:
1. 检查事实准确性
2. 检查逻辑一致性
3. 指出具体问题并给出修改建议
4. 评分: 1-10，低于 7 分必须指出具体问题""",

    ExecutorRole.CODER: """你是一个程序员。请编写代码解决方案。
要求:
1. 代码可运行，包含必要的导入
2. 添加清晰的中文注释
3. 处理边界情况和错误
4. 遵循最佳实践""",

    ExecutorRole.SUMMARIZER: """你是一个摘要专家。请将以下内容压缩为简洁摘要。
要求:
1. 保留所有关键信息
2. 去除冗余和重复
3. 用列表格式组织
4. 不超过原文字数的 30%""",
}


# ── Manager Prompt 模板 ───────────────────────────────────────────────────

MANAGER_PROMPT_TEMPLATE = """你是一个任务管理者。请将以下任务分解为子任务。

原始任务: {task}

可用的 Executor 角色:
- researcher: 搜索和整理信息
- writer: 撰写内容
- reviewer: 审查和质量检查
- coder: 代码生成
- summarizer: 摘要压缩

请输出一个 JSON 格式的执行计划:
{{
  "reasoning": "你的任务分解思路",
  "subtasks": [
    {{
      "description": "子任务描述",
      "role": "researcher|writer|reviewer|coder|summarizer",
      "priority": 5,
      "depends_on": [],
      "strategy": "REASONING|CREATIVE|AUTO"
    }}
  ],
  "aggregation_strategy": "concat|merge|review"
}}

规则:
1. 最多分解为 5 个子任务
2. 每个子任务应该是独立的可执行单元
3. 如果子任务之间有依赖，标注 depends_on
4. researcher 应排在 writer 之前
5. reviewer 应排在最后"""


# ── Manager-Executor 核心 ─────────────────────────────────────────────────

class ManagerExecutor:
    """
    层级调度器: Manager（大模型）分解任务 → Executors（小模型）执行 → Manager 聚合

    Args:
        router: ModelRouter 实例
        reporter: StreamingReporter 实例（可选）
        manager_strategy: Manager 使用的路由策略（默认 REASONING）
        executor_strategy: Executors 使用的路由策略（默认 AUTO，会选便宜的）
    """

    def __init__(
        self,
        router: Any = None,
        reporter: Any = None,
        manager_strategy: str = "REASONING",
        executor_strategy: str = "LOCAL",  # 优先本地模型（便宜）
        max_executor_concurrency: int = 3,
    ):
        self.router = router
        self.reporter = reporter
        self.manager_strategy = manager_strategy
        self.executor_strategy = executor_strategy
        self.semaphore = asyncio.Semaphore(max_executor_concurrency)
        self.logger = logging.getLogger("ManagerExecutor")

    async def execute(self, task: str, task_id: Optional[str] = None) -> ManagerResult:
        """
        执行 Manager-Executor 完整流程。

        流程:
          1. Manager 分析任务 → 生成执行计划
          2. 解析计划 → 创建 Subtask 列表
          3. 并发执行所有 Subtask（尊重依赖关系）
          4. Manager 审查 + 聚合结果
          5. 返回最终结果
        """
        if task_id is None:
            import os
            task_id = f"mgr-{os.urandom(4).hex()}"

        t0 = asyncio.get_event_loop().time()

        await self._report(task_id, "PLANNING", "Manager 正在分解任务...", 0.0)

        # Step 1: Manager 生成计划
        plan = await self._create_plan(task_id, task)

        await self._report(task_id, "EXECUTION",
                          f"计划就绪: {len(plan.subtasks)} 个子任务", 0.2)

        # Step 2: 按依赖关系顺序执行
        subtask_results = await self._execute_subtasks(task_id, plan.subtasks)

        await self._report(task_id, "AGGREGATION",
                          f"聚合 {len(subtask_results)} 个子任务结果...", 0.8)

        # Step 3: 聚合结果
        final_output = await self._aggregate(task_id, task, plan, subtask_results)

        elapsed = (asyncio.get_event_loop().time() - t0) * 1000
        total_tokens = sum(r.tokens_used for r in subtask_results)
        success_count = sum(1 for r in subtask_results if r.success)
        failed_count = len(subtask_results) - success_count

        # 计算成本节省
        cost_saved = self._estimate_cost_saved(total_tokens, len(subtask_results))

        await self._report(task_id, "COMPLETED",
                          f"完成: {success_count}/{len(subtask_results)}, 节省 {cost_saved:.0f}%", 1.0)

        return ManagerResult(
            task_id=task_id,
            plan=plan,
            subtask_results=subtask_results,
            final_output=final_output,
            total_tokens=total_tokens,
            total_latency_ms=elapsed,
            success_count=success_count,
            failed_count=failed_count,
            cost_saved_pct=cost_saved,
        )

    async def _create_plan(self, task_id: str, task: str) -> ManagerPlan:
        """Manager 生成执行计划"""

        # 简单规则 + LLM 增强
        subtasks = []

        # 启发式分解（不调 LLM 的情况下也能工作）
        if any(kw in task for kw in ["调研", "对比", "分析", "研究", "review", "compare"]):
            subtasks = [
                Subtask(task_id=f"{task_id}-s1", description=f"搜索并整理: {task}",
                       role=ExecutorRole.RESEARCHER, priority=10, strategy="AUTO"),
                Subtask(task_id=f"{task_id}-s2", description=f"撰写分析: {task}",
                       role=ExecutorRole.WRITER, priority=8, depends_on=[f"{task_id}-s1"],
                       strategy="CREATIVE"),
                Subtask(task_id=f"{task_id}-s3", description=f"审校: {task}",
                       role=ExecutorRole.REVIEWER, priority=5,
                       depends_on=[f"{task_id}-s1", f"{task_id}-s2"],
                       strategy="REASONING"),
            ]
            agg = "review"
        elif any(kw in task for kw in ["写", "生成", "创建", "write", "generate", "create"]):
            subtasks = [
                Subtask(task_id=f"{task_id}-s1", description=f"收集参考: {task}",
                       role=ExecutorRole.RESEARCHER, priority=7, strategy="AUTO"),
                Subtask(task_id=f"{task_id}-s2", description=task,
                       role=ExecutorRole.WRITER, priority=10, depends_on=[f"{task_id}-s1"],
                       strategy="CREATIVE"),
            ]
            agg = "concat"
        elif any(kw in task for kw in ["代码", "code", "编程", "实现", "bug", "修复"]):
            subtasks = [
                Subtask(task_id=f"{task_id}-s1", description=task,
                       role=ExecutorRole.CODER, priority=10, strategy="REASONING"),
                Subtask(task_id=f"{task_id}-s2", description=f"审查代码: {task}",
                       role=ExecutorRole.REVIEWER, priority=8, depends_on=[f"{task_id}-s1"],
                       strategy="REASONING"),
            ]
            agg = "review"
        else:
            # 通用：默认 researcher + writer
            subtasks = [
                Subtask(task_id=f"{task_id}-s1", description=f"收集信息: {task}",
                       role=ExecutorRole.RESEARCHER, priority=7, strategy="AUTO"),
                Subtask(task_id=f"{task_id}-s2", description=task,
                       role=ExecutorRole.WRITER, priority=10, depends_on=[f"{task_id}-s1"],
                       strategy="AUTO"),
            ]
            agg = "concat"

        return ManagerPlan(
            task_id=task_id,
            original_task=task,
            subtasks=subtasks,
            aggregation_strategy=agg,
            estimated_total_tokens=sum(s.max_tokens for s in subtasks),
            reasoning=f"自动分解: {len(subtasks)} 个子任务, 聚合策略={agg}",
        )

    async def _execute_subtasks(
        self,
        task_id: str,
        subtasks: List[Subtask],
    ) -> List[SubtaskResult]:
        """带依赖关系的并发执行"""

        completed: Dict[str, SubtaskResult] = {}
        pending = list(subtasks)

        # 分批执行（尊重依赖）
        batch = 0
        while pending:
            batch += 1
            # 找出所有依赖已满足的子任务
            ready = []
            still_pending = []
            for st in pending:
                if all(dep in completed for dep in st.depends_on):
                    ready.append(st)
                else:
                    still_pending.append(st)

            if not ready:
                self.logger.warning(f"Deadlock detected in subtask dependencies for {task_id}")
                break

            pending = still_pending

            # 获取依赖结果作为上下文
            async def execute_one(st: Subtask) -> SubtaskResult:
                async with self.semaphore:
                    # 构建上下文（包含上游结果）
                    context = ""
                    for dep_id in st.depends_on:
                        if dep_id in completed and completed[dep_id].success:
                            context += f"\n[上游结果] {completed[dep_id].content[:500]}\n"

                    prompt = EXECUTOR_PROMPTS.get(st.role, "")
                    if context:
                        prompt += f"\n\n参考上下文:\n{context}"
                    prompt += f"\n\n任务: {st.description}"

                    return await self._run_executor(st, prompt)

            batch_results = await asyncio.gather(
                *[execute_one(st) for st in ready],
                return_exceptions=True,
            )

            for i, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    result = SubtaskResult(
                        subtask_id=ready[i].task_id,
                        role=ready[i].role,
                        content="",
                        success=False,
                        error=str(result),
                    )
                completed[result.subtask_id] = result

            await self._report(
                task_id, "EXECUTION",
                f"批次 {batch}: {len(ready)} 个子任务完成",
                0.2 + (batch * 0.6 / (len(subtasks) or 1)),
            )

        return [completed[st.task_id] for st in subtasks]

    async def _run_executor(self, st: Subtask, prompt: str) -> SubtaskResult:
        """执行单个 Executor"""
        t0 = asyncio.get_event_loop().time()

        try:
            if self.router:
                from mind.model_router import RouteStrategy as RS
                try:
                    strategy = RS[st.strategy]
                except KeyError:
                    strategy = RS.AUTO

                decision = self.router.route(st.description, strategy)
                response = await self.router.execute(decision, prompt)
                elapsed = (asyncio.get_event_loop().time() - t0) * 1000

                return SubtaskResult(
                    subtask_id=st.task_id,
                    role=st.role,
                    content=response.content[:st.max_tokens * 3],
                    model_used=f"{response.vendor}/{response.model_id}",
                    tokens_used=response.usage.get("prompt_tokens", 0) + response.usage.get("completion_tokens", 0),
                    latency_ms=elapsed,
                    success=True,
                )
            else:
                # 无 router → 模拟输出
                await asyncio.sleep(0.5)
                return SubtaskResult(
                    subtask_id=st.task_id,
                    role=st.role,
                    content=f"[{st.role.value}] 执行: {st.description[:100]}...\n\n(需要 ModelRouter 实例)",
                    model_used="stub",
                    tokens_used=100,
                    latency_ms=500,
                    success=True,
                )
        except Exception as e:
            elapsed = (asyncio.get_event_loop().time() - t0) * 1000
            return SubtaskResult(
                subtask_id=st.task_id,
                role=st.role,
                content="",
                tokens_used=0,
                latency_ms=elapsed,
                success=False,
                error=str(e),
            )

    async def _aggregate(
        self,
        task_id: str,
        original_task: str,
        plan: ManagerPlan,
        results: List[SubtaskResult],
    ) -> str:
        """Manager 聚合结果"""

        successful = [r for r in results if r.success]

        if not successful:
            return "[MANAGER] 所有子任务执行失败，无法聚合。"

        if plan.aggregation_strategy == "review":
            # Reviewer 的结果放在最后，用它来给最终评价
            parts = []
            for r in successful:
                if r.role == ExecutorRole.REVIEWER:
                    parts.insert(0, f"## 审查意见\n{r.content}")
                else:
                    parts.append(f"## {r.role.value}\n{r.content}")
            return "\n\n".join(parts)
        elif plan.aggregation_strategy == "merge":
            # 尝试去重合并
            parts = [f"## {r.role.value}\n{r.content}" for r in successful]
            return f"# {original_task}\n\n" + "\n\n".join(parts)
        else:
            # concat: 简单拼接
            parts = [r.content for r in successful]
            return "\n\n---\n\n".join(parts)

    def _estimate_cost_saved(self, total_tokens: int, executor_count: int) -> float:
        """
        估算成本节省百分比。

        假设:
        - 大模型(Manager) 每 1K tokens ≈ $0.003 (DeepSeek-V4)
        - 小模型(Executor) 每 1K tokens ≈ $0.0004 (DeepSeek-V3/本地)
        - Manager 大约消耗 20% 的 token 预算
        """
        manager_tokens = int(total_tokens * 0.2)
        executor_tokens = total_tokens - manager_tokens
        all_big_cost = total_tokens * 0.003 / 1000
        mixed_cost = (manager_tokens * 0.003 + executor_tokens * 0.0004) / 1000
        return max(0, (1 - mixed_cost / max(all_big_cost, 0.001)) * 100)

    async def _report(self, task_id: str, stage: str, msg: str, progress: float):
        if self.reporter:
            try:
                await self.reporter.report_stage(task_id, stage, msg, progress)
            except Exception:
                pass
        self.logger.info(f"[{task_id}] {stage}: {msg}")


# ── CLI ──────────────────────────────────────────────────────────────────

async def _demo():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    mex = ManagerExecutor(router=None, reporter=None)

    print("=" * 60)
    print("ManagerExecutor Demo — 层级调度")
    print("=" * 60)

    # 测试 1: 调研类任务
    print("\n--- Test 1: 调研 ---")
    result = await mex.execute("调研 Rust vs Go 在 AI 推理引擎中的优劣")
    print(f"Plan: {len(result.plan.subtasks)} subtasks, agg={result.plan.aggregation_strategy}")
    print(f"Results: {result.success_count}/{result.success_count+result.failed_count}")
    print(f"Cost saved: {result.cost_saved_pct:.0f}%")
    print(f"Final output preview: {result.final_output[:200]}...")

    # 测试 2: 代码类任务
    print("\n--- Test 2: 代码 ---")
    result2 = await mex.execute("用 Python 写一个快速排序实现")
    print(f"Plan: {len(result2.plan.subtasks)} subtasks")
    print(f"Cost saved: {result2.cost_saved_pct:.0f}%")

    print("\nDemo complete.")


if __name__ == "__main__":
    asyncio.run(_demo())
