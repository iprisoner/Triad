# Skill Market 接入方案 — OpenClaw 与 Triad 集成

> 本文档定义了 Triad 系统中 Skill Market 的三层架构：官方生态接入（ClawHub）、自我进化区（Self-Evolved Skills）和用户自定义区（Custom Skills）。
> 涵盖从技能导入、自动生成到前端展示的完整数据流与接口规范。

---

## 一、总体架构概览

Triad 的 Skill Market 采用三层架构模型：

```
┌─────────────────────────────────────────────────────────────────┐
│                      Skill Market 前端界面                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  ClawHub     │  │ Self-Evolved │  │     Custom           │  │
│  │  官方技能     │  │ AI 进化技能   │  │  用户自定义           │  │
│  │  [导入按钮]   │  │ [✨ 徽章]    │  │  [创建按钮]          │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└─────────┼──────────────────┼─────────────────────┼──────────────┘
          │                  │                     │
          ▼                  ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Hermes 技能调度层                         │
│              (Skill Registry + 热加载 + 冲突仲裁)                 │
└─────────────────────────────────────────────────────────────────┘
```

### 技能来源对比

| 维度 | ClawHub（官方） | Self-Evolved（AI 进化） | Custom（用户自定义） |
|------|----------------|------------------------|---------------------|
| **来源** | 社区 ClawHub | SkillCrystallizer 自动生成 | 用户手动创建 |
| **作者** | 社区贡献者 | AI 系统 | 用户本人 |
| **版本控制** | 语义化版本（v1.2.3） | 时间戳 + 迭代号（v20250115_3） | 用户自定义 |
| **质量审核** | 社区审核 | 自动评估（质量分 > 7.5） | 无 |
| **徽章标识** | 🏷️ Official | ✨ AI Auto-Generated | 📝 Custom |
| **存储路径** | `~/.triad/memory/skills/clawhub/` | `~/.triad/memory/skills/self-evolved/` | `~/.triad/memory/skills/custom/` |

---

## 二、第一层：官方生态接入（ClawHub）

### 2.1 导入流程

```
用户点击"导入官方技能"
        │
        ▼
┌─────────────────────┐
│ 1. 查询 ClawHub API │ ← GET /api/v1/skills?filter=available
│   获取可用技能列表   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 2. 展示技能卡片      │ ← 名称、描述、下载量、评分、类别
│   用户选择技能      │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 3. 下载 Skill 定义   │ ← GET /api/v1/skills/{skill_id}/download
│   文件到本地目录    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 4. Hermes 热加载    │ ← 文件系统监听 (fs.watch)
│   新技能自动生效    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ 5. 前端展示新技能   │ ← 技能可用状态更新
│   状态更新          │
└─────────────────────┘
```

### 2.2 ClawHub API 接口规范

#### 获取技能列表
```http
GET https://clawhub.openclaw.ai/api/v1/skills
Content-Type: application/json
Authorization: Bearer ${CLAWHUB_API_KEY}

Query Parameters:
  - category: novel | code | automation | design (可选)
  - sort: downloads | rating | newest (默认: downloads)
  - limit: 1-100 (默认: 20)
  - offset: 分页偏移量

Response:
{
  "skills": [
    {
      "skill_id": "xiaohongshu_copywriting_v1",
      "name": "小红书爆款文案模板",
      "description": "基于 1000+ 爆款笔记分析总结的文案生成模板...",
      "category": "novel",
      "author": "community",
      "version": "1.2.0",
      "downloads": 15420,
      "rating": 4.8,
      "tags": ["文案", "小红书", "社交媒体"],
      "updated_at": "2025-01-10T08:30:00Z"
    }
  ],
  "total": 342,
  "page": 1
}
```

#### 下载技能定义
```http
GET https://clawhub.openclaw.ai/api/v1/skills/{skill_id}/download
Content-Type: application/octet-stream

Response: skill_definition.md 文件内容
```

### 2.3 Skill 文件目录结构

