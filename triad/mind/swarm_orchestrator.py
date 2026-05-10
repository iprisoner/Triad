from __future__ import annotations

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable
import asyncio
import logging
import time
import re

# ---------------------------------------------------------------------------
# 外部依赖兼容层：真实环境导入失败时提供可独立运行的 Stub，避免崩溃
# ---------------------------------------------------------------------------

try:
    from model_router import RouteStrategy, LLMResponse, RoutingDecision, ModelRouter
except Exception:
    class RouteStrategy(Enum):
        AUTO = auto()
        CREATIVE = auto()
        REASONING = auto()
        LONGFORM = auto()
        REVIEW = auto()
        CHAT = auto()
        LOCAL = auto()

    @dataclass
    class RoutingDecision:
        strategy: RouteStrategy
        primary: Any = None
        secondary: Optional[Any] = None
        estimated_input_tokens: int = 0
        estimated_output_tokens: int = 0
        context_summary: str = ""
        will_truncate: bool = False
        metadata: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class LLMResponse:
        vendor: str = ""
        model_id: str = ""
        content: str = ""
        usage: Dict[str, int] = field(default_factory=dict)
        finish_reason: Optional[str] = None
        latency_ms: float = 0.0
        raw_response: Optional[Dict[str, Any]] = None

    class ModelRouter:
        def route(
            self,
            task_description: str,
            strategy: RouteStrategy = RouteStrategy.AUTO,
            preferred_provider: Optional[str] = None,
            context_length_hint: Optional[int] = None,
        ) -> RoutingDecision:
            return RoutingDecision(strategy=strategy)

        async def execute(
            self,
            decision: RoutingDecision,
            prompt: str,
            call_fn: Optional[Callable] = None,
        ) -> LLMResponse:
            return LLMResponse(content="")


try:
    from prompts.roles import RoleConfig, ROLES, get_role
except Exception:
    @dataclass
    class RoleConfig:
        id: str = ""
        name: str = ""
        system_prompt: str = ""
        model_pref: str = "CHAT"
        allowed_tools: List[str] = field(default_factory=list)
        temperature: float = 0.7
        max_tokens: int = 4096
        description: str = ""

    ROLES: Dict[str, RoleConfig] = {}

    def get_role(role_id: str) -> Optional[RoleConfig]:
        return ROLES.get(role_id)


try:
    from acp_adapter.streaming_reporter import StreamingReporter
except Exception:
    class StreamingReporter:
        async def report_stage(
            self,
            task_id: str,
            stage: str,
            message: str,
            progress: Optional[float] = None,
        ) -> bool:
            return True


# ---------------------------------------------------------------------------
# 日志初始化
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("SwarmExecutor")


# ---------------------------------------------------------------------------
# 领域模型
# ---------------------------------------------------------------------------

class AggregationMode(Enum):
    """蜂群结果聚合模式。"""

    CONCAT = "concat"
    JOIN = "join"
    BEST = "best"
    MERGE = "merge"


@dataclass
class TemporaryAgent:
    """蜂群中的临时 Agent 定义。"""

    name: str
    role_id: str
    system_prompt: str
    allowed_tools: List[str]
    model_pref: str  # "REASONING" / "CREATIVE" / "CHAT" 等
    temperature: float = 0.7
    max_tokens: int = 4096
    priority: int = 5  # 1-10
    timeout: int = 60
    custom_params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """单个 Agent 的执行结果。"""

    agent_name: str
    content: str
    model_used: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    tool_calls: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    success: bool = True


@dataclass
class SwarmTask:
    """蜂群任务描述。"""

    task_id: str
    description: str
    agents: List[TemporaryAgent]
    parallel_limit: int = 3
    aggregation_mode: AggregationMode = AggregationMode.CONCAT
    context: Dict[str, Any] = field(default_factory=dict)
    evaluator: Optional[Callable[[List[AgentResult]], AgentResult]] = None
    max_output_tokens: int = 6000  # ★★★ 地雷 2 修复：聚合结果 Token 上限 ★★★


@dataclass
class SwarmResult:
    """蜂群任务最终聚合结果。"""

    task_id: str
    individual_results: List[AgentResult]
    aggregated_content: str
    total_tokens: int
    total_latency_ms: float
    aggregation_mode: str
    agent_count: int
    success_count: int
    failed_count: int


# ---------------------------------------------------------------------------
# 蜂群执行器
# ---------------------------------------------------------------------------

