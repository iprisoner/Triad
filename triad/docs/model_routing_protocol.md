# Triad Model Routing Protocol

## ACP (Agent Control Protocol) 扩展定义

> 本文档定义 Hermes Agent（Python 认知层）的模型动态路由协议扩展。OpenClaw（TypeScript 宿主层）通过 ACP 发送任务时，可附加 `ModelPreference` 与 `RoutingStrategy` 字段；Hermes 侧的 `model_router.py` 消费这些字段完成调度决策。

---

## 1. 协议扩展概览

### 1.1 设计目标

| 目标 | 说明 |
|------|------|
| **策略可控** | 用户/系统可显式指定路由策略（创意型、推理型、长文型等），也可完全交由系统自动推断 |
| **上下文无缝** | 跨模型传递上下文时，自动处理 tokenizer 差异、窗口溢出、信息压缩 |
| **弹性降级** | 主模型超时/熔断时，在 500ms 内切换到次选模型，用户无感知 |
| **可观测** | 每次路由决策生成 `RoutingDecision` 日志，包含 token 估算、选择理由、降级链状态 |

### 1.2 协议栈位置

```
┌─────────────────────────────────────────────┐
│  OpenClaw (TypeScript)                       │
│  ├─ Message Gateway (WebSocket)              │
│  └─ ClawHub (Skill Market)                   │
├─────────────────────────────────────────────┤
│  ACP gRPC / WebSocket                         │
│  ├─ TaskRequest (扩展 model_pref, strategy)  │
│  └─ TaskResponse (含实际使用的 vendor/model)   │
├─────────────────────────────────────────────┤
│  Hermes Agent (Python)                        │
│  ├─ model_router.py ◄── 本文档定义的消费端    │
│  ├─ curator.py                               │
│  └─ context_engine.py                        │
├─────────────────────────────────────────────┤
│  MCP JSON-RPC                                 │
│  └─ Claude Code / Local Executor             │
└─────────────────────────────────────────────┘
```

---

## 2. Protobuf 定义

### 2.1 扩展后的 TaskRequest

