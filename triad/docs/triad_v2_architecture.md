# Triad 融合系统设计文档 v2.0
## 多模态 · 多模型动态路由 · 全链路内容生成工作站

---

## 文档元信息

| 项目 | 值 |
|------|-----|
| 版本 | v2.1 |
| 日期 | 2026-05-02 |
| 代号 | v2.0-llama-fixed |
| 硬件基准 | 双路 Intel Xeon E5-2673v3 (24C/48T) + 魔改 NVIDIA RTX 2080Ti 22GB |
| OS 基准 | WSL2 Ubuntu 22.04 / 原生 Linux 5.15+ |
| 目标场景 | 软件工程 + 小说推演 + 多模态内容生成 |

---

## 变更日志（v1.2 → v2.0 → v2.0-llama）

v1.2 是一个**纯文本/代码智能体系统**。v2.0 进化为**全链路多模态内容生成工作站**。核心跨越体现在五个维度：

1. **内核级模型动态路由**：从单一模型后端进化为 6 厂商（Grok/Kimi/DeepSeek/Gemini/Claude/Qwen）自动热切换
2. **多模态执行层**：从仅有 Claude Code（代码）扩展到 ComfyUI（文生图/图生视频/TTS/InstantID 面部一致性）
3. **文学创作工作流**：从只有代码修复 Skill 进化为并行支持小说推演四阶段流水线
4. **多模态记忆总线**：从纯 Markdown 文本进化为文本 + 二进制资产（图像/视频/音频）统一索引
5. **llama.cpp 本地推理引擎**：从 vLLM/Ollama 双服务进化为单一 llama-server，通过 `-ngl` 参数实现 GPU/CPU 跷跷板调度，LLM 服务永不中断

---

## 一、核心洞察：为什么 v2.0 是必要的

v1.2 的架构假设是：**所有任务都可以被文本描述，所有执行都可以归结为代码操作**。这在软件工程场景下成立，但在以下场景下崩溃：

- **小说推演**：需要评估"人物行为逻辑是否自洽"——这不是代码能执行的，需要专门的认知评估引擎
- **角色概念图生成**：需要协调 ComfyUI 的节点工作流、保持面部一致性（InstantID）、管理参考图资产
- **视频生成**：需要分钟级渲染时间的进度追踪、显存分时复用、预览帧实时推送
- **多模型脑暴**：需要让 Grok 负责破局、Kimi 负责长文铺垫、DeepSeek 负责逻辑推演——它们必须能在同一任务链中接力

**v2.0 的核心决策**：不把 Triad 做成一个"能做所有事的单体 Agent"，而是做成一个**能编排任意数量专用 Agent 的分布式操作系统**。模型路由、多模态执行、记忆总线，都是这个操作系统的基础设施。

---

## 二、v2.0 统一架构总览

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              【第一层：OpenClaw 宿主操作系统】                         │
│                                   TypeScript / Node.js                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │   Gateway    │  │  Channels    │  │   ClawHub    │  │    ClawPod 编排器        │   │
│  │  WebSocket   │  │ 微信/Slack   │  │  技能市场    │  │   Docker 生命周期管理     │   │
│  │  SSE 流式    │  │ 异步应答     │  │  NovelSkill │  │   NUMA 感知调度          │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘   │
│         │                 │                  │                       │               │
│  ┌──────▼─────────────────▼──────────────────▼───────────────────────▼─────────────┐  │
│  │                           ACP Router (Agent Control Protocol v2)                │  │
│  │    TaskRequest + ModelPreference + RoutingStrategy ──► Hermes Agent            │  │
│  │    StatusUpdate + ImagePreview / VideoFrame ──► 用户前端                        │  │
│  └────────────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼ ACP / gRPC
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                          【第二层：Hermes 认知编排内核】                               │
│                                  Python 3.11+                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │ Model Router │  │  Curator     │  │   Context    │  │    Memory Manager        │   │
│  │  动态路由     │  │  技能策展     │  │   Engine     │  │   文本+向量+资产索引      │   │
│  │  6 厂商热切换 │  │  自动进化     │  │  跨模型对齐   │  │   分层记忆                │   │
│  │  熔断降级     │  │  质量评估     │  │  上下文压缩   │  │   增量同步                │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘   │
│         │                  │                  │                       │               │
│  ┌──────▼──────────────────▼──────────────────▼───────────────────────▼─────────────┐   │
│  │                           Reflection Loop (反思循环 v2)                          │   │
│  │                                                                                 │   │
│  │   输入 ──► [模型路由决策] ──► [策略选择] ──► [工具调用] ──► [结果评估] ──► 输出   │   │
│  │                │                │                │               │              │   │
│  │                │                │                │               ▼              │   │
│  │                │                │                │        ┌──────────────┐      │   │
│  │                │                │                │        │ Error Class. │      │   │
│  │                │                │                │        │   失败时     │      │   │
│  │                │                │                │        │   策略调整   │      │   │
│  │                │                │                │        └──────┬───────┘      │   │
│  │                │                │                │               │              │   │
│  │                │                │                ▼               ▼              │   │
│  │                │                │        ┌──────────────┐  ┌──────────────┐      │   │
│  │                │                │        │   Claude     │  │  ComfyUI     │      │   │
│  │                │                │        │   Code       │  │  MCP Bridge  │      │   │
│  │                │                │        │   (代码)      │  │  (图像/视频)  │      │   │
│  │                │                │        └──────────────┘  └──────────────┘      │   │
│  │                │                │                                               │   │
│  │                ▼                ▼                                               │   │
│  │        ┌──────────────────────────────────┐                                    │   │
│  │        │ SkillCrystallizer (技能固化器)     │                                    │   │
│  │        │  成功策略 ──► NovelSkill / CodeSkill │                               │   │
│  │        │  ──► OpenClaw ClawHub 热加载          │                               │   │
│  │        └──────────────────────────────────┘                                    │   │
│  └────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼ MCP / JSON-RPC (多路复用)
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                           【第三层：多模态执行集群】                                   │
│                                                                                      │
│  ┌────────────────────────────┐    ┌────────────────────────────┐                  │
│  │     Claude Code Daemon     │    │    ComfyUI MCP Bridge      │                  │
│  │     (常驻 MCP Server)      │    │    (Docker 容器内)          │                  │
│  │  ┌────┐ ┌────┐ ┌────┐ ┌───┐│    │  ┌────────┐ ┌────────┐ ┌──┐ │                  │
│  │  │Read│ │Edit│ │Bash│ │Git││    │  │generate│ │generate│ │SVD│ │                  │
│  │  └────┘ └────┘ └────┘ └───┘│    │  │character│ │ scene  │ │   │ │                  │
│  │  代码执行 · Git 操作        │    │  │concept  │ │control │ │TTS│ │                  │
│  │                            │    │  │Instant  │ │net/IP  │ │   │ │                  │
│  └────────────────────────────┘    │  │ID      │ │Adapter │ │   │ │                  │
│                                     └────────────────────────────┘                  │
│  ┌────────────────────────────────────────────────────────────────────────────────┐│
│  │                     WSL2 宿主机原生: ComfyUI (Python venv)                        ││
│  │                     监听 0.0.0.0:8188, Docker 经 host.docker.internal 访问         ││
│  │                                                                                  ││
│  │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐                       ││
│  │   │ SDXL     │  │ControlNet│  │   SVD    │  │ InstantID│                       ││
│  │   │ + LoRA   │  │+IP-Adapt │  │ 视频生成  │  │ 面部一致  │                       ││
│  │   └──────────┘  └──────────┘  └──────────┘  └──────────┘                       ││
│  │                                                                                  ││
│  └────────────────────────────────────────────────────────────────────────────────┘│
│                                                                                      │
│  ┌────────────────────────────────────────────────────────────────────────────────┐ │
│  │                      VRAM Scheduler (22GB 魔改 2080Ti, llama.cpp)                 │ │
│  │                                                                                  │ │
│  │   常态 IDLE:  2GB Embedding + 9GB LLM GPU (-ngl 99) ──────────► 9GB 空闲缓冲    │ │
│  │          │                                                                       │ │
│  │          ▼ 收到渲染任务                                                           │ │
│  │   CPU_FALLBACK: 2GB Embedding + 0GB LLM GPU                      11GB 空闲缓冲   │ │
│  │                 llama-server 切 -ngl 0 (CPU 48线程)                              │ │
│  │                 LLM 不中断，只是变慢 (~5 tok/s)                                   │ │
│  │          │                                                                       │ │
│  │          ▼ ComfyUI 宿主机触发渲染                                                  │ │
│  │   RENDERING: 2GB Embedding + 0GB LLM + 20GB ComfyUI 宿主机独占                    │ │
│  │          │                                                                       │ │
│  │          ▼ 渲染完成                                                               │ │
│  │   RECOVERING: 2GB Embedding + 9GB LLM GPU (-ngl 99, mmap 热映射)               │ │
│  │                LLM 恢复速度 (~25 tok/s)                                          │ │
│  │                                                                                  │ │
│  └────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                      │
│  Fallback: pexpect CLI Wrapper (Claude Code) / --lowvram 降级 (ComfyUI)             │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、模块详解

