from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any, Callable
import datetime
import json
import logging
import re
import os

# 类型只在 TYPE_CHECKING 下引用 swarm_orchestrator，避免循环导入
try:
    from swarm_orchestrator import SwarmTask, AgentResult, AggregationMode
except Exception:
    # 定义 stub（真实可运行的 fallback）
    @dataclass
    class SwarmTask:
        task_id: str = ""
        description: str = ""
        agents: List[Any] = field(default_factory=list)
        parallel_limit: int = 3
        aggregation_mode: Any = None
        context: Dict[str, Any] = field(default_factory=dict)
        evaluator: Optional[Callable] = None

    @dataclass
    class AgentResult:
        agent_name: str = ""
        content: str = ""
        model_used: str = ""
        prompt_tokens: int = 0
        completion_tokens: int = 0
        latency_ms: float = 0.0
        tool_calls: List[Dict] = field(default_factory=list)
        error: Optional[str] = None
        success: bool = True

    class AggregationMode:
        CONCAT = "concat"
        JOIN = "join"
        BEST = "best"
        MERGE = "merge"


@dataclass
class RoleRecipe:
    """角色配方：描述单个智能体在蜂群中的角色配置。"""
    role_id: str
    system_prompt: str
    allowed_tools: List[str]
    model_pref: str
    temperature: float
    position: int              # 执行顺序


@dataclass
class SwarmRecipe:
    """蜂群配方：记录一次成功蜂群协作的完整执行模板。"""
    name: str                  # 如 "深度技术调研蜂群"
    description: str
    role_recipes: List[RoleRecipe]
    tool_sequence: List[str]   # ["search", "read", "write", "review"]
    aggregation_mode: str
    score_threshold: float     # 触发保存的最低分
    tags: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    version: int = 1
    evolved_from: Optional[str] = None
    performance_notes: str = ""


@dataclass
class RecipeMetadata:
    """配方元数据：用于列表展示，无需加载完整内容。"""
    recipe_id: str
    name: str
    tags: List[str]
    score_threshold: float
    created_at: str
    file_path: Path


