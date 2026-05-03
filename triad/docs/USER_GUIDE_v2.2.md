# Triad Station v2.2 用户指南
## 多标签工作台 · 角色路由 · 实时探针 — 使用手册

**版本**: v2.2  
**代号**: Lobster Station  
**日期**: 2026-05-03  

---

## 一、Triad Station 是什么？

Triad Station 是一台运行在你本地 WSL2 环境中的**多模态 AI 工作站**。它不是网页聊天框，而是一个拥有三层架构的操作系统级 Agent 集群：

- **🦞 龙虾控制台**：聊天、Agent 可视化、VRAM 监控、模型配置、技能市场
- **🎨 ComfyUI 画布**：节点式 AI 绘画、角色概念图、视频生成
- **📊 系统监控**：GPU 显存、Docker 容器、llama-server 状态、CPU/内存

**核心特性**：
- **@角色指令**：输入 `@novelist` 或 `@code_engineer` 切换专业角色
- **动态模型**：不限于 6 个厂商，支持无限添加模型，Web UI 直接配置
- **显存跷跷板**：llama.cpp `-ngl` 参数实现 GPU/CPU 自动切换，渲染时 LLM 不中断
- **一键部署**：`./triad_manager.sh install` 全自动安装环境

---

## 二、界面导览

### 2.1 顶部导航栏

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 🦞 Triad Station  v2.2          [🦞龙虾] [🎨ComfyUI] [📊监控]  [🔴系统] [⚙️] │
└─────────────────────────────────────────────────────────────────────────────┘
```

| 按钮 | 说明 |
|------|------|
| 🦞 龙虾 | 主控制台：聊天、Agent 画布、配置面板 |
| 🎨 ComfyUI | 打开 ComfyUI 节点式绘画界面 |
| 📊 监控 | 实时查看 GPU/容器/模型状态 |
| 🔴/🟢 系统 | 系统健康状态指示灯 |
| ⚙️ | 全局设置入口 |

**切换 Tab 时状态不丢失**：
- 切到 ComfyUI 画图时，龙虾的 WebSocket 在后台继续运行
- 切回龙虾时，最新消息立即显示
- ComfyUI 的渲染进度不会被中断（iframe 永不卸载）

### 2.2 龙虾控制台（Tab 1）

```
┌──────────────────────┬──────────────────────────────────────────────────┐
│  💬 对话面板          │  🎛️ 右侧工作区                                    │
│                      │                                                   │
│  历史消息              │  ┌─────────────┐  ┌─────────────┐  ┌─────────┐ │
│  ──────────────────   │  │ Agent 画布   │  │ VRAM 面板   │  │ 配置    │ │
│  [用户] 帮我写小说    │  │ 模型路由图   │  │ 22GB 显存条 │  │ 面板    │ │
│  [AI] 好的，请稍等   │  │             │  │             │  │         │ │
│  [进度] 分析中...     │  └─────────────┘  └─────────────┘  └─────────┘ │
│  [进度] 评估中...     │                                                   │
│                      │  当前 Tab: [模型配置] [技能市场] [审计日志]        │
│  [输入框]            │                                                   │
│  [@角色 ▼] [发送]    │                                                   │
└──────────────────────┴──────────────────────────────────────────────────┘
```

**输入框快捷指令**：
- 输入 `@` 弹出角色选择器
- `@code_engineer 帮我重构这个组件`
- `@novelist 写第一章，主角是个黑客`
- `@art_director 设计赛博朋克女主角`
- 不带 `@` → 走默认自动路由

### 2.3 ComfyUI 画布（Tab 2）

直接嵌入 ComfyUI 网页界面（`http://localhost:8188`）。

**使用流程**：
1. 在 ComfyUI 中连一个文生图工作流（Checkpoint → CLIP → KSampler → VAE → Save）
2. 点击 **Save (API Format)** 导出 JSON
3. 重命名为 `character_concept_api.json` 放到 `hand/` 目录
4. 回到龙虾控制台，输入 `@art_director 生成概念图`
5. 系统自动加载 JSON、注入 Prompt、执行生成、下载图像

### 2.4 系统监控（Tab 3）