```protobuf
syntax = "proto3";
package triad.acp;

// ---------------------------------------------------------------------------
// 枚举：路由策略
// ---------------------------------------------------------------------------
enum RoutingStrategy {
    ROUTING_STRATEGY_UNSPECIFIED = 0;  // 默认 = AUTO
    ROUTING_STRATEGY_AUTO        = 1;  // 自动推断
    ROUTING_STRATEGY_CREATIVE    = 2;  // 创意/破局/发散
    ROUTING_STRATEGY_REASONING   = 3;  // 逻辑推演/代码/数学
    ROUTING_STRATEGY_LONGFORM    = 4;  // 超长文本/世界观/铺垫
    ROUTING_STRATEGY_REVIEW      = 5;  // 审查/一致性/严谨性
    ROUTING_STRATEGY_CHAT        = 6;  // 细节描写/对话/中文语境
    ROUTING_STRATEGY_LOCAL       = 7;  // 本地模型/隐私/低成本
}

// ---------------------------------------------------------------------------
// 消息：模型偏好
// ---------------------------------------------------------------------------
message ModelPreference {
    // 用户/系统偏好的厂商。
    // 可选值: "grok" | "kimi" | "deepseek" | "qwen" | "claude" | "gemini"
    string preferred_vendor = 1;

    // 精确指定模型 ID（覆盖厂商默认选择）。
    // 例: "deepseek-reasoner", "claude-3-5-sonnet-20241022"
    string preferred_model_id = 2;

    // 温度覆盖（覆盖策略默认温度）。
    // 创意任务默认 0.7，推理任务默认 0.2。
    float temperature_override = 3;

    // 是否强制使用用户偏好，即使策略映射中该厂商为次选。
    // true  = 将 preferred_vendor 提升为 primary
    // false = 仅在策略映射匹配时生效（默认）
    bool force_preferred = 4;
}

// ---------------------------------------------------------------------------
// 消息：扩展后的 TaskRequest
// ---------------------------------------------------------------------------
message TaskRequest {
    // === 原有字段（OpenClaw 已有）===
    string task_id       = 1;
    string task_type     = 2;   // "code_review" | "creative_writing" | ...
    string payload       = 3;   // 实际任务内容
    string session_id    = 4;
    bytes  attachment    = 5;   // 可选附件
    map<string, string> metadata = 6;
    int32  priority      = 7;   // 0-9，数字越大优先级越高

    // === 新增字段（模型路由层扩展）===
    ModelPreference model_pref   = 8;  // 用户/系统指定的模型偏好
    RoutingStrategy strategy     = 9;  // 路由策略：AUTO | CREATIVE | REASONING | ...

    // 上下文长度提示（可选）。
    // 当已知任务携带大量上下文时，传入此值帮助路由决策判断是否需要压缩。
    // 单位为 tokens（tiktoken 统一估算）。
    int32 context_length_hint    = 10;

    // 是否启用降级链。
    // true  = 主模型失败时自动切换次选（默认）
    // false = 严格使用指定模型，失败时抛错
    bool enable_fallback         = 11;

    // 流水线模式：若为 true，Hermes 将保留此任务的输出上下文，
    // 供同 session 的下一个任务作为 upstream_output 传递。
    bool pipeline_mode           = 12;
}

// ---------------------------------------------------------------------------
// 消息：扩展后的 TaskResponse
// ---------------------------------------------------------------------------
message TaskResponse {
    string task_id       = 1;
    string status        = 2;   // "success" | "fallback" | "error"
    string result        = 3;     // 模型生成的文本结果
    string error_message = 4;

    // === 新增字段（路由可观测性）===
    string used_vendor    = 5;    // 实际使用的厂商，如 "deepseek"
    string used_model_id  = 6;    // 实际使用的模型 ID
    int32  input_tokens   = 7;    // 实际输入 token 数（厂商返回）
    int32  output_tokens  = 8;    // 实际输出 token 数
    float  latency_ms     = 9;    // 端到端延迟（含降级重试）
    bool   was_fallback   = 10;   // 是否触发了降级链
    string fallback_reason = 11;   // 降级原因："timeout" | "rate_limit" | "5xx"
    string applied_strategy = 12;  // 实际应用的策略名
}
```

### 2.2 消息流向图

```
OpenClaw                                    Hermes (model_router.py)
──────────                                  ─────────────────────────

  │  TaskRequest {
  │    strategy: CREATIVE,
  │    model_pref: {preferred_vendor: "grok"},
  │    pipeline_mode: true
  │  }
  │ ────────────────────────────────────────►
  │                                           │
  │                              ┌────────────┴────────────┐
  │                              │ 1. RouteStrategy.CREATIVE │
  │                              │    → primary: Grok       │
  │                              │    → secondary: Gemini   │
  │                              └────────────┬────────────┘
  │                                           │
  │                              ┌────────────┴────────────┐
  │                              │ 2. ContextAligner        │
  │                              │    → estimate tokens     │
  │                              │    → align if overflow   │
  │                              └────────────┬────────────┘
  │                                           │
  │                              ┌────────────┴────────────┐
  │                              │ 3. FallbackChain         │
  │                              │    → call primary        │
  │                              │    → timeout → secondary │
  │                              └────────────┬────────────┘
  │                                           │
  │  TaskResponse {
  │    used_vendor: "grok",
  │    was_fallback: false,
  │    latency_ms: 1240
  │  }
  │ ◄────────────────────────────────────────
```

---

## 3. 路由策略详解

### 3.1 策略映射表

| 策略 | Primary | Secondary | 核心能力 | 典型温度 | 适用场景 |
|------|---------|-----------|----------|----------|----------|
| **AUTO** | 动态推断 | — | 语义分析 + 关键词匹配 | 0.7 | 所有未明确分类的任务 |
| **CREATIVE** | Grok | Gemini | 发散性、破局思维、反套路 | 0.8 | 世界观脑暴、角色设定、情节创新 |
| **REASONING** | DeepSeek | Claude | 因果链、数学证明、代码 | 0.2 | 剧情推演、逻辑验证、算法生成 |
| **LONGFORM** | Kimi | Gemini 1.5 Pro | 128K-1M 上下文 | 0.6 | 编年史、长篇铺垫、资料整理 |
| **REVIEW** | Claude | DeepSeek | 严谨性、指令遵循 | 0.3 | 一致性审查、矛盾检测、校对 |
| **CHAT** | Kimi | Qwen | 中文语境、对话自然度 | 0.7 | 场景描写、角色对话、心理刻画 |
| **LOCAL** | Qwen | — | 零外泄、低成本、离线 | 0.7 | 敏感内容预处理、常规查询、健康探测 |

