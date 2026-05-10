# Triad v3.0 技术白皮书

## 本地 AI 智能体操作系统 — OpenClaw Native 重构

> **版本**: v3.0.0
> **代号**: Lobster Station (OpenClaw Native)
> **日期**: 2026-05

---

## 1. 摘要

Triad v3.0 是一次架构重构：放弃自建消息网关，全面接入 OpenClaw 生态系统。核心思路不变（本地 AI 工作站、蜂群调度、显存跷跷板、动态路由），但通信层从"重复造轮子"改为"寄生进化"。

### 1.1 版本背景

v2.x 存在的问题：
- 自建 WebSocket Gateway（~800 行 TypeScript）与 OpenClaw 原生功能严重重叠
- 自建 REST API（模型 CRUD、认证、限流）同样是轮子
- 自建心跳/断连恢复机制 — OpenClaw 原生已实现
- 模型不能直接修改系统配置，只能"给建议"
- 运维脚本和 Gateway 耦合，难以独立升级

v3.0 解决方案：
- 砍掉 `host/openclaw/src/gateway/` 全部三个文件
- Hermes 认知编排层包装为 OpenClaw Skill
- WebUI 直接对接 OpenClaw 原生 WebSocket
- 模型通过 OpenClaw 原生工具链改配置、管基础设施

---

## 2. 架构设计

### 2.1 三层架构（v3.0）

```
┌──────────────────────────────────────────────────────────┐
│  WebUI — React 18 / Vite / Tailwind / shadcn/ui          │
│  TriadPanel.tsx: 左聊天 + 右监控/配置                      │
│  useOpenClawWS.ts: 对接 OpenClaw 原生 WS                  │
├──────────────────────┬───────────────────────────────────┤
│  OpenClaw Gateway    │  Hermes Skill (Python)             │
│  (原生，不改代码)      │  hermes_skill.py                  │
│                      │  ├── model_router (动态路由)       │
│  - 会话管理           │  ├── swarm_orchestrator (蜂群)    │
│  - 认证授权           │  ├── skill_crystallizer (进化)    │
│  - 消息路由           │  ├── novel_curator (评估)         │
│  - 断连恢复           │  ├── vram_scheduler (显存)        │
│  - 工具调用           │  └── config_manager (配置)        │
├──────────────────────┴───────────────────────────────────┤
│  基础设施层                                                │
│  ├── llama-server (Docker, -ngl 99↔0)                    │
│  ├── ComfyUI (宿主机, 18188)                              │
│  ├── Qdrant (向量数据库)                                   │
│  └── Docker Compose (v3.0 精简版)                         │
└──────────────────────────────────────────────────────────┘
```

### 2.2 核心设计决策

**决策 1: 不自己写 Gateway**

OpenClaw 已提供生产级 WebSocket、REST API、认证、心跳、断连恢复。Triad 不再重复实现，直接复用。

**决策 2: Hermes 变成 Skill，不是独立服务**

Hermes 的模型路由、蜂群调度、技能进化作为 OpenClaw 可调用的工具存在。模型在对话中通过 `exec` 调用 `hermes_skill.py` 的 CLI 子命令。

**决策 3: 模型获得"动手"权限**

通过 System Prompt 授予模型使用 `exec`/`gateway`/`write`/`edit` 工具的权限。用户说"改配置"，模型直接执行。不再是对话式建议引擎，而是对话式管理员。

**决策 4: WebUI 纯前端，零状态存储**

WebUI 的数据全部从 OpenClaw WS 和 Hermes API 实时拉取。不存本地状态，刷新页面等于重置视图。

---

## 3. 新增模块详解

### 3.1 hermes_skill.py

**位置**: `triad/skills/hermes_skill.py`

**功能**: Hermes 全能力的 CLI + REST API 包装

**CLI 子命令**:

| 命令 | 功能 | OpenClaw 工具 |
|------|------|-------------|
| `route <prompt>` | 模型路由决策 | exec |
| `execute <prompt>` | 路由+执行 | exec |
| `swarm <task>` | 蜂群调度 | exec |
| `vram status` | 查看显存状态 | exec |
| `vram switch` | 切换 GPU/CPU 模式 | exec |
| `config get/set` | 读/写 .env 配置 | exec |
| `provider add/toggle/delete/list` | 模型供应商管理 | exec |
| `evaluate novel` | 小说质量评估 | exec |
| `skills list` | 技能市场列表 | exec |
| `evolve <recipe_id>` | 配方进化 | exec |
| `serve --port 19000` | 启动 REST API | exec (后台) |

**REST API 端点**:

| 方法 | 路径 | 功能 |
|------|------|------|
| POST | `/api/route` | 路由决策 |
| POST | `/api/swarm` | 蜂群调度 |
| GET | `/api/vram` | 显存状态 |
| GET | `/api/skills` | 技能列表 |
| GET/POST | `/api/config` | 配置管理 |
| GET | `/health` | 健康检查 |

### 3.2 TriadPanel.tsx

**位置**: `triad/webui/src/TriadPanel.tsx`