```
┌─────────────────────────────────────────────────────────┐
│ 📊 系统监控                                              │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ ⚡ GPU 显存 (NVIDIA RTX 2080Ti)                         │
│ ████████████████████░░░░░░░░░░  11GB / 22GB (50%)     │
│                                                         │
│ 🖥️ Docker 容器                                           │
│   openclaw      🟢 Up 2 hours                           │
│   hermes        🟢 Up 2 hours                           │
│   llama-server  🟢 Up 2 hours (GPU 模式)                │
│                                                         │
│ 🧠 llama-server     🟢 运行中 (GPU 模式, -ngl 99)        │
│                                                         │
│ ┌─────────────┐  ┌─────────────┐                       │
│ │ CPU (48核)   │  │ 内存         │                       │
│ │ 23.5%        │  │ 45.2%        │                       │
│ └─────────────┘  └─────────────┘                       │
│                                                         │
│ [每 3 秒自动刷新]                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 三、角色系统使用指南

### 3.1 内置角色速查表

| 角色 | 指令 | 擅长什么 | 用什么模型 | 禁止做什么 |
|------|------|---------|-----------|-----------|
| **代码工程师** | `@code_engineer` | 代码重构、Bug 修复、全栈开发 | DeepSeek/Qwen | 生成图像、访问无关网页 |
| **前端工程师** | `@frontend_engineer` | React/TypeScript/Tailwind | DeepSeek/Qwen | 修改后端 API、操作数据库 |
| **小说家** | `@novelist` | 现实主义小说、人物塑造、伏笔设计 | Grok/Kimi | 执行代码、生成图像 |
| **美术导演** | `@art_director` | 概念图、ComfyUI 工作流、角色一致性 | Grok/Gemini | 写代码、操作 git |
| **DevOps** | `@devops_engineer` | Docker/K8s、监控告警、自动化 | DeepSeek/Claude | 修改业务代码、写小说 |
| **深度调研蜂群** | `@deep_research_swarm` | 深度技术调研、竞品分析、多源交叉验证 | DeepSeek/Grok + 本地模型 | 单体执行 |
| **代码审查蜂群** | `@code_review_swarm` | 代码审查 + Bug 修复 + 规范检查 | DeepSeek/Qwen | 单体执行 |

### 3.2 角色使用示例

**写小说**：
```
@novelist 帮我写第一章。
要求：主角是一位前企业黑客，性格外冷内热，擅长社交工程。
风格：赛博朋克现实主义，节奏慢热，每章 3000 字。
```

**重构代码**：
```
@code_engineer 帮我重构 src/components/ChatPanel/MessageList.tsx
问题：组件超过 300 行，需要拆分成 smaller components。
要求：保持现有功能，添加类型定义，写单元测试。
```

**生成概念图**：
```
@art_director 设计上面那位黑客女主角的概念图。
外貌：银发紫瞳，身材纤细，左臂有神经接口疤痕。
风格：赛博朋克，电影级光影。
```

**部署服务**：
```
@devops_engineer 帮我写一个 Docker Compose 配置，
部署一个 Next.js 应用 + PostgreSQL + Redis。
要求：生产环境可用，带健康检查和自动重启。
```

### 3.3 蜂群模式使用示例

**深度调研**：
```
@deep_research_swarm 调研 Rust vs Go 在 AI 推理引擎中的优劣
要求：对比内存安全、并发模型、生态成熟度、推理性能
输出：结构化技术报告，包含优劣势矩阵和选型建议
```

预期行为：
```
[进度] 🎭 角色模式: 深度调研蜂群 (deep_research_swarm)
[进度] 🔥 检测到蜂群模式，正在准备子代理集群...
[进度] 🐝 蜂群就位: 3 个 Agent（研究员, 写手, 审校）
[进度] 研究员 正在处理...
[进度] 写手 正在处理...
[进度] 审校 正在处理...
[进度] ✅ 蜂群完成: 3/3 成功, 总 token=12580, 总延迟=2847ms
[进度] ✨ 蜂群配方已结晶: 深度调研_Rust_vs_Go_143052.md
[结果] （聚合后的技术报告...）
```

### 3.4 错误处理

如果输入了不存在的角色：
```
@unknown 写代码

→ 系统回复：
⚠️ 未知角色 'unknown'
可用角色: code_engineer, frontend_engineer, novelist, art_director, devops_engineer
→ 自动降级为默认路由（AUTO 策略）
```

如果蜂群中某个 Agent 失败：
```
→ 系统行为：
[警告] 研究员 执行失败: HTTP timeout
[进度] ✅ 蜂群完成: 2/3 成功
→ 聚合结果时跳过失败 Agent，用成功 Agent 的结果拼接
→ 返回完整结果（不会整体失败）
```

---

## 四、动态模型注册表使用指南

### 4.1 添加新模型

在 Web UI → 配置面板 → 模型配置中心：

1. 点击右侧 **"➕ 添加新模型"**
2. 填写表单：
   - 模型标识：`qwen-local`（唯一 ID）
   - 显示名称：`Qwen 14B 本地`
   - API URL：`http://127.0.0.1:8000/v1/chat/completions`
   - API Key：`sk-anything`（本地模型可随便填）
   - 上下文窗口：`8192`
   - 能力标签：`[x] reasoning [x] code [ ] creative`