class SwarmExecutor:
    """Triad v2.2 蜂群调度器，负责并发调度多个 Agent 并聚合结果。"""

    def __init__(
        self,
        model_router: Any,
        streaming_reporter: Any,
        max_concurrent: int = 3,
        vram_scheduler: Any = None,
    ) -> None:
        """
        初始化蜂群执行器。

        :param model_router: 模型路由实例，提供 route / execute 能力
        :param streaming_reporter: 流式上报实例，提供 report_stage 能力
        :param max_concurrent: 全局最大并发数（信号量限速）
        :param vram_scheduler: VRAM 调度器实例（可选），用于推理引用计数保护
        """
        self.model_router = model_router
        self.streaming_reporter = streaming_reporter
        self.vram_scheduler = vram_scheduler
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.logger = logging.getLogger("swarm")

    async def _safe_report(
        self,
        task_id: str,
        stage: str,
        message: str,
        progress: Optional[float] = None,
    ) -> None:
        """安全上报阶段信息，捕获 reporter 异常以避免打断主流程。"""
        try:
            await self.streaming_reporter.report_stage(
                task_id, stage, message, progress
            )
        except Exception as exc:
            self.logger.warning("流式上报失败: %s", exc)

    async def execute_swarm(self, task: SwarmTask) -> SwarmResult:
        """
        执行蜂群任务，并发调度多个 Agent 并聚合结果。

        流程：
        1. 上报蜂群启动；
        2. 通过 asyncio.gather 并发执行所有 Agent（双层信号量限速）；
        3. 收集 AgentResult 并过滤异常；
        4. 调用聚合策略生成统一输出；
        5. 上报蜂群完成；
        6. 返回 SwarmResult（即使全部失败也不抛异常）。

        :param task: 蜂群任务描述
        :return: 蜂群执行结果
        """
        self.logger.info(
            "蜂群启动，任务 %s，共 %d 个 Agent 就位",
            task.task_id,
            len(task.agents),
        )
        await self._safe_report(
            task.task_id,
            "ORCHESTRATION",
            f"蜂群启动，{len(task.agents)} 个 Agent 就位",
            progress=0.0,
        )

        # 任务级并发限速（与实例级信号量形成双层保护）
        task_sem = asyncio.Semaphore(task.parallel_limit)

        async def _run_with_task_limit(agent: TemporaryAgent) -> AgentResult:
            async with task_sem:
                return await self._execute_single_agent(agent, task)

        coros = [_run_with_task_limit(agent) for agent in task.agents]
        # v2.3.1: 添加整体超时防止永久阻塞
        try:
            gathered = await asyncio.wait_for(
                asyncio.gather(*coros, return_exceptions=True),
                timeout=task.timeout_sec if hasattr(task, 'timeout_sec') and task.timeout_sec else 300.0,
            )
        except asyncio.TimeoutError:
            self.logger.error("[Swarm] 蜂群执行整体超时")
            gathered = []
            for agent in task.agents:
                gathered.append(AgentResult(
                    agent_name=getattr(agent, 'name', 'unknown'),
                    content="",
                    model_used="",
                    error="Swarm execution timed out",
                    success=False,
                ))

        results: List[AgentResult] = []
        for item in gathered:
            if isinstance(item, Exception):
                self.logger.error(
                    "Agent 执行层出现未捕获异常: %s", item
                )
                results.append(
                    AgentResult(
                        agent_name="unknown",
                        content="",
                        model_used="",
                        error=str(item),
                        success=False,
                    )
                )
            else:
                results.append(item)

        aggregated = self._aggregate(
            results,
            task.aggregation_mode,
            task.context,
            task.evaluator,
        )

        total_tokens = sum(
            r.prompt_tokens + r.completion_tokens for r in results
        )
        total_latency = sum(r.latency_ms for r in results)
        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        self.logger.info(
            "蜂群完成，任务 %s，成功 %d，失败 %d",
            task.task_id,
            success_count,
            failed_count,
        )
        await self._safe_report(
            task.task_id,
            "ORCHESTRATION",
            f"蜂群完成，成功 {success_count}/{len(task.agents)}",
            progress=1.0,
        )

        # ★★★ 地雷 2 修复：Token 上限检查 + Map-Reduce 压缩 ★★★
        estimated_tokens = self._estimate_tokens(aggregated)
        if estimated_tokens > task.max_output_tokens:
            self.logger.warning(
                f"聚合结果预估 {estimated_tokens} tokens 超过上限 {task.max_output_tokens}，"
                f"触发 Map-Reduce 压缩..."
            )
            await self._safe_report(
                task.task_id,
                "ORCHESTRATION",
                f"⚠️ 聚合结果过长 ({estimated_tokens} tokens)，触发 Map-Reduce 压缩...",
                progress=0.95,
            )
            aggregated = await self._compress_aggregated(aggregated, task)
            await self._safe_report(
                task.task_id,
                "ORCHESTRATION",
                f"✅ Map-Reduce 压缩完成: {len(aggregated)} 字",
                progress=1.0,
            )

        return SwarmResult(
            task_id=task.task_id,
            individual_results=results,
            aggregated_content=aggregated,
            total_tokens=total_tokens,
            total_latency_ms=total_latency,
            aggregation_mode=task.aggregation_mode.value,
            agent_count=len(task.agents),
            success_count=success_count,
            failed_count=failed_count,
        )

    async def _execute_single_agent(
        self, agent: TemporaryAgent, task: SwarmTask
    ) -> AgentResult:
        """
        在信号量保护下执行单个 Agent。

        完整流程：
        1. 上报开始；
        2. 组装 system + user 提示词；
        3. 映射 model_pref 到 RouteStrategy 并获取 RoutingDecision；
        4. 注入 allowed_tools 到 decision.metadata；
        5. 带超时调用 model_router.execute；
        6. 提取响应指标并上报完成；
        7. 任何异常均内部捕获，返回 success=False 的 AgentResult，绝不抛到 gather 外层。

        :param agent: 临时 Agent 定义
        :param task: 所属蜂群任务
        :return: Agent 执行结果
        """
        async with self.semaphore:
            try:
                await self._safe_report(
                    task.task_id,
                    "EXECUTION",
                    f"[{agent.name}] 正在处理...",
                    progress=None,
                )

                # ★★★ 地雷 1 修复：声明开始本地 LLM 推理 ★★★
                has_vram_scheduler = False
                try:
                    has_vram_scheduler = (
                        self.vram_scheduler is not None
                        and hasattr(self.vram_scheduler, "begin_llm_inference")
                    )
                    if has_vram_scheduler:
                        try:
                            inference_ok = await self.vram_scheduler.begin_llm_inference(timeout_sec=5.0)
                            if not inference_ok:
                                self.logger.warning(f"[{agent.name}] VRAM 切换中，推理请求排队超时，继续尝试...")
                        except Exception as vram_exc:
                            self.logger.warning(f"[{agent.name}] begin_llm_inference 失败 (非致命): {vram_exc}")

                except Exception as _vram_outer_exc:
                    self.logger.warning(f"[{agent.name}] VRAM 调度器检查失败 (非致命): {_vram_outer_exc}")
                prompt_with_system = (
                    f"[系统指令]\n{agent.system_prompt}\n\n"
                    f"[用户请求]\n{task.description}"
                )

                # 映射 model_pref 到 RouteStrategy，失败则回退 AUTO
                try:
                    strategy = RouteStrategy[agent.model_pref.upper()]
                except (KeyError, AttributeError):
                    self.logger.warning(
                        "Agent %s 的 model_pref '%s' 无法映射到 RouteStrategy，"
                        "回退到 AUTO",
                        agent.name,
                        agent.model_pref,
                    )
                    strategy = RouteStrategy.AUTO

                decision = self.model_router.route(
                    task.description,
                    strategy=strategy,
                )

                if agent.allowed_tools:
                    decision.metadata["allowed_tools"] = agent.allowed_tools

                t0 = time.perf_counter()
                response = await asyncio.wait_for(
                    self.model_router.execute(decision, prompt_with_system),
                    timeout=agent.timeout,
                )
                latency_ms = (time.perf_counter() - t0) * 1000.0
                if response.latency_ms > 0:
                    latency_ms = response.latency_ms

                await self._safe_report(
                    task.task_id,
                    "EXECUTION",
                    f"[{agent.name}] 处理完成 ({response.model_id})",
                    progress=None,
                )

                return AgentResult(
                    agent_name=agent.name,
                    content=response.content,
                    model_used=response.model_id,
                    prompt_tokens=response.usage.get("prompt_tokens", 0),
                    completion_tokens=response.usage.get(
                        "completion_tokens", 0
                    ),
                    latency_ms=latency_ms,
                    success=True,
                )
            except Exception as exc:
                self.logger.error(
                    "Agent %s 执行失败: %s",
                    agent.name,
                    exc,
                    exc_info=True,
                )
                await self._safe_report(
                    task.task_id,
                    "EXECUTION",
                    f"[{agent.name}] 执行失败: {exc}",
                    progress=None,
                )
                return AgentResult(
                    agent_name=agent.name,
                    content="",
                    model_used="",
                    error=str(exc),
                    success=False,
                )
            finally:
                # ★★★ 地雷 1 修复：释放推理锁 ★★★
                if has_vram_scheduler:
                    try:
                        await self.vram_scheduler.end_llm_inference()
                    except Exception as vram_exc:
                        self.logger.warning(f"[{agent.name}] end_llm_inference 失败 (非致命): {vram_exc}")

    def _aggregate(
        self,
        results: List[AgentResult],
        mode: AggregationMode,
        context: Optional[Dict[str, Any]] = None,
        evaluator: Optional[Callable[[List[AgentResult]], AgentResult]] = None,
    ) -> str:
        """
        根据指定聚合模式将多个 Agent 结果合并为单一输出。

        策略说明：
        - CONCAT：使用 "\\n\\n---\\n\\n" 分隔拼接所有成功结果；
        - JOIN：使用 context 中 join_delimiter（默认换行）拼接；
        - BEST：优先调用 evaluator，否则选择 completion_tokens 最多的结果；
        - MERGE：按段落去重后保留顺序拼接。

        :param results: 各 Agent 执行结果列表
        :param mode: 聚合模式
        :param context: 任务上下文，用于 JOIN 获取分隔符
        :param evaluator: 外部评估函数，用于 BEST 模式
        :return: 聚合后的字符串内容
        """
        # 兼容性处理：如果 mode 是字符串（来自 stub），转换为枚举
        if isinstance(mode, str):
            try:
                mode = AggregationMode(mode)
            except ValueError:
                self.logger.warning(f"未知聚合模式 '{mode}'，回退到 CONCAT")
                mode = AggregationMode.CONCAT
        successful = [r for r in results if r.success and r.content]
        if not successful:
            return ""

        if mode == AggregationMode.CONCAT:
            return "\n\n---\n\n".join(r.content for r in successful)

        if mode == AggregationMode.JOIN:
            delimiter = "\n"
            if context:
                delimiter = context.get("join_delimiter", "\n")
            return delimiter.join(r.content for r in successful)

        if mode == AggregationMode.BEST:
            if evaluator is not None:
                try:
                    best = evaluator(results)
                    if best and best.content:
                        return best.content
                except Exception as exc:
                    self.logger.error(
                        "Evaluator 执行失败，回退到启发式选择: %s", exc
                    )
            best_result = max(
                successful, key=lambda r: r.completion_tokens
            )
            return best_result.content

        if mode == AggregationMode.MERGE:
            seen: set[str] = set()
            merged_parts: List[str] = []
            for r in successful:
                for paragraph in r.content.split("\n\n"):
                    para = paragraph.strip()
                    if para and para not in seen:
                        seen.add(para)
                        merged_parts.append(para)
            return "\n\n".join(merged_parts)

        # 未知模式回退：简单拼接
        return "\n\n".join(r.content for r in successful)

    # ------------------------------------------------------------------
    # 地雷 2 修复：Token 估算 + Map-Reduce 压缩
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        启发式 Token 估算（无 tiktoken 依赖）。

        中文字符 ≈ 1 token，英文单词 ≈ 1.3 tokens，
        标点符号按文本长度 10% 估算兜底。

        Args:
            text: 待估算文本

        Returns:
            预估 token 数（整数）
        """
        if not text:
            return 0
        cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        en_words = len(re.findall(r"[a-zA-Z]+", text))
        punctuation = int(len(text) * 0.1)
        return int(cn_chars + en_words * 1.3 + punctuation)

    async def _compress_aggregated(self, content: str, task: SwarmTask) -> str:
        """
        Map-Reduce 压缩：调用轻量模型将超长聚合内容摘要。

        当聚合结果预估 token 数超过 SwarmTask.max_output_tokens 时触发。
        构造压缩 prompt，调用 model_router 执行摘要，返回压缩后的文本。
        压缩失败时回退到截断策略。

        Args:
            content: 原始聚合内容（可能超长）
            task: 蜂群任务（含 max_output_tokens 限制）

        Returns:
            压缩后的文本
        """
        target_len = int(task.max_output_tokens * 0.8)
        compress_prompt = (
            f"请将以下多份报告的聚合内容压缩为一份精简的综合报告。\n"
            f"要求：\n"
            f"1. 保留核心观点、关键数据和结论\n"
            f"2. 去除重复信息和冗余描述\n"
            f"3. 输出不超过 {target_len} 字\n"
            f"4. 保持原有结构和层次\n\n"
            f"---\n{content}\n---"
        )
        try:
            decision = self.model_router.route(
                "文本摘要压缩",
                strategy=RouteStrategy.REASONING,
            )
            response = await self.model_router.execute(decision, compress_prompt)
            if response and response.content:
                self.logger.info(
                    f"Map-Reduce 压缩完成: {len(content)} 字 → {len(response.content)} 字"
                )
                return response.content
        except Exception as exc:
            self.logger.error(f"Map-Reduce 压缩失败: {exc}")

        # 回退：截断 + 保留首尾
        self.logger.warning("Map-Reduce 压缩失败，回退到截断策略")
        head_len = target_len // 3
        tail_len = target_len // 3
        middle = "\n\n...[内容过长，中间部分省略]...\n\n"
        return content[:head_len] + middle + content[-tail_len:]

    # -----------------------------------------------------------------------
    # 工厂方法：按角色与变体快速创建 TemporaryAgent
    # -----------------------------------------------------------------------

    @staticmethod
    def create_researcher(variant: str = "default") -> TemporaryAgent:
        """
        创建研究员 Agent 工厂实例，支持 default / deep / tech 变体。

        优先从 roles.get_role("researcher") 加载基础配置，失败则使用内置默认值。

        :param variant: 变体标识
        :return: 临时 Agent 实例
        """
        base_role: Optional[RoleConfig] = None
        try:
            base_role = get_role("researcher")
        except Exception:
            base_role = None

        if base_role is None:
            system_prompt = (
                "你是一名研究员，擅长信息收集、文献检索与多维度分析。"
                "请提供结构化、有理有据的研究成果。"
            )
            allowed_tools = ["web_search", "browser"]
            model_pref = "REASONING"
            name = "Researcher"
            role_id = "researcher"
            temperature = 0.7
            max_tokens = 4096
        else:
            system_prompt = base_role.system_prompt
            allowed_tools = list(base_role.allowed_tools)
            model_pref = base_role.model_pref
            name = base_role.name
            role_id = base_role.id
            temperature = base_role.temperature
            max_tokens = base_role.max_tokens

        if variant == "deep":
            system_prompt += (
                "\n请执行深度研究，挖掘深层信息、"
                "交叉验证来源并提供详尽分析。"
            )
            allowed_tools.append("deep_research")
            model_pref = "LONGFORM"
        elif variant == "tech":
            system_prompt += (
                "\n请聚焦技术实现、架构设计、"
                "性能指标与工程可行性分析。"
            )
            allowed_tools.append("code_search")
            model_pref = "REASONING"

        return TemporaryAgent(
            name=name,
            role_id=role_id,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model_pref=model_pref,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @staticmethod
    def create_writer(variant: str = "default") -> TemporaryAgent:
        """
        创建写作 Agent 工厂实例，支持 default / novel / copy / tech 变体。

        优先从 roles.get_role("writer") 加载基础配置，失败则使用内置默认值。

        :param variant: 变体标识
        :return: 临时 Agent 实例
        """
        base_role: Optional[RoleConfig] = None
        try:
            base_role = get_role("writer")
        except Exception:
            base_role = None

        if base_role is None:
            system_prompt = (
                "你是一名专业写手，擅长根据需求产出高质量文本。"
                "注意语言风格、结构与读者体验。"
            )
            allowed_tools: List[str] = []
            model_pref = "CREATIVE"
            name = "Writer"
            role_id = "writer"
            temperature = 0.7
            max_tokens = 4096
        else:
            system_prompt = base_role.system_prompt
            allowed_tools = list(base_role.allowed_tools)
            model_pref = base_role.model_pref
            name = base_role.name
            role_id = base_role.id
            temperature = base_role.temperature
            max_tokens = base_role.max_tokens

        if variant == "novel":
            system_prompt += (
                "\n请发挥创意，注重叙事节奏、"
                "人物塑造与情感张力，产出小说或故事类文本。"
            )
            model_pref = "CREATIVE"
        elif variant == "copy":
            system_prompt += (
                "\n请聚焦营销转化，产出吸睛标题、"
                "精炼卖点与行动召唤文案。"
            )
            allowed_tools.append("trend_search")
            model_pref = "CHAT"
        elif variant == "tech":
            system_prompt += (
                "\n请产出技术文档、API 说明或操作手册，"
                "注重准确性与可读性。"
            )
            model_pref = "LONGFORM"

        return TemporaryAgent(
            name=name,
            role_id=role_id,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model_pref=model_pref,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @staticmethod
    def create_reviewer(variant: str = "default") -> TemporaryAgent:
        """
        创建审校 Agent 工厂实例，支持 default / code / logic 变体。

        优先从 roles.get_role("reviewer") 加载基础配置，失败则使用内置默认值。

        :param variant: 变体标识
        :return: 临时 Agent 实例
        """
        base_role: Optional[RoleConfig] = None
        try:
            base_role = get_role("reviewer")
        except Exception:
            base_role = None

        if base_role is None:
            system_prompt = (
                "你是一名审校专家，擅长发现文本中的事实错误、"
                "逻辑漏洞与表达问题。请给出明确的修改建议。"
            )
            allowed_tools: List[str] = []
            model_pref = "REVIEW"
            name = "Reviewer"
            role_id = "reviewer"
            temperature = 0.3
            max_tokens = 4096
        else:
            system_prompt = base_role.system_prompt
            allowed_tools = list(base_role.allowed_tools)
            model_pref = base_role.model_pref
            name = base_role.name
            role_id = base_role.id
            temperature = base_role.temperature
            max_tokens = base_role.max_tokens

        if variant == "code":
            system_prompt += (
                "\n请聚焦代码审查：检查 Bug、安全漏洞、"
                "性能隐患与代码规范。"
            )
            allowed_tools.extend(["static_analysis", "lint"])
            model_pref = "REASONING"
        elif variant == "logic":
            system_prompt += (
                "\n请聚焦逻辑与论证审查："
                "检查因果链、前提假设与推理严密性。"
            )
            model_pref = "REASONING"

        return TemporaryAgent(
            name=name,
            role_id=role_id,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model_pref=model_pref,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @staticmethod
    def create_coder(variant: str = "default") -> TemporaryAgent:
        """
        创建程序员 Agent 工厂实例，支持 default / frontend / backend 变体。

        优先从 roles.get_role("coder") 加载基础配置，失败则使用内置默认值。

        :param variant: 变体标识
        :return: 临时 Agent 实例
        """
        base_role: Optional[RoleConfig] = None
        try:
            base_role = get_role("coder")
        except Exception:
            base_role = None

        if base_role is None:
            system_prompt = (
                "你是一名程序员，擅长编写高质量、可维护的代码。"
                "请遵循最佳实践，提供清晰的注释与错误处理。"
            )
            allowed_tools = ["code_search", "syntax_check"]
            model_pref = "REASONING"
            name = "Coder"
            role_id = "coder"
            temperature = 0.2
            max_tokens = 4096
        else:
            system_prompt = base_role.system_prompt
            allowed_tools = list(base_role.allowed_tools)
            model_pref = base_role.model_pref
            name = base_role.name
            role_id = base_role.id
            temperature = base_role.temperature
            max_tokens = base_role.max_tokens

        if variant == "frontend":
            system_prompt += (
                "\n请聚焦前端开发：HTML、CSS、JavaScript/TypeScript、"
                "React/Vue 及 UI/UX 实现。"
            )
            allowed_tools.extend(["ui_preview", "browser"])
            model_pref = "CREATIVE"
        elif variant == "backend":
            system_prompt += (
                "\n请聚焦后端开发：API 设计、数据库建模、"
                "并发处理与系统架构。"
            )
            allowed_tools.extend(["db_design", "api_test"])
            model_pref = "REASONING"

        return TemporaryAgent(
            name=name,
            role_id=role_id,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model_pref=model_pref,
            temperature=temperature,
            max_tokens=max_tokens,
        )

# v2.3.1: 别名兼容（文件名是 swarm_orchestrator，类名是 SwarmExecutor）
SwarmOrchestrator = SwarmExecutor
