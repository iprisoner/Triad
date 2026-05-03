# Triad 文学创作工作流设计

> 本文档定义 Triad 系统从「代码修复」适配到「小说创作推演」的完整工作流。
> 涵盖：四阶段流水线、NovelCurator 评估体系、自动策略调整、Skill 固化机制。

---

## 1. 架构概览

### 1.1 文学创作流水线

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Triad 文学创作工作流                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   阶段1: 大纲论证      阶段2: 剧情推演      阶段3: 细节描写      阶段4: 审查迭代  │
│   ─────────────────    ─────────────────    ─────────────────    ────────────────  │
│                                                                             │
│   ┌───────────┐       ┌───────────┐       ┌───────────┐       ┌───────────┐ │
│   │   Grok    │       │ DeepSeek  │       │   Kimi    │       │  Claude   │ │
│   │  破局设定  │──────►│  逻辑推演  │──────►│  场景描写  │──────►│  逻辑审查  │ │
│   │  发散脑洞  │       │  因果检查  │       │  对话设计  │       │  矛盾标记  │ │
│   └─────┬─────┘       └─────┬─────┘       └─────┬─────┘       └─────┬─────┘ │
│         │                   │                   │                   │         │
│   ┌─────▼─────┐       ┌─────▼─────┐       ┌─────▼─────┐       ┌─────▼─────┐ │
│   │   Kimi    │       │   Kimi    │       │   Grok    │       │ DeepSeek  │ │
│   │ 长文铺垫  │       │ 长文推演  │       │  对话润色  │       │ 推理复核  │ │
│   └───────────┘       └───────────┘       └───────────┘       └───────────┘ │
│                                                                             │
│         │                   │                   │                   │         │
│         └───────────────────┴───────────────────┴───────────────────┘         │
│                                   │                                         │
│                           ┌───────▼───────┐                                 │
│                           │  NovelCurator │                                 │
│                           │  4维质量评估   │                                 │
│                           │  策略调整决策  │                                 │
│                           └───────┬───────┘                                 │
│                                   │                                         │
│                           ┌───────▼───────┐                                 │
│                           │ SkillCrystallizer│                             │
│                           │  策略固化入库   │                                 │
│                           └───────────────┘                                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 与 Triad 三层架构的映射

| 工作流组件 | Triad 层级 | 协议 | 职责 |
|-----------|-----------|------|------|
| Grok / Kimi / DeepSeek / Claude | 第二层 Hermes | ACP (gRPC) | 模型调度与路由 |
| NovelCurator | 第二层 Hermes | 内部 Python API | 质量评估与策略调整 |
| SkillCrystallizer | 第二层 Hermes | 内部 Python API | 策略固化与复用 |
| Claude Code | 第三层 | MCP (JSON-RPC) | 代码级执行（如格式化、结构化输出） |
| OpenClaw UI | 第一层 | WebSocket | 用户交互与结果展示 |
| ClawHub | 第一层 | HTTP | NovelSkill 市场发布与订阅 |

---

## 2. 四阶段工作流详解

### 2.1 阶段一：大纲论证（Ideation & Blueprint）

**目标**：生成打破套路的创意世界观 + 严密可执行的故事大纲

#### 2.1.1 子阶段 A — 破局设定（Grok）

| 属性 | 配置 |
|------|------|
| 策略 | `CREATIVE` |
| 主模型 | Grok (`grok-2-latest`) |
| 次选 | Gemini (`gemini-1.5-pro-latest`) |
| 温度 | 0.85 |
| 输入 | 用户提供的 genre + 核心概念 + "打破套路"指令 |
| 输出 | 5-10 条反套路设定选项 + 推荐理由 |

**Prompt 模板**：

```
你是一位擅长打破类型小说套路的创意顾问。

【类型】{genre}
【核心概念】{core_concept}
【禁止清单】{avoid_list}

要求：
1. 生成 5 个反套路的世界观/设定变体
2. 每个变体必须违背该类型至少 2 个常见套路
3. 评估每个变体的「新鲜感」和「可延展性」（1-10分）
4. 推荐最佳选项并说明风险

输出格式：结构化 JSON
```

#### 2.1.2 子阶段 B — 长文铺垫（Kimi）