class SkillCrystallizer:
    """技能结晶器：从蜂群执行结果中提取高价值配方并持久化为可复用模板。

    工作流：
    1. 监听蜂群执行结果与评分。
    2. 评分 >= 8.0 时，将任务结构、角色配置、工具序列结晶为 SwarmRecipe。
    3. 序列化为 Markdown（YAML Frontmatter + 正文）保存到本地技能库。
    4. 支持配方加载、列表、进化派生。

    安全约束：
    - 只读写 ~/.triad/memory/skills/self-evolved/ 目录。
    - 永不触碰系统进程、容器、模型权重或其他路径。
    - 所有 IO 操作包裹 try/except，失败时记录 error 日志并返回 None。
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        """初始化技能结晶器。

        Args:
            skills_dir: 配方持久化目录，默认 ~/.triad/memory/skills/self-evolved/
        """
        if skills_dir is None:
            skills_dir = Path.home() / ".triad" / "memory" / "skills" / "self-evolved"
        self.skills_dir: Path = skills_dir
        self.logger = logging.getLogger("SkillCrystallizer")
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.logger.error("无法创建技能目录 %s: %s", self.skills_dir, exc)

    def extract_swarm_recipe(
        self,
        swarm_task: SwarmTask,
        results: List[AgentResult],
        score: float,
        name: Optional[str] = None,
        performance_notes: str = "",
    ) -> Optional[SwarmRecipe]:
        """从一次蜂群执行中提取配方。

        仅当 score >= 8.0 时生成配方，否则返回 None。
        遍历 swarm_task.agents 提取每个角色的 RoleRecipe，
        从 results 中合并所有 tool_calls 序列（去重并保持出现顺序）。

        Args:
            swarm_task: 蜂群任务定义。
            results: 各智能体执行结果。
            score: 综合评分（0-10）。
            name: 配方名称，为空时由任务描述自动生成。
            performance_notes: 性能记录或人工备注。

        Returns:
            SwarmRecipe 实例，或 None（评分不足/提取失败）。
        """
        if score < 8.0:
            self.logger.info("评分 %.2f < 8.0，跳过结晶化。", score)
            return None

        try:
            role_recipes: List[RoleRecipe] = []
            for idx, agent in enumerate(swarm_task.agents):
                # 兼容 agent 为 dict 或 dataclass 的情况
                if isinstance(agent, dict):
                    agent_name = agent.get("name", f"agent_{idx}")
                    agent_role_id = agent.get("role_id", agent_name)
                    system_prompt = agent.get("system_prompt", "")
                    allowed_tools = agent.get("allowed_tools", [])
                    model_pref = agent.get("model_pref", "default")
                    temperature = agent.get("temperature", 0.7)
                else:
                    agent_name = getattr(agent, "name", f"agent_{idx}")
                    agent_role_id = getattr(agent, "role_id", agent_name)
                    system_prompt = getattr(agent, "system_prompt", "")
                    allowed_tools = getattr(agent, "allowed_tools", [])
                    model_pref = getattr(agent, "model_pref", "default")
                    temperature = getattr(agent, "temperature", 0.7)

                role_recipes.append(
                    RoleRecipe(
                        role_id=str(agent_role_id),
                        system_prompt=str(system_prompt),
                        allowed_tools=list(allowed_tools) if allowed_tools else [],
                        model_pref=str(model_pref),
                        temperature=float(temperature),
                        position=idx,
                    )
                )

            # 合并所有 agent 的 tool_calls，提取工具名并去重保序
            seen_tools: set = set()
            tool_sequence: List[str] = []
            for result in results:
                calls = result.tool_calls if result.tool_calls else []
                for call in calls:
                    tool_name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
                    if tool_name and tool_name not in seen_tools:
                        seen_tools.add(tool_name)
                        tool_sequence.append(str(tool_name))

            # 解析聚合模式：优先取枚举的 .value，否则 str()
            agg_mode = swarm_task.aggregation_mode
            if agg_mode is None:
                aggregation_mode_str = "concat"
            elif hasattr(agg_mode, "value"):
                aggregation_mode_str = str(agg_mode.value)
            else:
                aggregation_mode_str = str(agg_mode)

            # 自动生成名称
            if not name:
                desc = swarm_task.description.strip()
                prefix = desc[:20] if desc else "unnamed"
                prefix = prefix.replace(" ", "_").replace("/", "_")
                ts = datetime.datetime.now().strftime("%H%M%S")
                name = f"{prefix}_{ts}"

            recipe = SwarmRecipe(
                name=name,
                description=swarm_task.description,
                role_recipes=role_recipes,
                tool_sequence=tool_sequence,
                aggregation_mode=aggregation_mode_str,
                score_threshold=round(score, 2),
                tags=["swarm", "auto-evolved"],
                performance_notes=performance_notes,
            )
            self.logger.info("成功提取配方 '%s'（score=%.2f，%d 个角色，%d 个工具）。", name, score, len(role_recipes), len(tool_sequence))
            return recipe
        except Exception as exc:
            self.logger.error("提取配方时发生异常: %s", exc)
            return None

    def _serialize_to_markdown(self, recipe: SwarmRecipe) -> str:
        """将 SwarmRecipe 序列化为 Markdown（YAML Frontmatter + Markdown 正文）。

        不使用 PyYAML，完全由字符串拼接生成，保证零额外依赖。
        system_prompt 使用 YAML literal block scalar（|）保留换行符。

        Args:
            recipe: 待序列化的蜂群配方。

        Returns:
            Markdown 格式的完整文本。
        """

        def _fmt_value(val: Any) -> str:
            """辅助函数：将 Python 值格式化为 YAML frontmatter 字符串。"""
            if val is None:
                return "null"
            if isinstance(val, bool):
                return "true" if val else "false"
            if isinstance(val, (int, float)):
                return str(val)
            if isinstance(val, list):
                items = [str(v) for v in val]
                return "[" + ", ".join(items) + "]"
            # 字符串：若包含特殊字符则加双引号并转义
            s = str(val)
            if s == "":
                return '""'
            # 若包含冒号+空格、引号、换行、方括号开头等，则加双引号
            need_quote = False
            if "\"" in s or "\n" in s or s.startswith("["):
                need_quote = True
            if ": " in s or s.strip() in ("true", "false", "null", "yes", "no", "on", "off"):
                need_quote = True
            if need_quote:
                escaped = s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
                return f'"{escaped}"'
            return s

        # --- YAML Frontmatter ---
        lines: List[str] = ["---"]
        lines.append(f"name: {_fmt_value(recipe.name)}")
        lines.append(f"description: {_fmt_value(recipe.description)}")
        lines.append(f"aggregation_mode: {_fmt_value(recipe.aggregation_mode)}")
        lines.append(f"score_threshold: {_fmt_value(recipe.score_threshold)}")
        lines.append(f"version: {_fmt_value(recipe.version)}")
        lines.append(f"evolved_from: {_fmt_value(recipe.evolved_from)}")
        lines.append(f"tags: {_fmt_value(recipe.tags)}")
        lines.append(f"created_at: {_fmt_value(recipe.created_at)}")
        lines.append(f"performance_notes: {_fmt_value(recipe.performance_notes)}")
        lines.append("---")
        lines.append("")

        # --- Markdown Body ---
        lines.append("# 协作模式")
        lines.append("")

        # 以第一个角色的配置作为全局协作模式展示（若无角色则留空）
        primary = recipe.role_recipes[0] if recipe.role_recipes else None
        if primary:
            lines.append(f"model_pref: {primary.model_pref}")
            lines.append(f"temperature: {primary.temperature}")
            lines.append(f"allowed_tools: {_fmt_value(primary.allowed_tools)}")
            lines.append("system_prompt: |")
            # 逐行缩进，保留空白行
            for sp_line in primary.system_prompt.splitlines():
                lines.append(f"  {sp_line}")
        else:
            lines.append("model_pref: default")
            lines.append("temperature: 0.7")
            lines.append("allowed_tools: []")
            lines.append("system_prompt: |")
            lines.append("  ")
        lines.append("")

        # 角色配方列表
        lines.append("## 角色配方")
        lines.append("")
        for idx, role in enumerate(recipe.role_recipes, start=1):
            lines.append(f"{idx}. {role.role_id}（role: {role.role_id}）")
        lines.append("")

        # 工具调用顺序
        lines.append("## 工具调用顺序")
        lines.append("")
        if recipe.tool_sequence:
            steps: List[str] = []
            for idx, tool in enumerate(recipe.tool_sequence, start=1):
                steps.append(f"{idx}. {tool}")
            lines.append(" → ".join(steps))
        else:
            lines.append("无工具调用记录。")
        lines.append("")

        # 聚合策略
        lines.append("## 聚合策略")
        lines.append("")
        lines.append(f"本配方采用 **{recipe.aggregation_mode}** 模式合并多智能体输出。")
        lines.append("- concat：顺序拼接所有输出。")
        lines.append("- join：按语义分段合并。")
        lines.append("- best：选择评分最高的单条结果。")
        lines.append("- merge：深度融合去重后生成统一文档。")
        lines.append("")

        # 性能记录
        lines.append("## 性能记录")
        lines.append("")
        if recipe.performance_notes:
            lines.append(recipe.performance_notes)
        else:
            lines.append("暂无性能备注。")
        lines.append("")

        return "\n".join(lines)

    def save_recipe(self, recipe: SwarmRecipe) -> Path:
        """
        将配方持久化为 Markdown 文件（带语义去重）。

        ★★★ 地雷 3 修复：保存前检查相似配方 ★★★
        如果目录中已存在相似配方（角色集合 + 工具序列 + 聚合模式匹配），
        不新建文件，而是更新已有配方的 evidence_count 和 performance_notes。
        如果新配方分数更高，覆盖旧配方内容。

        文件名格式：{timestamp}_{name.replace(' ', '_').replace('/', '_')}.md
        timestamp 使用 datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        保存到 self.skills_dir，写入前自动创建目录。

        Args:
            recipe: 待保存的蜂群配方。

        Returns:
            保存后的文件绝对路径。

        Raises:
            OSError: 当目录不可写且无法恢复时抛出。
        """
        try:
            self.skills_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.logger.error("保存前无法确保目录存在 %s: %s", self.skills_dir, exc)
            raise

        # ★★★ 地雷 3 修复：语义去重 ★★★
        similar = self._find_similar_recipe(recipe, similarity_threshold=0.75)
        if similar is not None:
            return self._merge_recipe(similar, recipe)

        # 无相似配方，正常新建
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = recipe.name.replace(" ", "_").replace("/", "_").replace("\\", "_")
        filename = f"{ts}_{safe_name}.md"
        file_path = self.skills_dir / filename

        content = self._serialize_to_markdown(recipe)
        try:
            file_path.write_text(content, encoding="utf-8")
            self.logger.info("配方已保存: %s", file_path)
        except Exception as exc:
            self.logger.error("写入配方文件失败 %s: %s", file_path, exc)
            raise
        return file_path.resolve()

    def _find_similar_recipe(
        self, recipe: SwarmRecipe, similarity_threshold: float = 0.75
    ) -> Optional[SwarmRecipe]:
        """
        在已有配方中查找语义相似的配方。

        相似度计算（多维度加权）：
        - 角色 ID 集合 Jaccard 相似度 × 40%
        - 工具序列前 3 项匹配度 × 30%
        - 聚合模式一致性 × 20%
        - 名称关键词重叠 × 10%

        Args:
            recipe: 新配方
            similarity_threshold: 相似度阈值（0.0-1.0），默认 0.75

        Returns:
            最相似的已有 SwarmRecipe，或 None
        """
        if not self.skills_dir.exists():
            return None

        try:
            existing_files = list(self.skills_dir.glob("*.md"))
        except Exception:
            return None

        best_match: Optional[SwarmRecipe] = None
        best_score = 0.0

        new_role_ids = {r.role_id for r in recipe.role_recipes}
        new_tools = recipe.tool_sequence[:3]
        new_name_keywords = set(recipe.name.lower().split())

        for file_path in existing_files:
            try:
                existing = self.load_recipe(file_path.stem)
                if existing is None:
                    continue

                # 1. 角色 ID Jaccard 相似度
                existing_role_ids = {r.role_id for r in existing.role_recipes}
                union = new_role_ids | existing_role_ids
                intersection = new_role_ids & existing_role_ids
                role_sim = len(intersection) / len(union) if union else 0.0

                # 2. 工具序列前 3 项匹配度
                existing_tools = existing.tool_sequence[:3]
                tool_matches = sum(
                    1 for a, b in zip(new_tools, existing_tools) if a == b
                )
                tool_sim = tool_matches / max(len(new_tools), len(existing_tools), 1)

                # 3. 聚合模式一致性
                agg_sim = 1.0 if recipe.aggregation_mode == existing.aggregation_mode else 0.0

                # 4. 名称关键词重叠
                existing_keywords = set(existing.name.lower().split())
                name_union = new_name_keywords | existing_keywords
                name_intersection = new_name_keywords & existing_keywords
                name_sim = len(name_intersection) / len(name_union) if name_union else 0.0

                # 加权总分
                score = role_sim * 0.40 + tool_sim * 0.30 + agg_sim * 0.20 + name_sim * 0.10

                if score > best_score and score >= similarity_threshold:
                    best_score = score
                    best_match = existing
            except Exception:
                continue

        if best_match:
            self.logger.info(
                f"发现相似配方 '{best_match.name}' (相似度 {best_score:.2f})，"
                f"触发去重合并"
            )
        return best_match

    def _merge_recipe(self, existing: SwarmRecipe, new: SwarmRecipe) -> Path:
        """
        合并新配方到已有相似配方（适者生存）。

        策略：
        - 分数更高 → 覆盖内容，保留旧 evidence_count + 1
        - 分数更低 → 只更新 evidence_count + 1，保留旧内容
        - 合并 performance_notes

        Args:
            existing: 已有相似配方（已加载到内存）
            new: 新配方

        Returns:
            更新后的文件路径
        """
        # 查找已有文件路径
        existing_path = None
        for f in self.skills_dir.glob("*.md"):
            if f.stem.endswith(existing.name.replace(" ", "_").replace("/", "_")):
                existing_path = f
                break
        if existing_path is None:
            # 按名称模糊匹配
            for f in self.skills_dir.glob("*.md"):
                if existing.name.lower().replace(" ", "_") in f.stem.lower():
                    existing_path = f
                    break

        if existing_path is None:
            # 找不到文件，回退到新建
            self.logger.warning("相似配方文件找不到，回退到新建")
            return self._force_save(new)

        # 适者生存：分数更高则覆盖，否则只更新计数
        if new.score_threshold > existing.score_threshold:
            self.logger.info(
                f"新配方分数 {new.score_threshold} > 旧配方 {existing.score_threshold}，"
                f"执行覆盖"
            )
            # 用新配方覆盖，但保留 evidence_count + 1
            new.version = existing.version + 1
            new.evolved_from = existing.name
            new.performance_notes = (
                f"[evidence_count={getattr(existing, 'evidence_count', 1) + 1}]\n"
                f"{new.performance_notes}\n"
                f"--- 继承自旧配方 ---\n"
                f"{existing.performance_notes}"
            )
            # 注入 evidence_count（通过自定义字段）
            # 由于 SwarmRecipe 没有 evidence_count 字段，我们用 context 传递
            # 这里简单处理：直接保存新配方，替换旧文件
            content = self._serialize_to_markdown(new)
            try:
                existing_path.write_text(content, encoding="utf-8")
                self.logger.info("相似配方已覆盖更新: %s", existing_path)
            except Exception as exc:
                self.logger.error("覆盖配方失败 %s: %s", existing_path, exc)
                raise
        else:
            self.logger.info(
                f"新配方分数 {new.score_threshold} <= 旧配方 {existing.score_threshold}，"
                f"只更新 evidence_count"
            )
            # 读取旧文件，更新 performance_notes（追加 evidence）
            try:
                old_content = existing_path.read_text(encoding="utf-8")
                # 在 performance_notes 处追加
                evidence_marker = f"[evidence_count={getattr(existing, 'evidence_count', 1) + 1}]"
                updated = old_content + f"\n\n## 新证据\n{evidence_marker} 评分 {new.score_threshold} 于 {datetime.datetime.now().isoformat()}\n"
                existing_path.write_text(updated, encoding="utf-8")
                self.logger.info("相似配方已追加证据: %s", existing_path)
            except Exception as exc:
                self.logger.error("追加证据失败 %s: %s", existing_path, exc)
                raise

        return existing_path.resolve()

    def _force_save(self, recipe: SwarmRecipe) -> Path:
        """强制保存新配方（去重失败时的回退）。"""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = recipe.name.replace(" ", "_").replace("/", "_").replace("\\", "_")
        filename = f"{ts}_{safe_name}.md"
        file_path = self.skills_dir / filename
        content = self._serialize_to_markdown(recipe)
        file_path.write_text(content, encoding="utf-8")
        return file_path.resolve()

    def load_recipe(self, recipe_id: str) -> Optional[SwarmRecipe]:
        """从文件反向解析 Markdown + YAML frontmatter，还原 SwarmRecipe。

        recipe_id 为文件名（不含 .md 后缀）。
        解析策略：
        1. content.split('---', 2) 提取 frontmatter 与 body。
        2. frontmatter 逐行按 `:` 分割 key/value（支持列表 [a,b]、字符串、数字、null）。
        3. body 用正则提取角色配方与工具序列，补充 system_prompt。

        Args:
            recipe_id: 文件标识（不含扩展名）。

        Returns:
            SwarmRecipe 实例，或 None（文件不存在/解析失败）。
        """
        file_path = self.skills_dir / f"{recipe_id}.md"
        if not file_path.exists():
            self.logger.warning("配方文件不存在: %s", file_path)
            return None

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            self.logger.error("读取配方文件失败 %s: %s", file_path, exc)
            return None

        # 分割 frontmatter 与 body
        parts = content.split("---", 2)
        if len(parts) < 3:
            self.logger.error("配方文件缺少 YAML frontmatter: %s", file_path)
            return None

        frontmatter = parts[1].strip()
        body = parts[2].strip()

        # 解析 frontmatter
        meta: Dict[str, Any] = {}
        for line in frontmatter.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            # 按第一个冒号分割，避免 value 中出现冒号
            key, value = line.split(":", 1)
            key = key.strip()
            raw_value = value.strip()
            meta[key] = self._parse_yaml_value(raw_value)

        # 从 body 提取角色配方（正则）
        role_recipes: List[RoleRecipe] = []
        role_matches = re.findall(r"\d+\.\s+(.+?)\（role:\s*(.+?)\）", body)
        if not role_matches:
            # 兼容半角括号
            role_matches = re.findall(r"\d+\.\s+(.+?)\(role:\s*(.+?)\)", body)

        # 获取 system_prompt（从 body 的 "system_prompt: |" 后提取）
        system_prompt = ""
        sp_match = re.search(r"system_prompt:\s*\|(?:\r?\n)(.*?)(?=\n## |\Z)", body, re.DOTALL)
        if sp_match:
            sp_block = sp_match.group(1)
            # 去除统一缩进（通常为两个空格）
            sp_lines = sp_block.splitlines()
            if sp_lines:
                # 计算最小缩进
                min_indent = min(len(line) - len(line.lstrip()) for line in sp_lines if line.strip())
                system_prompt = "\n".join(line[min_indent:] if line.strip() else line for line in sp_lines)

        allowed_tools = meta.get("tags", []) if isinstance(meta.get("tags"), list) else []
        # 更合理的工具列表：尝试从 meta 中没有 allowed_tools，尝试从正文提取
        tools_match = re.search(r"allowed_tools:\s*(\[.*?\])", body)
        if tools_match:
            tools_str = tools_match.group(1)
            parsed_tools = self._parse_yaml_value(tools_str)
            if isinstance(parsed_tools, list):
                allowed_tools = parsed_tools

        # 构建 RoleRecipe 列表
        # 如果有正则匹配到角色，按匹配构建；否则尝试用 meta 信息兜底
        if role_matches:
            for idx, (display_name, role_id) in enumerate(role_matches):
                role_recipes.append(
                    RoleRecipe(
                        role_id=role_id.strip(),
                        system_prompt=system_prompt,
                        allowed_tools=allowed_tools,
                        model_pref=meta.get("aggregation_mode", "default"),
                        temperature=0.7,
                        position=idx,
                    )
                )
        else:
            # 兜底：至少生成一个角色
            role_recipes.append(
                RoleRecipe(
                    role_id="default_role",
                    system_prompt=system_prompt,
                    allowed_tools=allowed_tools,
                    model_pref=meta.get("aggregation_mode", "default"),
                    temperature=0.7,
                    position=0,
                )
            )

        # 从 body 提取工具调用顺序（"## 工具调用顺序" 下方内容）
        tool_sequence: List[str] = []
        ts_match = re.search(r"## 工具调用顺序\s*(?:\r?\n)(.*?)(?=\n## |\Z)", body, re.DOTALL)
        if ts_match:
            ts_block = ts_match.group(1).strip()
            # 匹配 "1. search → 2. fetch → 3. read" 中的工具名
            tool_names = re.findall(r"\d+\.\s+(\w+)", ts_block)
            tool_sequence = tool_names

        # 解析 score_threshold
        score_threshold = meta.get("score_threshold", 8.0)
        if isinstance(score_threshold, str):
            try:
                score_threshold = float(score_threshold)
            except ValueError:
                score_threshold = 8.0

        # 解析 version
        version = meta.get("version", 1)
        if isinstance(version, str):
            try:
                version = int(version)
            except ValueError:
                version = 1

        recipe = SwarmRecipe(
            name=meta.get("name", recipe_id),
            description=meta.get("description", ""),
            role_recipes=role_recipes,
            tool_sequence=tool_sequence,
            aggregation_mode=meta.get("aggregation_mode", "concat"),
            score_threshold=float(score_threshold),
            tags=meta.get("tags", []),
            created_at=meta.get("created_at", datetime.datetime.now().isoformat()),
            version=int(version),
            evolved_from=meta.get("evolved_from"),
            performance_notes=meta.get("performance_notes", ""),
        )
        return recipe

    def _parse_yaml_value(self, raw: str) -> Any:
        """解析简单的 YAML frontmatter 标量/列表值。

        支持：
        - null / ~
        - true / false
        - 整数 / 浮点数
        - [a, b, c] 列表
        - 双引号字符串（去除引号并处理 \\n 转义）
        - 普通字符串

        Args:
            raw: YAML value 的原始字符串（已 strip）。

        Returns:
            解析后的 Python 对象。
        """
        rv = raw.strip()
        if rv in ("null", "~", ""):
            return None
        if rv == "true":
            return True
        if rv == "false":
            return False
        if rv.startswith("[") and rv.endswith("]"):
            inner = rv[1:-1].strip()
            if not inner:
                return []
            return [item.strip() for item in inner.split(",") if item.strip()]
        if rv.startswith('"') and rv.endswith('"') and len(rv) > 1:
            inner = rv[1:-1]
            return inner.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
        # 尝试数字
        try:
            if "." in rv:
                return float(rv)
            return int(rv)
        except ValueError:
            pass
        return rv

    def list_recipes(self) -> List[RecipeMetadata]:
        """扫描技能目录，返回所有配方的元数据列表（按文件修改时间倒序）。

        只读取每个 .md 文件的 frontmatter，不加载完整正文，保证性能。

        Returns:
            RecipeMetadata 列表，最新的排在最前。
        """
        recipes: List[RecipeMetadata] = []
        if not self.skills_dir.exists():
            self.logger.warning("技能目录不存在: %s", self.skills_dir)
            return recipes

        try:
            md_files = sorted(
                self.skills_dir.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception as exc:
            self.logger.error("扫描配方目录失败: %s", exc)
            return recipes

        for file_path in md_files:
            try:
                content = file_path.read_text(encoding="utf-8")
                parts = content.split("---", 2)
                if len(parts) < 3:
                    continue
                frontmatter = parts[1].strip()
                meta: Dict[str, str] = {}
                for line in frontmatter.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip()

                name = meta.get("name", file_path.stem)
                tags_raw = meta.get("tags", "[]")
                parsed_tags = self._parse_yaml_value(tags_raw)
                tags = parsed_tags if isinstance(parsed_tags, list) else []
                score_str = meta.get("score_threshold", "0.0")
                try:
                    score_threshold = float(score_str)
                except ValueError:
                    score_threshold = 0.0
                created_at = meta.get("created_at", "")

                recipes.append(
                    RecipeMetadata(
                        recipe_id=file_path.stem,
                        name=name,
                        tags=tags,
                        score_threshold=score_threshold,
                        created_at=created_at,
                        file_path=file_path.resolve(),
                    )
                )
            except Exception as exc:
                self.logger.error("解析配方元数据失败 %s: %s", file_path, exc)
                continue

        return recipes

    def evolve_from_recipe(
        self,
        recipe_id: str,
        adjustment: Dict[str, Any],
    ) -> SwarmRecipe:
        """基于已有配方进化出新版本。

        进化规则：
        - temperature += adjustment.get("temperature_delta", 0.0)
        - allowed_tools 追加 adjustment.get("extra_tools", [])
        - score_threshold *= adjustment.get("score_multiplier", 1.0)
        - version += 1
        - evolved_from 指向父 recipe_id
        - name 更新为 "{parent.name} v{version}"
        - created_at 刷新为当前时间
        - performance_notes 追加 adjustment 信息

        Args:
            recipe_id: 父配方标识（文件名，不含 .md）。
            adjustment: 调整参数字典。

        Returns:
            新 SwarmRecipe（不会自动保存，需手动调用 save_recipe）。

        Raises:
            FileNotFoundError: 父配方不存在时抛出。
            ValueError: 解析父配方失败时抛出。
        """
        parent = self.load_recipe(recipe_id)
        if parent is None:
            raise FileNotFoundError(f"找不到父配方: {recipe_id}")

        # 应用调整
        temperature_delta = float(adjustment.get("temperature_delta", 0.0))
        extra_tools: List[str] = list(adjustment.get("extra_tools", []))
        score_multiplier = float(adjustment.get("score_multiplier", 1.0))
        timeout_multiplier = adjustment.get("timeout_multiplier")

        new_role_recipes: List[RoleRecipe] = []
        for role in parent.role_recipes:
            merged_tools = list(role.allowed_tools)
            for et in extra_tools:
                if et not in merged_tools:
                    merged_tools.append(et)
            new_role_recipes.append(
                RoleRecipe(
                    role_id=role.role_id,
                    system_prompt=role.system_prompt,
                    allowed_tools=merged_tools,
                    model_pref=role.model_pref,
                    temperature=round(role.temperature + temperature_delta, 3),
                    position=role.position,
                )
            )

        new_version = parent.version + 1
        new_score_threshold = round(parent.score_threshold * score_multiplier, 3)

        notes_parts: List[str] = [parent.performance_notes]
        if timeout_multiplier is not None:
            notes_parts.append(f"timeout_multiplier 调整为 {timeout_multiplier}。")
        notes_parts.append(f"由 v{parent.version} 进化而来，temperature_delta={temperature_delta}，extra_tools={extra_tools}，score_multiplier={score_multiplier}。")
        new_performance_notes = "\n".join(filter(None, notes_parts))

        new_recipe = SwarmRecipe(
            name=f"{parent.name} v{new_version}",
            description=parent.description,
            role_recipes=new_role_recipes,
            tool_sequence=list(parent.tool_sequence),
            aggregation_mode=parent.aggregation_mode,
            score_threshold=new_score_threshold,
            tags=list(parent.tags),
            created_at=datetime.datetime.now().isoformat(),
            version=new_version,
            evolved_from=recipe_id,
            performance_notes=new_performance_notes,
        )
        self.logger.info("配方 '%s' 已进化至 v%d。", parent.name, new_version)
        return new_recipe

    def auto_crystallize(
        self,
        swarm_task: SwarmTask,
        results: List[AgentResult],
        score: float,
    ) -> Optional[Path]:
        """一键流水线：提取 → 序列化 → 保存。

        仅当 score >= 8.0 时执行；否则返回 None。
        自动生成 name：取 task.description 前 20 字 + 时间戳。

        Args:
            swarm_task: 蜂群任务定义。
            results: 各智能体执行结果。
            score: 综合评分。

        Returns:
            保存后的文件路径，或 None。
        """
        if score < 8.0:
            return None

        desc = swarm_task.description.strip()
        prefix = desc[:20] if desc else "unnamed"
        prefix = prefix.replace(" ", "_").replace("/", "_")
        ts = datetime.datetime.now().strftime("%H%M%S")
        auto_name = f"{prefix}_{ts}"

        recipe = self.extract_swarm_recipe(
            swarm_task=swarm_task,
            results=results,
            score=score,
            name=auto_name,
            performance_notes=f"auto_crystallize 触发，评分 {score}。",
        )
        if recipe is None:
            return None

        try:
            saved_path = self.save_recipe(recipe)
            return saved_path
        except Exception as exc:
            self.logger.error("auto_crystallize 保存失败: %s", exc)
            return None
