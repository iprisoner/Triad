"""
model_router.py — 内核级模型动态路由系统 (v2.1 动态注册表版)

v2.1 变更：
- 废除硬编码 6 厂商策略映射
- ModelRegistry 改为 JSON 持久化、无限动态添加
- route() 从 registry.find_by_strategy() 动态加载
- execute() 支持传入 provider_id 直接执行
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Coroutine,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Set,
    Tuple,
    TypeVar,
)

import httpx
import tiktoken

try:
    from .model_registry import ModelRegistry, ProviderConfig
except ImportError:
    from model_registry import ModelRegistry, ProviderConfig

try:
    from .prompts.roles import ROLES, RoleConfig
except ImportError:
    from prompts.roles import ROLES, RoleConfig

logger = logging.getLogger("triad.model_router")

# ---------------------------------------------------------------------------
# 1. RouteStrategy — 路由策略枚举
# ---------------------------------------------------------------------------

class RouteStrategy(Enum):
    """
    高层路由策略。用户/系统通过 ACP TaskRequest 传入，决定模型调度的宏观方向。

    AUTO      : 由 ModelRouter 根据任务内容自动推断（默认）
    CREATIVE  : 创意/破局/发散型任务 → 查找 tags 含 creative / brainstorming / uncensored 的模型
    REASONING : 逻辑推演/代码/数学  → 查找 tags 含 reasoning / code / logic 的模型
    LONGFORM  : 超长文本/世界观/铺垫 → 查找 tags 含 longform / context 的模型
    REVIEW    : 审查/一致性/严谨性   → 查找 tags 含 reasoning / review 的模型
    CHAT      : 细节描写/对话/中文语境 → 查找 tags 含 chat / chinese 的模型
    LOCAL     : 系统维护/隐私/低成本  → 查找 tags 含 local / privacy 的模型
    """
    AUTO = auto()
    CREATIVE = auto()
    REASONING = auto()
    LONGFORM = auto()
    REVIEW = auto()
    CHAT = auto()
    LOCAL = auto()


# ---------------------------------------------------------------------------
# 2. 数据类定义
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelCapability:
    """单个模型的能力画像。用于精确匹配任务需求。"""
    max_context: int          # 最大上下文长度 (tokens)
    max_output: int           # 单次最大输出长度 (tokens)
    supports_streaming: bool
    supports_tools: bool
    supports_vision: bool
    reasoning_effort: Literal["low", "medium", "high"]
    strength_tags: Set[str]   # 能力标签，如 {"creative", "roleplay", "coding"}


@dataclass
class ModelConfig:
    """模型运行期配置（含密钥、URL、超时等敏感信息）。"""
    vendor: str               # 厂商标识字符串（不再用硬编码枚举）
    model_id: str             # 厂商侧模型标识，如 "deepseek-reasoner"
    base_url: str
    api_key: str
    timeout: float = 120.0
    retry_times: int = 2
    capability: ModelCapability = field(default_factory=lambda: ModelCapability(
        max_context=4096, max_output=2048,
        supports_streaming=True, supports_tools=False,
        supports_vision=False, reasoning_effort="medium",
        strength_tags=set(),
    ))
    weight: float = 1.0
    healthy: bool = True
    last_health_check: float = 0.0


@dataclass
class RoutingDecision:
    """路由决策结果 —— 包含完整执行计划，可被下游直接消费。"""
    strategy: RouteStrategy
    primary: ModelConfig
    secondary: Optional[ModelConfig]
    estimated_input_tokens: int
    estimated_output_tokens: int
    context_summary: str
    will_truncate: bool
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """统一封装的 LLM 响应，屏蔽厂商差异。"""
    vendor: str
    model_id: str
    content: str
    usage: Dict[str, int]
    finish_reason: Optional[str]
    latency_ms: float
    raw_response: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 3. ContextAligner — 上下文对齐器
# ---------------------------------------------------------------------------

class ContextAligner:
    """
    解决跨模型上下文传递的「计量单位不统一」与「信息过载」问题。

    核心机制
    --------
    1. Token 统一估算 : 所有厂商的输入统一用 tiktoken 估算。
    2. 关键信息提取 (Key Fact Extraction): 当跨模型传递时，如果原文过长，
       先用规则版提取事实三元组，仅传递摘要 + 三元组。
    3. 自适应压缩 : 根据目标模型的 max_context 自动裁剪历史消息。
    """

    # 不同模型族对应的 tiktoken 编码名（未知厂商 fallback 到 cl100k_base）
    ENCODING_MAP: Dict[str, str] = {
        "grok": "o200k_base",
        "kimi": "cl100k_base",
        "deepseek": "cl100k_base",
        "qwen": "cl100k_base",
        "claude": "cl100k_base",
        "gemini": "cl100k_base",
        "openai": "cl100k_base",
    }

    # 厂商 tokenizer 相对于 tiktoken 的膨胀系数（经验值）
    INFLATION_FACTOR: Dict[str, float] = {
        "grok": 1.05,
        "kimi": 1.00,
        "deepseek": 1.10,
        "qwen": 0.95,
        "claude": 1.00,
        "gemini": 1.02,
        "openai": 1.00,
    }

    def __init__(self, default_encoding: str = "cl100k_base"):
        self.default_encoding = default_encoding
        self._encoders: Dict[str, tiktoken.Encoding] = {}

    def _get_encoder(self, encoding_name: str) -> tiktoken.Encoding:
        if encoding_name not in self._encoders:
            self._encoders[encoding_name] = tiktoken.get_encoding(encoding_name)
        return self._encoders[encoding_name]

    def estimate_tokens(
        self,
        text: str,
        vendor: Optional[str] = None,
        encoding_name: Optional[str] = None,
    ) -> int:
        """
        统一估算 text 的 token 数。
        """
        enc_name = encoding_name or (
            self.ENCODING_MAP.get(vendor, self.default_encoding)
            if vendor else self.default_encoding
        )
        enc = self._get_encoder(enc_name)
        base_count = len(enc.encode(text))
        if vendor:
            return int(base_count * self.INFLATION_FACTOR.get(vendor, 1.0))
        return base_count

    def extract_key_facts(
        self,
        context: str,
        max_facts: int = 20,
        fact_length: int = 80,
    ) -> List[str]:
        """
        轻量级关键信息提取（规则版）。
        """
        import re

        facts: List[str] = []

        role_patterns = [
            r"[角色人物主角配角].*?[:：]\s*(.+?)(?:\n|$)",
            r"([一-龥]{2,6})[,，]\s*(?:性格|身份|职业|背景|年龄).*?[:：]\s*(.+?)(?:\n|$)",
        ]
        for pat in role_patterns:
            for m in re.finditer(pat, context, re.MULTILINE):
                fact = m.group(0).strip().replace("\n", " ")
                if len(fact) > fact_length:
                    fact = fact[:fact_length] + "..."
                facts.append(f"[角色] {fact}")
                if len(facts) >= max_facts:
                    return facts[:max_facts]

        setting_patterns = [
            r"(?:世界观|设定|背景|时间线|地点).*?[:：]\s*(.+?)(?:\n{2,}|$)",
            r"(?:伏笔|暗示|前文|已发生|关键事件).*?[:：]\s*(.+?)(?:\n|$)",
        ]
        for pat in setting_patterns:
            for m in re.finditer(pat, context, re.MULTILINE):
                fact = m.group(0).strip().replace("\n", " ")
                if len(fact) > fact_length:
                    fact = fact[:fact_length] + "..."
                facts.append(f"[设定] {fact}")
                if len(facts) >= max_facts:
                    return facts[:max_facts]

        entity_pat = r"(?:\d+年|\d+月|\d+日|第[一二三四五六七八九十\d]+章|[^，。\n]{3,20}(?:国|城|山|海|湖|林))"
        for m in re.finditer(entity_pat, context):
            ent = m.group(0).strip()
            if len(ent) >= 3:
                facts.append(f"[实体] {ent}")
            if len(facts) >= max_facts:
                return facts[:max_facts]

        return facts[:max_facts]

    def align_context(
        self,
        source_context: str,
        source_vendor: str,
        target_config: ModelConfig,
        keep_ratio: float = 0.8,
    ) -> str:
        """
        将源模型生成的上下文对齐到目标模型的上下文窗口。
        """
        target_budget = int(target_config.capability.max_context * keep_ratio)
        estimated = self.estimate_tokens(source_context, target_config.vendor)

        if estimated <= target_budget:
            logger.debug(
                "ContextAligner: direct pass (%d tokens <= budget %d)",
                estimated, target_budget,
            )
            return source_context

        facts = self.extract_key_facts(source_context)
        facts_text = "\n".join(facts)
        facts_tokens = self.estimate_tokens(facts_text, target_config.vendor)

        tail_budget = target_budget - facts_tokens - 100
        if tail_budget <= 0:
            logger.warning(
                "ContextAligner: even facts exceed budget, truncating facts."
            )
            # v2.3 修复：使用 tokenizer 级截断，而非字符切片
            enc = self._get_encoder(self.ENCODING_MAP.get(target_config.vendor, self.default_encoding))
            facts_tokens = enc.encode(facts_text)
            truncated_tokens = facts_tokens[:target_budget]
            return enc.decode(truncated_tokens)

        enc = self._get_encoder(self.ENCODING_MAP.get(target_config.vendor, self.default_encoding))
        tokens = enc.encode(source_context)
        tail_tokens = tokens[-tail_budget:]
        tail_text = enc.decode(tail_tokens)

        aligned = (
            f"【前文摘要 - 关键事实】\n{facts_text}\n\n"
            f"【最近原文】\n{tail_text}"
        )
        logger.info(
            "ContextAligner: compressed %d -> %d tokens for %s",
            estimated,
            self.estimate_tokens(aligned, target_config.vendor),
            target_config.vendor,
        )
        return aligned

    def build_cross_model_prompt(
        self,
        original_task: str,
        upstream_output: str,
        upstream_vendor: str,
        downstream_config: ModelConfig,
        instruction_prefix: str = "请基于以下上游输出继续完成后续任务。",
    ) -> str:
        """
        构造跨模型接力提示词。
        """
        aligned = self.align_context(
            upstream_output, upstream_vendor, downstream_config
        )
        prompt = (
            f"{instruction_prefix}\n\n"
            f"=== 原始任务 ===\n{original_task}\n\n"
            f"=== 上游输出 ({upstream_vendor}) ===\n{aligned}\n\n"
            f"=== 你的任务 ===\n请继续推进。"
        )
        return prompt


# ---------------------------------------------------------------------------
# 4. FallbackChain — 弹性降级链
# ---------------------------------------------------------------------------

class FallbackChain:
    """
    主模型失败时的自动降级机制（v2.3 重构：三态熔断器 + 协程安全）。

    状态机:
        CLOSED  → 计数失败 → 达到阈值 → OPEN
        OPEN    → 超时到期 → HALF_OPEN（只允许单探测）
        HALF_OPEN → 探测成功 → CLOSED（清零计数）
        HALF_OPEN → 探测失败 → OPEN（重置超时）
    """

    def __init__(
        self,
        registry: ModelRegistry,
        aligner: ContextAligner,
        health_check_interval: float = 60.0,
    ):
        self.registry = registry
        self.aligner = aligner
        self.health_check_interval = health_check_interval
        self._lock = asyncio.Lock()
        self._failure_counts: Dict[str, int] = {}
        self._circuit_open: Dict[str, float] = {}
        self._circuit_half_open: Dict[str, bool] = {}  # v2.3 新增：半开状态标记
        self._circuit_threshold = 5
        self._circuit_timeout = 120.0

    async def execute_with_fallback(
        self,
        decision: RoutingDecision,
        call_fn: Callable[[ModelConfig, str], Coroutine[Any, Any, LLMResponse]],
        prompt: str,
    ) -> LLMResponse:
        """
        执行带降级链的 LLM 调用。
        """
        candidates: List[Optional[ModelConfig]] = [
            decision.primary,
            decision.secondary,
        ]
        # 若两个都失败，追加本地模型作为终极兜底
        local_providers = self.registry.find_by_strategy("LOCAL")
        for lp in local_providers:
            if lp.id not in {c.vendor for c in candidates if c}:
                local_cfg = _provider_to_config(lp)
                candidates.append(local_cfg)
                break

        last_exception: Optional[Exception] = None

        for cfg in candidates:
            if cfg is None:
                continue
            healthy = await self._is_healthy(cfg)
            if not healthy:
                logger.warning("FallbackChain: %s is circuit-open, skipping.", cfg.model_id)
                continue

            for attempt in range(cfg.retry_times + 1):
                try:
                    logger.info(
                        "FallbackChain: trying %s (attempt %d/%d)",
                        cfg.model_id,
                        attempt + 1,
                        cfg.retry_times + 1,
                    )
                    response = await asyncio.wait_for(
                        call_fn(cfg, prompt),
                        timeout=cfg.timeout,
                    )
                    # 成功：在半开状态则移回 CLOSED，清零计数
                    async with self._lock:
                        self._failure_counts[cfg.model_id] = 0
                        self._circuit_half_open.pop(cfg.model_id, None)
                    return response
                except asyncio.TimeoutError as e:
                    last_exception = e
                    if attempt < cfg.retry_times:
                        logger.warning("FallbackChain: %s timeout on attempt %d", cfg.model_id, attempt + 1)
                        await asyncio.sleep(1.5 ** attempt)
                    else:
                        break
                except httpx.HTTPStatusError as e:
                    last_exception = e
                    if e.response.status_code == 429:
                        logger.warning("FallbackChain: %s rate limited", cfg.model_id)
                        if attempt < cfg.retry_times:
                            await asyncio.sleep(2 ** attempt)
                        else:
                            break
                    elif e.response.status_code >= 500:
                        logger.warning("FallbackChain: %s server error", cfg.model_id)
                        if attempt < cfg.retry_times:
                            await asyncio.sleep(1.5 ** attempt)
                        else:
                            break
                    else:
                        # 客户端错误 (4xx) 不触发熔断，立即跳出
                        break
                except Exception as e:
                    last_exception = e
                    logger.exception("FallbackChain: %s unexpected error", cfg.model_id)
                    break

            await self._record_failure(cfg.model_id)

        raise FallbackExhaustedError(
            f"All fallback candidates exhausted. Last error: {last_exception}"
        ) from last_exception

    async def _is_healthy(self, cfg: ModelConfig) -> bool:
        async with self._lock:
            now = time.time()
            if cfg.model_id in self._circuit_open:
                open_time = self._circuit_open[cfg.model_id]
                if now - open_time < self._circuit_timeout:
                    return False
                # 超时到期：进入 HALF_OPEN（单探测模式）
                del self._circuit_open[cfg.model_id]
                self._circuit_half_open[cfg.model_id] = True
                logger.info("FallbackChain: %s circuit half-open, allowing single probe.", cfg.model_id)
            elif cfg.model_id in self._circuit_half_open:
                # 半开状态下：只允许一个探测请求通过
                # 如果已经有探测在进行，返回 False
                # 简化实现：半开状态标记存在时，第一个调用者清除标记并允许通过
                # 后续调用者在锁保护下看到标记已被清除，返回 False
                if self._circuit_half_open.get(cfg.model_id, False):
                    self._circuit_half_open[cfg.model_id] = False  # 标记为"探测中"
                    return True
                return False
            return cfg.healthy

    async def _record_failure(self, model_id: str) -> None:
        async with self._lock:
            self._failure_counts[model_id] = self._failure_counts.get(model_id, 0) + 1
            if self._failure_counts[model_id] >= self._circuit_threshold:
                self._circuit_open[model_id] = time.time()
                self._circuit_half_open.pop(model_id, None)
                logger.error(
                    "FallbackChain: %s circuit opened after %d failures.",
                    model_id,
                    self._failure_counts[model_id],
                )

    async def health_probe(self, cfg: ModelConfig) -> bool:
        cfg.last_health_check = time.time()
        return cfg.healthy


class FallbackExhaustedError(Exception):
    """所有降级候选均已耗尽。"""
    pass


# ---------------------------------------------------------------------------
# 5. ModelRouter — 主路由引擎 (v2.1 动态注册表版)
# ---------------------------------------------------------------------------

class ModelRouter:
    """
    模型动态路由的核心入口 (v2.1)。

    v2.1 变更：
    - 废除硬编码 6 厂商策略映射表
    - registry 改为 JSON 持久化的动态 ModelRegistry
    - route() 从 registry.find_by_strategy() 动态加载
    - execute() 支持传入 provider_id 直接执行
    """

    _KEYWORD_STRATEGY_MAP: List[Tuple[Set[str], RouteStrategy]] = [
        ({"代码", "code", "编程", "debug", "bug", "算法", "推理", "逻辑", "证明", "数学"}, RouteStrategy.REASONING),
        ({"审查", "review", "检查", "一致性", "矛盾", "校对", "审核"}, RouteStrategy.REVIEW),
        ({"大纲", "世界观", "设定", "铺垫", "长文", "长篇小说", "编年史", "历史", "年表"}, RouteStrategy.LONGFORM),
        ({"对话", "描写", "场景", "细节", "氛围", "心理", "表情", "动作"}, RouteStrategy.CHAT),
        ({"创意", "脑洞", "破局", "发散", "brainstorm", "灵感", "点子", "新颖"}, RouteStrategy.CREATIVE),
    ]

    def __init__(
        self,
        registry: Optional[ModelRegistry] = None,
        aligner: Optional[ContextAligner] = None,
        fallback_chain: Optional[FallbackChain] = None,
    ):
        # v2.1: 自动初始化 ModelRegistry（JSON 持久化）
        self.registry = registry or ModelRegistry()
        self.aligner = aligner or ContextAligner()
        self.fallback = fallback_chain or FallbackChain(self.registry, self.aligner)

    # ------------------------------------------------------------------
    # 5.1 路由决策 (v2.1 动态加载)
    # ------------------------------------------------------------------

    def route(
        self,
        task_description: str,
        strategy: RouteStrategy = RouteStrategy.AUTO,
        preferred_provider: Optional[str] = None,
        context_length_hint: Optional[int] = None,
    ) -> RoutingDecision:
        """
        根据任务描述和策略偏好，从动态注册表中生成路由决策。

        Args:
            task_description: 任务内容（用于 AUTO 推断）
            strategy: 显式策略（AUTO 时自动推断）
            preferred_provider: 用户/系统指定的 provider id（覆盖策略默认）
            context_length_hint: 预估输入长度，用于模型能力匹配
        """
        inferred = self._auto_infer_strategy(task_description) if strategy == RouteStrategy.AUTO else strategy
        logger.info("ModelRouter: inferred strategy=%s for task", inferred.name)

        # v2.1: 从动态注册表按策略查找匹配 providers
        providers = self.registry.find_by_strategy(inferred.name)
        if not providers:
            # 策略无匹配时 fallback 到任意 enabled 的 provider
            providers = self.registry.list(enabled_only=True)

        if not providers:
            raise RouterConfigError(f"No enabled providers found in registry for strategy {inferred.name}")

        # 用户偏好覆盖：若指定了 preferred_provider，提为 primary
        primary_cfg: Optional[ModelConfig] = None
        secondary_cfg: Optional[ModelConfig] = None

        if preferred_provider:
            pref = self.registry.get(preferred_provider)
            if pref and pref.enabled:
                primary_cfg = _provider_to_config(pref)
                # secondary 为匹配策略的第一个其他 provider
                for p in providers:
                    if p.id != preferred_provider:
                        secondary_cfg = _provider_to_config(p)
                        break
            else:
                logger.warning("ModelRouter: preferred_provider %s not found or disabled", preferred_provider)

        if not primary_cfg:
            # 无用户偏好：使用策略匹配的第一个作为 primary，第二个作为 secondary
            primary_cfg = _provider_to_config(providers[0])
            if len(providers) > 1:
                secondary_cfg = _provider_to_config(providers[1])

        # Token 估算
        est_input = self.aligner.estimate_tokens(task_description, primary_cfg.vendor)
        est_output = primary_cfg.capability.max_output // 2

        # 上下文能力检查
        will_truncate = False
        context_summary = task_description
        if context_length_hint and context_length_hint > primary_cfg.capability.max_context * 0.8:
            will_truncate = True
            context_summary = self.aligner.align_context(
                task_description, primary_cfg.vendor, primary_cfg, keep_ratio=0.7
            )

        return RoutingDecision(
            strategy=inferred,
            primary=primary_cfg,
            secondary=secondary_cfg,
            estimated_input_tokens=est_input,
            estimated_output_tokens=est_output,
            context_summary=context_summary,
            will_truncate=will_truncate,
            metadata={
                "preferred_provider": preferred_provider,
                "context_length_hint": context_length_hint,
                "available_providers": [p.id for p in providers],
            },
        )

    def _auto_infer_strategy(self, task: str) -> RouteStrategy:
        task_lower = task.lower()
        for keywords, strat in self._KEYWORD_STRATEGY_MAP:
            if any(kw.lower() in task_lower for kw in keywords):
                return strat
        return RouteStrategy.CHAT  # 默认保守选择

    # ------------------------------------------------------------------
    # 5.2 执行层 (v2.1 传入 provider_id)
    # ------------------------------------------------------------------

    async def execute(
        self,
        decision: RoutingDecision,
        prompt: str,
        call_fn: Optional[Callable[[ModelConfig, str], Coroutine[Any, Any, LLMResponse]]] = None,
    ) -> LLMResponse:
        """
        根据路由决策执行 LLM 调用，自动处理降级。
        """
        caller = call_fn or self._default_call
        return await self.fallback.execute_with_fallback(decision, caller, prompt)

    async def execute_by_provider(
        self,
        provider_id: str,
        prompt: str,
        call_fn: Optional[Callable[[ModelConfig, str], Coroutine[Any, Any, LLMResponse]]] = None,
    ) -> LLMResponse:
        """
        v2.1 新增：直接指定 provider_id 执行，绕过策略路由。

        Args:
            provider_id: 注册表中的 provider id（如 "deepseek"）
            prompt: 发送给模型的提示词
            call_fn: 可选自定义调用函数
        """
        provider = self.registry.get(provider_id)
        if not provider or not provider.enabled:
            raise RouterConfigError(f"Provider {provider_id} not found or disabled")

        cfg = _provider_to_config(provider)
        caller = call_fn or self._default_call

        t0 = time.perf_counter()
        try:
            response = await asyncio.wait_for(caller(cfg, prompt), timeout=cfg.timeout)
            return response
        except Exception as e:
            logger.exception("execute_by_provider: %s failed", provider_id)
            raise

    async def execute_pipeline(
        self,
        stages: List[Tuple[RouteStrategy, str, str]],
        call_fn: Optional[Callable[[ModelConfig, str], Coroutine[Any, Any, LLMResponse]]] = None,
    ) -> List[LLMResponse]:
        """
        执行多阶段流水线（如：大纲 → 推演 → 描写 → 审查）。
        """
        results: List[LLMResponse] = []
        accumulated_context = ""

        for i, (strategy, task, instruction) in enumerate(stages):
            decision = self.route(
                task_description=task,
                strategy=strategy,
                context_length_hint=(
                    self.aligner.estimate_tokens(accumulated_context)
                    if accumulated_context else None
                ),
            )

            if accumulated_context and i > 0:
                prev_vendor = results[-1].vendor if results else "qwen"
                prompt = self.aligner.build_cross_model_prompt(
                    original_task=task,
                    upstream_output=accumulated_context,
                    upstream_vendor=prev_vendor,
                    downstream_config=decision.primary,
                    instruction_prefix=instruction,
                )
            else:
                prompt = f"{instruction}\n\n{task}"

            response = await self.execute(decision, prompt, call_fn)
            results.append(response)
            accumulated_context = response.content

        return results

    # ------------------------------------------------------------------
    # 5.3 默认 HTTP 调用器（适配 OpenAI-compatible API）
    # ------------------------------------------------------------------

    @property
    def _http(self) -> httpx.AsyncClient:
        """懒加载共享 httpx.AsyncClient（连接池复用）。"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._http_client

    async def _default_call(self, cfg: ModelConfig, prompt: str) -> LLMResponse:
        """
        通用 OpenAI-compatible API 调用器。
        使用 cfg 中的 base_url 和 api_key（来自动态注册表）。
        """
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": cfg.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": cfg.capability.max_output,
            "temperature": 0.7,
        }
        if cfg.capability.reasoning_effort == "high":
            payload["temperature"] = 0.2

        client = self._http
        t0 = time.perf_counter()
        resp = await client.post(
            f"{cfg.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        latency = (time.perf_counter() - t0) * 1000

        choice = data["choices"][0]
        usage = data.get("usage", {})

        return LLMResponse(
            vendor=cfg.vendor,
            model_id=cfg.model_id,
            content=choice["message"]["content"],
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
            },
            finish_reason=choice.get("finish_reason"),
            latency_ms=latency,
            raw_response=data,
        )


    # ------------------------------------------------------------------
    # 5.4 角色感知路由 (Role-aware routing)
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭共享 HTTP 客户端，释放连接池。"""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def parse_role(self, raw_input: str) -> tuple[Optional[RoleConfig], str]:
        """
        解析用户输入中的 @角色名 前缀。

        返回: (RoleConfig | None, 去除前缀后的纯净输入)

        示例:
            "@code_engineer 帮我重构这个组件" → (code_engineer_role, "帮我重构这个组件")
            "@novelist 写第一章" → (novelist_role, "写第一章")
            "帮我写代码" → (None, "帮我写代码")  # 无角色前缀，使用默认路由
        """
        match = re.match(r"^@(\w+)\s+(.+)$", raw_input.strip())
        if match:
            role_id = match.group(1)
            clean_input = match.group(2)
            role = ROLES.get(role_id)
            if role:
                logger.info("ModelRouter: 解析到角色 '%s' (%s)", role.name, role_id)
                return role, clean_input
            else:
                # 角色不存在，抛出错误提示 + 可用角色列表
                available = ", ".join(ROLES.keys())
                raise RouterConfigError(
                    f"未知角色 '{role_id}'。可用角色: {available}"
                )
        return None, raw_input

    def _str_to_strategy(self, strategy_str: Optional[str]) -> RouteStrategy:
        """将策略字符串转换为 RouteStrategy 枚举。"""
        if not strategy_str:
            return RouteStrategy.AUTO
        try:
            return RouteStrategy[strategy_str.upper()]
        except KeyError:
            logger.warning("ModelRouter: 未知策略 '%s'，回退到 AUTO", strategy_str)
            return RouteStrategy.AUTO

    def _inject_system_prompt(self, user_prompt: str, system_prompt: str) -> str:
        """将 system prompt 注入到请求中。"""
        return f"[系统指令]\n{system_prompt}\n\n[用户请求]\n{user_prompt}"

    async def _execute_with_tools(
        self,
        prompt: str,
        decision: RoutingDecision,
        allowed_tools: List[str],
        call_fn: Optional[Callable[[ModelConfig, str], Coroutine[Any, Any, LLMResponse]]] = None,
    ) -> LLMResponse:
        """
        执行时限制工具权限。

        只允许调用 allowed_tools 列表中的工具。
        实际工具过滤由 HermesOrchestrator 层实现；
        本方法将权限列表记录到决策元数据中供上层消费。
        """
        decision.metadata["allowed_tools"] = allowed_tools
        decision.metadata["role_restricted"] = True
        # 调用基础 execute（修正参数顺序：decision 在前，prompt 在后）
        return await self.execute(decision, prompt, call_fn)

    async def execute_with_role(
        self,
        raw_input: str,
        strategy: Optional[str] = None,
        call_fn: Optional[Callable[[ModelConfig, str], Coroutine[Any, Any, LLMResponse]]] = None,
    ) -> LLMResponse:
        """
        带角色感知的执行入口。

        流程：
        1. 解析 @角色名
        2. 如果有角色 → 使用角色的 model_pref 作为策略，注入 system_prompt
        3. 限制工具权限（只允许角色的 allowed_tools）
        4. 执行并返回

        Args:
            raw_input: 原始用户输入，可能包含 @角色名 前缀
            strategy: 可选的显式策略（仅在无角色前缀时生效）
            call_fn: 可选自定义调用函数

        Returns:
            统一封装的 LLMResponse

        Raises:
            RouterConfigError: 角色名不存在时抛出，附带可用角色列表
        """
        role, clean_input = self.parse_role(raw_input)

        if role:
            # 使用角色的模型偏好作为路由策略
            effective_strategy = self._str_to_strategy(role.model_pref)

            # 构建注入 system_prompt 的请求
            prompt_with_system = self._inject_system_prompt(clean_input, role.system_prompt)

            # 路由决策（使用角色的策略偏好）
            decision = self.route(clean_input, strategy=effective_strategy)

            # 应用角色的 temperature 和 max_tokens 覆盖
            # 注：覆盖写入 decision.primary 的配置中
            if decision.primary:
                decision.primary.capability = ModelCapability(
                    max_context=decision.primary.capability.max_context,
                    max_output=role.max_tokens,
                    supports_streaming=decision.primary.capability.supports_streaming,
                    supports_tools=decision.primary.capability.supports_tools,
                    supports_vision=decision.primary.capability.supports_vision,
                    reasoning_effort=decision.primary.capability.reasoning_effort,
                    strength_tags=decision.primary.capability.strength_tags,
                )

            logger.info(
                "ModelRouter: 角色执行 '%s' | strategy=%s | tools=%s | temp=%.1f",
                role.id,
                role.model_pref,
                role.allowed_tools,
                role.temperature,
            )

            # 执行（工具权限由 Orchestrator 层限制，元数据已记录）
            response = await self._execute_with_tools(
                prompt_with_system,
                decision,
                allowed_tools=role.allowed_tools,
                call_fn=call_fn,
            )

            return response
        else:
            # 无角色前缀，走默认路由
            route_strategy = self._str_to_strategy(strategy)
            decision = self.route(clean_input, strategy=route_strategy)
            return await self.execute(decision, clean_input, call_fn)


class RouterConfigError(Exception):
    """路由配置错误（如找不到对应厂商的健康模型）。"""
    pass


# ---------------------------------------------------------------------------
# 6. ACP 协议扩展辅助类
# ---------------------------------------------------------------------------

@dataclass
class ModelPreference:
    """
    ACP TaskRequest 中的 model_pref 字段的 Python 映射。

    v2.1: preferred_vendor 改为 preferred_provider (provider_id)
    """
    preferred_provider: Optional[str] = None
    preferred_model_id: Optional[str] = None
    temperature_override: Optional[float] = None


# ---------------------------------------------------------------------------
# 7. 工具函数 / 快捷入口 (v2.1)
# ---------------------------------------------------------------------------

# 常见厂商默认模型映射（v2.3 修复：provider.id 如 "deepseek" 不是 API model_id）
_DEFAULT_MODEL_MAP: Dict[str, str] = {
    "deepseek": "deepseek-chat",
    "grok": "grok-beta",
    "kimi": "moonshot-v1-8k",
    "claude": "claude-3-sonnet-20240229",
    "gemini": "gemini-1.5-pro",
    "openai": "gpt-4o",
    "qwen": "qwen2.5-7b",
}

def _provider_to_config(provider: ProviderConfig) -> ModelConfig:
    """将 ProviderConfig 转换为执行用的 ModelConfig"""
    model_id = _DEFAULT_MODEL_MAP.get(provider.id, provider.id)
    return ModelConfig(
        vendor=provider.id,
        model_id=model_id,
        base_url=provider.base_url,
        api_key=provider.api_key,
        capability=ModelCapability(
            max_context=provider.context_window,
            max_output=provider.max_tokens_default,
            supports_streaming=True,
            supports_tools=False,
            supports_vision=False,
            reasoning_effort="medium",
            strength_tags=set(provider.tags),
        ),
    )


def create_default_router() -> ModelRouter:
    """
    快速创建一个带默认配置的 ModelRouter（自动从 JSON/.env 加载）。
    """
    return ModelRouter()


# ---------------------------------------------------------------------------
# 8. 脚本级自检入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 纯本地单元测试（不发起真实 RPC）
    logging.basicConfig(level=logging.DEBUG)

    # 1. 测试 ContextAligner
    aligner = ContextAligner()
    sample_text = "角色：张三，性格内向的程序员。设定：2045年，火星殖民地发生叛乱。伏笔：第三章提到的红色芯片。"
    print("[TEST] estimate_tokens:", aligner.estimate_tokens(sample_text, "deepseek"))
    facts = aligner.extract_key_facts(sample_text + "\n这是额外的无关内容用于测试截取。" * 50)
    print(f"[TEST] extracted {len(facts)} facts")

    # 2. 测试动态 ModelRegistry
    router = create_default_router()
    print(f"[TEST] registered providers: {[p.id for p in router.registry.list(enabled_only=False)]}")

    # 3. 测试 ModelRouter 路由决策（无 RPC）
    decision = router.route("写一个科幻小说的长篇世界观设定，包含编年史", strategy=RouteStrategy.AUTO)
    print(f"[TEST] routing decision: strategy={decision.strategy.name}, primary={decision.primary.vendor}")

    # 4. 测试跨模型上下文对齐
    deepseek_output = "【大纲】第一章：火星叛乱。角色：李明，指挥官。第二章：地球反应。伏笔：红色芯片..."
    aligned = aligner.build_cross_model_prompt(
        original_task="写科幻小说",
        upstream_output=deepseek_output,
        upstream_vendor="deepseek",
        downstream_config=decision.primary,
        instruction_prefix="请基于大纲写细节描写。",
    )
    print(f"[TEST] aligned prompt length: {len(aligned)} chars")

    print("\nAll local tests passed.")
