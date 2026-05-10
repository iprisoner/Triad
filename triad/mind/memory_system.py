"""
memory_system.py — Triad 三层记忆系统 (v3.0 P0)

设计参考: Claude Code 7 层记忆 → Triad 聚焦最核心的 3 层
核心原则: 记忆是 Harness 的一部分，模型不直接管理记忆存储

三层记忆架构:
  Layer 1 — ConversationSummary: 自动压缩长对话为结构化摘要
  Layer 2 — FactExtractor:      提取实体/关系/事件三元组
  Layer 3 — SkillRecipes:       高分任务固化 (已由 skill_crystallizer 实现)

存储: ~/.triad/memory/
  ├── conversations/  — 对话摘要 (JSON)
  ├── facts/          — 关键事实 (JSONL)
  └── skills/         — 技能配方 (Markdown+YAML, 由 skill_crystallizer 管理)

用法:
  from mind.memory_system import MemorySystem
  mem = MemorySystem()
  mem.summarize_conversation(task_id, messages)  # Layer 1
  mem.extract_facts(text, source)                # Layer 2
  facts = mem.search_facts("角色名")              # 检索
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── 数据模型 ──────────────────────────────────────────────────────────────

class ConversationSummary:
    """对话摘要快照"""
    def __init__(self, task_id: str, summary: str, key_topics: List[str],
                 participant_count: int = 1, token_count: int = 0):
        self.task_id = task_id
        self.summary = summary
        self.key_topics = key_topics
        self.participant_count = participant_count
        self.token_count = token_count
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "summary": self.summary,
            "key_topics": self.key_topics,
            "participant_count": self.participant_count,
            "token_count": self.token_count,
            "timestamp": self.timestamp,
            "date": datetime.fromtimestamp(self.timestamp).isoformat(),
        }


class Fact:
    """单个关键事实（三元组）"""
    def __init__(self, fact_id: str, category: str, subject: str,
                 predicate: str, obj: str, source: str = "",
                 confidence: float = 1.0):
        self.fact_id = fact_id
        self.category = category       # "character" | "setting" | "event" | "decision" | "config"
        self.subject = subject
        self.predicate = predicate
        self.obj = obj                 # 对象/值
        self.source = source           # 来源（task_id / file）
        self.confidence = confidence
        self.timestamp = time.time()
        self.access_count = 0          # 被检索次数

    def to_line(self) -> str:
        return json.dumps({
            "id": self.fact_id,
            "cat": self.category,
            "s": self.subject,
            "p": self.predicate,
            "o": self.obj,
            "src": self.source,
            "conf": self.confidence,
            "ts": self.timestamp,
        }, ensure_ascii=False)

    @staticmethod
    def from_line(line: str) -> "Fact":
        d = json.loads(line)
        f = Fact(
            fact_id=d["id"],
            category=d["cat"],
            subject=d["s"],
            predicate=d["p"],
            obj=d["o"],
            source=d.get("src", ""),
            confidence=d.get("conf", 1.0),
        )
        f.timestamp = d.get("ts", 0)
        return f


# ── 记忆系统 ──────────────────────────────────────────────────────────────

class MemorySystem:
    """
    三层记忆的统一入口。

    Layer 1: ConversationSummary — 对话压缩
    Layer 2: FactExtractor       — 关键事实三元组
    Layer 3: SkillRecipes       — 委托 skill_crystallizer
    """

    def __init__(self, memory_root: Optional[Path] = None):
        self.root = memory_root or Path.home() / ".triad" / "memory"
        self.conv_dir = self.root / "conversations"
        self.facts_dir = self.root / "facts"
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self.facts_dir.mkdir(parents=True, exist_ok=True)

    # ── Layer 1: 对话摘要 ──────────────────────────────────────────────

    def summarize_conversation(
        self,
        task_id: str,
        messages: List[Dict[str, str]],
        max_summary_tokens: int = 500,
    ) -> ConversationSummary:
        """
        将对话压缩为结构化摘要。

        策略（规则版，不调用 LLM — 节省成本）:
          1. 提取用户问题（role=user 的消息）
          2. 提取系统响应摘要（role=assistant 的消息前 200 字）
          3. 合并为统一摘要

        生产级应接入 LLM 进行语义摘要。
        """
        user_messages = [m["content"] for m in messages if m.get("role") == "user"]
        assistant_messages = [m["content"] for m in messages if m.get("role") == "assistant"]

        parts = []
        key_topics = set()

        for i, msg in enumerate(user_messages[:5]):
            short = msg[:100].replace("\n", " ")
            parts.append(f"Q{i+1}: {short}")
            # 简单主题提取
            for keyword in self._extract_keywords(msg):
                key_topics.add(keyword)

        for i, msg in enumerate(assistant_messages[:5]):
            short = msg[:200].replace("\n", " ")
            parts.append(f"A{i+1}: {short}")

        summary_text = "\n".join(parts)
        if len(summary_text) > max_summary_tokens * 3:  # 粗略截断
            summary_text = summary_text[:max_summary_tokens * 3]

        summary = ConversationSummary(
            task_id=task_id,
            summary=summary_text,
            key_topics=list(key_topics)[:20],
            participant_count=2,
            token_count=sum(len(m.get("content", "")) for m in messages) // 3,
        )

        self._save_summary(summary)
        return summary

    def _save_summary(self, summary: ConversationSummary) -> Path:
        path = self.conv_dir / f"{summary.task_id}.json"
        path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return path

    def load_summary(self, task_id: str) -> Optional[ConversationSummary]:
        path = self.conv_dir / f"{task_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return ConversationSummary(
            task_id=data["task_id"],
            summary=data["summary"],
            key_topics=data["key_topics"],
            participant_count=data.get("participant_count", 1),
            token_count=data.get("token_count", 0),
        )

    def list_summaries(self, limit: int = 20) -> List[ConversationSummary]:
        """列出最近的对话摘要"""
        files = sorted(self.conv_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        summaries = []
        for f in files[:limit]:
            try:
                summaries.append(self.load_summary(f.stem))
            except Exception:
                pass
        return [s for s in summaries if s]

    # ── Layer 2: 关键事实提取 ───────────────────────────────────────────

    def extract_facts(self, text: str, source: str = "unknown") -> List[Fact]:
        """
        从文本中提取关键事实三元组。

        策略（规则版）:
          - 角色事实: "X 是 Y" / "X 的 Z 是 W"
          - 设定事实: "世界观" / "背景" 提及
          - 决策事实: "决定" / "选择" / "配置" 提及
          - 事件事实: "发生了" / "创建了" / "修改了" 提及

        生产级应接入轻量模型进行语义提取。
        """
        facts = []

        # 角色提取
        facts.extend(self._extract_character_facts(text, source))

        # 设定提取
        facts.extend(self._extract_setting_facts(text, source))

        # 决策提取
        facts.extend(self._extract_decision_facts(text, source))

        # 保存
        for f in facts:
            self._save_fact(f)

        return facts

    def _extract_character_facts(self, text: str, source: str) -> List[Fact]:
        facts = []
        import re
        # 模式: "XX（角色）是一个 YY" 或 "XX的性格是YY"
        patterns = [
            (r'([一-龥A-Za-z]{2,4})[（(]([^）)]+)[）)]', "character"),
            (r'([一-龥]{2,4})的(性格|身份|职业|背景|年龄|特征|特长)是([^，。\n]{2,30})', "character"),
            (r'([一-龥]{2,4})说[：:]\s*["\u201C]([^"\u201D]{5,50})["\u201D]', "dialogue"),
        ]
        for pattern, category in patterns:
            for m in re.finditer(pattern, text):
                if category == "character" and len(m.groups()) >= 1:
                    subj = m.group(1)
                    obj = m.group(2) if len(m.groups()) >= 2 else m.group(0)
                    facts.append(Fact(
                        fact_id=f"char_{hash(subj+obj) % 100000:05d}",
                        category="character",
                        subject=subj, predicate="是个", obj=obj,
                        source=source,
                    ))
                elif category == "character" and len(m.groups()) >= 3:
                    facts.append(Fact(
                        fact_id=f"char_{hash(m.group(1)+m.group(3)) % 100000:05d}",
                        category="character",
                        subject=m.group(1),
                        predicate=m.group(2),
                        obj=m.group(3),
                        source=source,
                    ))
                elif category == "dialogue":
                    facts.append(Fact(
                        fact_id=f"dial_{hash(m.group(1)+m.group(2)) % 100000:05d}",
                        category="dialogue",
                        subject=m.group(1),
                        predicate="说了",
                        obj=m.group(2)[:50],
                        source=source,
                    ))
        return facts

    def _extract_setting_facts(self, text: str, source: str) -> List[Fact]:
        facts = []
        import re
        patterns = [
            (r'(世界观|设定|背景|规则)[：:]\s*([^。\n]{3,80})', "世界观"),
            (r'(时间线|时间|年代)[：:]\s*([^。\n]{3,50})', "时间"),
            (r'(地点|场景|环境)[：:]\s*([^。\n]{3,50})', "地点"),
        ]
        for pattern, pred in patterns:
            for m in re.finditer(pattern, text):
                facts.append(Fact(
                    fact_id=f"set_{hash(m.group(0)) % 100000:05d}",
                    category="setting",
                    subject=m.group(1),
                    predicate=pred,
                    obj=m.group(2),
                    source=source,
                ))
        return facts

    def _extract_decision_facts(self, text: str, source: str) -> List[Fact]:
        facts = []
        import re
        patterns = [
            (r'(决定|决策|选择)[：:]\s*([^。\n]{3,100})', "decision"),
            (r'(配置|参数|设置)已?(修改|更改|调整|更新)为?\s*([^。\n]{3,80})', "config"),
        ]
        for pattern, category in patterns:
            for m in re.finditer(pattern, text):
                if len(m.groups()) >= 2:
                    facts.append(Fact(
                        fact_id=f"dec_{hash(m.group(0)) % 100000:05d}",
                        category=category,
                        subject="系统" if category == "config" else "用户",
                        predicate=m.group(1),
                        obj=m.group(2) if len(m.groups()) == 2 else m.group(3),
                        source=source,
                    ))
        return facts

    def search_facts(self, query: str, category: Optional[str] = None, limit: int = 20) -> List[Fact]:
        """
        搜索事实。简单关键词匹配，生产级应接入 Qdrant 向量检索。

        Args:
            query: 搜索关键词
            category: 过滤类别 (character/setting/decision/config)
            limit: 最大返回数
        """
        results = []
        files = sorted(self.facts_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

        for f in files:
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    fact = Fact.from_line(line)
                    if query.lower() in fact.subject.lower() or \
                       query.lower() in fact.obj.lower() or \
                       query.lower() in fact.predicate.lower():
                        if category and fact.category != category:
                            continue
                        results.append(fact)
                        if len(results) >= limit:
                            return results
                except Exception:
                    continue
        return results

    def _save_fact(self, fact: Fact) -> None:
        """追加事实到每日 JSONL 文件"""
        today = datetime.now().strftime("%Y-%m-%d")
        path = self.facts_dir / f"facts_{today}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(fact.to_line() + "\n")

    # ── 工具函数 ─────────────────────────────────────────────────────

    def _extract_keywords(self, text: str) -> List[str]:
        """简单关键词提取"""
        import re
        # 提取中文词组（2-4字）
        words = re.findall(r'[一-龥]{2,4}', text)
        # 去重 + 去停用词
        stopwords = {"可以", "一个", "这个", "那个", "什么", "怎么", "为什么", "如果", "因为"}
        seen = set()
        result = []
        for w in words:
            if w not in stopwords and w not in seen:
                seen.add(w)
                result.append(w)
        return result[:20]

    # ── 统计 ─────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """记忆系统统计"""
        conv_count = len(list(self.conv_dir.glob("*.json")))
        fact_files = list(self.facts_dir.glob("*.jsonl"))
        fact_count = 0
        for f in fact_files:
            fact_count += sum(1 for _ in f.read_text().splitlines() if _.strip())
        return {
            "conversations": conv_count,
            "facts": fact_count,
            "fact_files": len(fact_files),
        }

    def compact_conversations(self, max_age_days: int = 30) -> int:
        """
        压缩旧对话摘要 — 合并 30 天前的摘要为归档文件。
        Returns: 合并的摘要数
        """
        cutoff = time.time() - max_age_days * 86400
        old = []
        for f in self.conv_dir.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                old.append(f)

        if not old:
            return 0

        archive = self.conv_dir / f"archive_{datetime.now().strftime('%Y%m')}.json"
        all_data = []
        for f in old:
            try:
                all_data.append(json.loads(f.read_text()))
            except Exception:
                pass

        archive.write_text(json.dumps(all_data, ensure_ascii=False, indent=2))
        for f in old:
            f.unlink()

        return len(old)


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Triad Memory System")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("stats", help="显示记忆统计")

    p_search = sub.add_parser("search", help="搜索事实")
    p_search.add_argument("query")
    p_search.add_argument("--category", default=None)
    p_search.add_argument("--limit", type=int, default=20)

    p_summ = sub.add_parser("summarize", help="列出对话摘要")
    p_summ.add_argument("--limit", type=int, default=10)

    p_compact = sub.add_parser("compact", help="压缩旧对话")
    p_compact.add_argument("--max-age-days", type=int, default=30)

    args = parser.parse_args()
    mem = MemorySystem()

    if args.cmd == "stats":
        print(json.dumps(mem.get_stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "search":
        facts = mem.search_facts(args.query, args.category, args.limit)
        for f in facts:
            print(f"[{f.category}] {f.subject} {f.predicate} {f.obj}")
    elif args.cmd == "summarize":
        for s in mem.list_summaries(args.limit):
            print(f"\n=== {s.task_id} ({datetime.fromtimestamp(s.timestamp).strftime('%H:%M')}) ===")
            print(s.summary[:300])
            print(f"Topics: {', '.join(s.key_topics[:10])}")
    elif args.cmd == "compact":
        n = mem.compact_conversations(args.max_age_days)
        print(f"Compacted {n} old conversations.")
    else:
        parser.print_help()