```
~/.triad/memory/skills/
├── clawhub/                          # 官方 ClawHub 导入的技能
│   ├── xiaohongshu_copywriting_v1.md
│   ├── code_refactor_expert_v2.md
│   ├── technical_doc_writer_v1.md
│   └── data_analysis_template_v3.md
│
├── self-evolved/                     # SkillCrystallizer 自动生成的技能
│   ├── novel_suspense_v1.md
│   ├── frontend_bug_fix_v1.md
│   ├── scene_pacing_v2.md
│   └── api_integration_pattern_v1.md
│
└── custom/                           # 用户手动创建的技能
    └── my_custom_skill.md
```

### 2.4 Hermes 热加载机制

```typescript
// Hermes 热加载监听器
class SkillHotReloader {
  private watchers: Map<string, fs.FSWatcher> = new Map();

  startWatching(basePath: string): void {
    const skillDirs = ['clawhub', 'self-evolved', 'custom'];

    for (const dir of skillDirs) {
      const watchPath = path.join(basePath, dir);
      const watcher = fs.watch(watchPath, (eventType, filename) => {
        if (filename && filename.endsWith('.md')) {
          this.handleSkillChange(dir, filename, eventType);
        }
      });
      this.watchers.set(dir, watcher);
    }
  }

  private handleSkillChange(source: string, filename: string, event: string): void {
    const skillPath = path.join(this.basePath, source, filename);

    if (event === 'rename' && fs.existsSync(skillPath)) {
      // 新增技能
      const skill = this.parseSkillFile(skillPath);
      this.skillRegistry.register(skill);
      this.emit('skill:added', { source, skill });
    } else if (event === 'change') {
      // 技能更新
      const skill = this.parseSkillFile(skillPath);
      this.skillRegistry.update(skill);
      this.emit('skill:updated', { source, skill });
    } else if (event === 'rename' && !fs.existsSync(skillPath)) {
      // 技能删除
      const skillId = filename.replace('.md', '');
      this.skillRegistry.unregister(skillId);
      this.emit('skill:removed', { source, skillId });
    }
  }
}
```

---

## 三、第二层：自我进化区（Self-Evolved Skills）

### 3.1 SkillCrystallizer 工作原理

SkillCrystallizer 是一个自动化的技能提取引擎，在每次任务完成后运行：

```
任务执行完成
    │
    ▼
┌──────────────────────────┐
│ 1. 执行质量评估           │ ← NovelCurator / CodeCurator 评分
│   (质量分 > 7.5 ?)       │
└───────────┬──────────────┘
            │
    ┌───────┴───────┐
    ▼               ▼
  是 (> 7.5)      否 (<= 7.5)
    │               │
    ▼               ▼
┌──────────┐   ┌──────────┐
│ 2. 策略   │   │ 丢弃     │
│   可复用  │   │ 不固化   │
│   性检查  │   │          │
└─────┬────┘   └──────────┘
      │
      ▼
┌──────────────────────────┐
│ 3. 自动提取策略模板       │
│   - 触发条件 (trigger)   │
│   - 执行步骤 (steps)     │
│   - 参数模板 (params)    │
│   - 评估标准 (criteria)  │
└───────────┬──────────────┘
            │
            ▼
┌──────────────────────────┐
│ 4. 生成 Skill 定义文件    │
│   YAML Frontmatter +     │
│   Markdown 步骤描述       │
└───────────┬──────────────┘
            │
            ▼
┌──────────────────────────┐
│ 5. 写入 self-evolved/    │
│   目录                   │
└───────────┬──────────────┘
            │
            ▼
┌──────────────────────────┐
│ 6. Hermes 热加载          │
│   新技能即时可用          │
└──────────────────────────┘
```

### 3.2 质量评估标准

SkillCrystallizer 触发条件（需同时满足）：

| 指标 | 阈值 | 说明 |
|------|------|------|
| **执行质量分** | > 7.5 / 10 | NovelCurator/CodeCurator 的综合评分 |
| **策略复用性** | 可模式化 | 策略可抽象为通用模板，非一次性方案 |
| **成功证据数** | >= 3 次 | 相同策略在不同任务中成功应用的次数 |
| **用户满意度** | 正面反馈 | 用户对输出结果表达认可 |