### 3.1 模块 A：内核级模型动态路由 (Model Router)

**位置**：Hermes Agent 核心 (`mind/model_router.py`)
**解决的问题**：单一模型无法在所有任务类型上都最优。Grok 发散性强但不够严谨，DeepSeek 推理强但创意不足，Kimi 长文本强但破局能力弱。

#### 3.1.1 路由策略矩阵

| 策略 | 首选模型 | 次选模型 | 温度 | 适用场景 |
|------|---------|---------|------|---------|
| `CREATIVE` | Grok | Gemini | 1.0 | 破局设定、脑暴、反套路设计 |
| `REASONING` | DeepSeek | Claude | 0.3 | 逻辑推演、因果链检查、代码生成 |
| `LONGFORM` | Kimi | Gemini 1.5 Pro | 0.7 | 世界观铺垫、超长上下文梳理 |
| `REVIEW` | Claude | DeepSeek | 0.2 | 一致性审查、矛盾标记、逻辑审核 |
| `CHAT` | Kimi | Qwen | 0.8 | 对话描写、细节润色、中文语境 |
| `LOCAL` | Qwen(本地) | — | 0.5 | 系统维护、隐私敏感、低成本查询 |
| `AUTO` | 自动推断 | 自动推断 | 动态 | 默认策略，由关键词 + 历史统计推断 |

#### 3.1.2 ACP 协议扩展

```protobuf
// TaskRequest v2 扩展字段
message TaskRequest {
  // ... v1.2 原有字段 ...
  
  ModelPreference model_pref = 8;   // 用户/系统指定的模型偏好
  RoutingStrategy strategy = 9;     // 路由策略：AUTO | CREATIVE | REASONING | ...
}

message ModelPreference {
  string primary_vendor = 1;        // 例如 "grok", "deepseek", "kimi"
  float temperature_override = 2;   // 可选：覆盖默认温度
  int32 max_tokens_override = 3;    // 可选：覆盖默认 max_tokens
}

enum RoutingStrategy {
  ROUTING_STRATEGY_AUTO = 1;
  ROUTING_STRATEGY_CREATIVE = 2;
  ROUTING_STRATEGY_REASONING = 3;
  ROUTING_STRATEGY_LONGFORM = 4;
  ROUTING_STRATEGY_REVIEW = 5;
  ROUTING_STRATEGY_CHAT = 6;
  ROUTING_STRATEGY_LOCAL = 7;
}
```

#### 3.1.3 上下文无缝传递

**核心问题**：DeepSeek 用 2000 tokens 生成小说大纲后，如何传递给 Kimi 进行 3000 tokens 的细节描写？两者 tokenizer 不同，直接传递可能溢出或丢失关键信息。

**解决方案（ContextAligner）**：
1. **统一估算**：所有模型统一用 `tiktoken`（cl100k_base）估算，为各模型附加膨胀系数：
   - DeepSeek: ×1.10（中文 tokenizer 密度低）
   - Kimi: ×1.05
   - Qwen: ×0.95
2. **关键信息提取**：不是全量传递，而是提取 `KeyFacts`（角色名单、核心冲突、世界观规则）→ 结构化 JSON
3. **自适应压缩**：如果上下文超过目标模型窗口的 80%，自动触发 `ContextCompressor` 摘要

```python
# 跨模型传递示例
async def handoff_deepseek_to_kimi(deepseek_output: str, task: TaskRequest):
    # 1. 统一 token 估算
    tokens = aligner.estimate(deepseek_output, source="deepseek")
    
    # 2. 关键信息提取（结构化接力 Prompt）
    key_facts = await extract_key_facts(deepseek_output)
    relay_prompt = build_relay_prompt(key_facts, target="kimi")
    
    # 3. 调用 Kimi
    return await model_router.execute(relay_prompt, strategy=RoutingStrategy.CHAT)
```

#### 3.1.4 弹性降级链 (FallbackChain)

**三层降级**：
1. **指数退避重试**：主模型超时，等待 1s → 2s → 4s 重试，最多 3 次
2. **次选模型切换**：主模型 3 次失败后，自动切到次选模型（如 Grok → Gemini）
3. **本地 Qwen 终极兜底**：全部云端模型不可用时，切到本地部署的 Qwen

**熔断器状态机**：
- `CLOSED`：正常调用
- `OPEN`：连续 5 次失败，熔断 30 秒
- `HALF-OPEN`：30 秒后放 1 个试探请求，成功则恢复 CLOSED

---

### 3.2 模块 B：文学创作工作流 (Novel Workflow)

**位置**：Hermes Agent 扩展 (`mind/novel_curator.py`)
**解决的问题**：v1.2 的 Curator 只懂代码质量（测试通过率、编译错误），不懂文学质量（人设一致性、伏笔回收率、节奏控制）。

#### 3.2.1 四阶段流水线

```
阶段1: 大纲论证          阶段2: 剧情推演          阶段3: 细节描写          阶段4: 审查迭代
─────────────────    ─────────────────    ─────────────────    ─────────────────

┌───────────┐          ┌───────────┐          ┌───────────┐          ┌───────────┐
│   Grok    │          │ DeepSeek  │          │   Kimi    │          │  Claude   │
│  破局设定  │─────────►│  逻辑推演  │─────────►│  场景描写  │─────────►│  逻辑审查  │
│  发散脑洞  │          │  因果检查  │          │  对话设计  │          │  矛盾标记  │
└───────────┘          └───────────┘          └───────────┘          └───────────┘
       │                      │                      │                      │
       ▼                      ▼                      ▼                      ▼
┌───────────┐          ┌───────────┐          ┌───────────┐          ┌───────────┐
│   Kimi    │          │   Kimi    │          │   Grok    │          │ DeepSeek  │
│ 长文铺垫  │          │ 长文推演  │          │  对话润色  │          │ 推理复核  │
└───────────┘          └───────────┘          └───────────┘          └───────────┘
```

