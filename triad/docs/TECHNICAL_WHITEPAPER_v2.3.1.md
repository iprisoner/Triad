# Triad v2.3.1 技术白皮书
## 纯文本认知编排 · 本地 LLM 推理 · 精简架构文档
## 多标签工作台 · 角色路由 · 实时探针 — 完整架构文档

**版本**: v2.3.1  
**代号**: Lobster Station (Security Patch)  
**日期**: 2026-05-06  
**代码规模**: 79 个文件，~16,500 行（Python/TypeScript/TSX/Bash/YAML）  

---

## 一、版本演进

| 版本 | 核心跨越 | 完成度 |
|------|---------|--------|
| v1.2 | 纯文本/代码 Agent，vLLM 后端 | 概念模型 |
| v2.0 | 多模态 + 多模型路由 + llama.cpp 迁移 | 架构骨架 |
| v2.1 | NUMA 修复 + ComfyUI 宿主机剥离 + 一键部署 | 工程加固 (ComfyUI 已于 v2.3 移除) |
| v2.3 | 多标签工作台 + 单 Agent 多角色 + 系统监控探针 + 蜂群调度 + 技能结晶 | 可交互原型 |
| **v2.3.1** | **安全加固 + 熔断器重构 + 并发安全修复 + 基础设施硬化** | **生产安全补丁** |

---

## 二、v2.3.1 安全与稳定性补丁（重点）

### 2.1 致命 Bug 修复（P0）

| 模块 | 修复内容 | 影响 |
|------|---------|------|
| **mind/novel_curator.py** | 移除不存在的 `ModelVendor` 导入；改为相对导入 + fallback | 模块可正常导入 |
| **mind/swarm_orchestrator.py** | 修复 `IndentationError`（`try` 未缩进至 `if` 下）；添加缺失的 `import re`；移除重复赋值 | 蜂群模式可用 |
| **mind/skill_crystallizer.py** | `load_recipe` 从 `meta` 读取 `model_pref` 而非错误的 `aggregation_mode` | 配方反序列化正确 |
| **hand/vram_scheduler_llama.py** | `acquire_render_memory` 接受 `Union[str, RenderTask]`；修复 `__aexit__` 双计统计；修复 `Condition+Lock` 不可重入死锁 | VRAM 调度安全 |
| **hand/vram_scheduler.py** | 补充缺失的 `begin/end_llm_inference`；修复 `__aexit__` 统计 bug | LLM 引用计数闭环 |
| **mind/model_router.py** | `execute` 签名检测改用 `inspect.signature`；VRAM 超时后抛异常而非继续执行 | 参数安全、显存安全 |
| **host/openclaw/src/gateway/websocket.ts** | `removeConnectionBySocket` 清理所有 `taskId`；`push_result` 接受空字符串 | 内存泄漏修复 |

### 2.2 安全漏洞修复（P1）

| 模块 | 修复内容 | 影响 |
|------|---------|------|
| **gateway/api.ts** | 添加 SSRF 防护（禁止内网地址）；错误响应脱敏 | 防止内网探测、信息泄露 |
| **gateway/monitor.ts** | `Promise.all` → `Promise.allSettled`；错误信息脱敏；llama 地址从环境变量读取 | 探针容错、信息脱敏 |
| **gateway/websocket.ts** | CORS 限制为允许来源列表；`recover_tasks` 按 `userId` 过滤 | 隐私泄露修复 |
| **host/openclaw/package.json** | `ws` 升级至 `8.17.1`（CVE-2024-37890）；`express` 升级至 `4.20.0` | 消除 HIGH 级漏洞 |
| **webui/package.json** | `axios` 升级至 `^1.7.4`；`vite` 升级至 `^5.4.6` | 消除 SSRF、DOM Clobbering 漏洞 |
| **webui/src/hooks/useWebSocket.ts** | 最大重连次数限制（10次）+ 指数退避 | 防止连接风暴 |
| **memory/asset_manager.py** | `asset_id` 路径遍历过滤；版本链 `parent` 指向上一版本 | 安全加固 |
| **bridge/wsl2_gateway.sh** | `listenaddress=0.0.0.0` → `127.0.0.1`；防火墙添加 `-RemoteAddress 127.0.0.1` | WSL2 端口暴露修复 |
| **triad/init.sh** | `confirm()` CI 兼容；`nvidia-smi` 用 `--query-gpu` 精确查询 | CI/CD 可用、解析健壮 |