### 3.3 与官方技能的区分

| 维度 | 官方技能 (ClawHub) | 自我进化技能 (Self-Evolved) |
|------|---------------------|------------------------------|
| **来源** | 社区 ClawHub | SkillCrystallizer 自动生成 |
| **作者** | 社区贡献者 | AI 系统 |
| **版本控制** | 语义化版本（v1.2.3） | 时间戳 + 迭代号（v20250115_3） |
| **质量审核** | 社区人工审核 | 自动评估（质量分 > 7.5） |
| **徽章标识** | 🏷️ Official | ✨ AI Auto-Generated |
| **可编辑** | 否（只读） | 否（只读，可被迭代覆盖） |
| **迭代方式** | 社区更新 | 自动发现更优策略时覆盖 |
| **元数据** | 标准 YAML | 包含 `creation_context` 和 `evidence_count` |

### 3.4 Skill 元数据标准

所有 Skill 文件统一采用 Markdown + YAML Frontmatter 格式：

```yaml
---
# ========== 核心标识 ==========
skill_id: novel_suspense_v1
name: "悬疑桥段设计 + 伏笔提前 3 章埋设"
type: novel                    # novel | code | automation | design
source: self-evolved           # clawhub | self-evolved | custom

# ========== 来源上下文 (自我进化技能特有) ==========
creation_context:
  trigger_pattern: "悬疑类章节 + 需要意外转折"
  source_models: [grok, deepseek]
  avg_assessment: 8.2
  evidence_count: 5              # 基于多少次成功任务固化

# ========== 版本信息 ==========
version: 1
author: "SkillCrystallizer"
created_at: "2025-01-15T10:30:00Z"
updated_at: "2025-01-15T10:30:00Z"

# ========== 执行定义 ==========
trigger:                          # 触发条件
  keywords: ["悬疑", "伏笔", "反转", "意外"]
  types: ["novel_chapter"]
  min_word_count: 2000

steps:                            # 执行步骤
  - id: 1
    action: "analyze_genre"
    description: "分析当前章节的悬疑类型和氛围"
    params:
      suspense_level: "high"
  - id: 2
    action: "plant_foreshadowing"
    description: "在 3 章前埋设关键伏笔"
    params:
      foreshadowing_depth: 3
      subtlety_level: "medium"
  - id: 3
    action: "design_twist"
    description: "设计符合逻辑的意外转折"
    params:
      twist_type: "revelation"
      emotional_impact: "high"
  - id: 4
    action: "verify_consistency"
    description: "验证伏笔与反转的逻辑一致性"

# ========== 质量评估标准 ==========
evaluation_criteria:
  foreshadowing_clarity: "伏笔需足够隐蔽但可被回溯发现"
  twist_logic: "反转需符合前文逻辑，不可强行反转"
  emotional_impact: "转折需引发读者情感共鸣"
  reread_value: "读者重读时应能发现伏笔痕迹"

# ========== 分类标签 ==========
tags: ["悬疑", "伏笔", "反转", "小说创作", "情节设计"]
---

# 悬疑桥段设计 + 伏笔提前 3 章埋设

## 概述
本技能用于在小说创作中设计高质量的悬疑桥段，核心方法论是**提前 3 章埋设伏笔**，确保反转既出人意料又在情理之中。

## 执行步骤

### Step 1: 悬疑类型分析
识别当前章节的悬疑子类型：
- **信息差悬疑**：读者知道但角色不知道
- **身份悬疑**：角色的真实身份存疑
- **事件悬疑**：某件事的真相不明
- **心理悬疑**：角色的心理状态/动机不明

### Step 2: 伏笔埋设（提前 3 章）
在目标章节前 3 章的适当位置植入伏笔线索：
- **视觉线索**：环境细节、物品特写
- **对话线索**：看似无关紧要的对话
- **行为线索**：角色的异常行为或习惯
- **认知线索**：角色的知识盲区或特殊认知

### Step 3: 意外转折设计
确保转折满足以下条件：
1. **可回溯性**：读者事后可以追溯线索
2. **唯一性**：排除其他可能的解释
3. **情感冲击**：转折应引发强烈的情感反应
4. **逻辑自洽**：与前文所有事实不矛盾

### Step 4: 一致性验证
使用回溯检查清单验证伏笔的有效性。

## 示例
[此处省略示例内容...]
```

