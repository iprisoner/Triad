# Triad v2.2 项目续接指南
## 上下文恢复包 (Context Continuation Package)

**生成日期**: 2026-05-03  
**项目版本**: Triad Station v2.2 (Lobster Station)  
**累计代码**: 79 个文件，~15,300 行  

---

## 一、项目当前状态（一句话摘要）

Triad 是一个运行在本地的三层 AI Agent 工作站（WSL2 + 双路 E5 + 2080Ti），已实现：
- **前端**: 浏览器多标签工作台（🦞龙虾/🎨ComfyUI/📊监控）
- **后端**: OpenClaw Gateway (WebSocket + REST API)
- **认知层**: Hermes 主循环 + 动态模型路由 + 5个角色系统 + 小说评估 + **蜂群调度 + 技能进化**
- **执行层**: llama.cpp 显存跷跷板 + ComfyUI MCP Bridge
- **部署**: 一键脚本 triad_manager.sh

---

## 二、关键文件清单（按模块）

### 前端 (React + TSX)
```
webui/src/BrowserShell.tsx                           — 多标签外壳（3 Tab）
webui/src/App.tsx                                    — 入口（简化为 BrowserShell）
webui/src/components/SystemMonitorTab.tsx             — 系统监控面板（3秒轮询）
webui/src/components/ConfigPanel/ProviderManager.tsx   — 动态模型注册中心
webui/src/components/ConfigPanel/SkillMarketTab.tsx    — 技能市场（MCP Tools + Skills）
webui/src/hooks/useProviders.ts                      — 模型 CRUD Hook
webui/src/hooks/useWebSocket.ts                      — WebSocket 连接管理
webui/src/types/index.ts                             — TypeScript 类型定义
```

### OpenClaw Gateway (Node.js/TypeScript)
```
host/openclaw/src/gateway/websocket.ts               — WebSocket Server (:8080)
host/openclaw/src/gateway/api.ts                     — REST API (/api/models CRUD)
host/openclaw/src/gateway/monitor.ts                 — 系统探针 (/api/system/status)
```

### 认知层 (Python)
```
mind/hermes_orchestrator.py                         — 主循环编排器（5步串联）
mind/model_router.py                                 — 动态路由（strategy→tags匹配 + @角色解析）
mind/model_registry.py                               — providers.json CRUD（无限模型注册）
mind/novel_curator.py                                — 4维小说评估
mind/prompts/roles.py                                — 5个角色定义（code_engineer/novelist/art_director...）
mind/acp_adapter/streaming_reporter.py               — 状态回传总线（非阻塞HTTP POST）
mind/config_manager.py                               — .env 配置单例
mind/swarm_orchestrator.py                           — 蜂群调度器（asyncio.gather 并发多Agent）
mind/skill_crystallizer.py                           — 技能结晶器（Markdown+YAML 配方固化）
```

### 执行层 (Python)
```
hand/vram_scheduler_llama.py                        — llama.cpp 显存跷跷板（CPU_FALLBACK状态机）
hand/comfyui_mcp_bridge.py                          — ComfyUI API JSON参数注入 + 轮询下载
hand/vram_scheduler.py（旧版）                      — vLLM版（已废弃）
memory/asset_manager.py                             — asset:// URI + 版本链
```

### 部署与配置
```
triad_manager.sh                                     — 一键部署启停脚本（install/start/stop/status/logs）
init.sh                                              — WSL2环境初始化（ext4检查+国内镜像源）
bridge/wsl2_gateway.sh                               — Windows端口代理（powershell netsh）
docker-compose.hpc.yml                               — Docker编排（llama-server + openclaw + hermes + qdrant）
bridge/mcp_registry.json                             — 8个MCP Server配置模板
.env.example                                         — 28项环境变量模板（6厂商API Key）
```

### 文档
```
docs/TECHNICAL_WHITEPAPER_v2.2.md                    — 技术白皮书（架构图+完成度详表+API参考）
docs/USER_GUIDE_v2.2.md                              — 用户指南（角色速查+30分钟教程+FAQ）
docs/mcp_server_catalog.md                           — 8个MCP Server清单
docs/skill_market_integration.md                     — Skill市场三层接入方案
docs/comfyui_setup_guide.md                          — ComfyUI傻瓜式操作指南
```

---

## 三、待办事项（下一步任务）

### 🔥 当前进行中
- [x] **编写 mind/swarm_orchestrator.py** — 轻量蜂群调度器 ✅ 已完成
  - SwarmExecutor 类管理"临时 Agent"（仅数据结构：Name + System Prompt + Allowed Tools）
  - 使用 asyncio.gather 并发触发多个临时 Agent
  - 每个临时 Agent 底层调用 model_router.py 的 execute()
  - 实时调用 streaming_reporter.report_stage() 推送进度到前端
  