**布局**: 左边 440px 聊天区 + 右边自适应监控/配置区

**右侧面板（4 个标签页）**:

1. **VRAM 调度** — 显存分区可视化、GPU/CPU 模式切换按钮
2. **模型路由** — 供应商列表、最近配置变更时间线
3. **角色配置** — 5 个角色参数展示、状态指示灯
4. **技能市场** — 已结晶配方、评分、标签

**数据流**:
```
OpenClaw WS ─→ useOpenClawWS ─→ systemStatus (3s 自动刷新)
                             ─→ configEvents (配置变更推送)
Hermes API ─→ fetch('/api/vram') (作为 WS 的补充数据源)
```

### 3.3 useOpenClawWS.ts

**位置**: `triad/webui/src/hooks/useOpenClawWS.ts`

**功能**: 对接 OpenClaw 原生 WebSocket，替换 v2.x 的 useWebSocket.ts

**特性**:
- 自动重连 + 指数退避（最多 10 次，最长 30s 间隔）
- `systemStatus` 和 `configUpdate` 事件订阅
- 连接恢复时自动请求任务历史
- 暴露 `send()` 和 `status` 给上层组件

---

## 4. 删除的代码

| 文件 | 原因 |
|------|------|
| `triad/host/openclaw/src/gateway/websocket.ts` (~400行) | OpenClaw 原生 WebSocket 替代 |
| `triad/host/openclaw/src/gateway/api.ts` (~350行) | OpenClaw 原生 REST API 替代 |
| `triad/host/openclaw/src/gateway/monitor.ts` (~200行) | Hermes API 替代监控数据 |
| `triad/webui/src/BrowserShell.tsx` | 替换为 TriadPanel.tsx |
| `triad/webui/src/hooks/useWebSocket.ts` | 替换为 useOpenClawWS.ts |
| `triad/docker-compose.hpc.yml` | 保留但标记 deprecated，新文件为 docker-compose.v3.yml |

**删除统计**: ~950 行 TypeScript（去除冗余）

---

## 5. 性能影响

| 指标 | v2.x | v3.0 | 变化 |
|------|------|------|------|
| Gateway 代码维护量 | ~950 行 TS | 0（托管给 OpenClaw）| -100% |
| 消息延迟（请求→响应） | ~50ms（自定义 Gateway）| ~20ms（OpenClaw 原生）| -60% |
| 内存占用 | +200MB（Gateway 进程）| 0（寄生在 OpenClaw）| -200MB |
| 配置修改路径 | 手动编辑文件→重启 | 对话中实时热改 | 从分钟级→秒级 |

---

## 6. 安全模型

| 操作 | 权限要求 | 确认方式 |
|------|---------|---------|
| 查看配置/状态 | 无需额外权限 | 自动执行 |
| 修改 .env 配置 | exec 权限 | 自动执行 |
| 添加/停用供应商 | exec 权限 | 自动执行 |
| 切换 VRAM 模式 | exec 权限 | 自动执行 |
| 重启 Gateway | gateway.restart 权限 | 用户确认 |
| 删除供应商 | exec 权限 | 用户确认 |
| 修改 API Key | 拒绝 | 要求用户手动操作 |

---

## 7. 迁移检查清单

- [ ] 移除 `triad/host/openclaw/` 目录
- [ ] 使用 `docker-compose.v3.yml` 替代 `docker-compose.hpc.yml`
- [ ] 将 `TRIAD_SYSTEM_PROMPT.md` 加载到 OpenClaw 的 SOUL.md
- [ ] 构建新的 WebUI: `cd triad/webui && npm install && npm run build`
- [ ] WebUI 连接地址改为 OpenClaw Gateway 端口（默认 40088）
- [ ] 启动 Hermes Skill API: `python triad/skills/hermes_skill.py serve`
- [ ] 确认 `providers.json` / `.env` 路径正确（`~/.triad/memory/config/`）
- [ ] 测试：对话中说 "查看 VRAM 状态"，确认返回正确结果

---

## 8. 未来路线图 (v3.1+)

- [ ] 语音输入/输出集成（Whisper + TTS）
- [ ] CodeCurator 真实 AST 静态检查
- [ ] 配方发布到 ClawHub 社区市场
- [ ] 多 GPU 支持（nvlink / MIG 分区）
- [ ] 云端同步备份（可选，加密传输）

---

## 9. 致谢

Triad v3.0 的架构决策得益于：
- OpenClaw 项目提供的生产级网关基础设施
- v2.x 的 53 个 bug 修复为重构奠定了稳定基础
- 社区对"对话即运维"理念的持续反馈

---

> **Triad v3.0 — 寄生进化，对话即运维。**
# Triad v3.1 技术白皮书增补 — Code Agent

## v3.1 新增：代码 Agent 桥接

v3.1 集成 CheetahClaws as code execution engine. Key additions:
- code_agent_bridge.py — 3-tier delegation
- manager_executor.py — hierarchical scheduling
- memory_system.py — 3-layer memory
- permission_gate.py — 5-layer permission pipeline
