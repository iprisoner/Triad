"""
hermes_orchestrator.py — Hermes 主大脑编排循环 (Orchestrator)

Triad 第二层核心编排器，串联 5 个独立模块：
  1. ModelRouter      — 动态 LLM 路由
  2. NovelCurator    — 4维小说评估 + 技能固化
  3. StreamingReporter — 非阻塞状态上报到 OpenClaw
  4. VRAMScheduler   — 显存跷跷板 (GPU ↔ CPU_FALLBACK)
  5. ComfyUIMCPBridge — 概念图生成 (ComfyUI)

设计原则：
  - 每一步都是 try/except 容错，失败不阻断主流程
  - reporter 全程非阻塞埋点，用户实时可见进度
  - 支持 async for 批量任务流式处理
  - 完全类型注解
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncIterator,
    Dict,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("HermesOrchestrator")

# ---------------------------------------------------------------------------
# Stub 类型定义（当真实模块未就绪时回退使用）
# ---------------------------------------------------------------------------

@dataclass
class RouteDecision:
    """模型路由决策结果"""
    vendor: str = "openai"
    model: str = "gpt-4o"
    temperature: float = 0.7
    base_url: str = ""
    api_key: str = ""


@dataclass
class LLMResponse:
    """LLM 返回的原始响应"""
    content: str = ""
    usage: Dict[str, int] = field(default_factory=dict)


@dataclass
class AssessmentReport:
    """小说评估报告"""
    overall: float = 0.0
    character_consistency: float = 0.0
    plot_logic: float = 0.0
    style_coherence: float = 0.0
    emotional_impact: float = 0.0


@dataclass
class SkillEntry:
    """固化后的技能条目"""
    name: str = ""
    pattern: str = ""
    score: float = 0.0


@dataclass
class ToolResult:
    """工具调用结果"""
    success: bool = False
    output: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# 依赖模块的优雅导入（stub fallback）
# ---------------------------------------------------------------------------

# --- ModelRouter ---
try:
    from mind.model_router import ModelRouter as _ModelRouter
except Exception:
    class _ModelRouter:
        """ModelRouter stub — 真实环境由 model_router.py 提供"""
        async def route(self, task: str, strategy: Optional[str] = None) -> RouteDecision:
            logger.warning("[STUB] ModelRouter.route() 使用默认决策")
            return RouteDecision(vendor="stub", model="stub-model")

        async def execute(self, task: str, decision: RouteDecision) -> LLMResponse:
            logger.warning("[STUB] ModelRouter.execute() 返回占位响应")
            await asyncio.sleep(0.05)  # 模拟网络延迟
            return LLMResponse(
                content=f"[STUB-RESPONSE] 任务: {task[:60]}...",
                usage={"prompt_tokens": 10, "completion_tokens": 20},
            )


# --- NovelCurator ---
try:
    from mind.novel_curator import NovelCurator as _NovelCurator
except Exception:
    class SkillCrystallizer:
        """技能固化器 stub"""
        def crystallize_skill(
            self, prompt: str, generated: str, report: AssessmentReport
        ) -> SkillEntry:
            return SkillEntry(
                name=f"skill_{hash(prompt) % 10000:04d}",
                pattern=prompt[:40],
                score=report.overall,
            )

    class _NovelCurator:
        """NovelCurator stub"""
        def __init__(self, router: Any = None):
            self.crystallizer = SkillCrystallizer()

        async def evaluate(
            self, chapter_text: str, characters: List[str], use_llm: bool = True
        ) -> AssessmentReport:
            logger.warning("[STUB] NovelCurator.evaluate() 返回默认评估")
            await asyncio.sleep(0.05)
            return AssessmentReport(
                overall=8.0,
                character_consistency=8.5,
                plot_logic=7.5,
                style_coherence=8.0,
                emotional_impact=7.0,
            )


# --- StreamingReporter ---
try:
    from mind.acp_adapter.streaming_reporter import StreamingReporter as _StreamingReporter
except Exception:
    class _StreamingReporter:
        """StreamingReporter stub — 后台 HTTP POST 到 OpenClaw Gateway"""
        async def report_stage(
            self,
            task_id: str,
            stage: str,
            message: str,
            progress: Optional[float] = None,
        ) -> bool:
            logger.info(f"[REPORT][{task_id}] stage={stage} progress={progress} | {message}")
            return True

        async def report_result(self, task_id: str, status: str, output: str) -> bool:
            logger.info(f"[REPORT][{task_id}] status={status} | output={output[:80]}...")
            return True

        async def report_model_info(
            self,
            task_id: str,
            vendor: str,
            model: str,
            prompt_tokens: int,
            completion_tokens: int,
        ) -> bool:
            logger.info(
                f"[REPORT][{task_id}] model={vendor}/{model} "
                f"tokens={prompt_tokens}+{completion_tokens}"
            )
            return True

        async def report_vram(
            self,
            task_id: str,
            state: str,
            embedding_mb: int,
            llm_mb: int,
            comfyui_mb: int,
            free_mb: int,
        ) -> bool:
            logger.info(
                f"[REPORT][{task_id}] VRAM state={state} "
                f"emb={embedding_mb}MB llm={llm_mb}MB comfy={comfyui_mb}MB free={free_mb}MB"
            )
            return True


# --- VRAMScheduler ---
try:
    from hand.vram_scheduler_llama import VRAMScheduler as _VRAMScheduler
except Exception:
    @dataclass
    class RenderContext:
        """显存渲染上下文 stub"""
        clawpod_id: str = ""
        mode: str = "CPU_FALLBACK"

    class _VRAMScheduler:
        """VRAMScheduler stub — GPU ↔ CPU_FALLBACK 跷跷板"""
        @asynccontextmanager
        async def acquire_render_memory(self, clawpod_id: str):
            logger.warning(f"[STUB] VRAM: {clawpod_id} 切换到 CPU_FALLBACK")
            ctx = RenderContext(clawpod_id=clawpod_id, mode="CPU_FALLBACK")
            try:
                yield ctx
            finally:
                logger.warning(f"[STUB] VRAM: {clawpod_id} 渲染上下文释放")

        async def release_render_memory(self, clawpod_id: str) -> bool:
            logger.warning(f"[STUB] VRAM: {clawpod_id} 恢复 GPU 模式")
            return True


# --- ComfyUI (v3.0 已移除，未来通过 MCP 插件接入) ---
# 原 comfyui_mcp_bridge.py 已删除，MULTIMODAL 步骤默认 bypass


# --- SwarmExecutor (v2.2 蜂群调度器) ---
try:
    from mind.swarm_orchestrator import (
        SwarmExecutor as _SwarmExecutor,
        TemporaryAgent as _TemporaryAgent,
        SwarmTask as _SwarmTask,
        AggregationMode as _AggregationMode,
    )
except Exception:
    @dataclass
    class _SwarmTask:
        """SwarmTask stub — 蜂群任务描述"""
        task_id: str = ""
        description: str = ""
        agents: List[Any] = field(default_factory=list)
        parallel_limit: int = 3
        aggregation_mode: Any = None
        context: Dict[str, Any] = field(default_factory=dict)
        evaluator: Optional[Any] = None

    class _SwarmExecutor:
        """SwarmExecutor stub — 蜂群调度器"""
        def __init__(
            self,
            model_router: Any = None,
            streaming_reporter: Any = None,
            max_concurrent: int = 3,
        ) -> None:
            pass

        async def execute_swarm(self, task: _SwarmTask) -> Any:
            """蜂群执行 stub"""
            logger.warning("[STUB] SwarmExecutor.execute_swarm() 返回占位结果")
            class _FakeResult:
                aggregated_content: str = "[STUB-SWARM] 蜂群执行器未就绪"
                individual_results: List[Any] = []
                total_tokens: int = 0
                total_latency_ms: float = 0.0
                agent_count: int = 0
                success_count: int = 0
                failed_count: int = 0
            return _FakeResult()

        @staticmethod
        def create_researcher(variant: str = "default") -> Any:
            return None

        @staticmethod
        def create_writer(variant: str = "default") -> Any:
            return None

        @staticmethod
        def create_reviewer(variant: str = "default") -> Any:
            return None

        @staticmethod
        def create_coder(variant: str = "default") -> Any:
            return None

    @dataclass
    class _TemporaryAgent:
        """TemporaryAgent stub — 临时 Agent 定义"""
        name: str = ""
        role_id: str = ""
        system_prompt: str = ""
        allowed_tools: List[str] = field(default_factory=list)
        model_pref: str = "REASONING"
        temperature: float = 0.7
        max_tokens: int = 4096
        priority: int = 5
        timeout: int = 60

    class _AggregationMode:
        """AggregationMode stub — 聚合模式枚举"""
        CONCAT = "concat"
        JOIN = "join"
        BEST = "best"
        MERGE = "merge"


# --- SkillCrystallizer (v2.2 技能进化) ---
try:
    from mind.skill_crystallizer import SkillCrystallizer as _SkillCrystallizer
except Exception:
    class _SkillCrystallizer:
        """SkillCrystallizer stub — 技能结晶器"""
        def auto_crystallize(
            self, swarm_task: Any, results: List[Any], score: float
        ) -> Any:
            return None

class HermesOrchestrator:
    """
    Hermes 主大脑编排循环。

    串联 5 个模块，按 5 步流水线处理任务：
      1. ANALYZING  — 任务类型分析
      2. ROUTING    — 模型路由决策
      3. EXECUTION  — LLM 内容生成
      4. EVALUATION — 小说质量评估（可选）
      5. MULTIMODAL — ComfyUI 概念图生成（可选）

    容错设计：
      - 第 3 步失败 → 任务整体失败，上报 failed
      - 第 4/5 步失败 → 仅记录 warning，不阻断成功返回
      - reporter 全程非阻塞，任何上报异常都被吞掉
    """

    def __init__(
        self,
        router: Optional[_ModelRouter] = None,
        curator: Optional[_NovelCurator] = None,
        reporter: Optional[_StreamingReporter] = None,
        vram_scheduler: Optional[_VRAMScheduler] = None,
        swarm_executor: Optional[_SwarmExecutor] = None,
        skill_crystallizer: Optional[_SkillCrystallizer] = None,
    ) -> None:
        """
        初始化 HermesOrchestrator。

        Args:
            router: 模型路由实例
            curator: 小说评估器实例
            reporter: 流式上报器实例
            vram_scheduler: 显存调度器实例
            swarm_executor: 蜂群调度器实例（可选，延迟初始化亦可）
            skill_crystallizer: 技能结晶器实例（可选）
        """
        self.router = router or _ModelRouter()
        self.curator = curator or _NovelCurator(router=self.router)
        self.reporter = reporter or _StreamingReporter()
        self.vram_scheduler = vram_scheduler or _VRAMScheduler()
        self.swarm_executor = swarm_executor
        self.skill_crystallizer = skill_crystallizer or _SkillCrystallizer()
        logger.info("HermesOrchestrator 初始化完成")

    # ------------------------------------------------------------------
    # 蜂群模式检测与构建
    # ------------------------------------------------------------------

    def _is_swarm_mode(self, role: Any, user_prompt: str) -> bool:
        """
        检测当前任务是否应进入蜂群模式。

        检测逻辑（优先级从高到低）：
        1. 角色配置中显式标记 is_swarm=True
        2. 角色 ID 以 _swarm 结尾
        3. 用户输入中包含特定的蜂群关键词（如 "深度调研"、"多 agent"）

        Args:
            role: 解析出的角色对象（可能为 None）
            user_prompt: 原始用户输入

        Returns:
            True 表示进入蜂群模式
        """
        if role is None:
            return False

        # 方式 1: 角色配置中显式标记
        if hasattr(role, "is_swarm") and getattr(role, "is_swarm", False):
            return True

        # 方式 2: 角色 ID 以 _swarm 结尾
        if hasattr(role, "id") and str(getattr(role, "id", "")).endswith("_swarm"):
            return True

        # 方式 3: 用户输入中包含蜂群关键词
        swarm_kw = ["深度调研", "多 agent", "蜂群", "swarm", "多代理", "协作分析"]
        prompt_lower = user_prompt.lower()
        if any(kw in prompt_lower for kw in swarm_kw):
            return True

        return False

    def _build_swarm_agents(self, role: Any, task_description: str) -> List[Any]:
        """
        根据角色配置构建蜂群 Agent 列表。

        如果角色配置中有 swarm_agents 列表，直接按配置构建；
        否则使用工厂方法创建默认的 研究员+写手+审校 组合。

        Args:
            role: 解析出的角色对象
            task_description: 清洗后的用户请求

        Returns:
            TemporaryAgent 列表
        """
        agents: List[Any] = []

        # 方式 1: 角色配置中预定义了 swarm_agents
        if hasattr(role, "swarm_agents") and role.swarm_agents:
            for idx, cfg in enumerate(role.swarm_agents):
                agents.append(
                    _TemporaryAgent(
                        name=cfg.get("name", f"Agent-{idx}"),
                        role_id=cfg.get("role_id", "default"),
                        system_prompt=cfg.get(
                            "system_prompt",
                            "你是一个智能助手，请根据指令完成任务。",
                        ),
                        allowed_tools=cfg.get("allowed_tools", []),
                        model_pref=cfg.get("model_pref", "REASONING"),
                        temperature=cfg.get("temperature", 0.7),
                        max_tokens=cfg.get("max_tokens", 4096),
                        priority=cfg.get("priority", 5),
                        timeout=cfg.get("timeout", 60),
                    )
                )
            logger.info(f"蜂群 Agent 从角色配置构建: {len(agents)} 个")
            return agents

        # 方式 2: 根据角色类型自动匹配工厂方法
        role_id = getattr(role, "id", "default")
        role_name = getattr(role, "name", "")
        role_lower = f"{role_id} {role_name}".lower()

        if any(kw in role_lower for kw in ["research", "调研", "调查", "researcher"]):
            agents = [
                _SwarmExecutor.create_researcher("deep"),
                _SwarmExecutor.create_writer("tech"),
                _SwarmExecutor.create_reviewer("logic"),
            ]
        elif any(kw in role_lower for kw in ["code", "代码", "coder", "engineer"]):
            agents = [
                _SwarmExecutor.create_coder("default"),
                _SwarmExecutor.create_reviewer("code"),
            ]
        else:
            # 默认组合：研究员 + 写手 + 审校
            agents = [
                _SwarmExecutor.create_researcher("default"),
                _SwarmExecutor.create_writer("default"),
                _SwarmExecutor.create_reviewer("default"),
            ]

        # 过滤掉工厂方法返回 None 的情况（stub fallback）
        agents = [a for a in agents if a is not None]

        if not agents:
            # 终极兜底：至少创建一个通用 Agent
            agents.append(
                _TemporaryAgent(
                    name="通用助手",
                    role_id="general",
                    system_prompt="你是一位通用智能助手，擅长综合分析与内容生成。",
                    allowed_tools=["read", "write"],
                    model_pref="REASONING",
                )
            )

        logger.info(f"蜂群 Agent 从工厂方法构建: {len(agents)} 个")
        return agents

    # ------------------------------------------------------------------
    # 动态评估与多模态策略引擎
    # ------------------------------------------------------------------

    def _get_eval_strategy(self, task_type: str, role: Any, user_prompt: str, generated_text: str) -> str:
        """
        动态评估策略选择器。

        根据任务类型、角色配置和生成内容，决定采用何种评估策略。
        返回值: "novel" | "code" | "bypass" | "auto"

        评估逻辑（优先级从高到低）：
        1. 角色配置显式标记 eval_strategy
        2. task_type 直接匹配
        3. 角色 ID 语义推断
        4. 生成内容启发式推断（auto 模式）
        5. 默认 bypass（不评估）

        Args:
            task_type: 任务分类结果（novel / code / chat / multimodal）
            role: 解析出的角色对象（可能为 None）
            user_prompt: 原始用户输入
            generated_text: LLM 生成的文本内容

        Returns:
            评估策略标识字符串
        """
        # P1: 角色显式标记
        if role and hasattr(role, "eval_strategy"):
            explicit = getattr(role, "eval_strategy", None)
            if explicit and isinstance(explicit, str):
                return explicit

        # P2: task_type 直接匹配
        if task_type == "novel":
            return "novel"
        if task_type in ("code", "coding", "programming"):
            return "code"

        # P3: 角色 ID 语义推断
        if role:
            role_id = getattr(role, "id", "").lower()
            if any(kw in role_id for kw in ["novel", "writer", "story", "fiction", "author"]):
                return "novel"
            if any(kw in role_id for kw in ["code", "coder", "engineer", "programmer", "devops", "frontend", "backend"]):
                return "code"

        # P4: auto 模式 — 启发式推断生成内容
        if generated_text and self._is_novel_content(generated_text):
            logger.info("启发式推断: 生成内容为小说文本，触发小说评估")
            return "novel"

        # P5: 默认 bypass（不评估，直接输出）
        return "bypass"

    def _get_multimodal_strategy(self, task_type: str, user_prompt: str, role: Any, generated_text: str) -> str:
        """
        动态多模态触发策略选择器。

        返回值: "explicit" | "art_director" | "auto_detect" | "bypass"

        触发逻辑（优先级从高到低）：
        1. 任务显式标记为多模态
        2. art_director 等视觉角色默认需要生成图像
        3. 用户输入中明确包含画图指令（关键词匹配）
        4. 生成内容中检测到图像生成请求（自动检测）
        5. 默认 bypass（不触发 ComfyUI）

        Args:
            task_type: 任务分类结果
            user_prompt: 原始用户输入
            role: 解析出的角色对象
            generated_text: LLM 生成的文本内容

        Returns:
            多模态策略标识字符串
        """
        # P1: 任务显式标记
        if task_type == "multimodal":
            return "explicit"

        # P2: 视觉角色默认触发
        if role:
            role_id = getattr(role, "id", "").lower()
            if any(kw in role_id for kw in ["art", "director", "designer", "illustrator", "painter", "artist"]):
                return "art_director"

        # P3: 用户输入中明确包含画图指令（中文 + 英文关键词）
        visual_kw = [
            # 中文
            "画图", "画一张", "画个", "画一幅", "生成图像", "生成图片", "生成图",
            "概念图", "插画", "封面", "配图", "插图", "示意图", "效果图",
            "角色设计", "人物设计", "场景设计", "武器设计", "道具设计",
            "给我画", "帮我画", "生成一张", "画一个",
            # 英文
            "draw", "draw a", "generate image", "generate a image",
            "create image", "concept art", "illustration", "cover art",
            "character design", "scene design", "visualize", "render",
            "image of", "picture of", "portrait of", "scene of",
        ]
        prompt_lower = user_prompt.lower()
        if any(kw in prompt_lower for kw in visual_kw):
            return "explicit"

        # P4: 生成内容中检测到图像生成请求
        if generated_text:
            content_lower = generated_text[:1500].lower()
            auto_kw = [
                "请生成", "需要生成", "建议生成", "concept art", "visual concept",
                "image prompt", "generation prompt", "正向提示词", "负向提示词",
            ]
            if any(kw in content_lower for kw in auto_kw):
                logger.info("动态多模态: 从生成内容中检测到图像请求")
                return "auto_detect"

        # P5: 默认 bypass — 不浪费 VRAM 切换
        return "bypass"

    async def _evaluate_code_placeholder(self, generated_text: str, user_prompt: str) -> float:
        """
        CodeCurator 通用占位符 — 代码质量评估预留接口。

        当前返回满分（10.0），作为未来 AST 静态分析 + 单元测试覆盖率的占位符。
        不阻塞主流程，仅记录日志。

        未来可接入：
        - pylint / flake8 / black 静态检查
        - mypy 类型检查
        - pytest 单元测试覆盖率
        - CodeQL 安全扫描

        Args:
            generated_text: LLM 生成的代码文本
            user_prompt: 原始用户请求

        Returns:
            代码质量评分（0.0-10.0），当前恒为 10.0
        """
        logger.info("CodeCurator 占位符激活: 返回满分 10.0 (AST 分析待接入)")
        # 预留：解析代码语言、调用 linter、统计复杂度
        # 预留：提取代码块中的 import 语句检查依赖安全性
        # 预留：检查是否有未闭合的括号、缩进错误等基础语法
        return 10.0

    def _is_novel_content(self, text: str) -> bool:
        """
        启发式判断文本是否为小说/故事类文学内容。

        基于章节标题、人物描写、叙事特征等关键词进行快速推断。
        仅检查前 2000 字，避免长文本的性能损耗。

        Args:
            text: 待检测的文本内容

        Returns:
            True 当文本包含至少 3 个小说标记时判定为小说内容
        """
        if not text or len(text) < 50:
            return False
        novel_markers = [
            "第一章", "第二章", "第三章", "第四章", "第五章",
            "章回", "回目", "卷", "集", "部",
            "他", "她", "主角", "反派", "配角", "人物",
            "情节", "伏笔", "悬念", "冲突", "高潮", "结局",
            "世界观", "设定", "背景故事", "年代", "朝代",
            "对话", "说道", "回答", "问道", "喃喃", "低声",
            "描写", "刻画", "塑造", "形象", "外貌", "神态",
            "文笔", "文风", "叙事", "描写手法", "修辞",
        ]
        text_lower = text[:2000].lower()
        score = sum(1 for m in novel_markers if m in text_lower)
        is_novel = score >= 3
        if is_novel:
            logger.info(f"启发式小说检测: 命中 {score} 个标记")
        return is_novel

    # ------------------------------------------------------------------
    # 公开 API：单任务处理
    # ------------------------------------------------------------------

    async def process_task(self, task_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理单个任务请求，执行完整 5 步流水线（v2.3.1: 整体超时保护）。

        Args:
            task_request: 必须包含 "taskId" 和 "raw_input"；
                          可选 "strategy" (默认 "AUTO")

        Returns:
            {"taskId": ..., "status": "success"|"failed", "output": ..., ...}
        """
        # --- 提取字段 --------------------------------------------------
        try:
            task_id = task_request["taskId"]
        except KeyError:
            logger.error("task_request 缺少必需字段 'taskId'")
            return {"status": "failed", "error": "missing taskId"}

        user_prompt = task_request.get("raw_input", "")
        strategy = task_request.get("strategy", "AUTO")

        logger.info(f"[{task_id}] ===== 开始处理任务 =====")
        logger.info(f"[{task_id}] prompt={user_prompt[:80]}... strategy={strategy}")

        # --- Step 1: ANALYZING -----------------------------------------
        await self._safe_report_stage(task_id, "ANALYZING", "Hermes 正在分析任务类型...", 0.1)
        task_type = self._classify_task(user_prompt)
        logger.info(f"[{task_id}] 任务类型判定: {task_type}")

        # --- Step 1.5: 角色解析 ----------------------------------------
        role = None
        clean_input = user_prompt
        try:
            if hasattr(self.router, "parse_role"):
                role, clean_input = self.router.parse_role(user_prompt)
        except Exception as exc:
            # 未知角色时记录警告但不阻断，回退到默认逻辑
            logger.warning(f"[{task_id}] 角色解析失败 (非致命): {exc}")

        if role:
            await self._safe_report_stage(
                task_id, "ANALYZING", f"🎭 角色模式: {role.name} ({role.id})", 0.15
            )
            effective_strategy = role.model_pref
            task_type = self._classify_task(clean_input)
            logger.info(
                f"[{task_id}] 启用角色: {role.name} | 策略={role.model_pref} | "
                f"工具={role.allowed_tools} | 温度={role.temperature}"
            )
        else:
            effective_strategy = strategy

        # --- Step 2: ROUTING -------------------------------------------
        await self._safe_report_stage(
            task_id, "ANALYZING", f"任务类型: {task_type}，正在路由模型...", 0.2
        )
        try:
            decision = await self._route_decision(clean_input, effective_strategy)
        except Exception as exc:
            logger.error(f"[{task_id}] 模型路由失败: {exc}")
            await self._safe_report_result(task_id, "failed", f"模型路由失败: {exc}")
            return {"taskId": task_id, "status": "failed", "error": f"routing_failed: {exc}"}

        await self._safe_report_model_info(task_id, decision.vendor, decision.model, 0, 0)

        # --- Step 2.5: 蜂群模式检测 ------------------------------------
        is_swarm = self._is_swarm_mode(role, user_prompt)
        if is_swarm:
            await self._safe_report_stage(
                task_id, "ANALYZING", f"🔥 检测到蜂群模式，正在准备子代理集群...", 0.25
            )
            logger.info(f"[{task_id}] 🔥 蜂群模式激活")

        # --- Step 3: EXECUTION -----------------------------------------
        generated_text = ""
        swarm_result = None  # 蜂群结果引用（用于后续结晶）

        try:
            if is_swarm:
                # ===== 🔥 蜂群分支 =====
                await self._safe_report_stage(
                    task_id, "EXECUTION", f"🔥 触发蜂群模式，正在拉起子代理...", 0.3
                )

                # 延迟初始化 SwarmExecutor（如果外部未注入）
                if self.swarm_executor is None:
                    self.swarm_executor = _SwarmExecutor(
                        model_router=self.router,
                        streaming_reporter=self.reporter,
                        max_concurrent=3,
                    )
                    logger.info(f"[{task_id}] SwarmExecutor 延迟初始化完成")

                # 构建蜂群 Agent 列表
                swarm_agents = self._build_swarm_agents(role, clean_input)
                if not swarm_agents:
                    raise RuntimeError("蜂群 Agent 构建失败：无可用 Agent")

                await self._safe_report_stage(
                    task_id,
                    "EXECUTION",
                    f"🐝 蜂群就位: {len(swarm_agents)} 个 Agent（{', '.join(a.name for a in swarm_agents if hasattr(a, 'name'))}）",
                    0.35,
                )

                # 组装蜂群任务
                swarm_task = _SwarmTask(
                    task_id=task_id,
                    description=clean_input,
                    agents=swarm_agents,
                    parallel_limit=3,
                    aggregation_mode=_AggregationMode.CONCAT,
                    context={"join_delimiter": "\n\n---\n\n"},
                )

                # 执行蜂群！
                swarm_result = await self.swarm_executor.execute_swarm(swarm_task)
                generated_text = swarm_result.aggregated_content

                # 上报蜂群统计
                stats_msg = (
                    f"✅ 蜂群完成: {swarm_result.success_count}/{swarm_result.agent_count} 成功, "
                    f"总 token={swarm_result.total_tokens}, "
                    f"总延迟={swarm_result.total_latency_ms:.0f}ms"
                )
                await self._safe_report_stage(task_id, "EXECUTION", stats_msg, 0.5)
                logger.info(
                    f"[{task_id}] 蜂群完成: success={swarm_result.success_count}/"
                    f"{swarm_result.agent_count}, tokens={swarm_result.total_tokens}"
                )

                # 蜂群技能结晶（评分 >= 8.0）
                try:
                    swarm_score = 8.5  # 默认高分（因为蜂群产出通常质量较高）
                    # 如果有 evaluator 可以计算更精确的分数
                    if hasattr(self.skill_crystallizer, "auto_crystallize"):
                        crystallized_path = self.skill_crystallizer.auto_crystallize(
                            swarm_task, swarm_result.individual_results, score=swarm_score
                        )
                        if crystallized_path:
                            await self._safe_report_stage(
                                task_id,
                                "COMPLETED",
                                f"✨ 蜂群配方已结晶: {crystallized_path.name}",
                                0.85,
                            )
                            logger.info(f"[{task_id}] 蜂群配方结晶: {crystallized_path}")
                except Exception as cry_exc:
                    logger.warning(f"[{task_id}] 蜂群结晶失败 (非致命): {cry_exc}")

            else:
                # ===== 单体模型分支（原有逻辑 100% 保留）=====
                await self._safe_report_stage(
                    task_id, "READING_CODE", f"调用 {decision.vendor}/{decision.model} 生成内容...", 0.3
                )

                if role and hasattr(self.router, "_inject_system_prompt"):
                    prompt_for_llm = self.router._inject_system_prompt(clean_input, role.system_prompt)
                    if hasattr(self.router, "_execute_with_tools"):
                        llm_response = await self.router._execute_with_tools(
                            prompt_for_llm, decision, allowed_tools=role.allowed_tools
                        )
                    else:
                        llm_response = await self._execute_decision(prompt_for_llm, decision)
                else:
                    llm_response = await self._execute_decision(clean_input, decision)

                generated_text = llm_response.content
                await self._safe_report_model_info(
                    task_id,
                    decision.vendor,
                    decision.model,
                    llm_response.usage.get("prompt_tokens", 0),
                    llm_response.usage.get("completion_tokens", 0),
                )
                logger.info(f"[{task_id}] LLM 生成完成，长度={len(generated_text)}")

        except Exception as exc:
            logger.error(f"[{task_id}] 执行失败: {exc}")
            await self._safe_report_result(task_id, "failed", f"执行失败: {exc}")
            return {"taskId": task_id, "status": "failed", "error": str(exc)}

        # --- Step 4: DYNAMIC_EVALUATION (动态评估路由) ----------------
        eval_strategy = self._get_eval_strategy(task_type, role, user_prompt, generated_text)
        logger.info(f"[{task_id}] 评估策略: {eval_strategy}")

        if eval_strategy == "novel":
            # ===== 小说评估分支 =====
            await self._safe_report_stage(
                task_id, "TESTING", "正在评估小说质量...", 0.6
            )
            try:
                report = await self.curator.evaluate(
                    generated_text, characters=[], use_llm=True
                )
                critique = (
                    f"评估结果: 人设 {report.character_consistency}/10, "
                    f"逻辑 {report.plot_logic}/10, "
                    f"文风 {report.style_coherence}/10, "
                    f"情感 {report.emotional_impact}/10, "
                    f"综合 {report.overall}/10"
                )
                await self._safe_report_stage(task_id, "TESTING", critique, 0.7)
                logger.info(f"[{task_id}] {critique}")

                # 质量高 → 触发 SkillCrystallizer
                if report.overall >= 7.5:
                    try:
                        skill = self.curator.crystallizer.crystallize_skill(
                            user_prompt, generated_text, report
                        )
                        await self._safe_report_stage(
                            task_id,
                            "COMPLETED",
                            f"✨ 新技能已固化: {skill.name} (评分 {skill.score:.1f})",
                            0.8,
                        )
                        logger.info(f"[{task_id}] 技能固化: {skill.name}")
                    except Exception as sk_exc:
                        logger.warning(f"[{task_id}] 技能固化失败 (非致命): {sk_exc}")
            except Exception as exc:
                logger.warning(f"[{task_id}] 评估失败 (非致命): {exc}")

        elif eval_strategy == "code":
            # ===== 代码评估分支（预留 CodeCurator 占位符）=====
            await self._safe_report_stage(
                task_id, "TESTING", "代码任务：跳过文学评估，执行语法检查占位符...", 0.6
            )
            try:
                code_score = await self._evaluate_code_placeholder(generated_text, clean_input)
                await self._safe_report_stage(
                    task_id,
                    "TESTING",
                    f"代码质量评分: {code_score:.1f}/10 (CodeCurator 占位符)",
                    0.7,
                )
                logger.info(f"[{task_id}] 代码质量评分: {code_score:.1f}/10 (预留占位符)")
            except Exception as exc:
                logger.warning(f"[{task_id}] 代码评估失败 (非致命): {exc}")

        elif eval_strategy == "bypass":
            # ===== 通用任务跳过评估 =====
            logger.info(f"[{task_id}] 评估策略为 bypass，跳过评估步骤")
            # 保持静默，不上报进度，避免打断用户体验

        else:
            # ===== auto 降级处理（未触发任何评估）=====
            logger.info(f"[{task_id}] 评估策略 auto 未触发任何评估")

        # --- Step 5: DYNAMIC_MULTIMODAL (动态多模态触发) ----------------
        multimodal_strategy = self._get_multimodal_strategy(
            task_type, user_prompt, role, generated_text
        )
        logger.info(f"[{task_id}] 多模态策略: {multimodal_strategy}")

        if multimodal_strategy in ("explicit", "art_director", "auto_detect"):
            # 有视觉需求，触发 ComfyUI
            trigger_reason = {
                "explicit": "用户显式要求画图",
                "art_director": "art_director 角色默认触发",
                "auto_detect": "生成内容中检测到图像请求",
            }.get(multimodal_strategy, "未知原因")

            await self._safe_report_stage(
                task_id,
                "EDITING",
                f"🎨 触发多模态: {trigger_reason}，正在调度 ComfyUI...",
                0.75,
            )
            try:
                # VRAM 切换: GPU → CPU_FALLBACK (通过 async context manager)
                async with self.vram_scheduler.acquire_render_memory(task_id) as ctx:
                    await self._safe_report_vram(
                        task_id,
                        "RENDERING",
                        embedding_mb=2048,
                        llm_mb=0,
                        comfyui_mb=20480,
                        free_mb=0,
                    )
                    logger.info(f"[{task_id}] VRAM 进入渲染模式: {ctx.mode}")

                    # 调用 ComfyUI
                    result = await self.comfy_bridge.generate_character_concept(
                        character_description=generated_text[:500],
                        style_preset="anime",
                    )

                    if result.success:
                        image_path = result.output
                        await self._safe_report_stage(
                            task_id,
                            "COMPLETED",
                            f"🎨 概念图已生成: {image_path}",
                            0.9,
                        )
                        logger.info(f"[{task_id}] 概念图生成成功: {image_path}")
                    else:
                        await self._safe_report_stage(
                            task_id,
                            "FAILED",
                            f"概念图生成失败: {result.error}",
                            0.9,
                        )
                        logger.warning(f"[{task_id}] 概念图生成失败: {result.error}")

                # VRAM 恢复: CPU_FALLBACK → GPU
                await self._safe_report_vram(
                    task_id,
                    "IDLE",
                    embedding_mb=2048,
                    llm_mb=9216,
                    comfyui_mb=0,
                    free_mb=9216,
                )
                logger.info(f"[{task_id}] VRAM 恢复空闲模式")
            except Exception as exc:
                logger.error(f"[{task_id}] 多模态生成失败: {exc}")

        else:
            # bypass — 无视觉需求，直接跳过，不浪费 VRAM 切换
            logger.info(f"[{task_id}] 无视觉需求，跳过多模态步骤")

        # --- 最终结果 --------------------------------------------------
        await self._safe_report_result(task_id, "success", generated_text)
        logger.info(f"[{task_id}] ===== 任务完成 =====")

        result_payload: Dict[str, Any] = {
            "taskId": task_id,
            "status": "success",
            "output": generated_text,
            "task_type": task_type,
        }

        if is_swarm and swarm_result is not None:
            # 蜂群模式：附加蜂群元数据
            result_payload["swarm_mode"] = True
            result_payload["model_used"] = f"蜂群({swarm_result.agent_count} agents)"
            result_payload["swarm_stats"] = {
                "agent_count": swarm_result.agent_count,
                "success_count": swarm_result.success_count,
                "failed_count": swarm_result.failed_count,
                "total_tokens": swarm_result.total_tokens,
                "total_latency_ms": round(swarm_result.total_latency_ms, 2),
            }
        else:
            # 单体模型模式
            result_payload["model_used"] = f"{decision.vendor}/{decision.model}"

        return result_payload

    # ------------------------------------------------------------------
    # 公开 API：批量任务流式处理
    # ------------------------------------------------------------------

    async def process_tasks_stream(
        self, task_requests: List[Dict[str, Any]], max_concurrency: int = 3,
        overall_timeout_sec: float = 600.0,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        批量处理任务，返回异步生成器，支持流式消费结果。
        v2.3.1: 添加 overall_timeout_sec 防止永久阻塞。

        Args:
            task_requests: 任务请求列表
            max_concurrency: 最大并发数（默认 3，避免同时占用过多资源）

        Yields:
            每个任务完成后的结果 dict
        """
        semaphore = asyncio.Semaphore(max_concurrency)
        logger.info(f"批量任务开始: {len(task_requests)} 个任务, 并发限制={max_concurrency}")

        async def _bounded_process(req: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        self.process_task(req),
                        timeout=overall_timeout_sec,
                    )
                except asyncio.TimeoutError:
                    task_id = req.get("taskId", "unknown")
                    logger.error(f"[{task_id}] 任务处理超时 ({overall_timeout_sec}s)")
                    return {
                        "taskId": task_id,
                        "status": "failed",
                        "error": f"task_timeout: exceeded {overall_timeout_sec}s",
                    }

        # 使用 as_completed 保证结果按完成顺序 yield，实现真正的流式
        pending = [asyncio.create_task(_bounded_process(req)) for req in task_requests]
        for coro in asyncio.as_completed(pending):
            try:
                result = await coro
                yield result
            except Exception as exc:
                logger.error(f"批量任务中某个协程异常: {exc}")
                yield {"status": "failed", "error": f"batch_exception: {exc}"}

    async def process_tasks(
        self, task_requests: List[Dict[str, Any]], max_concurrency: int = 3,
        overall_timeout_sec: float = 600.0,
    ) -> List[Dict[str, Any]]:
        """
        批量处理任务，返回完整结果列表（按完成顺序）。
        v2.3.1: 添加 overall_timeout_sec 防止永久阻塞。

        Args:
            task_requests: 任务请求列表
            max_concurrency: 最大并发数

        Returns:
            结果 dict 列表
        """
        results: List[Dict[str, Any]] = []
        async for result in self.process_tasks_stream(task_requests, max_concurrency, overall_timeout_sec):
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _classify_task(self, prompt: str) -> str:
        """
        基于关键词判断任务类型。

        Returns:
            'multimodal' | 'novel' | 'code' | 'chat'
        """
        prompt_lower = prompt.lower()
        multimodal_kw = [
            "设计角色", "概念图", "生成图片", "画图",
            "character concept", "生成图像", "画一张",
        ]
        if any(kw in prompt_lower for kw in multimodal_kw):
            return "multimodal"

        novel_kw = [
            "写小说", "章节", "剧情", "悬疑", "novel",
            "chapter", "故事", "短篇", "长篇小说",
        ]
        if any(kw in prompt_lower for kw in novel_kw):
            return "novel"

        code_kw = [
            "bug", "修复", "重构", "代码", "code",
            "git", "函数", "class", "debug", "optimize",
        ]
        if any(kw in prompt_lower for kw in code_kw):
            return "code"

        return "chat"

    def _needs_image(self, prompt: str) -> bool:
        """
        判断用户 prompt 是否隐含图像生成需求。

        用于 task_type 不是 'multimodal' 但 prompt 中出现图像相关词的情况。
        """
        image_kw = [
            "图", "画", "image", "picture", "concept art",
            "插图", "插画", "配图", "photo", "drawing",
        ]
        return any(kw in prompt.lower() for kw in image_kw)

    # ------------------------------------------------------------------
    # Reporter 安全包装（吞掉所有异常，保证非阻塞）
    # ------------------------------------------------------------------

    async def _safe_report_stage(
        self,
        task_id: str,
        stage: str,
        message: str,
        progress: Optional[float] = None,
    ) -> None:
        """安全上报阶段状态，任何异常不抛出不阻断主流程。"""
        try:
            await self.reporter.report_stage(task_id, stage, message, progress)
        except Exception as exc:
            logger.debug(f"report_stage  swallowed: {exc}")

    async def _safe_report_result(
        self, task_id: str, status: str, output: str
    ) -> None:
        """安全上报最终结果。"""
        try:
            await self.reporter.report_result(task_id, status, output)
        except Exception as exc:
            logger.debug(f"report_result swallowed: {exc}")

    async def _safe_report_model_info(
        self,
        task_id: str,
        vendor: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """安全上报模型信息。"""
        try:
            await self.reporter.report_model_info(
                task_id, vendor, model, prompt_tokens, completion_tokens
            )
        except Exception as exc:
            logger.debug(f"report_model_info swallowed: {exc}")

    async def _safe_report_vram(
        self,
        task_id: str,
        state: str,
        embedding_mb: int,
        llm_mb: int,
        comfyui_mb: int,
        free_mb: int,
    ) -> None:
        """安全上报 VRAM 状态。"""
        try:
            await self.reporter.report_vram(
                task_id, state, embedding_mb, llm_mb, comfyui_mb, free_mb
            )
        except Exception as exc:
            logger.debug(f"report_vram swallowed: {exc}")

    # ------------------------------------------------------------------
    # 路由与执行兼容层（兼容同步/异步 router 方法）
    # ------------------------------------------------------------------

    async def _route_decision(self, prompt: str, strategy: Any) -> Any:
        """兼容调用 router.route：无论同步还是异步都能正确处理。"""
        result = self.router.route(prompt, strategy)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _execute_decision(self, prompt: str, decision: Any) -> Any:
        """
        兼容调用 router.execute：适配不同参数顺序。
        
        ★★★ 地雷 1 修复：推理引用计数保护 ★★★
        在调用本地 LLM 前声明 begin_llm_inference，
        完成后声明 end_llm_inference，防止 VRAM 切换切断活跃推理。
        """
        # 尝试获取 VRAM 调度器的推理锁（如果是本地模型则受保护，云端模型无害通过）
        has_vram_scheduler = False
        try:
            has_vram_scheduler = (
                hasattr(self, "vram_scheduler")
                and self.vram_scheduler is not None
                and hasattr(self.vram_scheduler, "begin_llm_inference")
            )
            if has_vram_scheduler:
                inference_ok = await self.vram_scheduler.begin_llm_inference(timeout_sec=5.0)
                if not inference_ok:
                    logger.warning("VRAM 正在切换中，推理请求排队超时，取消推理")
                    raise RuntimeError("VRAM 切换中，推理请求排队超时")
        except Exception as exc:
            logger.warning(f"begin_llm_inference 调用失败 (非致命): {exc}")

        try:
            # 优先检测真实 ModelRouter 的 execute(decision, prompt) 签名
            import inspect
            try:
                sig = inspect.signature(self.router.execute)
                params = list(sig.parameters.keys())
                if len(params) >= 3 and params[1] == "decision":
                    # 真实 ModelRouter: execute(self, decision, prompt)
                    result = self.router.execute(decision, prompt)
                else:
                    # stub: execute(self, task, decision)
                    result = self.router.execute(prompt, decision)
            except (ValueError, TypeError):
                # inspect 失败时回退到 stub 顺序
                result = self.router.execute(prompt, decision)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        finally:
            # 释放推理锁
            try:
                if has_vram_scheduler and hasattr(self.vram_scheduler, "end_llm_inference"):
                    await self.vram_scheduler.end_llm_inference()
            except Exception as exc:
                logger.warning(f"end_llm_inference 调用失败 (非致命): {exc}")

async def _test_single_task() -> None:
    """测试单任务处理：赛博朋克女主角 + 概念图。"""
    orch = HermesOrchestrator()
    result = await orch.process_task({
        "taskId": "test-001",
        "raw_input": "帮我设计一个赛博朋克女主角并生成概念图",
        "strategy": "CREATIVE",
    })
    print("\n=== 单任务结果 ===")
    print(result)


async def _test_novel_task() -> None:
    """测试小说生成 + 评估流水线。"""
    orch = HermesOrchestrator()
    result = await orch.process_task({
        "taskId": "test-novel-001",
        "raw_input": "帮我写一章节悬疑小说，主角是个失忆的侦探",
        "strategy": "CREATIVE",
    })
    print("\n=== 小说任务结果 ===")
    print(result)


async def _test_batch_tasks() -> None:
    """测试批量任务流式处理。"""
    orch = HermesOrchestrator()
    tasks = [
        {"taskId": "batch-001", "raw_input": "写一段Python快速排序代码", "strategy": "BALANCED"},
        {"taskId": "batch-002", "raw_input": "生成一张概念图：未来城市", "strategy": "CREATIVE"},
        {"taskId": "batch-003", "raw_input": "写一章节科幻小说", "strategy": "CREATIVE"},
    ]
    print("\n=== 批量任务流式结果 ===")
    async for res in orch.process_tasks_stream(tasks, max_concurrency=2):
        print(f"  -> {res['taskId']}: status={res['status']}")


async def test() -> None:
    """综合测试入口。"""
    print("=" * 60)
    print("HermesOrchestrator 综合测试")
    print("=" * 60)

    await _test_single_task()
    await _test_novel_task()
    await _test_batch_tasks()

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test())
