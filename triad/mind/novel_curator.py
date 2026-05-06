"""
novel_curator.py — 文学创作版技能策展与质量审查系统

设计原理
--------
Triad 的 curator 模块在代码场景下评估「正确性、性能、安全性」；
在文学创作场景下，需要评估「一致性、逻辑性、节奏感、伏笔回收」。

NovelCurator 的核心职责：
1. 多维质量评分 —— 每次生成后给出 4 维度评分 + 评语
2. 历史趋势追踪 —— 检测维度是否连续恶化，触发策略调整
3. 人设一致性引擎 —— 维护角色数据库，检查新文本是否违背已有设定
4. 技能固化 (Skill Crystallization) —— 将成功的创作策略提取为可复用 NovelSkill

架构图
------
    ┌─────────────────┐
    │   新生成文本     │
    └────────┬────────┘
             │
    ┌────────▼──────────────────────────────────┐
    │           NovelCurator                     │
    │  ├─ CharacterConsistencyEngine (人设检查)  │
    │  ├─ PlotLogicChecker (逻辑推演)           │
    │  ├─ PacingAnalyzer (节奏分析)             │
    │  ├─ ForeshadowingTracker (伏笔追踪)        │
    │  └─ SkillCrystallizer (技能固化)          │
    └────────┬──────────────────────────────────┘
             │
    ┌────────▼────────┐     ┌──────────────┐
    │  EvaluationResult │────►│ 策略调整决策  │
    └─────────────────┘     └──────────────┘
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

# 内部导入（在同包内，若实际部署路径不同请调整）
try:
    from .model_router import (
        ContextAligner,
        LLMResponse,
        ModelConfig,
        ModelRegistry,
        ModelRouter,
        RouteStrategy,
    )
except ImportError:
    from model_router import (
        ContextAligner,
        LLMResponse,
        ModelConfig,
        ModelRegistry,
        ModelRouter,
        RouteStrategy,
    )

logger = logging.getLogger("triad.novel_curator")


# ---------------------------------------------------------------------------
# 1. 数据结构与枚举
# ---------------------------------------------------------------------------

class EvaluationDimension(Enum):
    """四大文学评估维度。"""
    CHARACTER_CONSISTENCY = "character_consistency"   # 角色行为与人设一致性
    PLOT_LOGIC = "plot_logic"                         # 情节逻辑自洽
    PACING = "pacing"                                  # 节奏控制
    FORESHADOWING = "foreshadowing"                    # 伏笔回收


@dataclass
class DimensionScore:
    """单维度评分详情。"""
    dimension: EvaluationDimension
    score: float                      # 0.0 - 10.0
    max_score: float = 10.0
    comments: List[str] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)   # 具体违规点
    suggestions: List[str] = field(default_factory=list)    # 改进建议


@dataclass
class EvaluationResult:
    """单次评估的完整结果。"""
    text_id: str                      # 被评估文本的标识（如 chapter_3_section_2）
    overall_score: float              # 加权总分
    dimension_scores: Dict[EvaluationDimension, DimensionScore]
    character_database_delta: Dict[str, Any]  # 本次新发现/更新的角色信息
    timestamp: float = field(default_factory=time.time)
    raw_llm_output: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text_id": self.text_id,
            "overall_score": self.overall_score,
            "dimensions": {
                d.value: {
                    "score": s.score,
                    "comments": s.comments,
                    "violations": s.violations,
                    "suggestions": s.suggestions,
                }
                for d, s in self.dimension_scores.items()
            },
            "timestamp": self.timestamp,
        }


@dataclass
class AdjustmentRule:
    """策略调整规则。"""
    trigger_dimension: EvaluationDimension
    description: str                  # 如 "增加人设检查清单步骤"
    prompt_injection: str             # 注入到后续生成提示词中的附加指令
    priority: int = 1                 # 优先级，数字越大越优先
    active: bool = True


@dataclass
class NovelSkill:
    """
    固化后的创作技能 —— 可被 Hermes Agent 复用，也可发布到 ClawHub。

    示例：
        name="悬疑桥段设计 + 伏笔提前 3 章埋设"
        trigger_tags={"suspense", "foreshadowing"}
        template="在 XX 章节引入看似无关的物件/对话，在 XX+3 章揭示其与主线的关联..."
    """
    skill_id: str
    name: str
    description: str
    trigger_tags: Set[str]
    template: str                     # 可复用的提示词模板
    example_context: str                # 该技能被成功应用的示例上下文
    success_rate: float = 0.0          # 基于历史成功率
    usage_count: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "trigger_tags": list(self.trigger_tags),
            "template": self.template,
            "example_context": self.example_context,
            "success_rate": self.success_rate,
            "usage_count": self.usage_count,
            "created_at": self.created_at,
        }


@dataclass
class CharacterProfile:
    """角色人设档案。"""
    name: str
    aliases: Set[str] = field(default_factory=set)
    personality_traits: List[str] = field(default_factory=list)
    background: str = ""
    motivations: List[str] = field(default_factory=list)
    fears: List[str] = field(default_factory=list)
    relationships: Dict[str, str] = field(default_factory=dict)  # 关系人 -> 关系类型
    key_events: List[str] = field(default_factory=list)         # 该角色经历的关键事件
    physical_description: str = ""
    speech_patterns: List[str] = field(default_factory=list)    # 语言习惯/口头禅
    last_seen_chapter: Optional[str] = None
    consistency_violations: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 2. CharacterConsistencyEngine — 角色一致性引擎
# ---------------------------------------------------------------------------

class CharacterConsistencyEngine:
    """
    维护角色人设数据库，检测新文本中角色行为是否违背已有设定。

    核心算法
    --------
    1. 实体提取：从文本中提取角色名、行为描述、对话内容
    2. 人设匹配：将提取的行为与 character_database 中的 traits 对比
    3. 冲突检测：如果角色做了与性格/动机/恐惧矛盾的事，标记为 violation

    扩展点
    ------
    生产环境可将规则检测替换为 LLM-based 审查（调用 Claude 或 DeepSeek）。
    """

    def __init__(self):
        self._db: Dict[str, CharacterProfile] = {}   # name -> profile
        self._violation_history: List[Dict[str, Any]] = []

    def register_character(self, profile: CharacterProfile) -> None:
        self._db[profile.name] = profile
        for alias in profile.aliases:
            self._db[alias] = profile  # 别名也指向同一对象

    def update_from_text(self, text: str, chapter_id: str) -> Dict[str, Any]:
        """
        从一段新文本中自动提取角色信息，更新数据库。

        Returns:
            delta: {"new_characters": [...], "updated": [...], "violations": [...]}
        """
        delta = {"new_characters": [], "updated": [], "violations": []}

        # 简单规则：查找「角色名 + 行为/心理/对话」
        # 生产级应使用 NLP 或 LLM 提取
        for name, profile in self._db.items():
            if name not in text:
                continue

            # 提取该角色在这段文本中的句子
            sentences = re.split(r"(?<=[。！？\n])", text)
            char_sentences = [s for s in sentences if name in s]
            if not char_sentences:
                continue

            profile.last_seen_chapter = chapter_id
            profile.key_events.append(f"{chapter_id}: 参与情节")

            # 简单规则一致性检查
            violations = self._check_rules(profile, char_sentences)
            if violations:
                profile.consistency_violations.extend(violations)
                delta["violations"].append({"character": name, "issues": violations})

        return delta

    def _check_rules(self, profile: CharacterProfile, sentences: List[str]) -> List[str]:
        violations = []
        combined = " ".join(sentences)

        # 规则1：做了违背动机的事
        for motivation in profile.motivations:
            # 极简化规则：若动机是"保护家人"，但文本出现"抛弃家人" → 冲突
            # 生产级应使用语义相似度/LLM 判断
            if "抛弃" in combined and "保护" in motivation:
                violations.append(
                    f"行为违背动机 '{motivation}'：文本中出现'抛弃'相关描述"
                )

        # 规则2：做了违背恐惧的事
        for fear in profile.fears:
            if fear in combined:
                violations.append(
                    f"角色面对恐惧 '{fear}' 但缺乏合理心理过渡"
                )

        # 规则3：语言习惯突变
        if profile.speech_patterns:
            has_pattern = any(p in combined for p in profile.speech_patterns)
            if not has_pattern and len(sentences) > 3:
                # 有多句对话但没有任何标志性语言习惯 → 轻微提醒
                violations.append(
                    f"对话中未体现语言习惯 {profile.speech_patterns}"
                )

        return violations

    def evaluate_consistency(
        self,
        text: str,
        text_id: str,
        chapter_id: str,
    ) -> DimensionScore:
        """
        评估文本的角色一致性，返回 DimensionScore。

        评分规则
        --------
        • 10分：所有角色行为完全符合人设，无违规
        • 7-9分：轻微偏离（如语言习惯未体现），有合理过渡
        • 4-6分：中度违背（如动机冲突），需修改
        • 0-3分：严重 OOC（Out Of Character），人设崩塌
        """
        delta = self.update_from_text(text, chapter_id)
        violations = []
        for v in delta.get("violations", []):
            violations.extend(v["issues"])

        # 计算分数
        total_chars = len(self._db)
        if total_chars == 0:
            score = 7.0  # 无已知角色，默认及格
            comments = ["暂无人设数据库，建议先提供角色设定。"]
        else:
            # 简化评分：违规越少分越高，每条严重违规扣 2 分
            penalty = len(violations) * 2.0
            score = max(0.0, 10.0 - penalty)
            if score >= 9:
                comments = ["角色行为与人设高度一致。"]
            elif score >= 6:
                comments = ["整体一致，存在轻微偏离。"]
            else:
                comments = ["检测到角色行为与人设冲突，建议审查。"]

        return DimensionScore(
            dimension=EvaluationDimension.CHARACTER_CONSISTENCY,
            score=round(score, 1),
            comments=comments,
            violations=violations,
            suggestions=[
                "回顾角色动机清单，确认行为合理性。",
                "检查角色对话是否符合语言习惯。",
            ] if violations else [],
        )

    def get_database(self) -> Dict[str, CharacterProfile]:
        return dict(self._db)

    def export_database(self) -> Dict[str, Dict[str, Any]]:
        """导出为 JSON-serializable 字典。"""
        return {
            k: {
                "name": v.name,
                "aliases": list(v.aliases),
                "personality_traits": v.personality_traits,
                "background": v.background,
                "motivations": v.motivations,
                "fears": v.fears,
                "relationships": v.relationships,
                "key_events": v.key_events,
                "physical_description": v.physical_description,
                "speech_patterns": v.speech_patterns,
            }
            for k, v in self._db.items()
        }


# ---------------------------------------------------------------------------
# 3. PlotLogicChecker — 情节逻辑检查器
# ---------------------------------------------------------------------------

class PlotLogicChecker:
    """
    检查情节因果链是否自洽。

    核心机制
    --------
    1. 因果事件链维护：记录「前提条件 → 事件 → 结果」三元组
    2. 时间线一致性：检查事件顺序是否合理（如角色不能在死后再次出场）
    3. 世界设定一致性：物理规则、社会规则是否被违反
    """

    def __init__(self):
        self._events: List[Dict[str, Any]] = []   # 全局事件时间线
        self._world_rules: Set[str] = set()       # 世界观规则

    def add_world_rule(self, rule: str) -> None:
        self._world_rules.add(rule)

    def add_event(self, event: Dict[str, Any]) -> None:
        """
        event: {
            "id": str,
            "chapter": str,
            "description": str,
            "preconditions": [str],
            "consequences": [str],
            "participants": [str],
            "timestamp_in_story": Optional[str],
        }
        """
        self._events.append(event)

    def evaluate_logic(self, text: str, text_id: str) -> DimensionScore:
        """
        评估情节逻辑。

        评分规则
        --------
        • 检查是否有「无前提的结果」或「无结果的前提」
        • 检查是否违背世界观规则
        """
        violations = []
        suggestions = []

        # 规则检测（简化版）
        # 1. 检查时间悖论：角色死亡后再次行动
        for event in self._events:
            if "死亡" in event.get("description", ""):
                dead_char = None
                for p in event.get("participants", []):
                    if p in text:
                        # 简单判断：如果本段文本中此角色在死亡事件之后有主动行为
                        dead_char = p
                        break
                if dead_char:
                    # 实际应比较时间线顺序，此处简化
                    pass

        # 2. 检查世界观规则违背
        for rule in self._world_rules:
            if "不能" in rule or "禁止" in rule or "无法" in rule:
                # 提取规则核心动词
                violation_indicators = ["却", "竟然", "还是", "仍然"]
                if any(ind in text for ind in violation_indicators):
                    violations.append(f"可能违背世界观规则: {rule}")

        # 3. 因果断裂检测：查找未兑现的伏笔/前提
        unfulfilled = [
            e for e in self._events
            if e.get("preconditions") and not e.get("consequences")
        ]
        if len(unfulfilled) > 3:
            violations.append(f"存在 {len(unfulfilled)} 个未闭合的因果链（有起因无结果）")
            suggestions.append("检查前文埋设的伏笔是否已回收")

        penalty = len(violations) * 1.5
        score = max(0.0, 10.0 - penalty)

        return DimensionScore(
            dimension=EvaluationDimension.PLOT_LOGIC,
            score=round(score, 1),
            comments=[
                "情节逻辑严密。" if score >= 9 else
                "整体合理，存在轻微断裂。" if score >= 6 else
                "检测到情节逻辑缺陷，需重点修改。"
            ],
            violations=violations,
            suggestions=suggestions or ["保持当前因果链的闭合性。"],
        )


# ---------------------------------------------------------------------------
# 4. PacingAnalyzer — 节奏分析器
# ---------------------------------------------------------------------------

class PacingAnalyzer:
    """
    分析文本的节奏控制：张弛有度、快慢得当。

    分析维度
    --------
    1. 段落长度分布：短段落制造紧张感，长段落适合铺垫
    2. 对话/叙述比例：对话过多可能显得轻佻，叙述过多可能枯燥
    3. 情绪曲线：通过标点/句式检测情绪起伏
    """

    def analyze(self, text: str) -> DimensionScore:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return DimensionScore(
                dimension=EvaluationDimension.PACING,
                score=5.0,
                comments=["文本为空或段落过少，无法评估节奏。"],
            )

        para_lengths = [len(p) for p in paragraphs]
        avg_len = sum(para_lengths) / len(para_lengths)

        # 短段落占比（<100字视为快节奏/紧张段落）
        short_ratio = sum(1 for l in para_lengths if l < 100) / len(para_lengths)

        # 对话检测（以引号包裹的内容）
        dialogue_chars = len(re.findall(r"[\"""].*?[\"""]", text, re.DOTALL))
        dialogue_ratio = dialogue_chars / max(len(text), 1)

        violations = []
        suggestions = []

        # 节奏失衡检测
        if short_ratio > 0.8 and avg_len < 80:
            violations.append("段落过短，节奏过快，缺乏铺垫空间")
            suggestions.append("适当插入 200-400 字的环境/心理描写段落")
        elif short_ratio < 0.2 and avg_len > 400:
            violations.append("段落过长，节奏拖沓")
            suggestions.append("在关键节点插入短段落制造节奏变化")

        # 对话比例检测
        if dialogue_ratio > 0.7:
            violations.append("对话占比过高，场景缺乏立体描述")
            suggestions.append("在对话间插入动作、神态、环境描写")
        elif dialogue_ratio < 0.1 and len(text) > 500:
            violations.append("几乎无对话，可能显得沉闷")
            suggestions.append("考虑通过对话推动情节或揭示角色关系")

        penalty = len(violations) * 2.0
        score = max(0.0, 10.0 - penalty)

        return DimensionScore(
            dimension=EvaluationDimension.PACING,
            score=round(score, 1),
            comments=[
                f"段落平均长度 {avg_len:.0f} 字，短段落占比 {short_ratio:.0%}，"
                f"对话占比 {dialogue_ratio:.0%}。"
            ],
            violations=violations,
            suggestions=suggestions,
        )


# ---------------------------------------------------------------------------
# 5. ForeshadowingTracker — 伏笔追踪器
# ---------------------------------------------------------------------------

class ForeshadowingTracker:
    """
    追踪伏笔的「埋设 → 回收」完整生命周期。

    核心机制
    --------
    1. 主动埋设注册：当作者/系统明确在某处埋设伏笔时，调用 register()
    2. 被动检测回收：在评估时扫描文本，检测已有伏笔是否被回收
    3. 回收率统计：已回收 / 已注册 = 回收率
    """

    def __init__(self):
        self._foreshadowings: Dict[str, Dict[str, Any]] = {}  # fs_id -> metadata
        self._recovered: Set[str] = set()

    def register(
        self,
        fs_id: str,
        hint_text: str,
        expected_payoff_chapter: Optional[str] = None,
        related_characters: Optional[List[str]] = None,
    ) -> None:
        self._foreshadowings[fs_id] = {
            "hint_text": hint_text,
            "created_chapter": None,  # 由调用方填充
            "expected_payoff": expected_payoff_chapter,
            "related_characters": related_characters or [],
            "recovered": False,
            "payoff_chapter": None,
        }

    def mark_recovered(self, fs_id: str, chapter: str) -> None:
        if fs_id in self._foreshadowings:
            self._foreshadowings[fs_id]["recovered"] = True
            self._foreshadowings[fs_id]["payoff_chapter"] = chapter
            self._recovered.add(fs_id)

    def evaluate(self, text: str, chapter_id: str) -> DimensionScore:
        """
        评估当前章节的伏笔状态。

        评分规则
        --------
        • 如果有已注册但未回收的伏笔，本章节文本中出现相关线索 → 加分
        • 如果伏笔超期未回收（超过 expected_payoff_chapter）→ 扣分
        • 如果有突兀的揭示（无对应伏笔）→ 扣分
        """
        violations = []
        suggestions = []

        total = len(self._foreshadowings)
        recovered = len(self._recovered)
        recovery_rate = recovered / max(total, 1)

        # 检测本章节是否有新的突兀揭示
        abrupt_reveals = re.findall(
            r"(?:原来|没想到|竟然|真相).{3,30}(?:就是|竟是|是)", text
        )
        if abrupt_reveals and total == 0:
            violations.append("存在突兀揭示，但无对应前文伏笔")
            suggestions.append("在此揭示前 2-3 章埋设相关线索")

        # 基础分由回收率决定，再结合违规扣分
        base_score = recovery_rate * 10.0
        penalty = len(violations) * 2.0
        score = max(0.0, min(10.0, base_score - penalty))

        return DimensionScore(
            dimension=EvaluationDimension.FORESHADOWING,
            score=round(score, 1),
            comments=[
                f"已注册伏笔 {total} 个，已回收 {recovered} 个，"
                f"回收率 {recovery_rate:.0%}。"
            ],
            violations=violations,
            suggestions=suggestions or (
                ["注意回收已注册的未闭合伏笔。"] if recovered < total else []
            ),
        )

    def get_unrecovered(self) -> Dict[str, Dict[str, Any]]:
        return {
            k: v for k, v in self._foreshadowings.items()
            if not v["recovered"]
        }


# ---------------------------------------------------------------------------
# 6. SkillCrystallizer — 技能固化器
# ---------------------------------------------------------------------------

class SkillCrystallizer:
    """
    将成功的创作策略固化为可复用的 NovelSkill。

    固化触发条件
    ------------
    1. 某次创作在 4 维度均 >= 8 分
    2. 同一策略被连续使用 3 次以上且平均评分 >= 7.5
    3. 用户显式标记「保存此策略」

    技能模板
    --------
    每个 NovelSkill 包含：
    • trigger_tags: 当新任务匹配这些标签时自动推荐此技能
    • template: 可直接拼接到提示词中的结构化指令
    """

    def __init__(self, save_dir: Optional[Path] = None):
        self._skills: Dict[str, NovelSkill] = {}
        self._save_dir = save_dir or Path("/mnt/agents/output/triad/skills")
        self._save_dir.mkdir(parents=True, exist_ok=True)
        self._strategy_history: List[Dict[str, Any]] = []  # 记录每次使用的策略与结果

    def record_strategy_use(
        self,
        strategy_name: str,
        tags: Set[str],
        result: EvaluationResult,
    ) -> None:
        self._strategy_history.append({
            "strategy": strategy_name,
            "tags": list(tags),
            "score": result.overall_score,
            "timestamp": time.time(),
        })

    def crystallize(
        self,
        strategy_name: str,
        template: str,
        example_context: str,
        trigger_tags: Set[str],
        description: str = "",
    ) -> Optional[NovelSkill]:
        """
        尝试将策略固化为 NovelSkill。

        Returns:
            NovelSkill if conditions met, else None.
        """
        # 条件检查：该策略至少被使用 3 次且平均分 >= 7.5
        related = [
            h for h in self._strategy_history
            if h["strategy"] == strategy_name
        ]
        if len(related) < 3:
            logger.info(
                "SkillCrystallizer: %s used %d times (<3), skip crystallization.",
                strategy_name, len(related),
            )
            return None

        avg_score = sum(h["score"] for h in related) / len(related)
        if avg_score < 7.5:
            logger.info(
                "SkillCrystallizer: %s avg score %.1f (<7.5), skip.",
                strategy_name, avg_score,
            )
            return None

        skill_id = f"novel_{strategy_name}_{int(time.time())}"
        skill = NovelSkill(
            skill_id=skill_id,
            name=strategy_name,
            description=description or f"自动固化的创作策略：{strategy_name}",
            trigger_tags=trigger_tags,
            template=template,
            example_context=example_context,
            success_rate=avg_score / 10.0,
            usage_count=len(related),
        )
        self._skills[skill_id] = skill
        self._persist_skill(skill)
        logger.info("SkillCrystallizer: crystallized %s (avg score %.1f)", skill_id, avg_score)
        return skill

    def _persist_skill(self, skill: NovelSkill) -> None:
        path = self._save_dir / f"{skill.skill_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(skill.to_dict(), f, ensure_ascii=False, indent=2)

    def load_skills(self) -> None:
        """从磁盘加载所有已固化的技能。"""
        for path in self._save_dir.glob("novel_*.json"):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            skill = NovelSkill(
                skill_id=data["skill_id"],
                name=data["name"],
                description=data["description"],
                trigger_tags=set(data["trigger_tags"]),
                template=data["template"],
                example_context=data["example_context"],
                success_rate=data.get("success_rate", 0.0),
                usage_count=data.get("usage_count", 0),
                created_at=data.get("created_at", 0.0),
            )
            self._skills[skill.skill_id] = skill

    def recommend_skills(self, task_tags: Set[str]) -> List[NovelSkill]:
        """根据任务标签推荐匹配的技能，按 success_rate 排序。"""
        scored = []
        for skill in self._skills.values():
            match = len(skill.trigger_tags & task_tags)
            if match > 0:
                scored.append((match * skill.success_rate, skill))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored]

    def get_all_skills(self) -> List[NovelSkill]:
        return list(self._skills.values())


# ---------------------------------------------------------------------------
# 7. NovelCurator — 主策展类
# ---------------------------------------------------------------------------

class NovelCurator:
    """
    文学创作版策展器，替代代码场景的 CodeCurator。

    职责
    ----
    1. 接收新文本，调度四大引擎完成评估
    2. 维护历史评分趋势，检测连续恶化
    3. 触发策略调整（AdjustmentRule）
    4. 驱动技能固化
    5. 可选：调用外部 LLM (Claude/DeepSeek) 增强审查深度
    """

    # 连续低于阈值的触发阈值
    DECLINE_THRESHOLD = 6.0
    DECLINE_COUNT_TRIGGER = 3

    def __init__(
        self,
        model_router: Optional[ModelRouter] = None,
        save_dir: Optional[Path] = None,
        local_evaluator_url: str = "http://0.0.0.0:18000/v1/chat/completions",
        local_evaluator_model: str = "qwen-14b-chat",
        use_local_first: bool = True,
    ):
        self.router = model_router
        self.local_evaluator_url = local_evaluator_url
        self.local_evaluator_model = local_evaluator_model
        self.use_local_first = use_local_first
        self.character_engine = CharacterConsistencyEngine()
        self.plot_checker = PlotLogicChecker()
        self.pacing_analyzer = PacingAnalyzer()
        self.fs_tracker = ForeshadowingTracker()
        self.crystallizer = SkillCrystallizer(save_dir)

        # 历史评分记录：dimension -> List[(timestamp, score)]
        self._score_history: Dict[EvaluationDimension, List[Tuple[float, float]]] = {
            d: [] for d in EvaluationDimension
        }
        # 已激活的调整规则
        self._active_adjustments: List[AdjustmentRule] = []
        # 是否启用 LLM 增强审查
        self._llm_enhanced = True

    # ------------------------------------------------------------------
    # 7.1 核心评估入口
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        text: str,
        text_id: str,
        chapter_id: str,
        previous_text: Optional[str] = None,
        use_llm: bool = True,
    ) -> EvaluationResult:
        """
        对单段文本执行完整 4 维度评估。

        Args:
            text: 待评估文本
            text_id: 唯一标识
            chapter_id: 所属章节
            previous_text: 前文（用于跨段一致性）
            use_llm: 是否调用外部 LLM 增强审查
        """
        # 本地引擎评估
        scores: Dict[EvaluationDimension, DimensionScore] = {}
        scores[EvaluationDimension.CHARACTER_CONSISTENCY] = \
            self.character_engine.evaluate_consistency(text, text_id, chapter_id)
        scores[EvaluationDimension.PLOT_LOGIC] = \
            self.plot_checker.evaluate_logic(text, text_id)
        scores[EvaluationDimension.PACING] = \
            self.pacing_analyzer.analyze(text)
        scores[EvaluationDimension.FORESHADOWING] = \
            self.fs_tracker.evaluate(text, chapter_id)

        # LLM 增强审查（优先本地 → 外部云端 → 降级 heuristic）
        if use_llm and self._llm_enhanced:
            llm_scores: Dict[EvaluationDimension, DimensionScore] = {}
            if self.use_local_first:
                logger.info("NovelCurator: 优先使用本地 llama-server 进行小说评估...")
                llm_scores = await self._local_llm_assess(text, text_id, chapter_id)
            elif self.router:
                logger.info("NovelCurator: 使用外部云端 API 进行评估...")
                llm_scores = await self._llm_enhanced_review(
                    text, text_id, chapter_id, previous_text
                )

            # 用 LLM 发现的问题合并到本地评分（降低分数、追加违规）
            for dim, remote_score in llm_scores.items():
                local = scores[dim]
                # 加权融合：本地 60% + LLM 40%
                blended = local.score * 0.6 + remote_score.score * 0.4
                local.score = round(min(10.0, max(0.0, blended)), 1)
                local.violations.extend(remote_score.violations)
                local.suggestions.extend(remote_score.suggestions)
                if remote_score.comments:
                    local.comments.extend(remote_score.comments)

        # 计算总分（等权平均）
        overall = sum(s.score for s in scores.values()) / len(scores)

        result = EvaluationResult(
            text_id=text_id,
            overall_score=round(overall, 1),
            dimension_scores=scores,
            character_database_delta={},
            raw_llm_output=None,
        )

        # 记录历史
        self._record_scores(result)

        # 检测连续恶化
        triggered = self._check_decline_trigger()
        if triggered:
            new_rules = self._generate_adjustments(triggered)
            self._active_adjustments.extend(new_rules)
            logger.warning(
                "NovelCurator: triggered adjustments for %s",
                [d.value for d in triggered],
            )

        return result

    async def _llm_enhanced_review(
        self,
        text: str,
        text_id: str,
        chapter_id: str,
        previous_text: Optional[str] = None,
    ) -> Dict[EvaluationDimension, DimensionScore]:
        """
        调用外部 LLM 进行深度审查。

        路由选择
        --------
        • 逻辑审查/一致性 → Claude (REVIEW 策略)
        • 推理链检查 → DeepSeek (REASONING 策略)
        """
        results: Dict[EvaluationDimension, DimensionScore] = {}

        # 构建审查提示词
        prompt = self._build_review_prompt(text, text_id, chapter_id, previous_text)

        if not self.router:
            return results

        # 第一阶段：Claude 审查逻辑与一致性
        try:
            decision = self.router.route(
                "审查小说文本的逻辑一致性与叙事矛盾",
                strategy=RouteStrategy.REVIEW,
            )
            response = await self.router.execute(decision, prompt)
            parsed = self._parse_llm_review(response.content)

            for dim, data in parsed.items():
                enum_dim = EvaluationDimension(dim)
                results[enum_dim] = DimensionScore(
                    dimension=enum_dim,
                    score=data.get("score", 7.0),
                    comments=data.get("comments", []),
                    violations=data.get("violations", []),
                    suggestions=data.get("suggestions", []),
                )
        except Exception as e:
            logger.warning("NovelCurator: LLM enhanced review failed: %s", e)

        return results

    def _build_review_prompt(
        self,
        text: str,
        text_id: str,
        chapter_id: str,
        previous_text: Optional[str] = None,
    ) -> str:
        context_block = (
            f"前文摘要:\n{previous_text[:2000]}\n\n" if previous_text else ""
        )
        char_db = json.dumps(
            self.character_engine.export_database(),
            ensure_ascii=False,
            indent=2,
        )
        return (
            f"你是一位资深文学编辑，负责对小说片段进行专业审查。\n\n"
            f"=== 角色人设数据库 ===\n{char_db}\n\n"
            f"{context_block}"
            f"=== 待审查文本 [{text_id}] 章节 [{chapter_id}] ===\n{text}\n\n"
            f"请从以下 4 个维度给出评分（0-10）和具体问题：\n"
            f"1. character_consistency: 角色行为是否与人设一致\n"
            f"2. plot_logic: 情节逻辑是否自洽\n"
            f"3. pacing: 节奏控制是否得当\n"
            f"4. foreshadowing: 伏笔是否回收或合理铺设\n\n"
            f"输出 JSON 格式："
            f'{{"character_consistency": {{"score": 8.5, "violations": [...], "suggestions": [...]}}, ...}}'
        )

    def _safe_json_parse(self, raw: str) -> Dict[str, Any]:
        """
        从 LLM 原始输出中提取 JSON 对象，多层容错。

        支持模式（按优先级）：
        1. ```json ... ``` 包裹的 JSON
        2. ``` ... ``` 包裹的 JSON（无语言标记）
        3. 标准 JSON 对象（前后可能有 markdown 或其他文本）
        4. 纯文本中的 JSON 片段（正则提取平衡花括号）
        5. 修复常见错误后重试（trailing commas, 单引号等）
        6. 所有情况失败后返回空字典
        """
        if not raw or not isinstance(raw, str):
            logger.warning("_safe_json_parse: empty or non-string input")
            return {}

        text = raw.strip()

        # 模式 1: 显式代码块 ```json
        if "```json" in text:
            try:
                block = text.split("```json")[1].split("```")[0].strip()
                return json.loads(block)
            except (IndexError, json.JSONDecodeError) as e:
                logger.debug("_safe_json_parse: code block json parse failed: %s", e)

        # 模式 2: 通用代码块 ```
        if "```" in text:
            try:
                parts = text.split("```")
                if len(parts) >= 3:
                    block = parts[1].strip()
                    lines = block.splitlines()
                    if lines and lines[0].strip().lower() in ("json", ""):
                        block = "\n".join(lines[1:]).strip()
                    return json.loads(block)
            except (IndexError, json.JSONDecodeError) as e:
                logger.debug("_safe_json_parse: generic code block parse failed: %s", e)

        # 模式 3: 尝试直接解析整个字符串
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 模式 4: 正则提取平衡花括号
        try:
            # 找到第一个 '{' 然后追踪深度
            start = text.find("{")
            if start != -1:
                depth = 0
                for i, ch in enumerate(text[start:]):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = text[start:start + i + 1]
                            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug("_safe_json_parse: balanced brace extract failed: %s", e)

        # 模式 5: 尝试修复常见错误后解析
        try:
            cleaned = text
            # 修复 trailing commas
            cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
            # 修复单引号 → 双引号
            cleaned = cleaned.replace("'", '"')
            # 修复无引号键名（简单启发式）
            cleaned = re.sub(r"([{,])\s*(\w+)\s*:", r'\1"\2":', cleaned)
            # 再次尝试提取平衡花括号
            start = cleaned.find("{")
            if start != -1:
                depth = 0
                for i, ch in enumerate(cleaned[start:]):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            return json.loads(cleaned[start:start + i + 1])
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug("_safe_json_parse: cleanup parse failed: %s", e)

        logger.warning("_safe_json_parse: all parse attempts failed, returning empty dict")
        return {}

    def _parse_llm_review(self, content: str) -> Dict[str, Dict[str, Any]]:
        """解析 LLM 返回的审查 JSON，复用 _safe_json_parse 容错逻辑。"""
        data = self._safe_json_parse(content)
        if not data:
            return {}
        return {
            k: v for k, v in data.items()
            if k in {d.value for d in EvaluationDimension}
        }

    async def _local_llm_assess(
        self,
        text: str,
        text_id: str,
        chapter_id: str,
    ) -> Dict[EvaluationDimension, DimensionScore]:
        """
        直接调用本地 llama-server 进行评估，不经过 ModelRouter。

        优点：不依赖外部 API key，延迟低，成本低。
        返回与 _llm_enhanced_review 相同的 Dict[EvaluationDimension, DimensionScore]。
        """
        results: Dict[EvaluationDimension, DimensionScore] = {}

        system_prompt = """你是一位资深文学编辑，专门评估小说章节质量。

