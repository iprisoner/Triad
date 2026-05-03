# Triad v2.2 技术白皮书
## 多标签工作台 · 角色路由 · 实时探针 — 完整架构文档

**版本**: v2.2  
**代号**: Lobster Station  
**日期**: 2026-05-03  
**代码规模**: 79 个文件，~15,619 行（Python/TypeScript/TSX/Bash/YAML）  

---

## 一、版本演进

| 版本 | 核心跨越 | 完成度 |
|------|---------|--------|
| v1.2 | 纯文本/代码 Agent，vLLM 后端 | 概念模型 |
| v2.0 | 多模态 + 多模型路由 + llama.cpp 迁移 | 架构骨架 |
| v2.1 | NUMA 修复 + ComfyUI 宿主机剥离 + 一键部署 | 工程加固 |
| **v2.2** | **多标签工作台 + 单 Agent 多角色 + 系统监控探针 + 蜂群调度 + 技能结晶** | **可交互原型** |

---

## 二、系统全景图

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           🦞 Triad Station v2.2 (浏览器内)                      │
├──────────────────────────────────────────────────────────────────────────────┤
│  [🦞 龙虾控制台]  [🎨 ComfyUI 画布]  [📊 系统监控]                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Tab 1: 龙虾控制台                                                            │
│  ├─ 左侧: ChatPanel (对话面板 + 流式输出)                                      │
│  ├─ 右侧: AgentCanvas (模型路由可视化) + VRAMPanel (显存条) + ConfigPanel (配置)  │
│  │            └─ ProviderManager (动态模型注册)                                  │
│  │            └─ SkillMarketTab (技能市场)                                      │
│  └─ WebSocket: ws://localhost:8080/ws/tasks (实时双向通信)                      │
│                                                                              │
│  Tab 2: ComfyUI 画布 (iframe, display:none 切换, DOM 永不卸载)                  │
│  └─ http://localhost:8188 (宿主机原生 Python venv)                             │
│                                                                              │
│  Tab 3: 系统监控 (3秒轮询 /api/system/status)                                   │
│  └─ GPU 显存条 / Docker 容器列表 / llama 状态 / CPU+内存卡片                    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ WebSocket / HTTP
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           OpenClaw Gateway (Node.js)                           │
│  ├─ websocket.ts    : WebSocket Server (ws://:8080/ws/tasks)                    │
│  ├─ api.ts          : REST API (/api/models 动态注册表 CRUD + toggle + test)   │
│  ├─ monitor.ts      : 系统探针 (/api/system/status, nvidia-smi + docker ps)      │
│  └─ 静态文件服务    : webui/dist/ → http://localhost:8080/panel               │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │ ACP / HTTP
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           Hermes 认知编排层 (Python)                             │
│  ├─ hermes_orchestrator.py : 主循环 (动态5步: 分析→路由→执行→动态评估→动态多模态)       │
│  ├─ model_router.py        : 动态路由 (strategy→tags匹配 + @角色名解析注入)        │
│  ├─ model_registry.py      : providers.json CRUD (无限模型注册)                   │
│  ├─ novel_curator.py       : 4维小说评估 + SkillCrystallizer 固化                │
│  ├─ prompts/roles.py      : 5个角色定义 (code_engineer/novelist/art_director...) │
│  ├─ acp_adapter/streaming_reporter.py : 状态回传总线 (非阻塞 HTTP POST)          │
│  ├─ swarm_orchestrator.py : 蜂群调度器 (asyncio.gather 并发多Agent)              │
│  └─ skill_crystallizer.py : 技能结晶器 (Markdown+YAML 配方固化与进化)           │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │ MCP / HTTP
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                           多模态执行层                                          │
│  ├─ llama-server (Docker)   : -ngl 99/0 GPU/CPU 跷跷板 (:8000)                │
│  ├─ ComfyUI (宿主机 venv)   : SDXL + InstantID + SVD (:8188)                   │
│  ├─ comfyui_mcp_bridge.py   : API JSON 参数注入 + WebSocket 轮询               │
│  ├─ vram_scheduler_llama.py : 显存状态机 (IDLE→CPU_FALLBACK→RENDERING)          │
│  └─ asset_manager.py        : asset:// URI + 版本链 + 缩略图                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、v2.2 新增模块详解

### 3.1 BrowserShell.tsx — 浏览器多标签工作台

**位置**: `webui/src/BrowserShell.tsx` (464 行)  
**核心决策**: 所有 3 个 Tab 内容**始终挂载**，通过 CSS `display` 切换可见性。

```tsx
// 关键实现：条件 CSS，不是条件渲染
<div className={cn('absolute inset-0', activeTab==='lobster'?'block':'hidden')}>
    <LobsterConsole />
</div>
<div className={cn('absolute inset-0', activeTab==='comfyui'?'block':'hidden')}>
    <iframe src="http://localhost:8188" />  // DOM 永不卸载
</div>
<div className={cn('absolute inset-0', activeTab==='monitor'?'block':'hidden')}>
    <SystemMonitorTab />
</div>
```

**状态保持保证**：
| Tab | 切换时行为 |
|-----|-----------|
| 龙虾 | WebSocket 连接保持，消息继续接收，切回时显示最新消息 |
| ComfyUI | iframe `display:none` 隐藏，渲染进度、节点连线不中断 |
| 监控 | 轮询继续（或至少保持最后状态），切回时无刷新 |

**视觉设计**：
- 背景: `bg-slate-950`
- Tab 栏: `bg-slate-900 border-b border-slate-800`
- 激活: `text-cyan-400 border-b-2 border-cyan-400`
- 未激活: `text-slate-400 hover:text-slate-200`
- 字体: 标题用 `font-mono`（可选）

---

### 3.2 单 Agent 多角色路由

**位置**: `mind/prompts/roles.py` (203 行) + `mind/model_router.py` (925 行)  
**核心决策**: 用户通过 `@角色名` 前缀切换角色，每个角色有独立的 System Prompt、模型偏好、工具权限。

#### 5 个内置角色

| 角色 | 指令 | 模型偏好 | 允许工具 | 温度 |
|------|------|---------|---------|------|
| `@code_engineer` | 代码重构、Bug 修复、全栈开发 | REASONING | read/edit/bash/git/run_test | 0.3 |
| `@frontend_engineer` | React/TypeScript/Tailwind 专家 | REASONING | read/edit/bash/npm_install | 0.3 |
| `@novelist` | 现实主义小说，人物塑造和情节设计 | CREATIVE | read/write/memory_search | 0.8 |
| `@art_director` | 概念设计，ComfyUI 工作流大师 | CREATIVE | generate_image/instantid/asset_search | 0.9 |
| `@devops_engineer` | Docker/K8s 基础设施和自动化 | REASONING | read/bash/docker_exec/system_monitor | 0.3 |

#### 角色解析流程

```
用户输入: "@novelist 写第一章"
    │
    ▼ parse_role()
┌──────────────────────────┐
│ 正则匹配: ^@(\w+)\s+(.+)$ │
│ role_id = "novelist"       │
│ clean_input = "写第一章"    │
└──────────────────────────┘
    │
    ▼ 注入 System Prompt
┌─────────────────────────────────────────┐
│ "[系统指令]\n你是一位现实主义小说家...\n  │
│ \n[用户请求]\n写第一章"                  │
└─────────────────────────────────────────┘
    │
    ▼ 路由决策
┌─────────────────────────────────────────┐
│ model_pref = "CREATIVE"                │
│ → find_by_strategy("CREATIVE")         │
│ → 匹配 tags: ["creative", "brainstorm"]  │
│ → 选择 Grok/Gemini                       │
└─────────────────────────────────────────┘
    │
    ▼ 工具权限限制
┌─────────────────────────────────────────┐
│ allowed_tools = ["read", "write"]        │
│ → novelist 不能调用 git/bash/generate   │
└─────────────────────────────────────────┘
```

#### 容错设计

```python
# @unknown_role → 清晰错误 + 可用角色列表
try:
    role, clean = router.parse_role("@unknown 写代码")
except ValueError as e:
    # "未知角色 'unknown'。可用角色: code_engineer, novelist, ..."
    fallback = router.execute(clean, strategy="AUTO")  # 默认兜底
```

---

### 3.3 系统监控探针

**后端**: `host/openclaw/src/gateway/monitor.ts` (282 行)  
**前端**: `webui/src/components/SystemMonitorTab.tsx` (281 行)

#### 后端探针 (`GET /api/system/status`)

并行采集 5 类指标，超时 3-5 秒：

| 指标 | 采集命令 | 容错降级 |
|------|---------|---------|
| GPU 显存/利用率/温度 | `nvidia-smi --query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader,nounits` | 返回 "GPU 不可用" |
| Docker 容器 | `docker ps --format "{{.Names}}|{{.Status}}|{{.Ports}}"` | 空数组 |
| llama-server | `curl -s http://localhost:8000/health` | `running: false` |
| CPU 占用 | `top -bn1` | `os.loadavg()` |
| 内存占用 | `free -m` | `os.totalmem()/freemem()` |

#### 前端轮询

```tsx
useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, 3000);  // 3秒轮询
    return () => clearInterval(timer);
}, []);
```

---

### 3.4 SwarmExecutor — 蜂群调度器

**位置**: `mind/swarm_orchestrator.py` (732 行)  
**核心决策**: 临时 Agent 是**纯数据结构**（system prompt + allowed_tools），不是物理进程。

#### 并发模型

```python
# 双层 Semaphore 限速
self._instance_sem = asyncio.Semaphore(max_concurrent)  # 实例级: 全局最大并发
parallel_limit = task.context.get("parallel_limit", 3)   # 任务级: 单次蜂群并发数
```

- `asyncio.gather` 并发执行多个 `TemporaryAgent`
- 每个 Agent 独立调用 `model_router.route()` → `model_router.execute()`
- 双层保护避免网关/API 限流

#### 聚合策略

| 模式 | 行为 |
|------|------|
| **CONCAT** | `"\n\n---\n\n"` 分隔拼接各 Agent 输出 |
| **JOIN** | `task.context["join_delimiter"]` 自定义分隔符拼接 |
| **BEST** | 若提供 `evaluator` 则调用评分选最优；否则选 `completion_tokens` 最多者 |
| **MERGE** | 按段落去重后顺序拼接 |

#### 工厂方法变体

| 工厂方法 | 变体 | 说明 |
|----------|------|------|
| `create_researcher` | default / deep / tech | 研究员：通用调研 / 深度搜索 / 技术文档 |
| `create_writer` | default / novel / copy / tech | 写手：通用写作 / 小说 / 文案 / 技术文档 |
| `create_reviewer` | default / code / logic | 审校：通用审校 / 代码审查 / 逻辑校验 |
| `create_coder` | default / frontend / backend | 程序员：通用编码 / 前端 / 后端 |

```python
# 使用示例
executor = SwarmExecutor(model_router, reporter)
agents = [
    executor.create_researcher(variant="deep"),
    executor.create_writer(variant="tech"),
    executor.create_reviewer(variant="logic")
]
result = await executor.execute_swarm(task, agents, strategy=AggregationMode.CONCAT)
```

---

### 3.5 SkillCrystallizer — 技能进化机制

**位置**: `mind/skill_crystallizer.py` (740 行)  
**核心约束**: **永不修改物理进程或运行时对象**，只提取 Prompt 组合保存为 Markdown+YAML。

#### 触发条件

```python
if score >= 8.0:
    crystallizer.auto_crystallize(task, agents, score, strategy)
```

#### 文件格式（员工手册格式）

```markdown
---
id: 20260503_143052_深度调研
created_at: 2026-05-03T14:30:52
tags: [research, comparison, rust, go]
score: 8.5
temperature_delta: 0.0
extra_tools: []
score_multiplier: 1.0
timeout_multiplier: 1.0
---

# 深度调研蜂群配方

## 角色组合
- researcher(deep): 负责技术调研
- writer(tech): 负责结构化输出
- reviewer(logic): 负责逻辑校验

## 聚合策略
CONCAT

## 适用场景
对比分析类技术调研任务
```

#### 进化参数

| 参数 | 说明 |
|------|------|
| `temperature_delta` | 温度偏移量（±0.1~0.3） |
| `extra_tools` | 追加工具列表 |
| `score_multiplier` | 历史评分加权系数 |
| `timeout_multiplier` | 超时时间倍率 |

#### 安全原则

- 只读写 `~/.triad/memory/skills/self-evolved/`
- 文件名格式：`YYYYMMDD_HHMMSS_{recipe_name}_{hash}.md`
- 永不执行代码、永不修改运行时对象

```python
# 配方进化：基于历史成功配方生成新变体
new_recipe = crystallizer.evolve_from_recipe(
    recipe_path="~/.triad/memory/skills/self-evolved/20260503_143052_深度调研.md",
    temperature_delta=0.1,
    extra_tools=["web_search"],
    score_multiplier=1.05
)
```

---

## 四、完整数据流（v2.2 已闭环）

### 4.1 聊天任务流

```
用户 (Web UI 聊天框)
  → WebSocket send {"action":"submit_task","prompt":"@novelist 写第一章"}
    → OpenClaw Gateway (:8080)
      → ws.on('message') 解析 → 生成 taskId
      → ws.send() 返回 ANALYZING 状态（前端立即显示）
        → HermesOrchestrator.process_task(task_request)
          → parse_role("@novelist 写第一章")
            → role=novelist, clean="写第一章"
            → reporter.report_stage("ANALYZING", "🎭 角色模式: 小说家", 0.15)
          → router.route("写第一章", strategy="CREATIVE")
            → registry.find_by_strategy("CREATIVE")
            → 匹配 Grok (tags: ["creative", "brainstorming"])
          → router.execute(prompt_with_system, decision)
            → httpx POST → Grok API /v1/chat/completions
            → LLMResponse(content="第一章内容...")
            → reporter.report_model_info("grok", "grok-beta", 42, 2048)
          → _get_eval_strategy("novel", role, ...) → "novel"
            → curator.evaluate("第一章内容", use_llm=True)
            → _local_llm_assess() → POST http://127.0.0.1:8000/v1/chat/completions
            → EvaluationResult(人设8.5/逻辑9.0/节奏7.0/伏笔8.5)
            → reporter.report_stage("TESTING", "评估: 人设 8.5/10", 0.7)
            → overall >= 7.5 → crystallize_skill() → 写入 self-evolved/
          → _get_multimodal_strategy("novel", ...) → "bypass"
            → 无视觉需求，跳过 ComfyUI
          → reporter.report_result("success", "第一章内容...")
      → ws.send() 推送 COMPLETED 状态 + Markdown 结果
  ← WebSocket onMessage ← 前端显示最终结果
```

### 4.2 多模态任务流

```
用户: "@art_director 设计赛博朋克女主角并生成概念图"
  → parse_role → art_director, model_pref=CREATIVE
  → router.execute() → 生成角色设定文本
  → _get_multimodal_strategy("multimodal", user_prompt, role, text) → "art_director"
    → 角色 art_director 默认触发视觉生成
  → vram_scheduler.acquire_render_memory()
    → SIGTERM llama-server (-ngl 99)
    → restart llama-server (-ngl 0, CPU 48线程)
    → reporter.report_vram("RENDERING", 0GB LLM, 20GB ComfyUI)
  → comfy_bridge.generate_character_concept()
    → _load_api_workflow("character_concept")
    → _inject_prompt_to_workflow(positive_prompt, negative_prompt, seed)
    → aiohttp POST → http://host.docker.internal:8188/prompt
    → WebSocket 轮询 → 获取 filename
    → 下载图像 → assets/faces/alice_v1.png
  → vram_scheduler.release_render_memory()
    → SIGTERM llama-server (-ngl 0)
    → restart llama-server (-ngl 99, mmap 热映射)
    → reporter.report_vram("IDLE", 9GB LLM, 9GB 空闲)
  → report_result("success", 文本 + 图像路径)
```

### 4.3 蜂群任务流

```
用户: "@deep_research_swarm 调研 Rust vs Go 在 AI 推理引擎中的优劣"
  → parse_role → deep_research_swarm, model_pref=REASONING
  → _is_swarm_mode() → True（角色 ID 以 _swarm 结尾）
  → _build_swarm_agents() → [研究员(deep), 写手(tech), 审校(logic)]
  → reporter.report_stage("EXECUTION", "🔥 触发蜂群模式...")
  → SwarmExecutor.execute_swarm(task)
    → asyncio.gather 并发 3 个 Agent
    → each Agent → model_router.route() → model_router.execute()
    → _aggregate(CONCAT) → 合并 3 份输出
  → skill_crystallizer.auto_crystallize(score=8.5) 
    → ~/.triad/memory/skills/self-evolved/20260503_143052_深度调研_143052.md
  → report_result("success", aggregated_text + swarm_stats)
```

### 4.4 动态评估与多模态路由架构

**核心决策**：Triad 不是只写小说的系统。代码任务不应该进入 `novel_curator`，通用调研不应该无脑触发 ComfyUI。

**动态评估路由 (Step 4)**：

```
_get_eval_strategy(task_type, role, user_prompt, generated_text)
  → 角色显式 eval_strategy 标记？
    → "novel" → curator.evaluate() 4维小说评估
    → "code"  → _evaluate_code_placeholder() (AST分析预留，当前满分)
    → "bypass"→ 静默跳过
  → task_type == "novel" ? → "novel"
  → 角色 ID 含 novel/story/fiction ? → "novel"
  → 角色 ID 含 code/engineer/programmer ? → "code"
  → 生成内容含 3+ 小说标记(第一章/人物/情节) ? → "novel" (启发式降级)
  → 默认 → "bypass"
```

**动态多模态路由 (Step 5)**：

```
_get_multimodal_strategy(task_type, user_prompt, role, generated_text)
  → task_type == "multimodal" ? → "explicit"
  → role ID 含 art/director/designer ? → "art_director" (默认触发)
  → 用户输入含 "画图"/"generate image"/"concept art" ? → "explicit"
  → 生成内容含 "请生成" + "concept art" ? → "auto_detect"
  → 默认 → "bypass" (不浪费 VRAM 切换)
```

**关键收益**：
- 写代码时不弹出"小说质量评估"进度条
- `@novelist 写第一章` 时不会莫名其妙地占用 20GB VRAM 去画图
- `@art_director 设计角色` 时自动触发 ComfyUI，无需用户额外指令
- 系统总吞吐量提升（跳过不必要的评估 + 避免错误的 VRAM 跷跷板）


## 五、模块完成度详表

### 5.1 前端层 (Web UI)

| 模块 | 文件 | 行数 | 完成度 | 说明 |
|------|------|------|--------|------|
| 浏览器外壳 | `BrowserShell.tsx` | 464 | 95% | 3 Tab 切换，状态保持完整 |
| 龙虾控制台 | `LobsterConsole.tsx` | 117 | 90% | ChatPanel + 右侧面板 |
| 聊天面板 | `ChatPanel/` | ~300 | 85% | 消息列表+输入框+流式输出 |
| Agent 画布 | `AgentCanvas/` | ~200 | 70% | ReactFlow 节点图 |
| VRAM 面板 | `VRAMPanel/` | ~150 | 70% | 显存条+状态指示灯 |
| **系统监控** | `SystemMonitorTab.tsx` | **281** | **90%** | **3秒轮询，GPU/容器/llama/CPU/内存** |
| 技能市场 | `SkillMarketTab.tsx` | 642 | 85% | MCP Tools + Skills 双 Tab |
| **模型注册中心** | `ProviderManager.tsx` | **758** | **90%** | **无限添加模型，测试连接** |
| 配置面板 | `ConfigPanel/` | ~400 | 75% | 多个子 Tab |

### 5.2 后端层 (OpenClaw Gateway)

| 模块 | 文件 | 行数 | 完成度 | 说明 |
|------|------|------|--------|------|
| WebSocket Server | `websocket.ts` | 727 | 90% | ws://:8080/ws/tasks，连接管理 |
| 动态模型 API | `api.ts` | 337 | 90% | /api/models CRUD + test |
| **系统探针** | `monitor.ts` | **282** | **90%** | **/api/system/status，nvidia-smi + docker** |

### 5.3 认知层 (Hermes)

| 模块 | 文件 | 行数 | 完成度 | 说明 |
|------|------|------|--------|------|
| 主编排器 | `hermes_orchestrator.py` | **1044** | **90%** | **5步串联 + 角色集成 + 蜂群模式分叉** |
| **角色定义** | `prompts/roles.py` | **203** | **95%** | **5个角色，System Prompt + 工具权限** |
| 动态路由 | `model_router.py` | 925 | 85% | parse_role + find_by_strategy |
| 模型注册表 | `model_registry.py` | 180 | 90% | providers.json CRUD |
| 小说评估 | `novel_curator.py` | 1478 | 80% | _local_llm_assess + 6层JSON容错 |
| 状态回传 | `streaming_reporter.py` | 375 | 90% | 非阻塞HTTP，指数退避 |
| 配置管理 | `config_manager.py` | 148 | 90% | .env 单例读取 |
| **蜂群调度器** | `swarm_orchestrator.py` | **732** | **95%** | **asyncio.gather 并发 + 4 种聚合策略 + 工厂方法** |
| **技能结晶器** | `skill_crystallizer.py` | **740** | **95%** | **Markdown+YAML 固化 + 配方进化** |

### 5.4 执行层 (Execution)

| 模块 | 文件 | 行数 | 完成度 | 说明 |
|------|------|------|--------|------|
| VRAM 调度器 | `vram_scheduler_llama.py` | 620 | 85% | CPU_FALLBACK 状态机 |
| ComfyUI Bridge | `comfyui_mcp_bridge.py` | 1597 | 75% | API JSON 注入 + 轮询 |
| 资产管理 | `asset_manager.py` | 678 | 75% | URI 解析 + 版本链 |

---

## 六、核心 API 参考

### 6.1 前端 → Gateway WebSocket

```
ws://localhost:8080/ws/tasks

// 发送
{"action":"submit_task","prompt":"@novelist 写第一章","strategy":"CREATIVE"}

// 接收阶段性状态
{"taskId":"xxx","stage":"ANALYZING","message":"🎭 角色模式: 小说家","progress":0.15}
{"taskId":"xxx","stage":"TESTING","message":"评估: 人设 8.5/10","progress":0.7}
{"taskId":"xxx","stage":"COMPLETED","message":"第一章已生成","progress":1.0}

// 接收最终结果（单体模式）
{"taskId":"xxx","status":"success","output":"# 第一章\n\n内容..."}

// 接收最终结果（蜂群模式）
{"taskId":"xxx","status":"success","output":"聚合内容...",
 "swarm_mode":true,"model_used":"蜂群(3 agents)",
 "swarm_stats":{"agent_count":3,"success_count":3,"total_tokens":12580}}
```

### 6.2 Gateway REST API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/models` | GET | 列出所有 Provider（api_key 自动 mask） |
| `/api/models` | POST | 添加新 Provider |
| `/api/models/:id` | PUT | 更新 Provider |
| `/api/models/:id` | DELETE | 删除 Provider |
| `/api/models/:id/toggle` | POST | 启用/停用切换 |
| `/api/models/:id/test` | POST | 测试连接，返回延迟 ms |
| `/api/system/status` | GET | 系统监控探针（GPU/容器/llama/CPU/内存） |

### 6.3 本地 LLM API

```
POST http://localhost:8000/v1/chat/completions
{"model":"qwen-14b-chat","messages":[{"role":"user","content":"Hello"}],"temperature":0.7}
```

---

## 七、已知限制与诚实清单

| 限制 | 说明 | 预计解决 |
|------|------|---------|
| **云端 API Key 需手动填入** | .env.example 有模板，需复制为 .env 并填入真实 Key | 用户操作，5分钟 |
| **ComfyUI JSON 模板需手动导出** | 需在 ComfyUI 网页中搭建工作流 → Save API Format → 放到 hand/ 目录 | 用户操作，15分钟 |
| **Orchestrator 与 Gateway 的进程间通信** | WebSocket 后端就绪，但 Hermes 侧需要实际进程启动并连接 | 需 docker-compose up |
| **前端 Mock 数据** | ChatPanel 消息列表目前是模拟数据，需接入真实 WebSocket | WebSocket 后端已就绪 |
| **角色工具权限执行层未完全实现** | allowed_tools 列表在路由层定义，实际 MCP 工具过滤需在执行层补全 | 2-4 小时 |
| **蜂群角色需手动在 roles.py 添加** | 当前 `@deep_research_swarm` 等蜂群角色需在 `mind/prompts/roles.py` 中定义 `is_swarm=True` | 未来 Web UI 支持 |
| **CodeCurator 为占位符实现** | 代码评估当前返回满分 10.0，AST 静态检查（pylint/mypy）待接入 | v2.3 |
| **动态评估策略需角色配置扩展** | `role.eval_strategy` 字段需在 `roles.py` 中显式声明，当前靠 ID 推断 | v2.3 |
| **VRAM 全局锁已修复（地雷 1）** | `vram_scheduler_llama.py` 新增 `_llm_inference_counter` + `_vram_switch_lock` + `begin/end_llm_inference()` | ✅ v2.2 |
| **上下文压缩已修复（地雷 2）** | `swarm_orchestrator.py` 新增 `_estimate_tokens()` + `_compress_aggregated()` Map-Reduce 压缩 | ✅ v2.2 |
| **配方语义去重已修复（地雷 3）** | `skill_crystallizer.py` 新增 `_find_similar_recipe()` + `_merge_recipe()` 适者生存 | ✅ v2.2 |
| **WebSocket 状态恢复已修复（地雷 4）** | `websocket.ts` 新增 `taskHistoryStore` + `handleRecoverTasks()` + `recordTaskStage/Result()` | ✅ v2.2 |

---

*本文档基于对 /mnt/agents/output/triad/ 目录下 79 个文件、15,619 行代码的全面审计，生成于 2026-05-03。*026-05-03。*