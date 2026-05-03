# Triad v2.2 🦞 Lobster Station

## 项目概述

Triad 是运行在本地 WSL2 环境中的**三层架构 AI 智能体操作系统**，代号 **Lobster Station（龙虾工作站）**。

它不是网页聊天框，而是一台拥有蜂群调度能力的生产级 AI 工作站：

- 🦞 **龙虾控制台**：聊天、Agent 可视化、VRAM 监控、模型配置
- 🎨 **ComfyUI 画布**：节点式 AI 绘画、角色概念图
- 📊 **系统监控**：GPU 显存、Docker 容器、模型状态、CPU/内存

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                    🦞 浏览器多标签工作台                         │
│       [龙虾控制台]  [ComfyUI画布]  [系统监控]                    │
├──────────────────────────────────────────────────────────────┤
│                    OpenClaw Gateway (Node.js)                  │
│       WebSocket Server + REST API + 系统探针                    │
├──────────────────────────────────────────────────────────────┤
│                    Hermes 认知编排层 (Python)                   │
│       动态角色路由 · 蜂群调度 · 技能进化 · 动态评估/多模态        │
├──────────────────────────────────────────────────────────────┤
│                    多模态执行层                                 │
│       llama.cpp (-ngl跷跷板) · ComfyUI · VRAM调度器            │
└──────────────────────────────────────────────────────────────┘
```

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **@角色系统** | `@novelist` / `@code_engineer` / `@art_director` 等 5 个内置角色，不同角色有不同 System Prompt、模型偏好和工具权限 |
| **无极蜂群** | `@deep_research_swarm` 触发多 Agent 并发协作（研究员+写手+审校），`asyncio.gather` 并发执行 |
| **动态模型路由** | 不限厂商，无限添加模型，Web UI 直接管理，tags 匹配自动路由 |
| **显存跷跷板** | llama.cpp `-ngl 99↔0` GPU/CPU 自动切换，渲染时 LLM 不中断 |
| **技能进化** | 高分任务自动固化配方为 Markdown+YAML，支持语义去重和适者生存 |
| **动态评估** | 小说任务进 4 维评估，代码任务跳过文学打分，通用任务直接 bypass |
| **动态多模态** | 仅 `art_director` 角色或明确画图指令才触发 ComfyUI，避免浪费 VRAM |
| **断连恢复** | WebSocket 断开后重连自动恢复任务历史和进度 |

---

## 硬件基准

- **CPU**: 双路 Intel Xeon E5-2673v3 (24C/48T)
- **GPU**: 魔改 NVIDIA RTX 2080Ti 22GB
- **内存**: 64GB DDR4 ECC
- **OS**: WSL2 Ubuntu 22.04

---

## 快速开始

```bash
# 1. 解压
# 2. 一键安装（20-40 分钟）
./triad_manager.sh install

# 3. 填入 API Key
cp .env.example .env
nano .env  # 填入 Grok/DeepSeek/Kimi/Claude Key

# 4. 一键启动
./triad_manager.sh start

# 5. 浏览器访问
open http://localhost:8080/panel
```

---

## 项目统计

- **文件数**: 79 个
- **代码行**: ~16,500 行（Python/TypeScript/TSX/Bash/YAML）
- **文档**: 技术白皮书 + 用户指南 + 续接指南

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + Vite + Tailwind CSS + shadcn/ui |
| Gateway | Node.js + TypeScript + WebSocket + Express |
| 认知层 | Python 3.10 + asyncio + dataclasses + aiohttp |
| 执行层 | llama.cpp (Docker) + ComfyUI (Python venv) |
| 部署 | Docker Compose + Bash |

---

*Triad v2.2 — 本地智能体操作系统，数据不出站，算力全掌控。*