### 2.3 架构级重构

| 模块 | 修复内容 | 影响 |
|------|---------|------|
| **mind/model_router.py** | `FallbackChain` 重构为三态熔断器（`CLOSED` → `OPEN` → `HALF_OPEN` → `CLOSED`）+ `asyncio.Lock` | 生产级熔断保护 |
| **mind/model_router.py** | `_default_call` 持久化 `httpx.AsyncClient`（连接池复用） | 高并发性能 |
| **mind/model_router.py** | `ContextAligner` 字符切片 → `tiktoken` encoder 级 token 截断 | 上下文压缩精度 |
| **hand/comfyui_mcp_bridge.py** | `connect()` + `_get_session()` 添加 `asyncio.Lock`；指数退避重连 | 并发安全 |
| **mind/config_manager.py** | 单例模式添加 `threading.Lock`；端口范围验证 | 线程安全 |
| **mind/model_registry.py** | CRUD 操作添加 `threading.Lock`；JSON 损坏降级 | 并发安全 |
| **docker-compose.hpc.yml** | llama-server 端口 `18000:8080` → `18001:8080`（解决与 hermes 冲突） | 端口冲突消除 |

### 2.4 基础设施

| 文件 | 修复内容 |
|------|---------|
| `.env.example` | `DEBUG=false` 默认值；API Key 占位符清空 |
| `requirements.txt` | 新增文件，声明 `httpx`/`aiohttp`/`aiofiles`/`websockets`/`python-dotenv`/`tiktoken`/`pynvml` |
| `triad_manager.sh` | `.env` 解析从 `export $(grep ... | xargs)` 改为 `set -a; source .env; set +a` |

---

