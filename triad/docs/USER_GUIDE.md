# Triad 全链路内容生成工作站 — 用户指南
## 版本 v2.1 | 2026-05-02

---

## 一、Triad 是什么？

Triad 是一台运行在你本地 WSL2 环境中的**多模态 AI 工作站**。它不是网页聊天框，不是简单的"大模型套壳"，而是一个拥有三层架构的操作系统级 Agent 集群：

- **第一层（OpenClaw）**：消息网关，连接微信/Slack/浏览器，统一管理所有任务的入口
- **第二层（Hermes）**：认知大脑，负责在 Grok/Kimi/DeepSeek/Claude 等 6 个模型之间自动切换，自我进化出创作技能
- **第三层（执行层）**：Claude Code 负责改代码，ComfyUI 负责画图/做视频，llama.cpp 负责本地隐私推理

**一句话定义**：Triad = 扣子（Coze）的编排能力 + Claude Code 的代码能力 + ComfyUI 的多模态能力 + llama.cpp 的本地推理能力，全部运行在你的双路 E5 + 2080Ti 工作站上。

---

## 二、当前能做什么？（已实现功能）

### ✅ 可以直接运行的功能

| 功能 | 说明 | 操作方式 |
|------|------|---------|
| **一键环境安装** | ext4 安全检查 + 国内镜像源 + Docker 镜像拉取 + npm build | `./triad_manager.sh install` |
| **一键全栈启动** | 检查 ComfyUI → 启动 llama-server → Docker 容器群 → WSL2 网关 | `./triad_manager.sh start` |
| **一键停止** | 优雅关闭所有容器和进程，确保数据写入完毕 | `./triad_manager.sh stop` |
| **状态监控** | 容器状态 + nvidia-smi 显存 + llama 健康 + Web UI 状态 | `./triad_manager.sh status` |
| **本地 LLM 推理** | Qwen-14B Q4_K_M，~25 tok/s（GPU 模式），支持 OpenAI 兼容 API | `http://localhost:8000/v1/chat/completions` |
| **显存分时复用** | llama.cpp `-ngl` 跷跷板：渲染时切 CPU（LLM 不中断），渲染完恢复 GPU | 自动调度 |
| **WSL2 网关** | Windows 浏览器访问 `http://localhost:8080` 直达 WSL2 内部 | `wsl2_gateway.sh setup` |
| **Web UI 骨架** | 扣子风格界面：左侧对话 + 右侧 Agent 画布/VRAM 面板/配置面板 | `npm run dev` 启动 |
| **技能市场 UI** | 8 个 MCP 工具 + 6 个 Skill 的展示、搜索、Toggle 开关 | 前端已可交互 |

### ⚠️ 可以运行但有限制的功能

| 功能 | 当前状态 | 限制说明 |
|------|---------|---------|
| **模型动态路由** | 路由逻辑完成 | 需要填入 6 个厂商的 API Key 才能实际调用 |
| **小说评估** | 4 维评分体系完成 | 需要接入 LLM 实际调用才能自动评分（目前是模拟评分） |
| **角色概念图生成** | ComfyUI MCP Bridge 接口完成 | 需要配置 ComfyUI 工作流 JSON 模板 |
| **资产版本链** | URI 解析 + .meta.json 逻辑完成 | 需要实际运行才能自动管理版本 |

### ❌ 暂时还不能做的功能（ Roadmap 中）

| 功能 | 预计 Phase | 阻塞原因 |
|------|-----------|---------|
| **微信/Slack 接入** | Phase 1 | OpenClaw Gateway WebSocket 后端未实现 |
| **AI 自动评估小说** | Phase 3 | 需要接入真实 LLM 评估调用 |
| **Skill 自动固化到磁盘** | Phase 3 | SkillCrystallizer 缺少自动写入 IO |
| **Agent 多轮协作** | Phase 2 | Hermes 主循环编排器未实现 |
| **ComfyUI 工作流自动填充** | Phase 2 | 需要准备标准化的节点图 JSON 模板 |

---

## 三、诚实的使用建议

### 3.1 适合谁用？

**现在就可以用的场景**：
- 你需要一个**本地私有化**的 LLM 推理环境（隐私敏感，不上云）
- 你需要**ComfyUI + 本地 LLM 分时复用**在同一台 2080Ti 上跑
- 你需要一个**设计参考**：看看多模型动态路由、VRAM 跷跷板、自我进化 Skill 是怎么架构的

**暂时不建议的场景**：
- 你想"开箱即用"像扣子那样直接聊天——WebSocket 后端还没接好
- 你想让 AI 自动帮你写小说并自动评估质量——LLM 评估调用还没接
- 你想一键生成角色概念图——ComfyUI 工作流模板还没准备

### 3.2 推荐的探索路径