| 属性 | 配置 |
|------|------|
| 策略 | `LONGFORM` |
| 主模型 | Kimi (`moonshot-v1-128k`) |
| 次选 | Gemini 1.5 Pro |
| 温度 | 0.6 |
| 输入 | Grok 选定设定 + "写 3000 字详细世界观文档" |
| 输出 | 编年史 + 地理志 + 社会结构 + 技术树 |

**上下文传递**：
- Grok 输出约 500-800 tokens（设定摘要）
- Kimi 接收完整设定，因其 128K 窗口完全容纳
- ContextAligner 判断：无需压缩，直接传递

#### 2.1.3 输出物

```yaml
outline_stage_output:
  selected_concept: "火星叛乱的真正起因是地球AI的误判"
  worldbuilding_doc: "3000字世界观"
  character_seed_list: ["李明-指挥官", "王强-叛军领袖", "AI-CORE-7"]
  foreshadowing_seed_list: ["红色芯片", "4分钟通信延迟", "氧气泄漏记录"]
```

---

### 2.2 阶段二：剧情推演（Plot Deduction）

**目标**：将世界观转化为严密的因果链剧情，检测逻辑漏洞

#### 2.2.1 子阶段 A — 因果链推演（DeepSeek）

| 属性 | 配置 |
|------|------|
| 策略 | `REASONING` |
| 主模型 | DeepSeek (`deepseek-reasoner`) |
| 次选 | Claude (`claude-3-5-sonnet`) |
| 温度 | 0.15 |
| 输入 | 世界观文档 + "设计 10 章大纲，每章标注前提条件与直接后果" |
| 输出 | 结构化大纲，含因果三元组 |

**Prompt 模板**：

```
你是一位剧情逻辑工程师。请基于以下世界观设计 10 章大纲。

【世界观摘要】{worldbuilding_summary}
【关键角色】{character_list}

要求：
1. 每章必须包含：前提条件 → 触发事件 → 直接后果 → 长期影响
2. 检测角色行为是否由动机驱动（而非作者强行推动）
3. 标注所有伏笔的埋设章节和预期回收章节
4. 检查是否有 "无动机行为" 或 "未兑现前提"

输出格式：每章一个 JSON 对象，包含 causal_chain 字段
```

#### 2.2.2 子阶段 B — 逻辑验证（DeepSeek 自我检查）

```python
# DeepSeek 生成大纲后，立即调用自身进行一致性自检
self_check_prompt = """
请审查刚才生成的大纲：
1. 是否存在「角色做了不符合其动机的事」？
2. 是否存在「事件 B 的前提在事件 A 之前未成立」？
3. 是否存在「同一伏笔被埋设两次但指向不同结果」？
4. 输出问题列表，如无问题输出 "PASSED"
"""
```

#### 2.2.3 人设一致性预检查

```python
# 将角色种子导入 NovelCurator 的人设数据库
curator.import_characters([
    CharacterProfile(
        name="李明",
        personality_traits=["内向", "谨慎", "善良"],
        motivations=["保护家人", "追求真相"],
        fears=["被背叛", "孤独"],
    ),
    # ...
])
```

#### 2.2.4 输出物

```yaml
plot_stage_output:
  chapter_outlines: 10  # 每章含 causal_chain
  foreshadowing_registry:  # 正式注册到 ForeshadowingTracker
    fs_red_chip: {hint: "第三章出现", expected_payoff: "第六章", status: "pending"}
    fs_delay: {hint: "通信延迟", expected_payoff: "第九章", status: "pending"}
  consistency_check: "PASSED"  # 或问题列表
```

---

### 2.3 阶段三：细节描写（Detailing）

**目标**：将结构化大纲转化为有文学质感的场景、对话、心理描写

#### 2.3.1 子阶段 A — 场景描写（Kimi）

| 属性 | 配置 |
|------|------|
| 策略 | `CHAT` |
| 主模型 | Kimi (`moonshot-v1-128k`) |
| 次选 | Qwen 本地 |
| 温度 | 0.7 |
| 输入 | 单章大纲 + 角色人设 + "写 3000 字场景描写" |
| 输出 | 完整章节文本 |

