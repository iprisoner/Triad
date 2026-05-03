# Triad Control Panel

扣子（Coze）风格的 Triad 三层 AI Agent 融合系统 Web UI。

## 技术栈

- **React 19** + TypeScript
- **Tailwind CSS** v3.4
- **shadcn/ui** 风格组件（自包含实现）
- **Vite** 构建工具
- **@xyflow/react**（ReactFlow）工作流画布
- **react-markdown** + remark-gfm 消息渲染
- **WebSocket** 实时通信

## 项目结构

```
src/
├── components/
│   ├── ui/                  # shadcn 风格基础组件
│   ├── ChatPanel/
│   │   └── MessageList.tsx  # 消息列表（Markdown / 图片 / 视频帧 / 资产卡片）
│   ├── AgentCanvas/
│   │   └── ModelRouterGraph.tsx  # ReactFlow 模型路由可视化
│   ├── VRAMPanel/
│   │   └── VRAMBar.tsx      # 显存条 + 状态机
│   └── ConfigPanel/
│       └── ModelConfigTab.tsx    # API 配置 / llama-server / Fallback 链
├── hooks/
│   ├── useWebSocket.ts      # WebSocket 连接 + 自动重连 + 心跳
│   └── useTaskStream.ts     # 任务流式数据消费 + 消息合并
├── types/
│   └── index.ts             # TypeScript 类型定义
├── lib/
│   └── utils.ts             # cn() 工具函数
├── App.tsx                  # 主布局（左侧聊天 + 右侧画布/VRAM/配置）
├── main.tsx                 # React 19 createRoot 入口
└── index.css                # Tailwind 指令 + CSS 变量主题
```

## 快速开始

```bash
# 进入项目目录
cd /mnt/agents/output/triad/webui

# 安装依赖（React 19 + Vite + Tailwind + ReactFlow）
npm install

# 启动开发服务器
npm run dev
```

默认运行在 http://localhost:15173

## 界面布局（扣子风格）

```
┌─ Header: Triad Control Panel [新建] [保存] [调试] [WS状态] [⚙️] ─┐
├─ Left: ChatPanel ─┬─ Right: Workspace ─────────────────────────┤
│  历史/消息列表      │  Agent 集群 / 模型路由画布 (ReactFlow)       │
│  多模态渲染         │  VRAM 状态机可视化（22GB 分段）              │
│  Markdown + 代码   │  配置面板 Tabs                              │
│  输入框 + 策略选择   │    [模型配置] [VRAM调度] [技能市场] [审计日志]│
├─ Footer: WebSocket | ClawPod | VRAM ────────────────────────────┘
```

## 核心功能

### 1. 对话面板（左侧）
- **消息列表**：滚动加载，Markdown + 代码高亮（react-markdown + remark-gfm）
- **流式输出**：WebSocket `StatusUpdate`，阶段性状态实时更新
- **多模态渲染**：
  - `image`：base64 JPEG 缩略图
  - `video_frame`：关键帧 + 标签
  - `asset://`：角色卡片（参考图、语音样本）
- **输入框**：Enter 发送，Shift+Enter 换行，策略选择器（AUTO / CREATIVE / REASONING / LONGFORM / REVIEW）

### 2. Agent 集群画布（右上）
- **模型路由可视化**：Grok → DeepSeek → Kimi → Claude（6 厂商节点）
- **节点状态**：🟢 空闲 / 🟡 处理中（脉动动画） / 🔴 错误 / ⚪ 未配置
- **边动画**：活跃链路带动画箭头，标注 context 传递

### 3. VRAM 状态机（右中）
- **显存条**：22GB 总显存实时分段可视化
  - 2GB Embedding（蓝色）
  - 9GB LLM GPU（绿色 / CPU 回退灰色 / 渲染时隐藏）
  - 9GB 空闲/ComfyUI（灰色 ↔ 紫色）
  - 2GB 系统（深灰色）
- **状态指示灯**：IDLE / CPU_FALLBACK / RENDERING / RECOVERING
- **实时指标**：tok/s、渲染步数

### 4. 配置面板（右下 Tabs）
- **模型配置**：6 厂商 API Key 管理（localStorage 存储）、llama-server 参数、健康检查、Fallback 降级链
- **VRAM 调度**：显存分区、手动/自动策略、强制切换（占位）
- **技能市场**：ClawHub 技能管理（占位）
- **审计日志**：任务时间线、过滤、导出 CSV（占位）

## WebSocket 协议

前端默认连接 `ws://localhost:18080/ws/tasks`。

```typescript
interface TaskStreamMessage {
  taskId: string;
  stage: 'ANALYZING' | 'READING_CODE' | 'EDITING' | 'TESTING' | 'COMPLETED' | 'FAILED';
  message: string;
  progress?: number;        // 0.0 - 1.0
  preview?: { type: 'text' | 'image' | 'video_frame'; data: string; metadata?: any };
  modelInfo?: { vendor: string; model: string; tokensIn: number; tokensOut: number };
  vramInfo?: { state: 'IDLE' | 'CPU_FALLBACK' | 'RENDERING' | 'RECOVERING'; embeddingMb: number; llmMb: number; comfyuiMb: number; freeMb: number };
}
```

Hook 层自动处理重连（指数退避）和心跳（15s ping/pong）。

## 离线模拟

如果 WebSocket 未连接，点击发送会自动触发本地模拟流：
ANALYZING → READING_CODE → EDITING → TESTING → COMPLETED，每阶段 800ms，便于 UI 演示。

## 自定义主题

CSS 变量定义在 `src/index.css` 的 `:root` 中，基于 shadcn/ui 的 HSL 设计令牌：

- `--primary`: 主色调（Indigo 600）
- `--destructive`: 错误/发送按钮红色
- `--radius`: 全局圆角 0.5rem
- 所有组件使用 Tailwind 的 `hsl(var(--primary))` 方式引用，支持一键切换暗黑模式。

## 注意事项

- 生产环境请对 localStorage 中的 API Key 进行主密码加密，当前演示版本为明文存储。
- `@xyflow/react` 自带 CSS，已在 `ModelRouterGraph.tsx` 中 import。
- 所有样式均通过 Tailwind CSS 类名实现，无 inline style。