**每阶段职责**：

| 阶段 | 负责模型 | 输入 | 输出 | 关键能力 |
|------|---------|------|------|---------|
| 大纲论证 | Grok + Kimi | 一句话灵感 | 万字大纲 + 世界观设定 | Grok 打破套路，Kimi 铺垫长文 |
| 剧情推演 | DeepSeek + Kimi | 大纲 | 章节细纲 + 因果链 | DeepSeek 检查"如果 A 则 B 是否必然" |
| 细节描写 | Kimi + Grok | 细纲 | 完整章节文本 | Kimi 场景，Grok 对话 |
| 审查迭代 | Claude + NovelCurator | 章节文本 | 评分 + 修改建议 | Claude 标记矛盾，NovelCurator 量化 |

#### 3.2.2 NovelCurator 四维评估体系

替代 v1.2 的代码版 Curator，专为文学质量设计：

| 维度 | 权重 | 评估内容 | 低于 6 分的自动调整 |
|------|------|---------|-------------------|
| `character_consistency` | 30% | 角色行为是否与人设一致（动机/恐惧/语言习惯） | 注入"人设检查清单"步骤 |
| `plot_logic` | 30% | 情节因果链是否自洽 | 注入"因果预推演"步骤 |
| `pacing` | 20% | 节奏控制（慢热/快节奏是否得当） | 注入"段落长度变化模板" |
| `foreshadowing` | 20% | 伏笔回收率（埋设 → 传递 → 回收） | 注入"伏笔注册表"步骤 |

**评估示例**：
```python
assessment = novel_curator.assess(chapter_text, characters=[alice, bob])
# 输出:
# {
#   "character_consistency": 8.5,  # Alice 的行为符合她的"谨慎"人设
#   "plot_logic": 6.0,             # ⚠️ 此处 Bob 突然知道了他不可能知道的信息
#   "pacing": 7.0,                  # 前 500 字铺垫充足，转折速度适中
#   "foreshadowing": 9.0,           # 第3章埋的"红色纽扣"伏笔在本章完美回收
#   "overall": 7.6,
#   "adjustments": ["inject_causal_check", "inject_pacing_template"]
# }
```

#### 3.2.3 Skill 固化机制 (NovelSkill)

成功的创作策略自动固化为 `NovelSkill`，存入 OpenClaw ClawHub：

```yaml
# ~/.triad/memory/skills/novel_suspense_v1.md
skill_id: novel_suspense_v1
name: "悬疑桥段设计 + 伏笔提前 3 章埋设"
type: novel
creation_context:
  trigger_pattern: "悬疑类章节 + 需要意外转折"
  source_models: [grok, deepseek]
  avg_assessment: 8.2
steps:
  - "第1步: Grok 生成 3 个反套路选项"
  - "第2步: DeepSeek 检查每个选项的因果可行性"
  - "第3步: 选择最佳选项，在第 N-3 章埋设无关紧要的日常细节作为伏笔"
  - "第4步: 在第 N 章让该细节成为关键证据"
  - "第5步: NovelCurator 验证回收率 ≥ 8.0"
tags: [悬疑, 伏笔, 反转, 群像]
```

---

### 3.3 模块 C：HPC 硬件极致调度

**位置**：`docker-compose.hpc.yml` + `docs/hpc_scheduling.md`
**解决的问题**：双路 E5-2673v3 有 48 线程但 NUMA 拓扑复杂，魔改 22G 2080Ti 不支持 MIG 显存硬分区——必须在软件层实现极致调度。

#### 3.3.1 NUMA 感知 CPU 绑定

双路 E5-2673v3 拓扑（Node 0 + Node 1，各 12 核 24 线程）：

| 服务 | NUMA Node | CPU 绑定 (逻辑核) | 内存策略 | 说明 |
|------|-----------|------------------|---------|------|
| OpenClaw | Node 0 | `0-7,24-31` (16 线程) | preferred:0 | I/O 密集型，独占 Node 0 前 8 物理核 |
| Hermes | Node 0+1 | `8-11,32-35,12-19,36-43` (24 线程) | interleave | Python GIL 宽队列，跨 Node 调度 |
| Qdrant | Node 1 | `20-21,44-45` (4 线程) | bind:1 | 向量库隔离，避免 Node 0 内存带宽争用 |
| Registry | Node 1 | `22-23,46-47` (4 线程) | bind:1 | MCP 注册中心，极低频 |
| llama-server (本地 LLM) | Node 1 | `20-23,44-47` (8 线程) | bind:1 | llama-server GPU 模式 (-ngl 99)，profile 控制启动 |
| ComfyUI (宿主机原生) | Node 1 | `12-19,36-43` (16 线程) | bind:1 | 渲染区，与 Hermes 共享核心（Hermes Embedding 低负载） |
| ClawPod-* | 动态 | OpenClaw 运行时注入 | — | small: 2 核 / medium: 4 核 / large: 8 核 |

**关键验证**：4 个常驻服务间 CPU 零冲突。ComfyUI 宿主机进程与 Hermes 共享 Node 1 核心，但 Hermes 的 Embedding 服务是低负载常驻，不影响渲染峰值。

#### 3.3.2 GPU 显存分区（魔改 2080Ti 22GB，llama.cpp）

```
22GB 总分区:
├── 2GB 常驻: Hermes Embedding (BGE-large fp16, 永不卸载)
│            PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True 软限制
├── 9GB LLM GPU: Qwen-14B Q4_K_M (常态, -ngl 99, 全部层在 GPU)
│                llama-server 提供 OpenAI 兼容 API
├── 9GB 空闲/缓冲: 常态下 ComfyUI 可预热缓存
├── 2GB 系统预留: CUDA Context / 驱动开销

渲染触发时:
├── 2GB 常驻: Embedding（永不卸载）
├── 0GB LLM GPU: llama-server 切到 -ngl 0, 模型权重保留在内存(RAM)
│                双路 E5-2673v3 的 48 线程接管推理 (~5 tok/s)
│                LLM 服务不中断！
├── 20GB ComfyUI 独占: SDXL + ControlNet + SVD
└── 2GB 系统预留

渲染完成:
├── 2GB 常驻: Embedding
├── 9GB LLM GPU: llama-server 重启为 -ngl 99, mmap 热映射 (<2秒)
│                恢复 ~25 tok/s 推理速度
└── 11GB 空闲回归
```

**协作式管理**（2080Ti 不支持 MIG）：
- llama.cpp 通过 `-ngl` 参数精确控制 GPU 层数
- 渲染任务触发时，vram_scheduler 发送 SIGTERM 关闭 GPU 模式 llama-server
- 瞬间拉起 `-ngl 0` CPU 模式 llama-server，权重仍在内存中（mmap）
- 显存释放到 ~2GB，ComfyUI 独占 20GB
- 渲染完成后，SIGTERM CPU 模式，重启 `-ngl 99` GPU 模式，mmap 热映射 <2秒

#### 3.3.3 Docker Compose Profile 策略

| Profile | 启动命令 | 包含服务 | 适用场景 |
|---------|---------|---------|---------|
| 默认 | `docker compose up -d` | openclaw, hermes, qdrant, registry | 纯云端模型，无本地推理 |
| `local-llm` | `--profile local-llm up -d` | + llama-server (GPU 模式) | 本地 LLM 推理，隐私优先 |
| `render` | `--profile render up -d` | 核心服务 (ComfyUI 在宿主机手动启动) | 多模态生成 |
| `hpc-full` | `--profile hpc-full up -d` | 核心服务 + llama-server + clawpod 模板 | 全功能工作站 |
| `debug` | `--profile debug up -d` | + text-generation-webui | 模型调试 |
| `clawpod` | `docker compose run --rm clawpod-<size>` | 动态任务容器 | 隔离任务执行 |