---

## 四、第三层：用户自定义区（Custom Skills）

### 4.1 创建流程

1. 用户在前端 SkillMarket 点击"创建自定义技能"
2. 进入可视化编辑器或 Markdown 编辑器
3. 填写 YAML Frontmatter（元数据）和步骤内容
4. 保存到 `~/.triad/memory/skills/custom/` 目录
5. Hermes 热加载，技能即时可用

### 4.2 编辑权限

| 操作 | ClawHub | Self-Evolved | Custom |
|------|---------|-------------|--------|
| 查看 | ✅ | ✅ | ✅ |
| 复制 | ✅ | ✅ | ✅ |
| 编辑 | ❌ 只读 | ❌ 只读 | ✅ 完全编辑 |
| 删除 | ❌ | ❌ | ✅ |
| 导出 | ✅ | ✅ | ✅ |

---

## 五、Hermes 技能调度接口

### 5.1 技能注册表 API

```typescript
// Skill 注册表核心接口
interface SkillRegistry {
  // 注册新技能
  register(skill: SkillDefinition): Promise<void>;

  // 更新已有技能
  update(skill: SkillDefinition): Promise<void>;

  // 注销技能
  unregister(skillId: string): Promise<void>;

  // 查询技能（支持多维度过滤）
  query(filters: {
    type?: SkillType;
    source?: SkillSource;
    tags?: string[];
    keywords?: string[];
  }): Promise<SkillDefinition[]>;

  // 匹配适合当前任务的技能
  match(taskContext: TaskContext): Promise<SkillMatch[]>;

  // 获取技能详情
  get(skillId: string): Promise<SkillDefinition | null>;

  // 列出所有可用技能
  list(): Promise<SkillDefinition[]>;
}

// 技能定义结构
interface SkillDefinition {
  skill_id: string;
  name: string;
  type: 'novel' | 'code' | 'automation' | 'design';
  source: 'clawhub' | 'self-evolved' | 'custom';
  version: string;
  steps: SkillStep[];
  trigger?: SkillTrigger;
  evaluation_criteria?: Record<string, string>;
  tags: string[];
  content: string;          // Markdown 完整内容
  created_at: string;
  updated_at: string;
}
```

### 5.2 技能匹配算法

```python
class SkillMatcher:
    def match(self, task_context: TaskContext) -> list[SkillMatch]:
        """根据任务上下文匹配最适合的技能"""

        matches = []
        all_skills = self.registry.list()

        for skill in all_skills:
            score = self._calculate_match_score(skill, task_context)
            if score > 0.3:  # 最低匹配阈值
                matches.append(SkillMatch(
                    skill=skill,
                    score=score,
                    reason=self._generate_match_reason(skill, task_context)
                ))

        return sorted(matches, key=lambda m: m.score, reverse=True)[:5]

    def _calculate_match_score(self, skill: Skill, context: TaskContext) -> float:
        scores = []

        # 关键词匹配 (0-0.4)
        keyword_score = self._keyword_match(skill, context)
        scores.append(keyword_score * 0.4)

        # 类型匹配 (0-0.3)
        type_score = 1.0 if skill.type == context.task_type else 0.0
        scores.append(type_score * 0.3)

        # 标签匹配 (0-0.2)
        tag_score = len(set(skill.tags) & set(context.keywords)) / max(len(skill.tags), 1)
        scores.append(tag_score * 0.2)

        # 来源权重 (0-0.1)
        source_weights = {
            'custom': 0.1,
            'clawhub': 0.08,
            'self-evolved': 0.07
        }
        scores.append(source_weights.get(skill.source, 0.05))

        return sum(scores)
```