### 3.2 策略选择算法（AUTO 模式）

```python
def _auto_infer_strategy(task: str) -> RouteStrategy:
    # 按优先级从高到低匹配关键词集合
    if any(kw in task for kw in {"代码","debug","算法","推理","证明","数学"}):
        return RouteStrategy.REASONING
    if any(kw in task for kw in {"审查","检查","一致","矛盾","校对"}):
        return RouteStrategy.REVIEW
    if any(kw in task for kw in {"大纲","世界观","设定","铺垫","编年史"}):
        return RouteStrategy.LONGFORM
    if any(kw in task for kw in {"对话","描写","场景","细节","氛围"}):
        return RouteStrategy.CHAT
    if any(kw in task for kw in {"创意","脑洞","破局","灵感","新颖"}):
        return RouteStrategy.CREATIVE
    return RouteStrategy.CHAT  # 保守默认
```

### 3.3 温度（Temperature）策略

```
创意任务  : 0.7 - 0.9   (鼓励发散，容忍非常规输出)
推理任务  : 0.1 - 0.3   (低温保证确定性)
审查任务  : 0.2 - 0.4   (低温保证严谨)
描写任务  : 0.6 - 0.8   (适度随机避免模板化)
```

---

## 4. 上下文无缝传递设计

### 4.1 问题定义

当多模型协作完成同一任务时（如 DeepSeek 生成大纲 → Kimi 写细节），需要解决：

1. **Tokenizer 差异**：DeepSeek 对中文的分词更细（膨胀系数 1.10），Qwen 更粗（0.95）
2. **窗口溢出**：DeepSeek 输出 2000 tokens 的大纲，Kimi 的 128K 窗口可完全容纳；但若传递 50K tokens 的上下文到 Grok（131K），需对齐
3. **信息冗余**：全量上下文传递浪费 token，且可能淹没关键信息

### 4.2 解决方案：ContextAligner

#### 4.2.1 Token 统一估算

所有厂商的输入统一用 **tiktoken** 估算，得到与厂商无关的 "reference token count"。

```python
class ContextAligner:
    # 各厂商相对于 tiktoken 的膨胀系数（经验值，需定期校准）
    INFLATION_FACTOR = {
        ModelVendor.GROK:      1.05,
        ModelVendor.KIMI:      1.00,
        ModelVendor.DEEPSEEK:  1.10,
        ModelVendor.QWEN:      0.95,
        ModelVendor.CLAUDE:    1.00,
        ModelVendor.GEMINI:    1.02,
    }

    def estimate_tokens(self, text: str, vendor: ModelVendor) -> int:
        encoder = tiktoken.get_encoding("cl100k_base")  # 统一基准
        base_count = len(encoder.encode(text))
        return int(base_count * self.INFLATION_FACTOR[vendor])
```

#### 4.2.2 关键信息提取（Key Fact Extraction）

当跨模型传递的上下文超过目标模型预算的 80% 时，触发压缩：

```
源上下文（DeepSeek 大纲，2000 tokens）
    │
    ├─ 提取关键事实（Key Facts）
    │    • [角色] 李明，指挥官，性格谨慎
    │    • [设定] 2045年火星叛乱
    │    • [伏笔] 红色芯片在第三章出现
    │
    ├─ 提取尾部原文（保留最近 N tokens 的原始文本）
    │
    └─ 拼接为对齐上下文
         【前文摘要 - 关键事实】
         ...（事实列表）...
         【最近原文】
         ...（尾部原文）...
```

关键信息提取使用 **规则 + 轻量模型** 两级架构：