**上下文传递**：
```python
# DeepSeek 输出的大纲（约 2000 tokens）→ Kimi
prompt = aligner.build_cross_model_prompt(
    original_task="写第三章：李明发现红色芯片",
    upstream_output=deepseek_chapter_outline,
    upstream_vendor=ModelVendor.DEEPSEEK,
    downstream_config=kimi_config,
    instruction_prefix="请基于大纲写一段有质感的科幻场景描写。",
)
```

**关键设计**：
- DeepSeek 对中文分词更细（膨胀 1.10），Kimi 为 1.00
- 2000 / 1.10 * 1.00 ≈ 1818 tokens（Kimi 视角）
- Kimi 窗口 128K，预算 102.4K → **无需压缩，全文传递**

#### 2.3.2 子阶段 B — 对话润色（Grok）

| 属性 | 配置 |
|------|------|
| 策略 | `CREATIVE` |
| 主模型 | Grok |
| 温度 | 0.8 |
| 输入 | Kimi 生成的章节 + "优化对话：使每个角色的语言习惯鲜明" |
| 输出 | 对话增强版文本 |

**规则**：
- 保持角色语言习惯一致性（参考 CharacterProfile.speech_patterns）
- 增加潜台词（subtext），避免直白表达
- 通过对话揭示关系变化，而非叙述说明

#### 2.3.3 输出物

```yaml
detail_stage_output:
  chapter_drafts:  # 每章一个版本
    - version: "kimi_v1"
      text: "..."
      tokens: 3500
    - version: "grok_dialogue_enhanced"
      text: "..."
      tokens: 3600
```

---

### 2.4 阶段四：审查迭代（Review & Iterate）

**目标**：检测逻辑矛盾、人设偏离、伏笔遗漏，生成修改建议

#### 2.4.1 子阶段 A — 逻辑审查（Claude）

| 属性 | 配置 |
|------|------|
| 策略 | `REVIEW` |
| 主模型 | Claude (`claude-3-5-sonnet-20241022`) |
| 次选 | DeepSeek |
| 温度 | 0.3 |
| 输入 | 完整章节 + 前文摘要 + 角色数据库 + 伏笔清单 |
| 输出 | 矛盾标记列表 + 修改建议 |

**Prompt 模板**：

```
你是一位严格的文学编辑。请审查以下章节。

【角色数据库】{character_db_json}
【已注册伏笔】{foreshadowing_registry}
【前文关键事件】{previous_events}

【待审查章节】
{chapter_text}

要求：
1. 标记所有 "此处与前文矛盾" 的位置
2. 标记角色行为与人设不符的地方
3. 标记伏笔未回收或突兀揭示的地方
4. 对每个问题给出修改建议

输出格式：行号 + 问题类型 + 说明 + 建议
```

#### 2.4.2 子阶段 B — NovelCurator 量化评估

```python
result = await curator.evaluate(
    text=chapter_text,
    text_id="chapter_3_v2",
    chapter_id="ch3",
    previous_text=previous_chapters_summary,
    use_llm=True,  # 启用 Claude/DeepSeek 增强
)
```

**评估输出示例**：

```json
{
  "text_id": "chapter_3_v2",
  "overall_score": 7.8,
  "dimensions": {
    "character_consistency": {
      "score": 8.5,
      "violations": [],
      "suggestions": []
    },
    "plot_logic": {
      "score": 7.0,
      "violations": ["李明在第二章决定保密，第三章却主动向王强透露 — 缺乏心理过渡"],
      "suggestions": ["添加一段内心挣扎：李明为何改变主意"]
    },
    "pacing": {
      "score": 8.0,
      "violations": [],
      "suggestions": ["可考虑在高潮前插入 200 字环境描写制造张力"]
    },
    "foreshadowing": {
      "score": 7.5,
      "violations": ["红色芯片的功能在本章揭示，但前文伏笔指向不够明确"],
      "suggestions": ["在第一章增加芯片的异常温度描写"]
    }
  }
}
```

#### 2.4.3 子阶段 C — 策略调整触发

如果某维度 **连续 3 次低于 6 分**，自动注入调整规则：

