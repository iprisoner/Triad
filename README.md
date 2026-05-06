<p align="center">
  <img src="https://raw.githubusercontent.com/iprisoner/Triad/main/assets/logo.png" alt="Triad Logo" width="120">
</p>
<h1 align="center">Triad v2.3.1 🦞 Lobster Station (Security Patch)</h1>
<p align="center">
  <strong>本地 AI 智能体操作系统 · 蜂群并发 · 显存跷跷板 · 动态路由</strong>
</p>
<p align="center">
  <a href="https://github.com/iprisoner/Triad/releases"><img src="https://img.shields.io/github/v/release/iprisoner/Triad" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/Node-18%2B-green" alt="Node">
  <img src="https://img.shields.io/badge/React-18%2B-61dafb" alt="React">
</p>

---

## 📖 项目简介

Triad 是一款运行在**本地 WSL2 环境**中的三层架构 AI 智能体操作系统，代号 **Lobster Station（龙虾工作站）**。它不是网页聊天框，而是一台拥有蜂群调度能力的**生产级 AI 工作站**。

**核心理念**：数据不出站，算力全掌控。所有推理、绘画、编排都在你的本地硬件上完成，云端 API 仅作为可选增强。

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         🦞 浏览器多标签工作台                                  │
│            [🦞 龙虾控制台]  [📊 系统监控]                                      │
│              React 18 + Vite + Tailwind + shadcn/ui                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                         OpenClaw Gateway (Node.js/TS)                          │
│     WebSocket Server (:8080) · REST API (/api/models) · 系统探针 (/status)    │
├─────────────────────────────────────────────────────────────────────────────┤
│                         Hermes 认知编排层 (Python 3.10+)                        │
│  动态角色路由 · 蜂群调度(SwarmExecutor) · 技能进化(SkillCrystallizer)           │
│  动态评估(小说/代码/bypass) · 本地推理路由                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                         本地推理执行层                                        │
│  llama-server (Docker, -ngl 99↔0 跷跷板)  本地推理                            │
│  VRAMScheduler (读者-写者锁 · NUMA 亲和性调度)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## ✨ 核心特性

### 已生产就绪 (v2.3.1)

| 特性 | 说明 | 状态 |
|------|------|------|
| **@角色系统** | `@novelist` / `@code_engineer` / `@art_director` 等 5+ 内置角色，独立 System Prompt + 模型偏好 + 工具权限 | ✅ |
| **无极蜂群** | `@deep_research_swarm` 触发多 Agent 并发（研究员+写手+审校），`asyncio.gather` + Semaphore 限速 | ✅ |
| **动态模型路由** | 不限厂商，无限添加，Web UI 管理，tags 匹配自动路由 | ✅ |
| **显存跷跷板** | `-ngl 99↔0` GPU/CPU 自动切换，**推理引用计数锁**保证切换安全 | ✅ |
| **技能进化** | 高分任务自动固化配方为 Markdown+YAML，**语义去重** + 适者生存 | ✅ |
| **动态评估** | 小说→4维评估，代码→AST占位，通用→bypass，绝不浪费算力 | ✅ |
| **断连恢复** | WebSocket 断开后重连自动恢复任务历史和进度（内存级持久化） | ✅ |
| **上下文压缩** | Map-Reduce 自动压缩超长聚合结果，防止 8192 窗口溢出 | ✅ |
| **系统监控** | 3秒轮询 GPU/容器/llama/CPU/内存，实时显存条可视化 | ✅ |

---

## 🚀 快速开始

### 硬件要求

- **CPU**: 推荐双路 Xeon E5-v3 (24C/48T) 或同等性能
- **GPU**: 推荐魔改 2080Ti 22GB / 3090 24GB / 4090 24GB
- **内存**: 64GB+ DDR4 ECC
- **存储**: NVMe SSD，模型文件占 50GB+
- **网络**: 可访问清华源、阿里云镜像

### 环境

- WSL2 Ubuntu 22.04
- Docker Desktop（WSL2 后端）
- NVIDIA Docker Runtime（`nvidia-smi` 在 WSL2 可用）

### 一键安装

```bash
# 1. 克隆仓库
git clone https://github.com/iprisoner/Triad.git
cd Triad/triad

# 2. 一键安装（20-40 分钟）
chmod +x triad_manager.sh
./triad_manager.sh install
# 输出：
# [✓] ext4 检查通过
# [✓] 清华源/阿里云镜像/npm 淘宝源已配置
# [✓] Docker 镜像拉取完成
# [✓] Web UI 构建完成
# [✓] Qwen GGUF 模型就绪
# [✓] llama.cpp 检测模式（用户自行安装）
# [✓] .env 已生成

# 3. 填入 API Key（必须）
cp .env.example .env
nano .env
# 填入你的 Grok / DeepSeek / Kimi / Claude Key（不需要的可留空）

# 4. 一键启动
./triad_manager.sh start

# 5. Windows 浏览器访问
open http://localhost:8080/panel
```