- **L0 规则引擎**（零 RPC）：正则提取角色、设定、伏笔、实体
- **L1 模型提取**（本地 Qwen）：当规则提取不足时，调用本地模型生成结构化摘要

#### 4.2.3 跨模型接力 Prompt 构造

```python
def build_cross_model_prompt(
    original_task: str,           # "写一部科幻悬疑小说"
    upstream_output: str,         # DeepSeek 生成的 2000 token 大纲
    upstream_vendor: ModelVendor, # DEEPSEEK
    downstream_config: ModelConfig,  # Kimi 的配置
    instruction_prefix: str,      # "请基于大纲写第三章的细节描写"
) -> str:
    aligned = aligner.align_context(upstream_output, upstream_vendor, downstream_config)
    return (
        f"{instruction_prefix}\n\n"
        f"=== 原始任务 ===\n{original_task}\n\n"
        f"=== 上游输出 ({upstream_vendor.value}) ===\n{aligned}\n\n"
        f"=== 你的任务 ===\n请继续推进。"
    )
```

### 4.3 示例：大纲 → 细节的完整传递

```
阶段1: DeepSeek (REASONING)
─────────────────────────────
输入: "设计一部火星叛乱科幻小说的 10 章大纲，要求因果链严密"
输出: 2000 tokens 的结构化大纲

阶段2: ContextAligner
─────────────────────
源: DeepSeek (膨胀系数 1.10)
目标: Kimi (膨胀系数 1.00)
估算: 2000 / 1.10 * 1.00 ≈ 1818 tokens (Kimi 视角)
预算: 128K * 0.8 = 102.4K
结论: 无需压缩，直接传递全文

阶段3: Kimi (CHAT)
──────────────────
输入: build_cross_model_prompt(...)
      包含完整大纲 + "请基于大纲写第三章的细节描写"
输出: 3000 tokens 的详细场景描写
```

---

## 5. FallbackChain 降级链设计

### 5.1 触发条件

| 条件 | 示例 | 立即处理 |
|------|------|----------|
| 超时 | asyncio.TimeoutError | 同厂商重试（指数退避） |
| 速率限制 | HTTP 429 | 退避后重试，最多 3 次 |
| 服务端错误 | HTTP 5xx | 切换次选模型 |
| 内容安全拦截 | finish_reason="content_filter" | 切换次选模型 |
| 模型熔断 | 连续 5 次失败 | 跳过该模型 120 秒 |

### 5.2 降级顺序

```
用户请求 → Primary Model
              │
              ├─ 成功 ───────────────────────► 返回结果
              │
              ├─ 超时/429 ──► 同厂商重试 (1.5^n 秒退避)
              │                │
              │                ├─ 成功 ───► 返回
              │                └─ 失败 ───► 切换 Secondary
              │                              │
              ├─ 5xx/内容过滤 ───────────────► 切换 Secondary
              │                              │
              │                              ├─ 成功 ───► 返回
              │                              └─ 失败 ───► 终极兜底
              │                                             │
              └─ 熔断 (连续5次失败) ───────────────────────► 终极兜底
                                                               │
                                                               ▼
                                                    本地 Qwen (LOCAL)
                                                        │
                                                        ├─ 成功 ──► 返回
                                                        └─ 失败 ──► FallbackExhaustedError
```

### 5.3 熔断器状态机

```
        ┌──────────┐
   ┌───►│  CLOSED  │◄──── 成功探测
   │    │ (正常)   │
   │    └────┬─────┘
   │         │ 连续失败 >= 5
   │    ┌────▼─────┐
   │    │  OPEN    │
   │    │ (熔断)   │
   │    └────┬─────┘
   │         │ 120 秒超时
   │    ┌────▼─────┐
   └───┐│ HALF-OPEN│
      ││ (半开)   │
      │└────┬─────┘
      └─────┘ 下次请求成功则关闭，失败则重新打开
```

---

## 6. 配置示例

### 6.1 OpenClaw 侧（TypeScript）发送任务