```python
# NovelCurator 自动触发
triggered = curator._check_decline_trigger()
# 假设 plot_logic 连续 3 次 < 6

# 生成调整规则
adjustments = curator._generate_adjustments([EvaluationDimension.PLOT_LOGIC])
# → AdjustmentRule: "增加因果链预推演步骤"

# 将规则注入下一轮的生成提示词
next_prompt = curator.compose_adjusted_prompt(base_prompt)
# 现在 base_prompt 末尾附加了因果链检查清单
```

#### 2.4.4 循环迭代

```
生成 → 评估 → 若 overall_score < 6.0 或某维度 < 6.0（连续3次）
    │
    ├─ 触发策略调整 ──► 注入 prompt_injection
    │                      │
    ├─ 重新生成 <───────┘  (最多 3 轮)
    │
    └─ 若 3 轮后仍 < 6.0 ──► 标记为 "需人工干预"，推送到 OpenClaw UI
```

---

## 3. NovelCurator 评估体系

### 3.1 四大评估维度

```
┌────────────────────────────────────────────────────────────────┐
│                    EvaluationDimension                          │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  character_consistency   角色行为是否与人设一致（0-10分）          │
│  ├─ 检测：动机冲突、恐惧违背、语言习惯突变、关系逻辑矛盾          │
│  ├─ 权重：30%（小说最核心的可信度来源）                          │
│  └─ 低于 6 分触发：注入「人设检查清单」步骤                      │
│                                                                 │
│  plot_logic              情节逻辑是否自洽（0-10分）              │
│  ├─ 检测：因果断裂、时间悖论、世界观规则违背、无前提结果          │
│  ├─ 权重：30%                                                   │
│  └─ 低于 6 分触发：注入「因果链预推演」步骤                      │
│                                                                 │
│  pacing                  节奏控制是否得当（0-10分）              │
│  ├─ 检测：段落长度分布、对话/叙述比例、情绪起伏曲线              │
│  ├─ 权重：20%                                                   │
│  └─ 低于 6 分触发：注入「段落长度变化与情绪标注」规则            │
│                                                                 │
│  foreshadowing           伏笔回收率（0-10分）                    │
│  ├─ 检测：已注册伏笔的回收状态、突兀揭示、未兑现承诺              │
│  ├─ 权重：20%                                                   │
│  └─ 低于 6 分触发：注入「伏笔提前注册与回收承诺」机制            │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 评分算法

```python
overall_score = (
    character_consistency * 0.30 +
    plot_logic           * 0.30 +
    pacing               * 0.20 +
    foreshadowing        * 0.20
)
```

### 3.3 评分标准（详细）

#### character_consistency

| 分数 | 标准 | 示例 |
|------|------|------|
| 9-10 | 完全一致性，角色所有行为都有人设支撑 | 内向的李明在压力下仍然谨慎决策 |
| 7-8 | 轻微偏离，有合理过渡 | 李明偶尔冲动，但前文铺垫了他被逼到绝境 |
| 4-6 | 中度违背，需要修改 | 恐惧孤独的李明主动抛弃队友，无心理描写 |
| 0-3 | 严重 OOC，人设崩塌 | 善良的李明毫无理由背叛朋友 |

#### plot_logic

| 分数 | 标准 | 示例 |
|------|------|------|
| 9-10 | 因果链完整，无逻辑漏洞 | 叛乱因资源分配不公 + AI 误判双重驱动 |
| 7-8 | 小漏洞，不影响主线 | 某配角的动机稍有模糊 |
| 4-6 | 中度断裂，需要修补 | 关键道具的出现没有前置铺垫 |
| 0-3 | 严重矛盾，无法自圆其说 | 已死亡的角色在后续章节再次出场 |

#### pacing

| 分数 | 标准 | 示例 |
|------|------|------|
| 9-10 | 张弛有度，情绪曲线平滑 | 紧张-舒缓-紧张的波浪式节奏 |
| 7-8 | 整体得当，局部可优化 | 某段落稍长但不影响阅读 |
| 4-6 | 节奏失衡 | 全文都是短段落，缺乏铺垫空间 |
| 0-3 | 严重拖沓或过于仓促 | 关键转折只用一句话交代 |

#### foreshadowing

| 分数 | 标准 | 示例 |
|------|------|------|
| 9-10 | 伏笔回收率 > 80%，无突兀揭示 | 红色芯片在第三章出现，第六章回收，第九章二次回收 |
| 7-8 | 回收率 60-80%，少量突兀 | 大部分伏笔回收，个别揭示稍快 |
| 4-6 | 回收率 < 60%，多处突兀 | 大量伏笔未回收，新信息无铺垫 |
| 0-3 | 几乎无伏笔管理 | 关键信息凭空出现 |

### 3.4 自动策略调整规则库

```python
ADJUSTMENT_RULES = {
    EvaluationDimension.CHARACTER_CONSISTENCY: AdjustmentRule(
        description="增加人设检查清单步骤",
        prompt_injection="""
【人设检查清单】在生成前，请先回顾角色数据库，
确认每个出场角色的：
1. 核心动机（该场景是否由动机驱动？）
2. 恐惧/弱点（该场景是否触及恐惧？是否有合理应对？）
3. 语言习惯（对话是否符合 speech_patterns？）
生成后自检：若角色做了重大决策，反查其动机是否支持。
"""
    ),
    EvaluationDimension.PLOT_LOGIC: AdjustmentRule(
        description="增加因果链预推演步骤",
        prompt_injection="""
【因果链检查】在写此段落前，先列出：
1. 前提条件（什么已经发生了？）
2. 触发事件（什么导致了当前场景？）
3. 直接后果（场景结束后立即发生什么？）
4. 长期影响（对本章后续/后续章节的影响？）
确保每个结果都有合理的前提支撑，禁止 "无因之果"。
"""
    ),
    EvaluationDimension.PACING: AdjustmentRule(
        description="强制段落长度变化与情绪标注",
        prompt_injection="""
【节奏控制】每段文字必须包含至少两种段落长度：
- 短段落（50-100字）：用于紧张、转折、对话、冲击
- 中段落（150-250字）：用于过渡、交代
- 长段落（300-500字）：用于环境铺垫、心理描写
每段末尾标注 [情绪强度: 1-5]，确保相邻段落强度有变化。
"""
    ),
    EvaluationDimension.FORESHADOWING: AdjustmentRule(
        description="伏笔提前注册与回收承诺机制",
        prompt_injection="""
【伏笔管理】每埋设一个新伏笔，立即明确：
- 伏笔 ID（如 fs_001）
- 埋设位置（本章第几段）
- 预期回收章节
当前章节若回收伏笔，请标注：
- 对应伏笔 ID
- 回收方式（直接揭示 / 间接暗示 / 反转让读者重新理解）
若揭示全新信息，先检查：是否有对应伏笔？若无，改为先埋设再揭示。
"""
    ),
}
```

---

## 4. Skill 固化机制

### 4.1 固化触发条件

```
条件1: 单次 4 维度均 >= 8.0 分
        └──► 标记为 "高质策略候选"

