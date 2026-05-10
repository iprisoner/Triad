# 竞品/参考项目分析 — Aider & SWE-agent (2026-05-10)

## Aider (github.com/Aider-AI/aider)

### 定位
AI pair programming in terminal. "结对编程" 风格。

### 核心亮点

1. **Repo Map（代码库地图）**
   - 对整个代码库生成一个紧凑的结构图（文件树 + 关键符号）
   - LLM 不只看当前文件，还知道项目整体结构
   - 这是 Aider 在大项目中表现好的关键

2. **Edit Format（结构化编辑）**
   - 使用特定的 search/replace diff 格式
   - LLM 输出精确的编辑指令，不是原始代码
   - 减少了"幻觉修改"的概率

3. **自动 Git 提交**
   - 每次修改自动 commit，附带合理的 commit message
   - 可以用 git diff 回溯 AI 的修改

4. **Auto Lint/Test**
   - 修改后自动运行 linter 和测试
   - 失败自动修复

### 对 Triad 的参考价值
- **Repo Map → Hermes ContextAligner 升级**: 代码任务前先生成项目地图
- **Edit Format → CodeCurator 升级**: 结构化编辑指令代替文本生成
- **Auto Git → 可以直接用**: 模型改配置后自动记录到 git

---

## SWE-agent (github.com/SWE-agent/SWE-agent)

### 定位
自动修复 GitHub issue。NeurIPS 2024 论文。Princeton + Stanford。

### 核心亮点

1. **ACI (Agent-Computer Interface)**
   - 给 LM 一个 shell + 文件查看器 + 文件编辑器
   - "把 LM 当成一个人类开发者，给它和人类一样的工具"
   - 核心哲学: "maximal agency to the LM"

2. **YAML 统一配置**
   - 所有配置在一个 YAML 文件里
   - 工具、权限、提示词、环境变量全部集中管理

3. **mini-SWE-agent（重要！）**
   - 100 行 Python 达到 65% SWE-bench
   - **简单 harness > 复杂编排** 的铁证
   - 验证了 Triad v3.0 精简方向的正确性

### 对 Triad 的参考价值
- **ACI 思路 → Hermes Skill 的工具集**: 给模型的工具越简单直接越好
- **YAML 配置 → Triad 配置统一**: 把 .env + providers.json + roles.py 合成一个
- **mini 的教训 → 少即是多**: 100 行能搞定的事不需要 1000 行

---

## 与 Claude Code 的对比

| 维度 | Claude Code | Aider | SWE-agent |
|------|------------|-------|-----------|
| 定位 | 商业编码 Agent | 开源结对编程 | 学术研究框架 |
| 架构 | 500K 行 TS | Python CLI | Python + YAML |
| Harness 复杂度 | 极高（19-44 工具） | 中等（编辑+git+lint） | 低（shell+viewer+editor） |
| 上下文策略 | Cache + 压缩 | Repo Map | 不压缩，给模型最大窗口 |
| 对 Triad 启示 | 权限/缓存/蜂群 | 代码地图/结构化编辑 | 简单就是最强/ACI 设计 |

## 结论

三者共同验证了 **Harness Engineering** 的核心地位:
- 模型不是瓶颈，模型周围的工具系统才是
- Triad 的 v3.0 方向（寄生在 OpenClaw 上，专注 Hermes 编排层）完全正确
- 下一步: 参考 Aider 的 Repo Map + SWE-agent 的 ACI + Claude Code 的权限管道