**Week 1：玩基础设施**
```bash
./triad_manager.sh install   # 看着它跑完所有安装
./triad_manager.sh start     # 看到绿色的启动面板
./triad_manager.sh status    # 看到 nvidia-smi 的显存分配
```
目标：熟悉这套系统的"发动机"和"仪表盘"。

**Week 2：玩本地 LLM**
```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen-14b-chat", "messages": [{"role": "user", "content": "你好"}]}'
```
目标：让 llama.cpp 跑起来，感受 `-ngl 99` 和 `-ngl 0` 的速度差异。

**Week 3：玩 ComfyUI**
```bash
# 启动 ComfyUI（在另一个终端）
cd ~/.triad/apps/comfyui
source ~/.triad/venvs/comfyui/bin/activate
python main.py --listen 0.0.0.0 --port 8188
```
浏览器打开 `http://localhost:8188`，手动做一个角色概念图。
目标：让 ComfyUI 跑起来，理解它和 Triad 的关系。

**Week 4：看代码，填逻辑**
- 打开 `mind/model_router.py`，找到 `_call_vendor_api()` 方法，填入你的 DeepSeek API Key
- 打开 `hand/comfyui_mcp_bridge.py`，准备一个 SDXL 工作流 JSON
- 打开 Web UI 的 `SkillMarketTab.tsx`，把 Mock 数据换成真实配置
目标：自己动手，让系统从"骨架"变成"有血有肉"。

---

## 四、快速上手（30 分钟教程）

### 4.1 准备工作

你需要：
- WSL2 Ubuntu 22.04（已安装）
- Docker Desktop for Windows（已安装，WSL2 后端模式）
- NVIDIA Docker Runtime（已安装，`nvidia-smi` 在 WSL2 中可用）
- 魔改 2080Ti 22GB（驱动正常工作）
- 网络：能访问清华源、阿里云 Docker 镜像

### 4.2 下载与解压

```bash
# 假设你已下载 triad-v2.1.tar.gz
cd ~
tar xzf triad-v2.1.tar.gz
cd triad
chmod +x triad_manager.sh
chmod +x init.sh
chmod +x bridge/wsl2_gateway.sh
```

### 4.3 一键安装（约 20-40 分钟）

```bash
./triad_manager.sh install
```

你会看到：
```
[✓] ext4 文件系统检查通过
[✓] Ubuntu 清华源已配置
[✓] Docker 阿里云镜像已配置
[✓] Docker 镜像拉取完成
[✓] Web UI npm build 完成
[✓] Qwen-14B GGUF 模型下载完成（或提示手动下载）
[✓] ComfyUI venv 安装完成
[✓] .env 文件已生成

🎉 安装完成！下一步运行：./triad_manager.sh start
```

### 4.4 一键启动

```bash
./triad_manager.sh start
```

你会看到绿色的启动面板：
```
╔═══════════════════════════════════════════════════════════╗
║              🟣 Triad Control Panel 启动成功              ║
╠═══════════════════════════════════════════════════════════╣
║  Web UI:     http://localhost:8080/panel                  ║
║  llama-server: http://localhost:8000/v1/chat/completions ║
║  ComfyUI:   http://localhost:8188                        ║
╚═══════════════════════════════════════════════════════════╝
```

### 4.5 Windows 浏览器访问

打开 Windows 浏览器，访问：
- `http://localhost:8080/panel` → Triad Control Panel（扣子风格 Web UI）
- `http://localhost:8188` → ComfyUI（节点式画图工具）

---

## 五、系统限制与已知问题（诚实清单）

### 5.1 架构级限制

| 限制 | 说明 | 预计解决 |
|------|------|---------|
| **OpenClaw 宿主层未实现** | TypeScript Gateway 代码未生成，WebSocket 后端不存在 | Phase 1（需要开发者编写） |
| **Hermes 主循环未组装** | ModelRouter、NovelCurator 各自独立，没有被同一个 orchestrator 串联 | Phase 2 |
| **云端模型调用是占位符** | `_call_vendor_api()` 需要填入真实的 HTTP 请求 | 用户自行填入 API Key |
| **Skill 自动写入未实现** | SkillCrystallizer 生成定义对象但不自动写磁盘 | Phase 3 |

### 5.2 使用级限制

| 限制 | 说明 |  workaround |
|------|------|------------|
| **Web UI 数据是 Mock** | 聊天消息、Agent 状态、VRAM 数据都是前端模拟 | 仅用于 UI 预览，不反映真实后端 |
| **ComfyUI 工作流模板为空** | `comfyui_mcp_bridge.py` 中的 JSON 模板未填充 | 需要手动导出 ComfyUI 工作流并填入 |
| **国内网络下载可能超时** | HuggingFace 模型下载依赖 hf-mirror.com | 手动 aria2c 下载后放入 ~/.triad/models/ |
| **Docker 镜像可能拉取失败** | ghcr.io/ggerganov/llama.cpp 可能被墙 | 使用阿里云镜像或手动构建 |

### 5.3 性能预期