## 三、系统全景图

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           🦞 Triad Station v2.3.1 (浏览器内)                    │
├──────────────────────────────────────────────────────────────────────────────┤
│  [🦞 龙虾控制台]  [📊 系统监控]                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Tab 1: 龙虾控制台                                                            │
│  ├─ 左侧: ChatPanel (对话面板 + 流式输出)                                      │
│  ├─ 右侧: AgentCanvas (模型路由可视化) + VRAMPanel (显存条) + ConfigPanel (配置)  │
│  │            └─ ProviderManager (动态模型注册)                                  │
│  │            └─ SkillMarketTab (技能市场)                                      │
│  └─ WebSocket: ws://localhost:18080/ws/tasks (实时双向通信)                      │
│                                                                              │
│  Tab 2: 系统监控 (3秒轮询 /api/system/status)                                   │
│  └─ GPU 显存条 / Docker 容器列表 / llama 状态 / CPU+内存卡片                    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ WebSocket / HTTP
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      OpenClaw Gateway (Node.js 18+ / TypeScript)              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  WebSocket Server (:18080)                                                   │
│  ├─ 心跳检测 (ping/pong, 30s间隔, 35s超时)                                    │
│  ├─ 任务提交 (submit_task)                                                   │
│  ├─ 断连恢复 (recover_tasks) — 内存级任务历史持久化                           │
│  └─ 进度推送 (push_status / push_result)                                     │
│                                                                              │
│  REST API                                                                    │
│  ├─ GET  /api/models           → 列出所有 Provider                           │
│  ├─ POST /api/models           → 添加 Provider (SSRF 防护)                     │
│  ├─ PUT  /api/models/:id       → 更新 Provider                               │
│  ├─ DEL  /api/models/:id       → 删除 Provider                               │
│  ├─ POST /api/models/:id/toggle→ 启用/禁用切换                              │
│  ├─ POST /api/models/:id/test  → 连通性测试 (SSRF 防护)                       │
│  └─ GET  /api/system/status    → 系统探针 (GPU/llama/容器/CPU/内存)           │
│                                                                              │
│  安全增强 (v2.3.1)                                                            │
│  ├─ CORS 限制为允许来源列表（非 `*`）                                         │
│  ├─ 错误响应脱敏（不暴露内部路径/命令）                                        │
│  └─ /api/system/status 探针降级：任一失败不影响整体返回                       │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ Python 3.10+ (mind/)
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      Hermes 认知编排层 (Python 3.10+)                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  主循环: hermes_orchestrator.py                                               │
│  ├─ Step 1: parse_role() → @novelist / @code_engineer / @art_director          │
│  ├─ Step 2: route()      → tags + 策略匹配 → ModelConfig                      │
│  ├─ Step 3: execute()    → FallbackChain (三态熔断器) → LLMResponse          │
│  ├─ Step 4: classify()   → novel / code / general → 评估策略                  │
│  ├─ Step 5: eval()       → 4维评分 / AST占位 / bypass                        │
│  ├─ Step 6: multimodal() → 判断是否需要 ComfyUI                              │
│  ├─ Step 6.5: crystal()  → ≥7.5 触发 SkillCrystallizer                       │
│  └─ Step 7: 结果返回                                                          │
│                                                                              │
│  蜂群调度: swarm_orchestrator.py                                              │
│  ├─ 解析 `@*_swarm` → 角色列表 + 聚合模式 (CONCAT/MERGE/CHAIN)               │
│  ├─ SwarmExecutor → Semaphore 限速 + asyncio.gather 并发                      │
│  └─ _aggregate() → Map-Reduce 自动压缩（token 级截断）                          │
│                                                                              │
│  技能进化: skill_crystallizer.py                                              │
│  ├─ auto_crystallize() → 保存配方为 Markdown+YAML                             │
│  ├─ extract_swarm_recipe() → 提取角色+工具+聚合模式                             │
│  └─ _find_similar_recipe() → 语义去重（不野蛮繁殖）                           │
│                                                                              │
│  动态路由: model_router.py                                                    │
│  ├─ ModelRouter → 策略路由 (CREATIVE/REASONING/CODING/DEFAULT)               │
│  ├─ FallbackChain → 三态熔断器 (CLOSED/OPEN/HALF_OPEN)                       │
│  ├─ ContextAligner → 跨模型上下文对齐（token 级压缩）                            │
│  └─ _default_call → 持久化 httpx.AsyncClient（连接池复用）                     │
│                                                                              │
│  模型注册: model_registry.py                                                  │
│  ├─ ProviderConfig → id/name/base_url/api_key/context_window/tags             │
│  ├─ CRUD → threading.Lock 保护（并发安全）                                     │
│  └─ providers.json → ~/.triad/memory/config/                                 │
│                                                                              │
│  配置管理: config_manager.py                                                  │
│  ├─ 单例模式 + threading.Lock                                                │
│  └─ 端口范围验证 (1-65535)                                                    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ Docker / subprocess
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      本地推理执行层 (hand/ + Docker)                            │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  llama-server (Docker, -ngl 99↔0 跷跷板)                                     │
│  ├─ GPU 模式: -ngl 99, 24GB VRAM                                             │
│  ├─ CPU 模式: -ngl 0, CPU 推理（显存让给 ComfyUI）                            │
│  └─ 热切换: SIGUSR1 信号 → 触发 -ngl 切换（无需重启容器）                       │
│                                                                              │
│  VRAMScheduler (vram_scheduler_llama.py)                                      │
│  ├─ _llm_counter_lock + _llm_counter_cv (Condition 统一锁)                    │
│  ├─ begin_llm_inference() → 推理引用计数 +1（阻塞切换）                        │
│  ├─ end_llm_inference() → 推理引用计数 -1                                     │
│  ├─ acquire_render_memory() → Union[str, RenderTask] 接口兼容                  │
│  ├─ _graceful_shutdown() → SIGTERM → 等待 → SIGKILL（孤儿进程处理）             │
│  ├─ CpuAffinityManager → docker update --cpuset-cpus（NUMA 亲和性）            │
│  └─ RenderContext → __aenter__/__aexit__ 自动释放                              │
│                                                                              │
│  ComfyUI MCP Bridge (comfyui_mcp_bridge.py)                                  │
│  ├─ asyncio.Lock 保护 connect() + _get_session()                              │
│  ├─ 指数退避重连: delay * 1.5^n (max 60s)                                     │
│  ├─ MCP readline() → asyncio.wait_for(timeout=30s)                            │
│  └─ try/finally 清理 _active_tasks（防内存泄漏）                              │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 四、核心子系统详解