---

### 3.4 模块 D：多模态工作流引擎 (ComfyUI MCP Bridge)

**位置**：`hand/comfyui_mcp_bridge.py` + `hand/vram_scheduler.py` + `scripts/install_comfyui.sh`
**架构变更**：ComfyUI 已从 Docker 容器迁移到 **WSL2 宿主机原生 Python venv** 运行。
**解决的问题**：
1. Hermes Agent 需要通过 MCP 协议调用 ComfyUI，但 ComfyUI 是节点式工作流而非命令行工具
2. 22GB 显存需要在 LLM 和渲染器之间分时复用
3. **Docker 内运行 ComfyUI 导致**: PyTorch CUDA 版本冲突、自定义节点臃肿、魔改 2080Ti 驱动兼容性风险

#### 架构变化概览

```
第三层执行层架构变化：
├── Docker 容器内：
│   ├── Claude Code Daemon（常驻 MCP Server）
│   └── MCP Bridge（调用 ComfyUI API via host.docker.internal:8188）
├── WSL2 宿主机原生：
│   └── ComfyUI（Python venv，监听 0.0.0.0:8188）
│       ├── SDXL + LoRA
│       ├── ControlNet + IP-Adapter
│       ├── SVD 视频生成
│       └── InstantID 面部一致性
```

**通信路径**：
- Docker 容器 → `host.docker.internal:8188` → WSL2 宿主机 ComfyUI
- 所有需要访问 ComfyUI 的容器需配置 `extra_hosts: ["host.docker.internal:host-gateway"]`
- MCP Bridge 启动时执行 health_check，ComfyUI 未启动时给出清晰错误提示

#### 3.4.1 MCP 工具接口

Hermes 通过 MCP `tools/list` 发现 5 个多模态工具：

| 工具名 | 输入 | 输出 | 内部 ComfyUI 工作流 |
|--------|------|------|-------------------|
| `generate_character_concept` | 角色描述, 风格预设, 尺寸 | 概念图路径 | SDXL Base + LoRA, 8 节点 |
| `generate_scene` | 场景描述, 氛围, 光照, 参考图? | 场景图路径 | ControlNet + IP-Adapter, 10 节点 |
| `generate_video_clip` | 输入图, 运动提示, 帧数, fps | 视频路径 + 预览帧 | SVD (Stable Video Diffusion), 6 节点 |
| `generate_tts` | 文本, 说话人ID, 情感, 语速 | 音频路径 + 时长 | 外部 TTS API (GPT-SoVITS / Qwen-TTS) |
| `instantid_face_swap` | 目标图, 面部参考图 | 换脸后图像路径 | InstantID 保持角色一致性, 13 节点 |

#### 3.4.2 VRAM 分时复用状态机 (ComfyUI 宿主机原生模式)

```
状态: IDLE ──────────────────────────────────────────────────────►
      ├── 2GB: Embedding（常驻，不可动）
      ├── 9GB: LLM GPU（llama-server -ngl 99，全部层在 GPU，~25 tok/s）
      └── 9GB: 空闲缓冲区

收到渲染任务 ──► 状态: CPU_FALLBACK ──────────────────────────────►
      ├── 2GB: Embedding（常驻）
      ├── 0GB: LLM GPU（llama-server 切 -ngl 0，权重在内存，~5 tok/s）
      └── 11GB: 空闲缓冲区（准备给 ComfyUI 宿主机）

收到渲染任务 ──► 状态: RENDERING ───────────────────────────────►
      ├── 2GB: Embedding（常驻）
      ├── 0GB: LLM（CPU 模式运行中，显存完全释放）
      └── 20GB: ComfyUI 宿主机独占
                  SDXL Base 6GB + Refiner 4GB + ControlNet 2GB + SVD 8GB
                  (ComfyUI 在 WSL2 宿主机 venv 运行，非 Docker)

渲染完成 ──────► 状态: RECOVERING ──────────────────────────────►
      ├── 2GB: Embedding（常驻）
      ├── 9GB: LLM GPU（llama-server 重启 -ngl 99，mmap 热映射，<2秒）
      └── 9GB: 空闲缓冲区
      └── 16GB: 回归空闲

状态: EMERGENCY（显存不足时）
      └── ComfyUI 宿主机降级到 --lowvram / --normalvram 模式
```

**宿主机原生模式的优势**：
1. **显存零开销**: 无 Docker 容器运行时显存占用，ComfyUI 直接使用宿主机 CUDA
2. **驱动兼容性**: 直接使用宿主机 NVIDIA 驱动，避免 Docker 内驱动版本错位
3. **依赖隔离**: Python venv 隔离 ComfyUI 依赖，不影响 Docker 内其他服务
4. **自定义节点自由**: 无需重建 Docker 镜像，git clone 即可安装新节点
5. **模型挂载简化**: 软链接 `~/.triad/models/comfyui/` → `ComfyUI/models/`，无需卷映射

**实现机制**：
1. `pynvml` 实时监控显存占用 (宿主机侧 ComfyUI 也可通过 nvidia-smi 查看)
2. 渲染前 vram_scheduler 发送 SIGTERM 关闭 llama-server GPU 模式
3. 瞬间拉起 `-ngl 0` CPU 模式 llama-server，权重保留在内存（mmap）
4. LLM 服务不中断，推理速度从 ~25 tok/s 降到 ~5 tok/s（双路 E5 48 线程硬扛）
5. 渲染完成后，SIGTERM CPU 模式，重启 `-ngl 99` GPU 模式，mmap 热映射 <2秒
6. 显存不足时 ComfyUI 宿主机自动降级 `--lowvram`（模型分块加载到内存）

#### 3.4.3 实时进度推送

ComfyUI 通过 WebSocket 返回每步进度，Bridge 转换为 ACP `StatusUpdate`：

```protobuf
message StatusUpdate {
  // ... v1.2 原有字段 ...
  
  oneof preview {
    TextPreview  text  = 10;   // "渲染中：Step 15/50，预计剩余 2 分钟"
    ImagePreview image = 11;   // 每 5 步一张预览图（JPEG base64，<200KB）
    VideoFrame   frame = 12;   // 视频生成的关键帧
  }
}

message ImagePreview {
  string mime_type = 1;       // "image/jpeg"
  bytes  data = 2;             // base64 编码图像数据
  int32  step = 3;            // 当前采样步数
  int32  total_steps = 4;     // 总步数
}
```

OpenClaw Gateway 将 `ImagePreview` 直接转发到用户前端（微信/Slack/Web），实现"边生成边看"的体验。

---

### 3.5 模块 E：多模态记忆总线升级

**位置**：`memory/asset_manager.py` + `docs/multimodal_bus.md`
**解决的问题**：v1.2 的记忆总线只存 Markdown 文本，无法管理角色概念图、视频片段、TTS 音频。

#### 3.5.1 新目录结构

```
~/.triad/memory/
├── facts/                      # 文本事实（增强：支持资产链接）
│   └── characters/
│       └── alice.md            # 包含 ![正面照](asset://faces/alice_v3.png)
├── skills/                     # Markdown 技能定义（NovelSkill / CodeSkill）
├── episodes/                   # Markdown 执行日志
├── vectors/                    # faiss 文本向量索引
└── assets/                     # ★ 新增：二进制媒体资产库
    ├── faces/                  # 角色面部参考图（PNG 1024x1024）
    │   ├── alice_v3.png
    │   └── alice_v3.png.meta.json
    ├── scenes/                 # 场景概念图（PNG 1344x768）
    ├── videos/                 # 视频片段（MP4 1024x576）
    ├── audio/                  # TTS 语音（WAV/MP3）
    └── thumbs/                 # 预览缩略图（JPEG 512x512，<500KB）
```