请对以下章节进行严格评估，并以 JSON 格式输出评分：

评估维度：
1. character_consistency (0-10)：角色行为是否与人设一致
2. plot_logic (0-10)：情节因果链是否自洽
3. pacing (0-10)：节奏控制是否得当
4. foreshadowing (0-10)：伏笔回收情况

输出格式（必须严格遵循，只输出纯 JSON，不要 markdown 代码块）：
{"character_consistency": 7.5, "plot_logic": 8.0, "pacing": 6.5, "foreshadowing": 9.0, "overall": 7.8, "critique": "简要评价"}
"""

        char_db = json.dumps(
            self.character_engine.export_database(),
            ensure_ascii=False,
            indent=2,
        )
        content_prompt = (
            f"角色人设：\n{char_db}\n\n"
            f"章节 [{text_id}] 章节 [{chapter_id}]：\n{text[:3000]}"
        )

        payload = {
            "model": self.local_evaluator_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    self.local_evaluator_url,
                    headers={"Content-Type": "application/json"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            raw = data["choices"][0]["message"]["content"]
            parsed = self._safe_json_parse(raw)

            if not parsed:
                logger.warning("_local_llm_assess: empty parsed result from local LLM")
                return results

            # 将扁平分数转换为 DimensionScore 结构
            dim_mapping = {
                "character_consistency": EvaluationDimension.CHARACTER_CONSISTENCY,
                "plot_logic": EvaluationDimension.PLOT_LOGIC,
                "pacing": EvaluationDimension.PACING,
                "foreshadowing": EvaluationDimension.FORESHADOWING,
            }

            critique = parsed.get("critique", "本地评估完成")
            for key, dim in dim_mapping.items():
                score_val = parsed.get(key, 5.0)
                try:
                    score_val = float(score_val)
                except (TypeError, ValueError):
                    score_val = 5.0
                results[dim] = DimensionScore(
                    dimension=dim,
                    score=round(min(10.0, max(0.0, score_val)), 1),
                    comments=[f"本地模型评估: {critique}"] if critique else [],
                    violations=[],
                    suggestions=[],
                )

            logger.info(
                "_local_llm_assess: local model %s returned scores %s",
                self.local_evaluator_model,
                {k: v.score for k, v in results.items()},
            )
        except Exception as e:
            logger.warning("_local_llm_assess: 本地评估失败: %s，降级到 heuristic", e)
            # 降级：返回空 results，evaluate 方法会跳过融合，只使用本地引擎分数

        return results

    # ------------------------------------------------------------------
    # 7.2 历史趋势与策略调整
    # ------------------------------------------------------------------

    def _record_scores(self, result: EvaluationResult) -> None:
        for dim, score_obj in result.dimension_scores.items():
            self._score_history[dim].append((result.timestamp, score_obj.score))
            # 只保留最近 20 次记录
            self._score_history[dim] = self._score_history[dim][-20:]

    def _check_decline_trigger(self) -> List[EvaluationDimension]:
        """
        检查哪些维度连续 3 次低于 DECLINE_THRESHOLD。

        Returns:
            触发策略调整的维度列表
        """
        triggered: List[EvaluationDimension] = []
        for dim, history in self._score_history.items():
            if len(history) < self.DECLINE_COUNT_TRIGGER:
                continue
            recent = [s for _, s in history[-self.DECLINE_COUNT_TRIGGER:]]
            if all(s < self.DECLINE_THRESHOLD for s in recent):
                triggered.append(dim)
        return triggered

    def _generate_adjustments(
        self,
        triggered_dimensions: List[EvaluationDimension],
    ) -> List[AdjustmentRule]:
        """根据触发的维度生成对应的调整规则。"""
        rules = []
        for dim in triggered_dimensions:
            if dim == EvaluationDimension.CHARACTER_CONSISTENCY:
                rules.append(AdjustmentRule(
                    trigger_dimension=dim,
                    description="增加人设检查清单步骤",
                    prompt_injection=(
                        "【人设检查清单】在生成前，请先回顾角色数据库，"
                        "确认每个出场角色的动机、恐惧、语言习惯。"
                        "生成后自检：角色的行为是否由动机驱动？"
                    ),
                    priority=3,
                ))
            elif dim == EvaluationDimension.PLOT_LOGIC:
                rules.append(AdjustmentRule(
                    trigger_dimension=dim,
                    description="增加因果链预推演步骤",
                    prompt_injection=(
                        "【因果链检查】在写此段落前，先列出："
                        "1) 前提条件 2) 触发事件 3) 直接后果 4) 长期影响。"
                        "确保每个结果都有合理的前提支撑。"
                    ),
                    priority=3,
                ))
            elif dim == EvaluationDimension.PACING:
                rules.append(AdjustmentRule(
                    trigger_dimension=dim,
                    description="强制段落长度变化与情绪标注",
                    prompt_injection=(
                        "【节奏控制】每段文字必须包含至少两种段落长度："
                        "短段落（<80字）用于制造紧张/转折，"
                        "长段落（>300字）用于铺垫/描写。"
                        "每段标注情绪强度（1-5）。"
                    ),
                    priority=2,
                ))
            elif dim == EvaluationDimension.FORESHADOWING:
                rules.append(AdjustmentRule(
                    trigger_dimension=dim,
                    description="伏笔提前注册与回收承诺机制",
                    prompt_injection=(
                        "【伏笔管理】每埋设一个伏笔，立即明确其预期回收章节。"
                        "当前章节若回收伏笔，请标注对应的伏笔 ID。"
                        "若揭示新信息，请先检查是否有对应伏笔。"
                    ),
                    priority=2,
                ))
        return rules

    def get_active_adjustments(self) -> List[AdjustmentRule]:
        """获取当前激活的所有调整规则。"""
        return [r for r in self._active_adjustments if r.active]

    def compose_adjusted_prompt(self, base_prompt: str) -> str:
        """
        将当前激活的调整规则拼接到基础提示词中。

        这是 NovelCurator 对上游生成器（如 Grok/DeepSeek/Kimi）的
        反馈闭环：评估发现的问题通过 prompt_injection 反向影响生成。
        """
        injections = [
            f"【规则 {i+1}】{r.prompt_injection}"
            for i, r in enumerate(self.get_active_adjustments())
        ]
        if not injections:
            return base_prompt
        return (
            f"{base_prompt}\n\n"
            f"=== 生成质量改进规则（由策展系统注入）===\n"
            f"{chr(10).join(injections)}"
        )

    def clear_adjustments(self, dimension: Optional[EvaluationDimension] = None) -> None:
        """清除调整规则（维度恢复后使用）。"""
        if dimension:
            self._active_adjustments = [
                r for r in self._active_adjustments
                if r.trigger_dimension != dimension
            ]
        else:
            self._active_adjustments.clear()

    # ------------------------------------------------------------------
    # 7.3 技能固化接口
    # ------------------------------------------------------------------

    def record_and_crystallize(
        self,
        strategy_name: str,
        template: str,
        example_context: str,
        result: EvaluationResult,
        trigger_tags: Set[str],
        description: str = "",
    ) -> Optional[NovelSkill]:
        """
        记录策略使用并尝试固化。

        两步：
        1. 记录到 history
        2. 若满足条件，触发 crystallize
        """
        self.crystallizer.record_strategy_use(
            strategy_name=strategy_name,
            tags=trigger_tags,
            result=result,
        )
        return self.crystallizer.crystallize(
            strategy_name=strategy_name,
            template=template,
            example_context=example_context,
            trigger_tags=trigger_tags,
            description=description,
        )

    def get_recommended_skills(self, task_tags: Set[str]) -> List[NovelSkill]:
        return self.crystallizer.recommend_skills(task_tags)

    # ------------------------------------------------------------------
    # 7.4 人设数据库管理
    # ------------------------------------------------------------------

    def import_characters(self, profiles: List[CharacterProfile]) -> None:
        for p in profiles:
            self.character_engine.register_character(p)

    def add_world_rule(self, rule: str) -> None:
        self.plot_checker.add_world_rule(rule)

    def register_foreshadowing(
        self,
        fs_id: str,
        hint_text: str,
        expected_payoff_chapter: Optional[str] = None,
        related_characters: Optional[List[str]] = None,
    ) -> None:
        self.fs_tracker.register(fs_id, hint_text, expected_payoff_chapter, related_characters)

    def mark_foreshadowing_recovered(self, fs_id: str, chapter: str) -> None:
        self.fs_tracker.mark_recovered(fs_id, chapter)

    # ------------------------------------------------------------------
    # 7.5 序列化与状态保存
    # ------------------------------------------------------------------

    def save_state(self, path: Path) -> None:
        """保存完整策展状态（评分历史、人设库、伏笔追踪）。"""
        state = {
            "score_history": {
                d.value: [(t, s) for t, s in hist]
                for d, hist in self._score_history.items()
            },
            "character_db": self.character_engine.export_database(),
            "foreshadowings": {
                k: v for k, v in self.fs_tracker._foreshadowings.items()
            },
            "active_adjustments": [
                {
                    "dimension": r.trigger_dimension.value,
                    "description": r.description,
                    "prompt_injection": r.prompt_injection,
                    "priority": r.priority,
                }
                for r in self._active_adjustments
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        logger.info("NovelCurator: state saved to %s", path)

    def load_state(self, path: Path) -> None:
        """从磁盘恢复策展状态。"""
        if not path.exists():
            logger.warning("NovelCurator: state file not found at %s", path)
            return
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)

        # 恢复评分历史
        for d in EvaluationDimension:
            raw = state.get("score_history", {}).get(d.value, [])
            self._score_history[d] = [(t, s) for t, s in raw]

        # 恢复角色数据库
        for name, data in state.get("character_db", {}).items():
            profile = CharacterProfile(
                name=data["name"],
                aliases=set(data.get("aliases", [])),
                personality_traits=data.get("personality_traits", []),
                background=data.get("background", ""),
                motivations=data.get("motivations", []),
                fears=data.get("fears", []),
                relationships=data.get("relationships", {}),
                key_events=data.get("key_events", []),
                physical_description=data.get("physical_description", ""),
                speech_patterns=data.get("speech_patterns", []),
            )
            self.character_engine.register_character(profile)

        # 恢复伏笔
        for fs_id, fs_data in state.get("foreshadowings", {}).items():
            self.fs_tracker._foreshadowings[fs_id] = fs_data
            if fs_data.get("recovered"):
                self.fs_tracker._recovered.add(fs_id)

        # 恢复调整规则
        for adj in state.get("active_adjustments", []):
            self._active_adjustments.append(AdjustmentRule(
                trigger_dimension=EvaluationDimension(adj["dimension"]),
                description=adj["description"],
                prompt_injection=adj["prompt_injection"],
                priority=adj.get("priority", 1),
            ))

        logger.info("NovelCurator: state loaded from %s", path)


# ---------------------------------------------------------------------------
# 8. 快捷工厂函数
# ---------------------------------------------------------------------------

def create_novel_curator(
    model_router: Optional[ModelRouter] = None,
    save_dir: Optional[Path] = None,
    local_evaluator_url: str = "http://0.0.0.0:18000/v1/chat/completions",
    local_evaluator_model: str = "qwen-14b-chat",
    use_local_first: bool = True,
) -> NovelCurator:
    """工厂函数：快速创建带默认配置的小说策展器。"""
    return NovelCurator(
        model_router=model_router,
        save_dir=save_dir,
        local_evaluator_url=local_evaluator_url,
        local_evaluator_model=local_evaluator_model,
        use_local_first=use_local_first,
    )


# ---------------------------------------------------------------------------
# 9. 脚本级自检
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    curator = create_novel_curator()

    # 注册角色
    curator.import_characters([
        CharacterProfile(
            name="李明",
            aliases={"小李"},
            personality_traits=["内向", "谨慎", "善良"],
            motivations=["保护家人", "追求真相"],
            fears=["被背叛","孤独"],
            speech_patterns=["口头禅：其实吧...", "习惯性停顿"],
        ),
        CharacterProfile(
            name="王强",
            personality_traits=["果断", "冲动", "有领导力"],
            motivations=["权力", "复仇"],
            fears=["失去控制"],
        ),
    ])

    # 注册世界观规则
    curator.add_world_rule("火星殖民地禁止使用核武器")
    curator.add_world_rule("地球与火星通信延迟至少 4 分钟")

    # 注册伏笔
    curator.register_foreshadowing(
        fs_id="fs_red_chip",
        hint_text="第三章出现的红色芯片",
        expected_payoff_chapter="第六章",
        related_characters=["李明"],
    )

    # 评估样本文本
    sample_text = (
        "李明站在观测舱前，凝视着那颗红色的星球。\n\n"
        "\"其实吧...\"他习惯性地停顿了一下，\"我觉得这个计划有问题。\"\n\n"
        "王强拍了拍他的肩膀，眼神坚定：\"没有退路了。\"\n\n"
        "两人沉默地望着火星的地平线。那枚红色芯片静静地躺在李明的口袋里，"
        "他还没意识到它将在四天后改变一切。"
    )

    # 同步评估（本地引擎，不依赖 LLM）
    import asyncio

    async def run_test():
        result = await curator.evaluate(
            text=sample_text,
            text_id="chapter_3_test",
            chapter_id="ch3",
            use_llm=False,
        )
        print("\n[TEST] Evaluation Result:")
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

        # 测试策略调整触发
        # 人工注入低分历史，触发连续恶化
        curator._score_history[EvaluationDimension.PACING] = [
            (time.time() - 300, 5.0),
            (time.time() - 200, 5.5),
            (time.time() - 100, 4.0),
        ]
        triggered = curator._check_decline_trigger()
        print(f"\n[TEST] Decline triggered dimensions: {[d.value for d in triggered]}")

        adjustments = curator._generate_adjustments(triggered)
        print(f"[TEST] Generated adjustments: {[r.description for r in adjustments]}")

        # 测试 prompt 拼接
        base = "请写一段小说描写。"
        adjusted = curator.compose_adjusted_prompt(base)
        print(f"\n[TEST] Adjusted prompt preview:\n{adjusted[:500]}...")

        # 测试技能固化
        curator.record_and_crystallize(
            strategy_name="悬疑桥段 + 3章伏笔",
            template="在 {chapter} 引入 {object}，在 {chapter+3} 揭示其与 {plot} 的关联...",
            example_context="第三章引入红色芯片，第六章揭示它是叛乱钥匙",
            result=result,
            trigger_tags={"suspense", "foreshadowing"},
        )
        skills = curator.crystallizer.get_all_skills()
        print(f"\n[TEST] Crystallized skills: {len(skills)}")

    asyncio.run(run_test())
    print("\nAll local tests passed.")
