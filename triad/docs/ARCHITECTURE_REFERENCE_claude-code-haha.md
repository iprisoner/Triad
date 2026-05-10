# Claude Code 可运行复刻分析 — claude-code-haha (2026-05-10)

## 仓库
github.com/suyancc/claude-code-haha

## 核心价值
第一个让泄露的 Claude Code 源码**真正跑起来**的修复版。
修复了 6 个阻塞问题：TUI 不启动 / 启动卡死 / print 卡死 / Enter 无响应 / setup 跳过 / 资源缺失。

## 泄露源码真实架构

```
src/
├── entrypoints/cli.tsx      # CLI 主入口
├── main.tsx                 # TUI 主逻辑 (Commander.js + React/Ink)
├── localRecoveryCli.ts      # 降级 Recovery CLI
├── setup.ts                 # 启动初始化
├── screens/REPL.tsx         # 交互 REPL 界面 (≡ Triad 聊天面板)
├── ink/                     # 终端渲染引擎 (Ink)
├── components/              # UI 组件
├── tools/                   # ⭐ Agent 工具层 (Bash, Edit, Grep...)
├── commands/                # ⭐ 斜杠命令 (/commit, /review...)
├── skills/                  # ⭐ Skill 系统
├── services/                # ⭐ 服务层 (API, MCP, OAuth)
├── hooks/                   # React hooks
└── utils/                   # 工具函数
```

## 关键突破：任意 API 兼容

支持连接到任意 Anthropic 兼容 API：
- MiniMax: ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
- OpenRouter
- 任何兼容 /v1/messages 端点的服务

**→ 验证了 Triad 多 Provider 路由设计的正确性**

## 与 Triad 架构映射

| Claude Code | Triad |
|------------|-------|
| `tools/` (Bash/Edit/Grep) | `hermes_skill.py` CLI 子命令 |
| `commands/` (/commit, /review) | `roles.py` + `@` 前缀路由 |
| `skills/` (内置 + 动态) | `skill_crystallizer.py` (仅自动进化, 缺内置技能包) |
| `services/` (API/MCP/OAuth) | `model_router.py` + providers |
| `screens/REPL.tsx` | `TriadPanel.tsx` |
| `ink/` (终端 TUI) | Web UI (React + Tailwind) |

## 对 Triad 的三个改进方向

1. **拆分 hermes_skill.py** — 参考 tools/ 层，每个工具独立文件
2. **内置技能包** — 参考 skills/ 目录的 .md 定义文件，给 skill_crystallizer 加默认技能
3. **斜杠命令** — 参考 commands/，在聊天中支持 /swarm、/route、/config 等快捷命令

## 技术栈记录

- 运行时: Bun
- 语言: TypeScript
- 终端 UI: React + Ink (vadimdemedes/ink)
- CLI: Commander.js
- API: Anthropic SDK
- 协议: MCP, LSP