---

## 六、前端展示规范

### 6.1 技能卡片 UI

```
┌─────────────────────────────────────────┐
│ 🏷️ Official  ✨ AI Auto  📝 Custom      │
│                                         │
│  技能名称                                │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━        │
│                                         │
│  类型: novel | code | automation        │
│  标签: #悬疑 #伏笔 #反转                │
│                                         │
│  描述: 基于 1000+ 爆款笔记分析...       │
│                                         │
│  ┌────────────┐  ⭐ 4.8  📥 15420      │
│  │   使用     │                         │
│  └────────────┘                         │
└─────────────────────────────────────────┘
```

### 6.2 徽章颜色规范

| 来源 | 徽章文本 | 背景色 | 文字色 |
|------|---------|--------|--------|
| ClawHub | 🏷️ Official | `#1a73e8` | `#ffffff` |
| Self-Evolved | ✨ AI Auto-Generated | `#34a853` | `#ffffff` |
| Custom | 📝 Custom | `#fbbc04` | `#000000` |

---

## 七、数据持久化

### 7.1 文件存储格式

所有 Skill 文件统一存储为 Markdown 格式，使用 YAML Frontmatter 承载元数据，正文承载详细的步骤描述。

```
文件名格式:
  clawhub:      {skill_id}_v{version}.md
  self-evolved: {skill_id}_v{timestamp}_{iteration}.md
  custom:       {user_defined_name}.md
```

### 7.2 索引文件

```json
// ~/.triad/memory/skills/.index.json
{
  "version": "1.0",
  "last_updated": "2025-01-15T12:00:00Z",
  "skills": {
    "clawhub": [
      {
        "skill_id": "xiaohongshu_copywriting_v1",
        "filename": "xiaohongshu_copywriting_v1.md",
        "hash": "sha256:a1b2c3...",
        "registered_at": "2025-01-10T08:30:00Z"
      }
    ],
    "self-evolved": [
      {
        "skill_id": "novel_suspense_v1",
        "filename": "novel_suspense_v1.md",
        "hash": "sha256:d4e5f6...",
        "registered_at": "2025-01-15T10:30:00Z",
        "evidence_count": 5,
        "avg_assessment": 8.2
      }
    ],
    "custom": [
      {
        "skill_id": "my_custom_skill",
        "filename": "my_custom_skill.md",
        "hash": "sha256:g7h8i9...",
        "registered_at": "2025-01-14T15:00:00Z"
      }
    ]
  }
}
```

---

## 八、错误处理与冲突仲裁

### 8.1 技能 ID 冲突

当不同来源存在相同 `skill_id` 时，优先级为：

```
Custom > ClawHub > Self-Evolved
```

即：用户自定义技能优先级最高，其次是官方技能，AI 进化技能优先级最低。

### 8.2 版本冲突

当同一技能的多个版本存在时：
- **ClawHub**：使用最新语义化版本
- **Self-Evolved**：保留最近 3 个版本，自动清理旧版本
- **Custom**：由用户决定保留哪些版本

### 8.3 加载失败处理

```python
def handle_skill_load_failure(skill_id: str, error: Exception):
    """技能加载失败时的处理策略"""

    # 记录错误日志
    logger.error(f"Skill load failed: {skill_id}", error=error)

    # 标记为不可用
    registry.mark_unavailable(skill_id, reason=str(error))

    # 通知前端
    event_bus.emit('skill:load_failed', {
        'skill_id': skill_id,
        'error': str(error),
        'timestamp': datetime.now().isoformat()
    })

    # 尝试使用降级技能
    fallback_skill = registry.find_fallback(skill_id)
    if fallback_skill:
        logger.info(f"Using fallback skill: {fallback_skill.skill_id}")
        return fallback_skill
```

---

*本文档定义了 Triad Skill Market 的完整技术方案，涵盖数据流、接口规范、存储格式和错误处理。实际实现时可根据具体需求调整细节。*