3. 点击 **🧪 测试连接**
   - 成功："连接成功，延迟 45ms"
   - 失败："HTTP 401 Unauthorized"
4. 点击 **💾 保存**

### 4.2 模型路由逻辑

添加模型时选择的能力标签，决定了它会被哪种策略调用：

| 策略 | 匹配标签 | 典型场景 |
|------|---------|---------|
| CREATIVE | creative, brainstorming | 写小说、脑暴 |
| REASONING | reasoning, code, logic | 代码、逻辑推演 |
| LONGFORM | longform, context | 长文本、世界观 |
| CHAT | chat, chinese | 日常对话 |
| REVIEW | reasoning, review | 审查、一致性检查 |

**示例**：
- 你添加的模型打了 `reasoning` 和 `code` 标签
- 用户输入 `@code_engineer 帮我修 Bug`
- 系统自动选择你的模型（如果它排在候选列表最前面）

### 4.3 启用/停用

列表左侧每个模型卡片都有 Switch 开关：
- 🟢 ON：模型可被路由选中
- ⚪ OFF：模型暂时禁用（保留配置，不参与路由）

---

## 五、快速上手（30 分钟教程）

### 5.1 准备工作

- WSL2 Ubuntu 22.04
- Docker Desktop（WSL2 后端模式）
- NVIDIA Docker Runtime（`nvidia-smi` 在 WSL2 可用）
- 魔改 2080Ti 22GB（驱动正常）
- 网络：能访问清华源、阿里云 Docker 镜像

### 5.2 下载与安装

```bash
# 1. 下载 triad-v2.2.tar.gz 并解压
cd ~
tar xzf triad-v2.2.tar.gz
cd triad
chmod +x triad_manager.sh

# 2. 一键安装（20-40 分钟）
./triad_manager.sh install
# 输出：
# [✓] ext4 检查通过
# [✓] 清华源/阿里云镜像/npm 淘宝源已配置
# [✓] Docker 镜像拉取完成
# [✓] Web UI 构建完成
# [✓] Qwen GGUF 模型就绪
# [✓] ComfyUI venv 安装完成
# [✓] .env 已生成

# 3. 填入 API Key（必须）
cp .env.example .env
nano .env
# 填入你的 Grok/DeepSeek/Kimi/Claude API Key（不需要的可留空）

# 4. 一键启动
./triad_manager.sh start
```

### 5.3 启动成功面板

```
╔═══════════════════════════════════════════════════════════╗
║              🟣 Triad Station v2.2 启动成功              ║
╠═══════════════════════════════════════════════════════════╣
║  Web UI:     http://localhost:8080/panel                  ║
║  Gateway:    ws://localhost:8080/ws/tasks               ║
║  llama-server: http://localhost:8000/v1/chat/completions ║
║  ComfyUI:   http://localhost:8188                        ║
╠═══════════════════════════════════════════════════════════╣
║  标签页: [🦞 龙虾控制台] [🎨 ComfyUI] [📊 系统监控]          ║
╠═══════════════════════════════════════════════════════════╣
║  VRAM 初始:                                               ║
║    [2GB Embed][████████████][9GB LLM GPU]               ║
║    [░░░░░░░░░░░░░░][9GB 空闲][2GB 系统]                  ║
╚═══════════════════════════════════════════════════════════╝
```

### 5.4 Windows 浏览器访问

打开 Windows 浏览器：
- `http://localhost:8080/panel` → 龙虾控制台
- 顶部 Tab 切换：🦞 / 🎨 / 📊

### 5.5 第一次对话

```
[输入框] @novelist 写一段 500 字的赛博朋克开场
[发送]

[进度] 🎭 角色模式: 小说家 (novelist)
[进度] 调用 Grok 生成内容...
[进度] 评估中: 人设 8.5/10, 逻辑 9.0/10
[进度] ✨ 新技能已固化: cyberpunk_opening_v1
[结果] （小说文本...）
```