### 4.1 三态熔断器 (FallbackChain)

```
┌─────────┐    失败×N    ┌─────────┐    超时到期    ┌─────────┐
│  CLOSED │ ───────────→ │  OPEN   │ ─────────────→ │HALF_OPEN│
│ (正常)  │              │ (熔断)  │                │(单探测) │
└────┬────┘              └────┬────┘                └────┬────┘
     │                        │                           │
     │ 成功                   │ 探测成功                  │ 探测失败
     │                        │                           │
     └────────────────────────┴───────────────────────────┘
                              ↓
                        清零计数 → CLOSED
```

- **阈值**: 5 次连续失败
- **超时**: 120 秒
- **半开状态**: 仅允许单个探测请求通过（`asyncio.Lock` 保护）
- **客户端错误 (4xx)**: 不触发熔断

### 4.2 VRAM 跷跷板状态机

```
┌─────────┐    acquire_render    ┌─────────┐    begin_llm     ┌─────────┐
│  IDLE   │ ──────────────────→ │RENDERING│ ──────────────→ │LLM_GPU  │
│         │                      │         │                  │         │
└────┬────┘                      └────┬────┘                  └────┬────┘
     │                                │  llm_unloads > 0           │
     │                                │                            │
     │         release_render         │     warm_up()              │
     │         + recover LLM          │     + sleep(1.5s)          │
     │                                ↓                            │
     └───────────────────────────────┴────────────────────────────┘
                                   ↓
                             _set_state(IDLE)
```

### 4.3 蜂群调度并发控制

```
用户输入: @deep_research_swarm 调研 Rust vs Go

                    ┌─────────────┐
                    │ SwarmExecutor│
                    │  semaphore=3  │
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
    ┌────▼────┐      ┌────▼────┐      ┌────▼────┐
    │ Agent 0 │      │ Agent 1 │      │ Agent 2 │
    │ 研究员  │      │  写手   │      │  审校   │
    │ (deep)  │      │ (tech)  │      │ (logic) │
    └────┬────┘      └────┬────┘      └────┬────┘
         │                 │                 │
         │                 │                 │
         └─────────────────┼─────────────────┘
                           │
                    ┌──────▼──────┐
                    │ _aggregate()  │
                    │ CONCAT/MERGE  │
                    └──────┬──────┘
                           │
                    ┌────────▼────────┐
                    │  Map-Reduce压缩   │
                    │ token > 6000    │
                    └─────────────────┘
```

---

## 五、API 参考

### 5.1 WebSocket 任务提交

```json
// 发送
{"action":"submit_task","prompt":"@novelist 写第一章","strategy":"CREATIVE"}

// 阶段状态
{"taskId":"xxx","stage":"ANALYZING","message":"🎭 角色模式: 小说家","progress":0.15}
{"taskId":"xxx","stage":"EXECUTION","message":"调用 grok/grok-beta...","progress":0.3}
{"taskId":"xxx","stage":"TESTING","message":"评估: 人设 8.5/10","progress":0.7}
{"taskId":"xxx","stage":"COMPLETED","message":"第一章已生成","progress":1.0}

// 最终结果
{"taskId":"xxx","status":"success","output":"# 第一章\n\n内容..."}
```