```typescript
import { acp } from "./proto/acp";

const request = acp.TaskRequest.create({
    taskId: "novel_ch3_detail",
    taskType: "creative_writing",
    payload: "基于火星叛乱大纲，写第三章李明在观测舱的心理描写",
    strategy: acp.RoutingStrategy.CHAT,        // 明确指定对话/描写策略
    modelPref: {
        preferredVendor: "kimi",              // 用户偏好 Kimi
        temperatureOverride: 0.75,
    },
    pipelineMode: true,                       // 保留上下文，供第四章接力
    contextLengthHint: 2500,                  // 已知携带 2500 tokens 大纲
});

ws.send(acp.TaskRequest.encode(request).finish());
```

### 6.2 Hermes 侧（Python）消费任务

```python
from model_router import ModelRouter, RouteStrategy, ModelPreference

# 解析 ACP 请求
pref = ModelPreference(
    preferred_vendor=request.model_pref.preferred_vendor,
    temperature_override=request.model_pref.temperature_override,
)
strategy = RouteStrategy(request.strategy)  # protobuf enum → Python enum

# 路由决策
decision = router.route(
    task_description=request.payload,
    strategy=strategy,
    preferred_vendor=pref.to_vendor_enum(),
    context_length_hint=request.context_length_hint,
)

# 执行（自动处理降级）
response = await router.execute(decision, prompt)
```

### 6.3 流水线模式：多阶段接力

```python
stages = [
    # (策略, 任务描述, 跨阶段指令前缀)
    (RouteStrategy.CREATIVE, "设计科幻悬疑世界观", "请打破传统套路，设计新颖设定。"),
    (RouteStrategy.LONGFORM,  "写编年史与详细设定", "请基于世界观写 3000 字编年史。"),
    (RouteStrategy.REASONING, "检查因果链并推演剧情", "请检查编年史中的因果逻辑。"),
    (RouteStrategy.CHAT,      "写第一章场景与对话", "请基于审定后的大纲写第一章。"),
    (RouteStrategy.REVIEW,    "审查第一章一致性", "请审查第一章是否有逻辑矛盾。"),
]

results = await router.execute_pipeline(stages)
# results[0] = Grok 的世界观
# results[1] = Kimi 的编年史（接收 Grok 输出）
# results[2] = DeepSeek 的推演（接收 Kimi 输出）
# ...
```

---

## 7. 可观测性与调试

### 7.1 路由决策日志（JSON 格式）

```json
{
  "timestamp": "2025-01-15T09:23:17Z",
  "task_id": "novel_ch3_detail",
  "routing": {
    "requested_strategy": "CHAT",
    "inferred_strategy": "CHAT",
    "primary": {
      "vendor": "kimi",
      "model_id": "moonshot-v1-128k",
      "estimated_input_tokens": 2847,
      "estimated_output_tokens": 4096
    },
    "secondary": {
      "vendor": "qwen",
      "model_id": "qwen2.5-72b-instruct"
    }
  },
  "context": {
    "aligned": false,
    "source_tokens": 2847,
    "target_budget": 102400,
    "compression_ratio": 1.0
  },
  "execution": {
    "was_fallback": false,
    "latency_ms": 1240,
    "actual_input_tokens": 2901,
    "actual_output_tokens": 3124
  }
}
```

### 7.2 降级事件日志

```json
{
  "timestamp": "2025-01-15T09:24:05Z",
  "task_id": "novel_ch3_detail",
  "event": "FALLBACK_TRIGGERED",
  "primary": "grok-2-latest",
  "reason": "timeout",
  "secondary": "gemini-1.5-pro-latest",
  "retry_attempt": 2,
  "circuit_status": "CLOSED"
}
```

---

## 8. 安全与隐私

| 风险 | 缓解措施 |
|------|----------|
| API Key 泄露 | ModelRegistry 中 key 标记为 `__repr__` 不输出；支持运行时热更新替换 |
| 敏感内容外泄 | LOCAL 策略强制路由到本地 Qwen，数据不出内网 |
| 提示词注入 | `ContextAligner.align_context` 对 upstream_output 做长度截断而非格式解析，避免执行链攻击 |
| 速率限制耗尽 | FallbackChain 指数退避 + 熔断，避免雪崩 |

---

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2025-01-15 | 初始版本，定义 7 种路由策略、ContextAligner、FallbackChain |