### 启动成功面板

```
╔═══════════════════════════════════════════════════════════╗
║              🟣 Triad Station v2.3 启动成功              ║
╠═══════════════════════════════════════════════════════════╣
║  Web UI:     http://localhost:8080/panel                  ║
║  Gateway:    ws://localhost:8080/ws/tasks               ║
║  llama-server: http://localhost:8000/v1/chat/completions ║
╠═══════════════════════════════════════════════════════════╣
║  标签页: [🦞 龙虾控制台] [📊 系统监控]                     ║
╠═══════════════════════════════════════════════════════════╣
║  VRAM 初始:                                               ║
║    [2GB Embed][████████████][9GB LLM GPU]               ║
║    [░░░░░░░░░░░░░░][9GB 空闲][2GB 系统]                  ║
╚═══════════════════════════════════════════════════════════╝
```

---

## 📂 目录结构

```
triad/
├── mind/                          # 认知编排层 (Python)
│   ├── hermes_orchestrator.py     # 主循环：动态5步 + 蜂群分叉
│   ├── swarm_orchestrator.py      # 蜂群调度器：asyncio.gather 并发
│   ├── skill_crystallizer.py      # 技能结晶器：Markdown+YAML 固化
│   ├── model_router.py            # 动态模型路由：strategy→tags匹配
│   ├── model_registry.py          # 模型注册表：providers.json CRUD
│   ├── novel_curator.py           # 小说评估器：4维评分
│   ├── prompts/roles.py           # 角色定义：5个内置角色
│   └── acp_adapter/
│       └── streaming_reporter.py  # 状态回传总线
│
├── hand/                          # 本地推理执行层
│   ├── vram_scheduler_llama.py   # VRAM 调度器：-ngl 跷跷板 + 全局锁
│
├── host/openclaw/src/gateway/     # 后端网关 (TypeScript)
│   ├── websocket.ts               # WebSocket Server + 断连恢复
│   ├── api.ts                     # REST API：模型 CRUD + test
│   └── monitor.ts                 # 系统探针：nvidia-smi + docker ps
│
├── webui/src/                     # 前端 (React + TSX)
│   ├── BrowserShell.tsx           # 多标签外壳：3 Tab CSS 切换
│   ├── components/
│   │   ├── SystemMonitorTab.tsx   # 系统监控面板
│   │   ├── ConfigPanel/
│   │   │   ├── ProviderManager.tsx # 动态模型注册中心
│   │   │   └── SkillMarketTab.tsx  # 技能市场
│   │   └── ChatPanel/
│   └── hooks/
│       ├── useProviders.ts        # 模型 CRUD Hook
│       └── useWebSocket.ts        # WebSocket 连接管理
│
├── docs/                          # 文档
│   ├── TECHNICAL_WHITEPAPER_v2.3.md   # 技术白皮书
│   ├── USER_GUIDE_v2.3.md            # 用户指南
│   └── CONTINUATION_GUIDE.md          # 上下文恢复包
│
├── docker-compose.hpc.yml         # Docker 编排
├── triad_manager.sh               # 一键部署脚本
├── init.sh                        # WSL2 环境初始化
└── .env.example                   # 环境变量模板
```

---

## 🎯 使用示例

### 单体模式（轻骑兵）

```
@novelist 帮我写第一章。
要求：主角是一位前企业黑客，性格外冷内热。
风格：赛博朋克现实主义，每章 3000 字。
```

**系统行为**：
1. `parse_role()` → 小说家角色，注入 System Prompt
2. `route()` → 匹配 CREATIVE 策略 → Grok/Gemini
3. `execute()` → 生成文本
4. `_get_eval_strategy()` → "novel" → 4维评估
5. `_get_multimodal_strategy()` → "bypass"（不画图）
6. 评分 ≥7.5 → SkillCrystallizer 固化配方

### 蜂群模式（特种部队）

```
@deep_research_swarm 调研 Rust vs Go 在 AI 推理引擎中的优劣
```

**系统行为**：
1. `_is_swarm_mode()` → True（`_swarm` 后缀）
2. `_build_swarm_agents()` → [研究员(deep), 写手(tech), 审校(logic)]
3. `asyncio.gather()` 并发执行 3 个 Agent
4. `_aggregate(CONCAT)` → 合并 3 份输出
5. `_estimate_tokens()` → 若 >6000 触发 Map-Reduce 压缩
6. `auto_crystallize()` → 评分 ≥8.0 保存配方

---

## 📚 文档