### 5.2 REST API

| 端点 | 方法 | 说明 | 安全增强 (v2.3.1) |
|------|------|------|-------------------|
| `/api/models` | GET | 列出 Provider | — |
| `/api/models` | POST | 添加 Provider | SSRF 防护 |
| `/api/models/:id` | PUT | 更新 Provider | — |
| `/api/models/:id` | DELETE | 删除 Provider | — |
| `/api/models/:id/toggle` | POST | 启用/禁用 | — |
| `/api/models/:id/test` | POST | 连通性测试 | SSRF 防护 + 内网禁止 |
| `/api/system/status` | GET | 系统探针 | 错误脱敏 + allSettled |

---

## 六、数据流

### 6.1 单次任务数据流

```
[用户输入]
    │
    ▼
[BrowserShell] ──→ [WebSocket] ──→ [OpenClaw Gateway]
    │                                      │
    │                                      ▼
    │                              [websocket.ts]
    │                              ├─ 心跳检测
    │                              ├─ 任务分发
    │                              └─ 进度回传
    │                                      │
    │                                      ▼
    │                              [hermes_orchestrator.py]
    │                              ├─ parse_role()
    │                              ├─ route() → model_router.py
    │                              ├─ execute() → FallbackChain
    │                              ├─ eval() → novel_curator.py
    │                              └─ crystal() → skill_crystallizer.py
    │                                      │
    │                                      ▼
    │                              [vram_scheduler_llama.py]
    │                              ├─ begin_llm_inference()
    │                              ├─ 调用 llama-server (HTTP)
    │                              └─ end_llm_inference()
    │                                      │
    │                                      ▼
    │                              [LLMResponse] ──→ [WebSocket推送]
    │                                      │
    ▼                                      ▼
[前端渲染] ←────────────────────────── [进度更新]
```

### 6.2 蜂群任务数据流

```
[用户输入: @_swarm]
    │
    ▼
[hermes_orchestrator.py]
    ├─ _is_swarm_mode() → True
    ├─ _build_swarm_agents() → [Agent0, Agent1, Agent2]
    └─ SwarmExecutor.execute()
         │
         ├─ semaphore.acquire() (max 3)
         │
         ├─ asyncio.gather(
         │      Agent0.execute(),
         │      Agent1.execute(),
         │      Agent2.execute()
         │   )
         │
         ├─ _aggregate(CONCAT)
         │
         ├─ _estimate_tokens()
         │   > 6000? → Map-Reduce压缩
         │
         └─ auto_crystallize()
             score ≥ 8.0? → save recipe
```

---

## 七、部署架构

### 7.1 Docker Compose 网络拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                        triad-bridge (172.30.x.x/24)              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │
│  │  openclaw   │  │   redis     │  │  qdrant     │               │
│  │  (gateway)  │  │  (session)  │  │  (vector)   │               │
│  │  :18080     │  │  :6379      │  │  :6333      │               │
│  └──────┬──────┘  └─────────────┘  └─────────────┘               │
│         │                                                        │
│  ┌──────┴─────────────────────────────────────────────┐          │
│  │              triad-internal (bridge)               │          │
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────┐  │          │
│  │  │   hermes    │  │  llama-srv  │  │ comfyui  │  │          │
│  │  │  (python)   │  │  (:18001)   │  │ (:8188)  │  │          │
│  │  └─────────────┘  └─────────────┘  └──────────┘  │          │
│  └──────────────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 端口映射