#### 3.5.2 资产链接机制

在 Markdown 事实文件中，使用 `asset://` URI 引用资产：

```markdown
# 角色：艾莉丝
- 年龄: 24岁
- 外貌: 银发紫瞳，身材纤细
- 参考图: ![正面照](asset://faces/alice_v3.png)
- 声音样本: [温柔音色](asset://audio/alice_gentle.wav)
- 概念演变: [v1初稿](asset://faces/alice_v1.png) → [v2修正](asset://faces/alice_v2.png) → [v3定稿](asset://faces/alice_v3.png)
```

OpenClaw 渲染 Markdown 时，自动将 `asset://` 解析为本地文件路径或 base64 内联（供前端展示）。

#### 3.5.3 资产元数据索引

每个资产伴随 `.meta.json`：

```json
{
  "asset_id": "alice_v3",
  "type": "face_reference",
  "format": "png",
  "dimensions": [1024, 1024],
  "linked_entities": ["character:alice"],
  "generation_params": {
    "model": "SDXL",
    "seed": 42,
    "prompt": "1girl, silver hair, purple eyes, slender build, character concept art",
    "negative_prompt": "nsfw, lowres, bad anatomy",
    "workflow": "character_concept_v2"
  },
  "version": 3,
  "parent": "alice_v2",
  "created_at": "2026-05-02T14:30:00Z",
  "file_size": 2097152,
  "checksum": "sha256:abc123..."
}
```

#### 3.5.4 版本链管理

角色面部迭代时，自动维护版本链：
```
alice_v1 (初稿) ──► alice_v2 (修正瞳色) ──► alice_v3 (定稿)
     │                    │                    │
     ▼                    ▼                    ▼
   备注: "太成熟"       备注: "紫色不够深"     备注: "通过"
```

`asset_manager.py` 自动备份旧版本，最新版本通过 `alice.latest` 软链接指向。

---

### 3.6 模块 F：WSL2 专属避坑与初始化

**位置**：`init.sh` + `bridge/wsl2_gateway.sh` + `docs/wsl2_deployment.md`
**解决的问题**：WSL2 的 NTFS 跨界挂载、Hyper-V 虚拟交换机冲突、Windows 访问 WSL2 内服务三大陷阱。

#### 3.6.1 初始化脚本核心检查

```bash
# 绝对禁止 NTFS 跨界挂载
df -T "$TRIAD_ROOT" | awk 'NR==2 {print $2}' | grep -qE 'ext4|btrfs' \
  || fatal_exit "triad-memory 必须在 ext4/btrfs 上" \
     "TRIAD_ROOT=$HOME/.triad (不可在 /mnt/ 下)"

# 如果在 /mnt/ 下立即 fatal
case "$TRIAD_ROOT" in /mnt/*)
  fatal_exit "检测到 NTFS 跨界挂载" "将 TRIAD_ROOT 设为 $HOME/.triad"
;; esac
```

#### 3.6.2 WSL2 网关路由

Windows 浏览器无法直接访问 WSL2 内的 Docker 服务。解决方案：

```powershell
# wsl2_gateway.sh 自动执行的 Windows 侧命令（通过 powershell.exe）
netsh interface portproxy add v4tov4 listenport=8080 listenaddress=127.0.0.1 connectport=8080 connectaddress=$WSL2_IP
New-NetFirewallRule -DisplayName "Triad-Gateway" -Direction Inbound -LocalPort 8080 -Protocol TCP -Action Allow
```

用户只需要在 Windows 浏览器打开 `http://localhost:8080`，流量自动转发到 WSL2 内的 OpenClaw Gateway。

---

### 3.7 模块 G：Triad Control Panel Web UI（扣子风格）

**位置**：`webui/`（React + TypeScript + Tailwind + Vite）
**解决的问题**：v1.2-v2.0 的所有交互都通过命令行或聊天渠道（微信/Slack），缺乏一个统一的控制面板来可视化 Agent 集群状态、VRAM 分时复用、模型路由决策和多模态生成进度。