| 文档 | 说明 |
|------|------|
| [技术白皮书](triad/docs/TECHNICAL_WHITEPAPER_v2.3.1.md) | 架构全景图、完成度详表、API 参考、数据流图示 |
| [用户指南](triad/docs/USER_GUIDE_v2.3.1.md) | 角色速查表、30分钟上手教程、FAQ |
| [续接指南](triad/docs/CONTINUATION_GUIDE.md) | 上下文恢复包（用于新 AI 助手续接） |

---

## ⚙️ 核心 API

### WebSocket 任务提交

```json
// 发送
{"action":"submit_task","prompt":"@novelist 写第一章","strategy":"CREATIVE"}

// 接收阶段状态
{"taskId":"xxx","stage":"ANALYZING","message":"🎭 角色模式: 小说家","progress":0.15}
{"taskId":"xxx","stage":"EXECUTION","message":"调用 grok/grok-beta...","progress":0.3}
{"taskId":"xxx","stage":"TESTING","message":"评估: 人设 8.5/10","progress":0.7}
{"taskId":"xxx","stage":"COMPLETED","message":"第一章已生成","progress":1.0}

// 最终结果
{"taskId":"xxx","status":"success","output":"# 第一章\n\n内容..."}
```

### 断连恢复

```json
// 重新连接后发送
{"action":"recover_tasks"}

// 接收历史任务
{"action":"recover_tasks_response","taskCount":5,"tasks":[...]}
```

---

## 🛡️ 已知限制（诚实清单）

| 限制 | 说明 | 预计解决 |
|------|------|---------|
| CodeCurator 为占位符 | 代码评估当前返回满分 10.0，AST 静态检查（pylint/mypy）待接入 | v2.4 |
| 蜂群角色需手动在 roles.py 添加 | `@deep_research_swarm` 等需定义 `is_swarm=True` | 未来 Web UI 支持 |
| 认证层为可选中间件 | 当前默认放行，需配置 TRIAD_API_KEY 环境变量启用 | v2.4 |
| API Key 内存存储 | 前端不再持久化 API Key，刷新后需重新输入 | v2.4 接入后端加密存储 |

---

## 🗺️ Roadmap

### 已实现（v2.3）
- [x] 多标签工作台（龙虾/监控）
- [x] 单 Agent 多角色（5 个内置角色）
- [x] 动态模型注册表（无限添加，Web UI 管理）
- [x] 系统监控探针（GPU/容器/llama/CPU/内存）
- [x] llama.cpp 显存跷跷板
- [x] 蜂群调度器（SwarmExecutor）— 多 Agent 并发协作
- [x] 技能结晶器（SkillCrystallizer）— 成功配方自动固化与进化
- [x] 动态评估路由 — 小说评估 / 代码跳过 / 通用 bypass
- [x] VRAM 死锁防护（推理引用计数 + 全局锁）
- [x] 上下文压缩（Map-Reduce Token 上限）
- [x] 配方语义去重（适者生存，不野蛮繁殖）
- [x] WebSocket 断连恢复（任务状态持久化）

### v2.3.1 安全补丁 (2026-05-06)
- [x] 熔断器三态重构（CLOSED/OPEN/HALF_OPEN）+ asyncio.Lock
- [x] SSRF 防护（禁止内网地址探测）
- [x] 错误响应脱敏（不暴露内部路径/命令）
- [x] WebSocket 连接风暴防护（指数退避 + 最大重连次数）
- [x] VRAM 调度器死锁修复（Condition+Lock 不可重入）
- [x] 蜂群模式语法修复（IndentationError）
- [x] 依赖漏洞升级（ws 8.17.1 / express 4.20.0 / axios 1.7.4 / vite 5.4.6）
- [x] 路径遍历过滤（asset_id 字符白名单）
- [x] WSL2 端口暴露限制（127.0.0.1）

### 下一步（v2.4）
- [ ] Web UI 直接添加自定义角色（不用改代码）
- [ ] CodeCurator 真实 AST 静态检查（pylint/mypy/pytest）
- [ ] 语音输入/输出集成
- [ ] 云端同步备份（可选）

---

## 📊 项目统计

- **文件数**: 79 个
- **代码行**: ~16,500 行（Python/TypeScript/TSX/Bash/YAML）
- **提交**: `3d8a96d` — Triad v2.3.1: Security patch — 58 bug fixes, 3-state circuit breaker, SSRF protection, API Key encryption

---

## 🤝 贡献

Triad 是一个持续演进的社区项目。欢迎通过以下方式参与：

- **提交 Issue**: 发现 Bug 或有新想法
- **提交 PR**: 改进代码或文档
- **分享经验**: 在 Discussion 中分享你的部署和调优经验

---

## 📄 License

本项目采用 [MIT License](LICENSE) 开源协议。

---

<p align="center">
  <strong>Triad v2.3.1 — 本地智能体操作系统，数据不出站，算力全掌控。</strong>
</p>