条件2: 同一策略名称连续使用 >= 3 次，且平均分 >= 7.5
        └──► 触发自动固化

条件3: 用户显式标记 "保存此策略"
        └──► 立即固化（跳过统计检查）
```

### 4.2 NovelSkill 结构

```python
@dataclass
class NovelSkill:
    skill_id: str              # 唯一标识，如 "novel_suspense_3ch_fs_20250115"
    name: str                  # 人类可读名
    description: str           # 详细说明
    trigger_tags: Set[str]     # 匹配标签，如 {"suspense", "foreshadowing"}
    template: str              # 可复用的提示词模板
    example_context: str         # 成功案例上下文
    success_rate: float        # 历史成功率
    usage_count: int           # 使用次数
```

### 4.3 固化示例

**示例 A：悬疑桥段 + 3 章伏笔**

```yaml
skill_id: "novel_suspense_3ch_fs"
name: "悬疑桥段设计 + 伏笔提前 3 章埋设"
description: "适用于悬疑/科幻类型的关键信息揭示场景"
trigger_tags: ["suspense", "foreshadowing", "sci-fi"]
template: |
  【悬疑桥段设计模板】
  
  1. 在 {bury_chapter} 以 "无关物件" 形式埋设 {clue}：
     - 描写要点：看似日常，但带有一两处异常细节
     - 读者反应："这有点奇怪，但可能不重要"
  
  2. 在 {bury_chapter+1} 不经意再次提及 {clue}：
     - 不同角色视角，强化 "这确实有点怪"
  
  3. 在 {bury_chapter+2} 让 {clue} 与主线产生弱关联：
     - 读者反应："原来之前那个东西可能有关？"
  
  4. 在 {reveal_chapter} 揭示 {clue} 的真实意义：
     - 揭示方式：{reveal_method}  # 直接/误导后反转/让读者自行联想
     - 必须让读者产生 "回头看才恍然大悟" 的效果
  
  【自检清单】
  - [ ] 伏笔首次出现时，是否被其他更重要的事分散注意力？
  - [ ] 揭示时是否提供了足够线索让聪明的读者能猜到？
  - [ ] 揭示后，前文所有关于 {clue} 的描写是否都能重新解读？