#### 3.7.1 界面布局（扣子 Coze 风格）

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  🟣 Triad Control Panel                                    [🔴 系统状态] [⚙️] │
├──────────────────┬─────────────────────────────────────────────────────────────┤
│                  │                                                             │
│  💬 对话面板      │  🎛️ Agent 集群 / 工作流画布                                │
│                  │                                                             │
│  ┌────────────┐  │  ┌─────────────────────────────────────────────────────┐   │
│  │ 历史对话    │  │  │  模型路由决策可视化                                    │   │
│  │ - 任务 #207 │  │  │  ┌─────┐    ┌─────┐    ┌─────┐    ┌─────┐       │   │
│  │ - 任务 #208 │  │  │  │Grok │───►│Deep │───►│Kimi │───►│Claude│       │   │
│  │ - 任务 #209 │  │  │  │脑洞 │    │推演 │    │描写 │    │审查 │       │   │
│  │             │  │  │  └─────┘    └─────┘    └─────┘    └─────┘       │   │
│  └────────────┘  │  └─────────────────────────────────────────────────────┘   │
│                  │                                                             │
│  ┌────────────┐  │  ┌─────────────────────────────────────────────────────┐   │
│  │ 当前任务    │  │  │  📊 VRAM 状态机可视化（实时）                         │   │
│  │  #210      │  │  │                                                     │   │
│  │  已接管... │  │  │  [2GB Embed][████████████][9GB LLM GPU]           │   │
│  │  分析中... │  │  │  [░░░░░░░░░░░░░░][9GB 空闲][2GB 系统]              │   │
│  │  读取代码  │  │  │                                                     │   │
│  │  [预览图]  │  │  │  状态: 🟢 IDLE  |  模式: llama-server GPU (-ngl 99)  │   │
│  └────────────┘  │  └─────────────────────────────────────────────────────┘   │
│                  │                                                             │
│  ┌────────────┐  │  ┌─────────────────────────────────────────────────────┐   │
│  │ 输入框      │  │  │  🛠️ 配置面板（Tab 切换）                              │   │
│  │ [帮我设计赛  │  │  │  [模型配置] [VRAM调度] [技能市场] [审计日志]            │   │
│  │  博朋克女...│  │  │                                                     │   │
│  │ [发送]     │  │  │  模型: llama-server  ✅                               │   │
│  └────────────┘  │  │  显存: 9GB / 20GB (45%)                               │   │
│                  │  │  路由策略: AUTO                                       │   │
│                  │  │  技能数: 47                                           │   │
│                  │  └─────────────────────────────────────────────────────┘   │
│                  │                                                             │
└──────────────────┴─────────────────────────────────────────────────────────────┘
│  📡 WebSocket: 🟢 已连接  |  📦 ClawPod: 3 运行中  |  🧠 VRAM: 11GB/22GB       │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### 3.7.2 核心功能模块

**左侧：对话面板（ChatPanel）**
- 消息列表：滚动加载，Markdown 渲染（代码高亮）
- 流式输出：WebSocket 实时接收 `StatusUpdate`，显示阶段性状态
- 多模态渲染：文本 / ImagePreview（每 5 步预览缩略图）/ VideoFrame / asset:// URI 角色卡片
- 输入框：自然语言输入 + 策略选择器（AUTO/CREATIVE/REASONING/LONGFORM/REVIEW）+ 附件上传

**右侧：Agent 集群画布（AgentCanvas）**
- 模型路由可视化：Grok/Kimi/DeepSeek/Gemini/Claude/Qwen 六节点图，当前活跃节点脉动高亮
- 工作流时间线：横向时间轴，标注每阶段耗时、模型、输出 token 数
- 失败重试链可视化：FAILED_ONCE → 策略调整 → 重试

**右侧：VRAM 状态机可视化（VRAMPanel）**
- 显存条：22GB 实时可视化（2GB Embed 蓝 / 9GB LLM GPU 绿 / 9GB 空闲灰 / 2GB 系统深灰）
- 状态指示灯：🟢 IDLE（-ngl 99）/ 🟡 CPU_FALLBACK（-ngl 0）/ 🔵 RENDERING / 🟣 RECOVERING
- 实时指标：显存占用百分比、LLM tok/s、ComfyUI 渲染步数

**右侧：配置面板（ConfigPanel，4 个 Tab）**
- **模型配置**：6 厂商 API key、llama-server 路径/-ngl/-t/ctx_size、健康检查、降级链配置
- **VRAM 调度**：显存分区拖动条、自动/手动策略、强制切换按钮、历史记录
- **技能市场（ClawHub）**：NovelSkill/CodeSkill 列表、启用/禁用、手动固化
- **审计日志**：任务时间线、过滤导出 CSV

#### 3.7.3 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 框架 | React 19 + TypeScript | 函数组件 + Hooks |
| 样式 | Tailwind CSS + shadcn/ui | 与 OpenClaw 宿主层一致 |
| 构建 | Vite | 快速 HMR，生产优化 |
| 可视化 | @xyflow/react (ReactFlow) | Agent 节点图 + 工作流画布 |
| 通信 | WebSocket (原生) | 连接 OpenClaw Gateway `/ws/tasks` |
| 渲染 | react-markdown + remark-gfm | Markdown 消息 + 代码高亮 |

#### 3.7.4 WebSocket 消息协议（前端视角）

```typescript
interface TaskStreamMessage {
  taskId: string;
  stage: 'ANALYZING' | 'READING_CODE' | 'EDITING' | 'TESTING' | 'COMPLETED' | 'FAILED';
  message: string;
  progress?: number;           // 0.0 - 1.0
  preview?: {
    type: 'text' | 'image' | 'video_frame';
    data: string;              // base64 或文本
    metadata?: Record<string, any>;
  };
  modelInfo?: {
    vendor: string;            // "grok", "deepseek", "kimi"...
    model: string;
    tokensIn: number;
    tokensOut: number;
  };
  vramInfo?: {
    state: 'IDLE' | 'CPU_FALLBACK' | 'RENDERING' | 'RECOVERING';
    embeddingMb: number;
    llmMb: number;
    comfyuiMb: number;
    freeMb: number;
  };
}
```

#### 3.7.5 部署方式

```bash
# 开发模式
cd triad/webui
npm install
npm run dev        # http://localhost:5173

# 生产构建
npm run build      # 输出 dist/ 目录
# 将 dist/ 挂载到 OpenClaw 的静态文件服务，或通过 nginx 反向代理
```

**WSL2 访问**：`wsl2_gateway.sh` 自动将 Windows 的 `8080` 端口转发到 WSL2 内的 OpenClaw Gateway，Web UI 作为 Gateway 的子路径 `/panel` 提供服务。

### 4.1 ACP v2 扩展（Agent Control Protocol）

v1.2 的 ACP 只传递文本状态。v2.0 需要传递模型路由决策和多模态预览。

```protobuf
// ACP v2 消息定义
message ACPMessage {
  string message_id = 1;
  string source = 2;
  string target = 3;
  
  oneof payload {
    TaskRequest    task    = 10;   // 扩展 model_pref + strategy + clawpod_id
    TaskResponse   result  = 11;   // 扩展实际使用的 vendor/model
    StatusUpdate   status  = 12;   // 扩展 image/video_frame 预览
    MemorySync     sync    = 13;   // 扩展资产同步
    SkillPublish   skill   = 14;   // 扩展 NovelSkill / CodeSkill 类型
    ModelSwitch    switch  = 15;   // ★ 新增：模型切换通知（上下文接力）
    Heartbeat      hb      = 16;
  }
}

// ★ 新增：模型切换通知
message ModelSwitch {
  string task_id = 1;
  string from_vendor = 2;       // "deepseek"
  string to_vendor = 3;         // "kimi"
  string relay_context = 4;     // 压缩后的接力上下文（KeyFacts JSON）
  int32  relay_tokens = 5;      // 接力上下文 token 数
}
```

### 4.2 MCP v2 扩展（Model Context Protocol）

v1.2 的 MCP 只连接 Claude Code。v2.0 需要同时连接 Claude Code + ComfyUI MCP Bridge。

```typescript
// MCP Client 多路复用
interface MCPClientRegistry {
  // 代码执行
  "claude-code": MCPClient;      // v1.2 已有
  
  // ★ 多模态执行
  "comfyui-bridge": MCPClient;   // 文生图 / 图生视频 / TTS
  
  // 动态发现：通过 claude-mcp-registry 查询可用服务
  discover(service_name: string): Promise<MCPClient>;
}

// ComfyUI 工具调用示例
interface ComfyUIToolCall {
  name: "generate_character_concept";
  args: {
    character_description: string;
    style_preset: "anime" | "realistic" | "fantasy";
    width: 1024;
    height: 1024;
    seed?: number;
    reference_face?: string;  // asset://faces/alice_v2.png
  };
}
```

---

## 五、数据流：小说推演 + 角色概念图生成 联合任务

```
[用户在 Slack 发送："帮我设计一个赛博朋克小说的女主角，要有概念图"]
           │
           ▼
┌─────────────────────┐
│  OpenClaw Gateway   │  ← 异步应答："任务已接管 (#207)，开始推演..."
│  创建 task_id=207   │
└──────────┬──────────┘
           │ ACP: TaskRequest(strategy=CREATIVE, model_pref={grok})
           ▼
┌─────────────────────┐
│  ModelRouter        │  ← 选择 Grok 进行破局设定
│  routing_decision   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Grok API           │  ← 生成角色设定：
│  (破局设定)         │     "艾莉丝，24岁，前企业黑客，银发紫瞳，
│                     │      左臂有神经接口疤痕，性格外冷内热..."
└──────────┬──────────┘
           │ ACP: ModelSwitch(grok → kimi)
           ▼
┌─────────────────────┐
│  ContextAligner     │  ← 提取 KeyFacts，压缩为接力 Prompt
│  relay_prompt       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Kimi API           │  ← 生成长文本背景故事（3000 字）
│  (长文铺垫)         │
└──────────┬──────────┘
           │ ACP: ModelSwitch(kimi → deepseek)
           ▼
┌─────────────────────┐
│  DeepSeek API       │  ← 检查角色行为逻辑一致性
│  (逻辑推演)         │     "验证：'前企业黑客'设定与'外冷内热'是否冲突？
│                     │      检查：她的技术能力是否能支撑后续剧情需求？"
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  NovelCurator       │  ← 四维评估：人设 9.0 / 逻辑 8.5 / 节奏 N/A / 伏笔 N/A
│  assessment         │  ← 通过阈值，进入概念图生成阶段
└──────────┬──────────┘
           │ MCP: tools/call → comfyui-bridge
           ▼
┌─────────────────────┐
│  VRAMScheduler      │  ← 申请 10GB 显存
│  state: IDLE→RENDERING│  ← 卸载本地 LLM，释放显存
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  ComfyUI MCP Bridge │  ← 调用 generate_character_concept
│  (Docker 容器内)     │  ← 通过 host.docker.internal:8188 访问宿主机 ComfyUI
│  Workflow: SDXL+LoRA│  ← 参数注入：
│                     │     prompt="1girl, silver hair, purple eyes, cyberpunk..."
│                     │     negative_prompt="nsfw, lowres..."
└──────────┬──────────┘
           │ HTTP API to host.docker.internal:8188
           ▼
┌─────────────────────┐
│  ComfyUI (宿主机)   │  ← SDXL 渲染执行
│  ~/.triad/apps/     │  ← 模型加载自 ~/.triad/models/comfyui/
│  python venv        │  ← 直接访问宿主机 GPU，无 Docker 开销
└──────────┬──────────┘
           │ WebSocket 实时进度 (宿主机 → MCP Bridge → Gateway)
           ▼
┌─────────────────────┐
│  OpenClaw Gateway   │  ← 每 5 步推送预览图到 Slack
│  StatusUpdate       │     "#207 概念图生成中：Step 15/50..."
│  (ImagePreview)     │     [预览图缩略图]
└─────────────────────┘
           │
           ▼
┌─────────────────────┐
│  ComfyUI 渲染完成   │  ← 输出：alice_concept_v1.png
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  VRAMScheduler      │  ← 释放显存，恢复本地 LLM
│  state: RENDERING→RECOVERING
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  AssetManager       │  ← 存储概念图到 assets/faces/alice_v1.png
│  生成 .meta.json    │  ← 记录生成参数、关联 character:alice
│  创建版本链         │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  OpenClaw Gateway   │  ← 推送最终结果到 Slack
│                     │     "✅ #207 完成
│                     │      角色设定：艾莉丝（24岁，前企业黑客）
│                     │      背景故事：[链接]
│                     │      概念图：[asset://faces/alice_v1.png]
│                     │      评估：人设 9.0 / 逻辑 8.5"
└─────────────────────┘
```

---

## 六、实施路线图（v2.0）

### Phase 0: 环境初始化（1 天）

```bash
# 一键初始化
bash init.sh
# 输出：.env + docker-compose.override.wsl2.yml + 硬件检测报告

# WSL2 网关绑定
bash bridge/wsl2_gateway.sh setup 8080 50051
```

### Phase 1: 核心基础设施（1 周）

1. **部署 OpenClaw + Hermes + Qdrant**
   ```bash
   docker compose -f docker-compose.hpc.yml up -d
   ```

2. **验证 ACP v2 通信**
   - OpenClaw 发 TaskRequest(model_pref={kimi}) → Hermes → ModelRouter 正确路由到 Kimi API
   - 验证 ModelSwitch 消息传递

3. **验证流式推送**
   - Hermes 上报 StatusUpdate(ImagePreview) → Gateway → 前端正确显示

### Phase 2: 模型路由 + 文学创作（1 周）

1. **接入 ModelRouter**
   - 配置 6 厂商 API key
   - 测试 7 种策略的自动推断准确率
   - 测试 FallbackChain（手动制造超时，验证降级）

2. **接入 NovelCurator**
   - 注册测试角色（含人设字段）
   - 生成测试章节，运行 4 维评估
   - 验证策略自动调整（连续低分触发注入）

3. **Skill 固化验证**
   - 同一类悬疑桥段执行 3 次
   - 验证第 4 次自动使用已固化的 NovelSkill

### Phase 3: 多模态执行层（1 周）

1. **安装 ComfyUI 到宿主机**
   ```bash
   bash scripts/install_comfyui.sh
   # 输出: ~/.triad/venvs/comfyui/ + ~/.triad/apps/comfyui/ + 自定义节点
   ```

2. **下载模型并启动 ComfyUI**
   ```bash
   # 放置模型到 ~/.triad/models/comfyui/checkpoints/
   source ~/.triad/venvs/comfyui/bin/activate
   cd ~/.triad/apps/comfyui
   python main.py --listen 0.0.0.0 --port 8188 --highvram
   ```

3. **验证 MCP Bridge 连接**
   - Bridge 启动时 health_check 验证 `host.docker.internal:8188` 可达
   - 测试工作流提交、WebSocket 进度回调、图像下载

4. **VRAM 调度验证**
   - 常态：LLM 常驻占用 9GB
   - 触发渲染：LLM 卸载 → ComfyUI 宿主机占用 20GB → 生成图像
   - 渲染完成：ComfyUI 释放 → LLM 恢复（计时，目标 <30 秒）

5. **资产存储验证**
   - 生成角色概念图 → 存入 assets/faces/
   - 验证 Markdown 中 `asset://` 正确解析
   - 验证版本链管理

### Phase 4: 全链路联合测试（1 周）

**测试场景**："设计一个赛博朋克女主角并生成概念图"
- 端到端耗时目标：<5 分钟
- 显存切换耗时目标：<30 秒
- 流式推送目标：每 5 步一张预览图

### Phase 5: 生产加固（持续）

- ComfyUI 模型下载缓存 CDN 优化
- 资产存储分卷（大容量 HDD 存视频，SSD 存活跃资产）
- 多用户 ClawPod 隔离
- 审计日志自动归档

---

## 七、风险与缓解（v2.0 新增）

| 风险 | 影响 | 缓解方案 | 状态 |
|------|------|---------|------|
| **6 厂商 API 同时失效** | **极高** | FallbackChain → 本地 Qwen 终极兜底 | v2.0 已设计 |
| **跨模型上下文传递丢失** | 高 | ContextAligner 关键信息提取 + 膨胀系数校准 | v2.0 已实现 |
| **ComfyUI VRAM 抢占导致 LLM 崩溃** | 高 | VRAMScheduler 状态机 + OOM Kill 自动重拉 | v2.0 已实现 |
| **角色概念图版本混乱** | 中 | AssetManager 版本链 + .meta.json 血缘追踪 | v2.0 已实现 |
| **视频生成 5 分钟无状态推送** | 中 | WebSocket 实时进度 + ImagePreview 每 5 步 | v2.0 已设计 |
| **WSL2 NTFS 挂载导致权限失效** | 高 | init.sh 强制 ext4 检测 + `/mnt/` 致命拦截 | v2.0 已实现 |
| **本地 LLM 与 ComfyUI 同时请求 GPU** | 高 | ComfyUI 宿主机原生 + VRAM 状态机分时复用 | v2.0 已设计 |
| **ComfyUI Docker 镜像臃肿** | 中 | 迁移到宿主机 venv，按需安装节点 | v2.0-native 已实施 |
| **ComfyUI 宿主机未启动** | 中 | MCP Bridge health_check + 清晰错误提示 | v2.0-native 已实施 |
| **Embedding 模型冷加载延迟** | 中 | 2GB VRAM 常驻 Embedding API，毫秒级响应 | v2.0 已配置 |

---

## 八、仓库结构（v2.0 完整版）

```
triad/
├── host/                    ← OpenClaw 宿主层（git submodule）
│   └── openclaw/
│       └── src/
│           ├── acp/         # ACP v2 协议实现（含 ModelSwitch / ImagePreview）
│           ├── gateway/
│           │   ├── streaming.ts      # WebSocket / SSE 流式推送
│           │   └── task-manager.ts   # 异步任务生命周期
│           └── mcp/         # MCP v2 客户端（多路复用）
│
├── mind/                    ← Hermes 认知层（git submodule）
│   └── hermes-agent/
│       └── agent/
│           ├── model_router.py      # ★ 内核级模型动态路由
│           ├── novel_curator.py     # ★ 文学创作版 Curator
│           ├── context_engine.py    # 上下文压缩 + 跨模型对齐
│           └── memory_manager.py    # 文本+向量+资产统一索引
│
├── hand/                    ← 多模态执行层
│   ├── claude/              # Claude Code MCP 连接（v1.2 已有）
│   ├── comfyui_mcp_bridge.py   # ★ ComfyUI MCP Server (连接宿主机 ComfyUI)
│   ├── workflow_templates/      # SDXL / ControlNet / SVD / InstantID JSON
│   ├── model_configs/         # 模型路径与触发条件
│   └── vram_scheduler.py   # ★ VRAM 分时复用调度器
│
├── bridge/                  ← 桥接基础设施
│   ├── docker-compose.hpc.yml      # ★ HPC 级 Docker 编排 (llama-server)
│   ├── docker-compose.yml          # 标准版编排
│   ├── init.sh                     # ★ WSL2 初始化脚本（严格权限检查）
│   ├── wsl2_gateway.sh             # ★ Windows 网关路由
│   └── model_router/               # 本地模型服务（llama-server 配置）
│
├── webui/                 # ★ 新增：扣子风格 Triad Control Panel
│   ├── src/
│   │   ├── components/
│   │   │   ├── ChatPanel/          # 左侧对话面板
│   │   │   ├── AgentCanvas/        # 右侧 Agent 集群画布
│   │   │   ├── VRAMPanel/          # 右侧 VRAM 可视化
│   │   │   └── ConfigPanel/        # 右侧配置面板
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts     # WebSocket 连接管理
│   │   │   └── useTaskStream.ts    # 任务流式数据消费
│   │   ├── services/
│   │   │   └── api.ts              # REST API 调用
│   │   ├── types/
│   │   │   └── index.ts            # TypeScript 类型定义
│   │   ├── App.tsx                 # 主应用组件
│   │   └── main.tsx                # 入口
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── index.html
│
├── memory/                  ← 统一记忆总线
│   ├── facts/               # Markdown 文本事实（含 asset:// 链接）
│   ├── skills/              # NovelSkill + CodeSkill
│   ├── episodes/            # 执行日志
│   ├── vectors/             # faiss 索引
│   ├── assets/              # ★ 二进制媒体资产库
│   │   ├── faces/
│   │   ├── scenes/
│   │   ├── videos/
│   │   ├── audio/
│   │   └── thumbs/
│   └── asset_manager.py     # ★ 多模态资产管理器
│
├── docs/
│   ├── triad_v2_architecture.md    # 本文档
│   ├── model_routing_protocol.md   # ACP v2 协议扩展
│   ├── novel_workflow.md           # 文学创作工作流
│   ├── multimodal_bus.md           # 多模态记忆总线
│   ├── hpc_scheduling.md           # HPC 调度与调优
│   ├── wsl2_deployment.md          # WSL2 部署手册
│   ├── comfyui_integration.md      # ComfyUI 集成指南
│   └── comfyui_native_deployment.md  # ★ ComfyUI 宿主机原生部署手册
│
└── tests/                   # 集成测试
    ├── test_model_router.py
    ├── test_vram_scheduler.py
    ├── test_asset_manager.py
    └── test_novel_workflow.py
```

---

## 九、性能基准与验收指标

| 指标 | 目标值 | 测试方法 |
|------|--------|---------|
| 模型路由决策延迟 | <100ms | 1000 次 TaskRequest 平均耗时 |
| 跨模型上下文传递损耗 | <15% | 相同任务链，传递前后的关键信息召回率 |
| 主模型→次选降级耗时 | <500ms | 手动触发超时，测量 FallbackChain 切换 |
| LLM unload → 渲染 | <10s | vram_scheduler 状态转换计时 |
| 渲染完成 → LLM 恢复 | <30s | llama-server 重启 -ngl 99，mmap 热映射后首个 token 返回时间 |
| 概念图生成（SDXL） | <120s | 1024x1024, 50 steps |
| 视频片段生成（SVD） | <300s | 1024x576, 25 frames |
| NovelCurator 评估 | <5s | 单章节 3000 字文本 |
| 技能固化触发准确率 | >90% | 人工标注 100 个任务，检查固化时机 |

---

*本文档基于 Triad v1.2 架构演进，整合四大模块生成：*
- *硬件极致调度编排（HPC Docker Compose + NUMA 感知）*
- *内核级模型动态路由（6 厂商自动切换 + 上下文对齐）*
- *文学创作工作流（NovelCurator 四维评估 + Skill 固化）*
- *多模态工作流引擎（ComfyUI MCP Bridge + VRAM 分时复用 + 资产管理）*

---

## 附：llama.cpp 迁移与 Web UI 新增说明

### llama.cpp 迁移（v2.0-llama）

**核心决策**：放弃 vLLM/Ollama 双服务架构，全面拥抱 llama.cpp (llama-server)。

| 维度 | vLLM/Ollama 版 | llama.cpp 版 |
|------|---------------|-------------|
| LLM 控制器 | `LLMSwapController` (HTTP `/unload`) | `LlamaCppProcessManager` (SIGTERM + `-ngl` 重启) |
| 状态机 | IDLE → RENDERING → RECOVERING | IDLE → **CPU_FALLBACK** → RENDERING → RECOVERING |
| LLM 可用性 | 卸载期间**完全中断** | CPU 降级运行，**不中断对话**（~5 tok/s） |
| 显存释放 | vLLM 池化显存难以完全释放 | 进程重启 `-ngl 0`，显存**瞬间释放到 ~2GB** |
| 模型格式 | AWQ/GPTQ（有限生态） | **GGUF 全生态覆盖** |
| 恢复时间 | 10-20 秒 warm_up() | **<2 秒** mmap 热映射 |
| CPU 利用率 | 卸载期间 CPU 闲置 | **48 线程硬扛推理**，绝不闲置 |

**关键代码文件**：
- `hand/vram_scheduler_llama.py` — 新版 VRAM 调度器（LlamaCppProcessManager + CPU_FALLBACK 状态）
- `docker-compose.hpc.yml` — llama-server 服务替换 vLLM + Ollama
- `docs/llama_migration.md` — 完整迁移手册

### Web UI 新增（Triad Control Panel）

**核心决策**：为 Triad 添加扣子（Coze）风格的 Web 控制面板，统一可视化 Agent 集群、VRAM 状态、模型路由和多模态生成进度。

**技术栈**：React 19 + TypeScript + Tailwind CSS + Vite + @xyflow/react + shadcn/ui

**核心功能**：
- 左侧：对话面板（Markdown 渲染 + ImagePreview 预览 + asset:// URI 角色卡片）
- 右侧：AgentCanvas（模型路由图 + 工作流时间线）、VRAMPanel（22GB 实时显存条）、ConfigPanel（模型配置 / VRAM 调度 / 技能市场 / 审计日志）

**关键代码文件**：
- `webui/src/App.tsx` — 主应用布局（左侧 + 右侧）
- `webui/src/components/ChatPanel/MessageList.tsx` — 消息列表
- `webui/src/components/AgentCanvas/ModelRouterGraph.tsx` — 模型路由可视化
- `webui/src/components/VRAMPanel/VRAMBar.tsx` — 显存实时条
- `webui/src/hooks/useWebSocket.ts` — WebSocket 连接管理

*所有代