### 5.6 第一次蜂群对话

```
[输入框] @deep_research_swarm 调研 Triad 项目的竞品方案
[发送]

[进度] 🎭 角色模式: 深度调研蜂群 (deep_research_swarm)
[进度] 🔥 触发蜂群模式，正在拉起子代理...
[进度] 🐝 蜂群就位: 3 个 Agent（研究员, 写手, 审校）
[进度] 研究员 正在处理... (DeepSeek API)
[进度] 写手 正在处理... (Grok API)
[进度] 审校 正在处理... (本地模型)
[进度] ✅ 蜂群完成: 3/3 成功, 总 token=15620, 总延迟=3200ms
[进度] ✨ 蜂群配方已结晶: 调研_Triad_竞品_143052.md
[结果] （完整竞品分析报告...）
```

**蜂群结果的 3 个 Agent 输出如何查看？**
当前版本蜂群结果自动聚合为统一文本输出。如需查看每个 Agent 的独立输出，可查看返回的 `swarm_stats` 字段（API 调用时）或查看日志文件。

---

## 六、常见问题（FAQ）

**Q: 这套系统和扣子（Coze）有什么区别？**
A: 扣子是云端 SaaS，模型跑在字节服务器上。Triad 是本地工作站，所有数据和计算都在你的双路 E5 + 2080Ti 上，隐私完全可控。

**Q: 我需要会编程才能用吗？**
A: install/start/status 三命令不需要编程。填 API Key 和用 `@角色` 指令需要基础命令行操作，但不需要写代码。

**Q: 为什么 ComfyUI 要单独放在一个 Tab 里，不嵌入右侧面板？**
A: ComfyUI 是一个全屏画布应用，节点拖拽、连线、右键菜单都需要大面积空间。强行嵌入右侧面板会导致节点图缩成邮票大小、右键菜单被裁剪。Tab 切换是正确的设计选择。

**Q: 角色系统是怎么工作的？**
A: 输入 `@novelist` 时，系统会：
1. 匹配到小说家角色的 System Prompt
2. 把 Prompt 注入到请求上下文
3. 限制只能使用 `read/write/memory_search` 工具（不能执行代码或画图）
4. 路由到擅长创意的模型（Grok/Kimi）

**Q: 我可以添加自己的角色吗？**
A: 当前需要在 `mind/prompts/roles.py` 中手动添加 RoleConfig。未来版本会支持 Web UI 直接添加角色。

**Q: 我的 2080Ti 是 11GB 不是 22GB，能用吗？**
A: 能用。把模型从 Qwen-14B 换成 Qwen-7B，ComfyUI 用 SD1.5 而不是 SDXL。`docker-compose.hpc.yml` 中的内存限制也需要调小。

**Q: 系统监控为什么不显示温度？**
A: 部分魔改卡或 WSL2 环境下，`nvidia-smi` 可能读不到温度。这是硬件/驱动限制，不影响显存和利用率显示。

**Q: 蜂群模式和普通 @角色 有什么区别？**
A: 普通 @角色 派一个"轻骑兵"（单体大模型）快打快回。蜂群模式派出一支"特种部队"——研究员去搜资料、写手去整理、审校去把关，3 个人同时开工，最后结果自动拼成一份完整报告。

**Q: 蜂群会消耗更多 API Token 吗？**
A: 是的。3 个 Agent 各调一次模型，总 token 数约是单体的 2-3 倍。但产出的深度和准确度通常也成比例提升。本地模型（llama-server）参与的 Agent 不消耗云端 API Key。

**Q: 蜂群配方结晶是什么？**
A: 当蜂群任务评分 >= 8.0 时，系统会自动把这次成功的"人员配置 + 工具顺序 + 聚合策略"保存为 Markdown 文件到 `~/.triad/memory/skills/self-evolved/`。下次遇到类似任务，可以直接复用这套配方。

**Q: 为什么我写代码时没有看到"质量评估"进度条？**
A: Triad 的评估是**动态路由**的——小说任务才会进入 4 维文学评估（人设/逻辑/文风/情感），代码任务直接跳过文学评估（否则给代码打"人设分"没有意义）。未来版本会接入 AST 静态检查作为代码评估。

**Q: 我输入 `@novelist 写第一章`，系统会自动画图吗？**
A: **不会。** Triad 的多模态触发也是动态路由的——只有 `@art_director` 角色或输入中明确包含"画图"/"生成图像"等指令时，才会启动 ComfyUI 并切换 VRAM。写小说不会浪费你的显存。