example_context: |
  成功案例：第三章观测舱出现 "温度异常的红色芯片"
  → 第四章维修记录中提及 "芯片批次问题"
  → 第五章李明梦见红色闪光
  → 第六章揭示芯片是叛乱触发器，温度异常是激活信号

success_rate: 0.85
usage_count: 5
```

**示例 B：群像戏 POV 切换**

```yaml
skill_id: "novel_multi_pov_1500"
name: "群像戏切换节奏：每 1500 字换一个 POV"
description: "适用于多主角并行叙事的场景"
trigger_tags: ["multi-pov", "ensemble", "epic"]
template: |
  【群像戏 POV 切换模板】
  
  当前 POV 角色：{current_pov}
  下一个 POV 角色：{next_pov}
  
  切换规则：
  1. 当前 POV 段落在 {current_pov} 的 "动作/决定" 处结束（而非描写处）
  2. 切换时插入 50 字 "时间/空间锚点"，防止读者迷失
  3. 新 POV 的 "首句" 必须与上一个 POV 的 "末句" 形成对照或反差
  4. 每 1500 字必须完成至少一次 POV 切换
  
  【禁止】
  - 同一章节内同一 POV 超过 2000 字
  - 切换时不提供时间/地点提示
  - 两个 POV 段落之间没有叙事关联

example_context: |
  成功案例：火星叛乱中李明(指挥官)和王强(叛军)双线叙事
  切换锚点："李明的命令发出时，王强正在 300 公里外的矿坑中..."

success_rate: 0.80
usage_count: 4
```

### 4.4 Skill 推荐与复用

```python
# 当新任务到来时，自动推荐匹配的技能
skills = curator.get_recommended_skills(
    task_tags={"suspense", "sci-fi", "foreshadowing"}
)
# → 返回按 success_rate 排序的 NovelSkill 列表

# 推荐技能的模板可直接拼接到 prompt 中
recommended_template = skills[0].template
augmented_prompt = f"{base_prompt}\n\n{recommended_template}"
```

### 4.5 ClawHub 发布流程

```
NovelSkill 固化
      │
      ├─ 本地评估：success_rate > 0.75 且 usage_count >= 3
      │
      ├─ 用户确认：OpenClaw UI 展示 Skill 详情，用户一键发布
      │
      └─ ClawHub 注册
            ├─ 上传 skill.json + example_context
            ├─ 设置标签（trigger_tags）
            ├─ 设置许可（private / team / public）
            └─ 生成 Skill ID 与安装命令
```

---

## 5. 人设一致性引擎

### 5.1 角色数据库

```python
CharacterProfile:
    name: str                    # 主名
    aliases: Set[str]            # 别名/昵称
    personality_traits: List[str] # 性格标签
    background: str               # 背景故事
    motivations: List[str]         # 核心动机
    fears: List[str]              # 恐惧/弱点
    relationships: Dict[str,str]   # 关系人 → 关系类型
    key_events: List[str]          # 经历的关键事件
    physical_description: str      # 外貌
    speech_patterns: List[str]     # 语言习惯/口头禅
    consistency_violations: List   # 历史违规记录