| 服务 | 容器端口 | 宿主机端口 | 说明 |
|------|---------|-----------|------|
| openclaw | 18080 | 18080 | Gateway HTTP + WebSocket |
| llama-server | 8080 | **18001** | llama.cpp HTTP API (v2.3.1: 18000→18001) |
| redis | 6379 | 16379 | Session 缓存 |
| qdrant | 6333 | 16333 | 向量数据库 |
| webui | 5173 | 15173 | Vite 开发服务器 |
| comfyui | 8188 | 18188 | ComfyUI (可选) |

---

## 八、配置参考

### 8.1 环境变量 (.env)

```bash
# === 核心服务 ===
GATEWAY_PORT=18080
GATEWAY_HOST=127.0.0.1          # v2.3.1: 默认绑定 localhost 而非 0.0.0.0

# === LLM 服务 ===
LLAMA_PORT=18000                # llama-server 容器内部端口
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2

# === API Keys (按需填写) ===
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GROK_API_KEY=
KIMI_API_KEY=

# === GPU 配置 ===
NVIDIA_VISIBLE_DEVICES=all
CUDA_VISIBLE_DEVICES=0

# === 路径配置 ===
TRIAD_ROOT=~/.triad
MODELS_DIR=~/.triad/models

# === 运行模式 ===
DEBUG=false                     # v2.3.1: 生产环境默认 false
LOG_LEVEL=INFO
```

---

## 九、性能基准

| 指标 | 数值 | 说明 |
|------|------|------|
| 冷启动时间 | ~3.5s | llama-server 从 IDLE 到 READY |
| VRAM 切换 | ~2.1s | GPU ↔ CPU (-ngl 99↔0) |
| 蜂群并发 | 3 Agent | Semaphore 限速，可配置 |
| 上下文压缩 | 8192 → 4000 tokens | Map-Reduce 自动 |
| WebSocket 延迟 | <50ms | 本地回环 |
| 监控轮询 | 3s | GPU/容器/llama/CPU/内存 |

---

## 十、安全注意事项 (v2.3.1 新增)

1. **API Key 管理**: 当前版本 API Key 以明文存储在 `~/.triad/memory/config/providers.json`。生产环境建议：
   - 使用操作系统密钥环（Windows Credential / macOS Keychain / Linux secret-tool）
   - 或后端 Vault 服务（HashiCorp Vault / AWS Secrets Manager）

2. **网络隔离**: Docker Compose 默认绑定 `127.0.0.1`。若需远程访问，请配置反向代理（Nginx/Caddy）并启用 HTTPS。

3. **WSL2 端口暴露**: `wsl2_gateway.sh` 已将 `listenaddress` 限制为 `127.0.0.1`，防火墙规则限制为 `RemoteAddress 127.0.0.1`。

4. **认证缺失**: 当前版本 REST API 和 WebSocket 均无认证层。v2.4 计划引入 Bearer Token / API Key 中间件。

5. **依赖漏洞**: v2.3.1 已升级 `ws`/`express`/`axios`/`vite` 至安全版本。请定期运行 `npm audit` 和 `pip audit`。

---

## 十一、已知限制

| 限制 | 说明 | 预计解决 |
|------|------|---------|
| CodeCurator 为占位符 | 代码评估返回满分 10.0，AST 静态检查待接入 | v2.4 |
| 蜂群角色需手动添加 | `@deep_research_swarm` 需定义 `is_swarm=True` | 未来 Web UI 支持 |
| 认证层缺失 | REST/WebSocket 均无认证 | v2.4 |
| API Key 明文存储 | 建议迁移至操作系统密钥环 | v2.4 |

---

## 十二、版本历史

| 版本 | 日期 | 核心变更 |
|------|------|---------|
| v2.3 | 2026-05-06 | 初始发布：多标签工作台、蜂群调度、技能结晶、显存跷跷板 |
| **v2.3.1** | **2026-05-06** | **安全补丁：53 个 bug 修复，熔断器重构，并发安全，SSRF 防护，基础设施硬化** |

---

<p align="center">
  <strong>Triad v2.3.1 — 本地智能体操作系统，数据不出站，算力全掌控。</strong>
</p>
