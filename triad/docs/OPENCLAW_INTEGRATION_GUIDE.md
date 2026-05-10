# Triad v3.0 OpenClaw 集成指南

## 1. 架构变化

```
v2.x:  Triad Gateway → Hermes → llama-server / ComfyUI
v3.0:  OpenClaw Gateway → Hermes Skill → llama-server / ComfyUI
```

**核心理念变化：**
- v2.x: Triad 自己写了完整的消息网关，和 OpenClaw 功能重叠
- v3.0: Triad 退化（升级）为一个 OpenClaw 插件，专注在 Hermes 能力层

## 2. 新增文件

| 文件 | 说明 |
|------|------|
| `triad/skills/hermes_skill.py` | Hermes 能力 CLI + REST API，OpenClaw 可直接调用 |
| `triad/skills/TRIAD_SYSTEM_PROMPT.md` | 给模型的 System Prompt，授权直接改配置 |
| `triad/webui/src/hooks/useOpenClawWS.ts` | 新的 WebSocket Hook，对接 OpenClaw 原生 WS |
| `triad/webui/src/TriadPanel.tsx` | 新主界面，左聊天 + 右监控配置 |
| `triad/docker-compose.v3.yml` | 精简版 Docker 编排，移除冗余 Gateway |

## 3. 部署方式

### 3.1 前置条件

```bash
# 1. OpenClaw Gateway 必须已运行
openclaw gateway status

# 2. 确保模型可访问
python triad/skills/hermes_skill.py vram status

# 3. 安装依赖
pip install -r triad/requirements.txt
```

### 3.2 启动 Hermes Skill API（可选）

```bash
# 方式 A: 直接运行
python triad/skills/hermes_skill.py serve --port 19000

# 方式 B: Docker
docker compose -f triad/docker-compose.v3.yml up -d hermes-skill-api
```

### 3.3 加载 System Prompt

把 `triad/skills/TRIAD_SYSTEM_PROMPT.md` 的内容放到 OpenClaw 的 SOUL.md 或 skill 中：

```bash
# 复制到 OpenClaw workspace
cat triad/skills/TRIAD_SYSTEM_PROMPT.md >> ~/.openclaw/workspace/SOUL.md
```

### 3.4 构建 WebUI

```bash
cd triad/webui
npm install
npm run build
# 产物在 dist/ 目录
```

### 3.5 OpenClaw 连接 WebUI

WebUI 默认连接 `ws://localhost:40088/ws`（OpenClaw Gateway 默认端口）。
如需更改，修改 `useOpenClawWS.ts` 中的 URL。

## 4. 模型如何"动手"

模型在对话中通过 **OpenClaw 原生工具** 操作 Triad：

```python
# 模型内部调用链（用户不可见）

# 用户: "VRAM 不够，LLM 切 CPU"
# ↓ 模型调用:
exec("python triad/skills/hermes_skill.py vram switch --mode cpu")
exec("docker update --cpuset-cpus=0-31 triad-llama-server")

# 用户: "给 novelist 温度调到 0.9"
# ↓ 模型调用:
read("triad/mind/prompts/roles.py")  # 先看当前值
edit("triad/mind/prompts/roles.py", ...)  # 修改

# 用户: "添加 Kimi"
# ↓ 模型调用:
exec("python triad/skills/hermes_skill.py provider add --id kimi --name Kimi --base-url https://api.moonshot.cn/v1")

# 用户: "重启 Gateway"
# ↓ 模型调用:
gateway.restart()
```

## 5. 从 v2.x 迁移

### 5.1 数据迁移

```bash
# 无需迁移。providers.json、.env、技能配方等数据格式不变。
```

### 5.2 配置变更

```bash
# v2.x: Triad 自己管 Gateway
# v3.0: OpenClaw 管 Gateway，Triad 只管 Hermes

# 移除 v2.x 的 Gateway 配置
rm -rf triad/host/openclaw

# 更新 docker-compose
# 使用 docker-compose.v3.yml 代替 docker-compose.hpc.yml
```

### 5.3 行为变化

| 场景 | v2.x | v3.0 |
|------|------|------|
| 用户发消息 | Triad Gateway 收→Hermes处理 | OpenClaw Gateway 收→模型处理→调用Hermes |
| 模型改配置 | 不支持 | ✅ 直接改 |
| 蜂群调度 | Triad Gateway 触发 | 模型触发 Hermes Skill |
| 断连恢复 | Triad 自己实现 | OpenClaw 原生支持 |
| 系统监控 | Triad monitor.ts | OpenClaw 探针 + Hermes API |

## 6. 开发者笔记

### 6.1 添加新的 Hermes 工具

在 `hermes_skill.py` 中添加新函数，然后更新 CLI 子命令：

```python
async def your_new_tool(param: str) -> dict:
    # 实现
    return {"success": True, "result": "..."}

# 在 CLI main() 中添加子命令
p_new = subparsers.add_parser("your-tool", help="你的工具")
p_new.add_argument("param")
```

### 6.2 WebUI 扩展

在 `TriadPanel.tsx` 的 `<Tabs>` 中添加新标签页即可。

### 6.3 调试

```bash
# 测试 Hermes Skill CLI
python triad/skills/hermes_skill.py route "测试" --strategy AUTO

# 测试 REST API
curl -X POST http://localhost:19000/api/route \
  -H "Content-Type: application/json" \
  -d '{"prompt":"测试","strategy":"AUTO"}'

# 查看 OpenClaw Gateway 日志
openclaw gateway run --verbose
```