| 场景 | 预期性能 | 备注 |
|------|---------|------|
| llama-server GPU 模式 | ~25 tok/s | Qwen-14B Q4_K_M，-ngl 99 |
| llama-server CPU 模式 | ~5-8 tok/s | 双路 E5 48 线程，-ngl 0，-t 32 |
| 显存切换 GPU→CPU | <3 秒 | SIGTERM + 进程重启 |
| 显存切换 CPU→GPU | <5 秒 | mmap 热映射 |
| ComfyUI SDXL 1024x1024 | ~60-120 秒 | 50 steps，2080Ti 22GB |
| Web UI 启动 | <3 秒 | Vite 开发服务器 |

---

## 六、Roadmap（开发路线图）

### Phase 0：基础设施验证（已完成 ✓）
- [x] Docker Compose 编排
- [x] llama.cpp 显存跷跷板
- [x] WSL2 部署脚本
- [x] Web UI 骨架

### Phase 1：宿主层与通信（当前缺口）
- [ ] OpenClaw TypeScript Gateway 实现
- [ ] WebSocket `/ws/tasks` 端点
- [ ] ACP gRPC 传输层
- [ ] 微信/Slack 渠道接入

### Phase 2：认知层串联（当前缺口）
- [ ] Hermes 主循环编排器（串联 ModelRouter → Curator → 执行器）
- [ ] 云端模型 API 调用层（填入 6 厂商 API Key）
- [ ] ErrorClassifier 独立模块
- [ ] 上下文引擎压缩测试

### Phase 3：执行层闭合（当前缺口）
- [ ] ComfyUI 标准化工作流 JSON 模板（8 个模板）
- [ ] SkillCrystallizer 自动写入磁盘
- [ ] Skill 加载器与执行器
- [ ] NovelCurator 真实 LLM 评估接入

### Phase 4：生态扩展（未来）
- [ ] ClawHub 官方技能导入 API
- [ ] MCP Server 一键安装（npm install 自动化）
- [ ] 多用户 ClawPod 隔离
- [ ] 云端同步与备份

---

## 七、常见问题（FAQ）

**Q: 这套系统和扣子（Coze）有什么区别？**
A: 扣子是"云端 SaaS 产品"，Triad 是"本地工作站操作系统"。扣子的 Agent 跑在字节的服务器上，Triad 的 Agent 跑在你的 E5 + 2080Ti 上。扣子不需要你写代码，Triad 需要你自己填 API Key 和调工作流。

**Q: 我需要会编程才能用吗？**
A: 第一阶段（install/start/status）不需要编程，跟着脚本跑就行。第二阶段（填 API Key、调工作流）需要基础的命令行和 JSON 编辑能力。如果你完全不想碰代码，建议等 Phase 1 完成后再用。

**Q: 为什么不能直接聊天？**
A: WebSocket 后端还没写好。当前 Web UI 是"视觉预览版"，能看到界面长什么样，但点击发送按钮不会真的调用模型。需要等 OpenClaw Gateway 实现后才能真聊天。

**Q: 我的 2080Ti 是 11GB 不是 22GB，能用吗？**
A: 能用，但需要调整显存分区。llama-server 用 Qwen-7B Q4  instead of Qwen-14B，ComfyUI 用 SD1.5 instead of SDXL。docker-compose.hpc.yml 中的内存限制也需要调小。

**Q: 为什么不用 vLLM 而用 llama.cpp？**
A: vLLM 预先锁死显存池，与 ComfyUI 争夺 22GB 时很僵硬。llama.cpp 的 `-ngl` 参数可以精确控制 GPU 层数，渲染时切到 CPU（LLM 不中断），渲染完切回 GPU。而且 GGUF 模型生态远多于 AWQ/GPTQ。

**Q: 这套系统安全吗？**
A: 相对安全。所有数据（对话记录、生成图像、记忆文件）都存在你的本地 WSL2 ext4 分区，不上云。但注意：如果你启用了 Brave Search/GitHub 等 MCP Server，搜索内容和代码片段会发送到对应厂商的 API。

---

## 八、支持与反馈

当前 Triad v2.1 是一个**开源架构原型**，没有官方客服。你可以：
1. 阅读 `docs/` 目录下的 12 份技术文档理解设计思路
2. 修改 `mind/`、`hand/`、`webui/` 目录下的代码填入你自己的逻辑
3. 参考 `triad_manager.sh` 的日志输出排查部署问题

**核心文件速查**：
- 部署问题 → `triad_manager.sh`、`init.sh`
- 显存调度 → `hand/vram_scheduler_llama.py`
- 模型路由 → `mind/model_router.py`
- 小说评估 → `mind/novel_curator.py`
- 前端界面 → `webui/src/App.tsx`
- 技能市场 → `webui/src/components/ConfigPanel/SkillMarketTab.tsx`

---

*本文档面向 Triad v2.1 用户，基于系统真实状态编写，不夸大、不隐瞒。*