**Q: 动态评估和动态多模态是怎么判断的？**
A: 系统从 5 个维度依次判断：
1. 角色有没有显式标记 `eval_strategy` / `multimodal_strategy`？
2. `task_type` 是什么？（novel / code / multimodal）
3. 角色 ID 的语义（含 "novel" → 小说评估；含 "art" → 触发画图）
4. 用户输入关键词（"画图" / "generate image" / "concept art"）
5. 生成内容的启发式推断（前 2000 字含 3+ 小说标记 → 降级评估）

如果都没命中，就 `bypass`（静默跳过），不浪费时间和资源。

**Q: 刷新浏览器后任务会丢失吗？**
A: **不会。** v2.2 已修复此问题。Gateway 会记录每个任务的阶段历史和最终结果到内存（最近 100 个任务）。重新连接后发送 `{"action": "recover_tasks"}`，服务器会把所有任务历史推送回来，UI 自动重建进度界面。

**Q: 蜂群任务会不会把显存搞崩？**
A: **不会。** v2.2 已引入 VRAM 全局锁：`vram_scheduler` 维护活跃 LLM 推理计数器。如果有 Agent 正在调用本地模型，其他 Agent 请求 VRAM 切换时会阻塞等待，直到所有推理安全完成。不会出现"写到一半被 SIGTERM 切断"的情况。

**Q: 3 个 Agent 各写 4000 字，合并后会不会超过上下文上限？**
A: **不会溢出。** v2.2 已引入 Map-Reduce 压缩：聚合结果超过 `max_output_tokens`（默认 6000）时，系统会自动调用一个轻量摘要 Agent，把 12000 字压缩到 6000 字以内，然后再传给下游节点。

---

## 七、Roadmap

### 已实现（v2.2）
- [x] 多标签工作台（龙虾/ComfyUI/监控）
- [x] 单 Agent 多角色（5 个内置角色）
- [x] 动态模型注册表（无限添加，Web UI 管理）
- [x] 系统监控探针（GPU/容器/llama/CPU/内存）
- [x] llama.cpp 显存跷跷板
- [x] 一键部署脚本
- [x] 蜂群调度器（SwarmExecutor）— 多 Agent 并发协作
- [x] 技能结晶器（SkillCrystallizer）— 成功配方自动固化与进化
- [x] 动态评估路由（Dynamic Evaluation）— 小说评估 / 代码跳过 / 通用 bypass
- [x] 动态多模态路由（Dynamic Multimodal）— art_director 自动触发 / 关键词检测 / 无需求跳过
- [x] VRAM 死锁防护（推理引用计数 + 全局锁）
- [x] 上下文压缩（Map-Reduce Token 上限）
- [x] 配方语义去重（适者生存，不野蛮繁殖）
- [x] WebSocket 断连恢复（任务状态持久化）

### 下一步（v2.3）
- [ ] Web UI 直接添加自定义角色（不用改代码）
- [x] ✅ 已完成：多 Agent 并行协作（@frontend + @backend 同时工作）— 已由蜂群调度器实现
- [ ] 语音输入/输出集成
- [ ] 云端同步备份（可选）

---

## 八、核心文件速查

| 功能 | 文件 |
|------|------|
| 部署问题 | `triad_manager.sh`, `init.sh` |
| 多标签外壳 | `webui/src/BrowserShell.tsx` |
| 角色定义 | `mind/prompts/roles.py` |
| 角色路由 | `mind/model_router.py` (parse_role, execute_with_role) |
| 主编排器 | `mind/hermes_orchestrator.py` (process_task: 动态5步 + 蜂群分叉 + 动态评估/多模态) |
| 蜂群调度 | `mind/swarm_orchestrator.py` (SwarmExecutor, TemporaryAgent) |
| 技能进化 | `mind/skill_crystallizer.py` (SkillCrystallizer, SwarmRecipe) |
| 动态模型 | `mind/model_registry.py`, `webui/src/components/ConfigPanel/ProviderManager.tsx` |
| 系统监控 | `host/openclaw/src/gateway/monitor.ts`, `webui/src/components/SystemMonitorTab.tsx` |
| 显存调度 | `hand/vram_scheduler_llama.py` |
| 小说评估 | `mind/novel_curator.py` |
| ComfyUI 注入 | `hand/comfyui_mcp_bridge.py` |

---

*本文档面向 Triad Station v2.2 用户，基于系统真实状态编写。*