# Claude Code 泄露 — 架构参考笔记 (2026-05-10)

## 来源
- wavespeed.ai — Agent Harness Architecture Breakdown
- dev.to/chen_zhang — Harness Engineering Masterclass
- particula.tech — 7 Agent Architecture Lessons

## 6 个可借鉴模式

### 1. Harness 三层模型
- 模型只管推理，Harness 管权限/工具/安全
- deny→ask→allow 管道
- 工具独立沙箱：每个工具有自己的权限门
- Triad 对应: roles.py 加 deny_tools + ask_tools

### 2. Prompt Cache 经济学
- promptCacheBreakDetection.ts — 14个失效向量 + sticky latch
- cache miss 是计费事件，不是性能优化
- Triad 对应: 加 CacheTracker 模块

### 3. 蜂群 = Prompt 驱动
- 子 Agent 协调用自然语言 prompt，不用状态机
- "The prompt IS the harness"
- Triad 对应: SwarmExecutor 改 prompt-driven

### 4. 工具独立沙箱
- ~19-44个工具，每个独立 sandbox
- 不是"Agent 有文件权限"而是"工具 X 有权限 Y"
- Triad 对应: roles.py 已有 allowed_tools，缺 deny/ask 层

### 5. 背景分类器自动审批
- 用 Sonnet 4.6 做后台风险评估
- 低风险自动过，高风险暂停
- Triad 对应: 加 RiskClassifier

### 6. Context 压缩 vs 截断
- 保留关键事实三元组，截掉冗余
- 不是按字符切，是按语义切
- Triad 对应: ContextAligner 已有基础，需升级到 LLM 语义压缩

## 关键引用
- Mitchell Hashimoto — "harness engineering" 概念
- Anthropic 工程博客 — Effective Harnesses for Long-Running Agents
- Claude Code 文档 — code.claude.com/docs/en/how-claude-code-works