- [x] **编写/改造 mind/skill_crystallizer.py** — 进化机制重构 ✅ 已完成
  - extract_swarm_recipe() 提取成功的 System Prompt 组合 + 调用顺序
  - 序列化保存为 ~/.triad/memory/skills/self-evolved/*.md（员工手册模板）
  - 不再修改物理进程，只生成 Prompt 模板

### 📋 计划内
- [ ] **前端集成** — 在 SkillMarketTab 中展示"Swarm Recipe"技能类型
- [ ] **Web UI 直接添加自定义角色** — 不用改 roles.py
- [ ] **语音输入/输出集成**
- [ ] **云端同步备份**（可选）

---

## 四、架构关键决策（必读）

### 4.1 "无限 Agent"的实现方式

Triad 采用**两层体系**：

| 层级 | 形态 | 数量 | 进化 | 示例 |
|------|------|------|------|------|
| **常驻核心** | Docker进程 | 3-5个 | SkillCrystallizer 长期进化 | Hermes/OpenClaw/llama-server |
| **临时工具** | 数据结构 | 无限 | 无进化，Prompt模板驱动 | @研究员/@写手/@审查员 |

**临时Agent的本质**：不是物理进程，而是HTTP请求中的 `messages[0] = {"role": "system", "content": "..."}`

### 4.2 显存跷跷板

llama-server 通过 `-ngl` 参数切换：
- GPU模式 (`-ngl 99`)：~25 tok/s，占用 9GB 显存
- CPU模式 (`-ngl 0`)：~5 tok/s，释放显存给 ComfyUI
- 切换时间：SIGTERM → restart → mmap热映射，<5秒

### 4.3 模型路由逻辑

```
用户输入 → parse_role() 解析 @角色名
  → 有角色: 使用角色的 model_pref 作为 strategy
    → registry.find_by_strategy("REASONING") 
      → 匹配 tags: ["reasoning", "code", "logic"]
        → 选择 Provider（按 context_window 排序）
  → 无角色: 默认 AUTO 策略
```

### 4.4 蜂群调度器 (SwarmExecutor)

`mind/swarm_orchestrator.py` 提供轻量级多 Agent 并发协作：

```python
# 工厂方法快速创建临时 Agent
agents = [
    SwarmExecutor.create_researcher("deep"),
    SwarmExecutor.create_writer("tech"),
    SwarmExecutor.create_reviewer("code"),
]

# 组装蜂群任务
task = SwarmTask(
    task_id="tech-survey-001",
    description="调研 Rust vs Go 在 AI 推理引擎中的优劣",
    agents=agents,
    parallel_limit=3,
    aggregation_mode=AggregationMode.CONCAT,
)

# 执行并聚合结果
result = await executor.execute_swarm(task)
```

**临时 Agent 本质**：不是物理进程，而是 `messages[0] = {"role": "system", "content": "..."}` 中的数据结构。通过 `asyncio.gather` 并发调用 `model_router.execute()`，由 Semaphore 限速。

### 4.5 技能结晶器 (SkillCrystallizer)

当蜂群任务评分 >= 8.0 时，自动将成功经验固化为可复用配方：

```python
crystallizer = SkillCrystallizer()
path = crystallizer.auto_crystallize(swarm_task, results, score=9.2)
# → ~/.triad/memory/skills/self-evolved/20260503_143052_深度调研_143052.md
```

配方格式：YAML Frontmatter + Markdown Body，人类可读的员工手册。支持版本进化（`evolve_from_recipe`）。


### 步骤 1：粘贴本文件
将本文件完整粘贴给 Kimi，让它快速了解项目全貌。

### 步骤 2：指向关键文件
```
项目所有代码在 /mnt/agents/output/triad/ 目录下。
请读取 [具体文件路径] 了解当前实现。
```

### 步骤 3：指定任务
```
基于 Triad v2.2 的当前状态，请完成 [待办事项中的具体任务]。
关键接口：
- model_router.py 的 execute(prompt, decision) → LLMResponse
- streaming_reporter.py 的 report_stage(task_id, stage, message, progress)
- hermes_orchestrator.py 的 process_task(task_request)
- swarm_orchestrator.py 的 execute_swarm(task) → SwarmResult（新）
- skill_crystallizer.py 的 auto_crystallize(task, results, score) → Path（新）
```

---

## 六、硬件基准

- **CPU**: 双路 Intel Xeon E5-2673v3 (24C/48T)
- **GPU**: 魔改 NVIDIA RTX 2080Ti 22GB
- **内存**: 64GB DDR4 ECC
- **OS**: WSL2 Ubuntu 22.04
- **存储**: NVMe SSD

---

*本文件是 Triad v2.2 的上下文恢复包，用于在 Kimi 新对话中快速重建项目认知。*