```

### 5.2 一致性检查流程

```
新文本输入
    │
    ├─ 1. 实体提取：找出所有出场角色名
    │       │
    │       └─ 规则引擎：正则匹配角色名 + 近邻句子
    │
    ├─ 2. 行为提取：提取角色的动作、对话、心理
    │       │
    │       └─ 关键词匹配："抛弃"、"背叛"、"勇敢"等
    │
    ├─ 3. 冲突检测：行为 vs 人设
    │       │
    │       ├─ 动机冲突：做了违背动机的事？
    │       ├─ 恐惧冲突：面对恐惧但无过渡？
    │       ├─ 语言冲突：对话不符合 speech_patterns？
    │       └─ 关系冲突：对关系人的态度突变无解释？
    │
    └─ 4. 输出：DimensionScore (0-10)
```

### 5.3 与 LLM 增强审查的结合

```python
# 规则引擎检测基础违规（零 RPC 开销）
local_score = character_engine.evaluate_consistency(text, ...)

# Claude 进行深度语义审查（检测更微妙的偏离）
llm_score = await curator._llm_enhanced_review(text, ...)

# 加权融合：规则 60% + LLM 40%
final_score = local_score * 0.6 + llm_score * 0.4
```

---

## 6. 伏笔追踪系统

### 6.1 伏笔生命周期

```
【埋设】                    【传递】                    【回收】
  │                           │                           │
  │  作者/系统调用            │  系统自动检测             │  作者/系统调用
  │  register_foreshadowing() │  evaluate() 扫描文本      │  mark_recovered()
  │                           │                           │
  ▼                           ▼                           ▼
┌─────────┐              ┌─────────┐                  ┌─────────┐
│ pending │─────────────►│ active  │─────────────────►│resolved │
└─────────┘              └─────────┘                  └─────────┘
     │                        │                           │
     │ 超期未回收             │ 文本中出现相关线索         │ 计入回收率
     ▼                        │                           │
┌─────────┐                   │                           │
│ overdue │◄──────────────────┘                           │
│ (扣分)  │                                               │
└─────────┘                                               │
                                                          ▼
                                                    ┌─────────┐
                                                    │  skill  │
                                                    │ 固化参考 │
                                                    └─────────┘
```

### 6.2 伏笔注册接口

```python
curator.register_foreshadowing(
    fs_id="fs_red_chip",
    hint_text="第三章观测舱出现的红色芯片，温度异常",
    expected_payoff_chapter="ch6",
    related_characters=["李明", "AI-CORE-7"],
)
```

### 6.3 自动回收检测

```python
# 在 evaluate() 中自动扫描
for fs_id, fs_data in foreshadowings.items():
    if not fs_data["recovered"]:
        if fs_data["hint_text"] in text or related_clue_in(text):
            # 发现可能的回收线索
            score.comments.append(f"检测到伏笔 {fs_id} 的可能回收")
```

---

## 7. 完整工作流示例：火星叛乱小说

### 7.1 执行日志（简化）

```
[09:00:00] OpenClaw 发送 TaskRequest
           task_type: "creative_writing"
           payload: "写一部火星叛乱科幻悬疑小说，10章"
           strategy: AUTO
           pipeline_mode: true

[09:00:01] Hermes ModelRouter 推断策略 = CREATIVE
           → 路由到 Grok (primary) / Gemini (secondary)

[09:00:03] Grok 返回 5 个反套路设定
           推荐: "叛乱的真正起因是地球AI的误判"

[09:00:05] 用户确认设定 → 进入 LONGFORM 阶段
           → 路由到 Kimi
           ContextAligner: Grok 输出 600 tokens → Kimi 视角 600*1.00 = 600
           预算充足，直接传递

[09:00:15] Kimi 返回 3000 字世界观文档
           注册角色: 李明、王强、AI-CORE-7
           注册伏笔种子: 红色芯片、通信延迟、氧气泄漏

[09:00:20] 进入 REASONING 阶段
           → 路由到 DeepSeek
           ContextAligner: Kimi 输出约 4000 tokens → DeepSeek 视角 4000*1.10 = 4400
           预算充足，直接传递

[09:00:45] DeepSeek 返回 10 章大纲 + 因果链
           自检: PASSED
           NovelCurator 注册伏笔:
             fs_red_chip: ch3 → ch6
             fs_delay: ch5 → ch9
             fs_leak: ch2 → ch8

[09:00:50] 进入 CHAT 阶段（Chapter 3）
           → 路由到 Kimi
           ContextAligner: DeepSeek 大纲约 800 tokens → Kimi 视角 800*1.00 = 800
           直接传递

[09:01:20] Kimi 返回 Chapter 3 初稿 (3500 tokens)
           → 路由到 Grok 润色对话

[09:01:40] Grok 返回对话增强版

[09:01:45] 进入 REVIEW 阶段
           → 路由到 Claude 审查
           → NovelCurator 量化评估:
             overall_score: 7.8
             character_consistency: 8.5
             plot_logic: 7.0  ⚠ (李明动机过渡不足)
             pacing: 8.0
             foreshadowing: 7.5 ⚠ (红色芯片指向不够)

[09:01:50] plot_logic < 8 但未连续触发
           → 记录历史，暂不调整策略

[09:02:00] 返回 TaskResponse 给 OpenClaw
           used_vendor: "kimi" (最终执行)
           was_fallback: false
           latency_ms: 120000 (含 4 轮模型调用)
```

### 7.2 第二轮（Chapter 4）的 Skill 复用

```
[09:15:00] 新任务: "写第四章，群像戏，李明和王强双线"

[09:15:01] NovelCurator.get_recommended_skills({"multi-pov", "ensemble"})
           → 推荐 skill_id: "novel_multi_pov_1500"

[09:15:02] 将 Skill 模板拼接到 prompt:
           "写第四章...\n\n【群像戏 POV 切换模板】每 1500 字换一个 POV..."

[09:15:30] Kimi 生成 Chapter 4，遵循 POV 切换模板

[09:15:45] 评估:
           pacing: 9.0 (↑ 因使用了 Skill 模板)
           → SkillCrystallizer 记录 "novel_multi_pov_1500" 使用一次
```

---

## 8. 错误处理与边界情况

### 8.1 模型全部不可用

```
Grok 熔断 → Gemini 超时 → Kimi 速率限制 → Qwen 本地正常
                                    │
                                    └─ FallbackChain 最终兜底成功
```

若全部失败：
- 返回 `FallbackExhaustedError`
- OpenClaw UI 显示 "所有模型暂时不可用，请稍后重试"
- 触发告警（PagerDuty / 企业微信）

### 8.2 上下文超限

```
任务携带 150K tokens 上下文 → Kimi 128K 窗口无法容纳
    │
    ├─ ContextAligner 启动压缩
    │    → 提取 Key Facts (约 500 tokens)
    │    → 尾部原文 (预算剩余)
    │    → 拼接对齐上下文
    │
    └─ 若压缩后仍超限 → 拆分为多个子任务（Map-Reduce 模式）
```

### 8.3 人设数据库冲突

```
用户修改角色设定（如李明的动机从 "保护家人" 改为 "复仇"）
    │
    ├─ NovelCurator 检测：设定变更
    ├─ 标记所有历史章节为 "需重新审查"
    └─ 通知用户："角色设定变更将影响 X 个已生成章节的一致性"
```

---

## 9. 性能指标

| 指标 | 目标 | 说明 |
|------|------|------|
| 路由决策延迟 | < 50ms | 纯本地计算（关键词匹配 + token 估算） |
| 降级切换延迟 | < 500ms | 从 primary 超时到 secondary 首次返回 |
| 上下文对齐耗时 | < 100ms | tiktoken 编码 + 规则提取 |
| 单章端到端耗时 | < 3min | 含 4-5 轮模型调用 + 审查 |
| 评估（本地） | < 200ms | 规则引擎分析 |
| 评估（LLM 增强） | < 5s | Claude/DeepSeek 审查 |

---

## 10. 未来扩展

| 方向 | 描述 |
|------|------|
| **多模态** | 引入 Gemini/Claude 的 vision 能力，支持「根据参考图片写场景描写」 |
| **读者模拟** | 增加 "reader_engagement" 维度，预测读者在每一章的跳出率 |
| **风格迁移** | 固化特定作家风格为 Skill（如 "海明威式简洁"、"莫言式魔幻"） |
| **实时协作** | 支持多用户同时编辑，NovelCurator 实时检测冲突 |
| **出版适配** | 根据目标平台（网文/实体/剧本）自动调整 pacing 和 foreshadowing 策略 |

---

*文档版本: v1.0*
*日期: 2025-01-15*
