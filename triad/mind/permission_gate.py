"""
permission_gate.py — Triad 工具权限三层管道 (v3.0 P0)

设计参考: Claude Code deny→ask→allow 管道
核心原则: 模型只管推理，Harness 管权限。规则不关心模型的理由。

管道顺序:
  1. deny  → 永远拒绝（最高优先级）
  2. ask   → 需用户确认后才执行
  3. allow → 自动放行

用法:
  from mind.permission_gate import PermissionGate, ToolAction

  gate = PermissionGate(role)
  result = gate.check("bash", "rm -rf /tmp/test")
  if result == "deny":
      print("拒绝执行")
  elif result == "ask":
      print("需要用户确认")
  else:
      print("自动放行")

设计约束:
  - 零外部依赖（不需要 LLM 辅助判断）
  - 规则纯文本匹配 + 正则，确定性执行
  - 与 roles.py 的 RoleConfig 完全兼容
  - 所有判断 1ms 内完成
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class PermissionResult(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class ToolAction:
    """描述一次工具调用"""
    tool: str           # 工具名: "bash", "edit", "git", "gateway.restart"
    args: str = ""      # 参数/命令文本: "rm -rf /", "docker kill"
    context: str = ""   # 额外上下文: "修改 ~/project/config.yaml"


# ── 内置 deny 规则（硬编码，不可绕过）────────────────────────────────────
# 这些规则覆盖最危险的操
# 优先级高于角色配置
HARD_DENY_PATTERNS: Dict[str, List[str]] = {
    # 绝不允许的命令
    "bash": [
        r"rm\s+-rf\s+/",           # rm -rf /
        r">\s*/dev/sda",            # 覆写磁盘
        r"mkfs\.",                  # 格式化
        r"dd\s+if=.*of=/dev/",      # 裸写磁盘
        r":\(\)\s*\{\s*:\|:&\s*\};:", # fork bomb
        r"chmod\s+777\s+/",         # 全系统 chmod 777
        r"wget.*\|\s*(ba)?sh",      # curl pipe bash (远程代码执行)
    ],
    # 文件编辑：禁止修改关键系统文件
    "edit": [
        r"^/etc/(passwd|shadow|sudoers|ssh/)",
        r"^/boot/",
        r"^/proc/",
        r"^/sys/",
        r"~?/\.openclaw/openclaw\.json$",  # OpenClaw 主配置需要用户确认
    ],
    # Git 操作
    "git": [
        r"push\s+--force.*(main|master)",   # force push 主分支
        r"push\s+--delete.*(main|master)",  # 删除远程主分支
        r"reset\s+--hard.*origin",          # hard reset
    ],
}

# ── 内置 ask 规则（默认需确认，用户可选择豁免）────────────────────────────
HARD_ASK_PATTERNS: Dict[str, List[str]] = {
    "bash": [
        r"docker\s+(rm|kill|stop)",     # 容器操作
        r"systemctl\s+(stop|restart)",  # 系统服务
        r"pip\s+install",               # 包安装
        r"npm\s+install\s+-g",          # 全局安装
        r"git\s+config",                # git 配置修改
        r"curl.*\|\s*python",           # curl pipe python
    ],
    "gateway.restart": [r".*"],         # 所有 Gateway 重启都需确认
    "gateway.config.patch": [r".*"],    # 所有配置热改都需确认
    "edit": [
        r"^~?/\.(bashrc|zshrc|profile)", # shell 配置文件
        r"\.env$",                        # 环境变量
    ],
}


class PermissionGate:
    """
    三层权限管道。

    deny → ask → allow，按此顺序判断，第一条命中即返回。

    Args:
        role: 角色配置（可选）。None 时仅使用 HARD_DENY/ASK 规则。
        exempt_patterns: 用户豁免的正则列表。匹配的规则不触发 ask。

    Example:
        gate = PermissionGate(role)
        result = gate.check(ToolAction("bash", "rm -rf /tmp/cache"))
        # → ALLOW (不在 deny 也不在 ask 规则中)
    """

    def __init__(self, role: Optional[any] = None, exempt_patterns: Optional[List[str]] = None):
        self.role = role
        self.exempt = exempt_patterns or []
        self._compiled_deny: Dict[str, List[re.Pattern]] = {}
        self._compiled_ask: Dict[str, List[re.Pattern]] = {}
        self._compile_rules()

    def _compile_rules(self) -> None:
        """预编译所有正则，避免每次 check 重复编译"""
        for tool, patterns in HARD_DENY_PATTERNS.items():
            self._compiled_deny[tool] = [re.compile(p) for p in patterns]
        for tool, patterns in HARD_ASK_PATTERNS.items():
            self._compiled_ask[tool] = [re.compile(p) for p in patterns]

    def check(self, action: ToolAction) -> PermissionResult:
        """
        检查单个工具调用。

        Returns:
            DENY:  绝不允许
            ASK:   需用户确认
            ALLOW: 自动放行
        """
        # ── Layer 1: Hard Deny（永不饶恕）────────────────────────
        deny_patterns = self._compiled_deny.get(action.tool, [])
        for pattern in deny_patterns:
            if pattern.search(action.args) or pattern.search(action.context):
                return PermissionResult.DENY

        # ── Layer 2: Role Deny（角色禁止）────────────────────────
        if self.role:
            deny_tools = getattr(self.role, "deny_tools", [])
            if action.tool in deny_tools:
                return PermissionResult.DENY

        # ── Layer 3: Hard Ask（默认需确认）────────────────────────
        ask_patterns = self._compiled_ask.get(action.tool, [])
        for pattern in ask_patterns:
            if pattern.search(action.args) or pattern.search(action.context):
                # 检查是否在用户豁免列表中
                if self._is_exempted(action):
                    return PermissionResult.ALLOW
                return PermissionResult.ASK

        # ── Layer 4: Role Ask（角色需确认）────────────────────────
        if self.role:
            ask_tools = getattr(self.role, "ask_tools", [])
            if action.tool in ask_tools:
                return PermissionResult.ASK

        # ── Layer 5: Role Allow（角色白名单）──────────────────────
        if self.role:
            allowed = getattr(self.role, "allowed_tools", [])
            if action.tool in allowed or "*" in allowed:
                return PermissionResult.ALLOW
            # 不在白名单 → 拒绝
            return PermissionResult.DENY

        # 无角色信息 → 默认允许
        return PermissionResult.ALLOW

    def check_batch(self, actions: List[ToolAction]) -> Dict[str, PermissionResult]:
        """批量检查"""
        return {action.tool: self.check(action) for action in actions}

    def is_allowed(self, action: ToolAction) -> bool:
        return self.check(action) == PermissionResult.ALLOW

    def needs_ask(self, action: ToolAction) -> bool:
        return self.check(action) == PermissionResult.ASK

    def _is_exempted(self, action: ToolAction) -> bool:
        for pattern in self.exempt:
            if re.search(pattern, action.args):
                return True
        return False


# ── 工厂函数 ──────────────────────────────────────────────────────────────

def create_permission_gate(role_id: str = "default") -> PermissionGate:
    """
    根据角色 ID 创建权限门。

    Args:
        role_id: 角色 ID（"code_engineer", "novelist", "general" 等）

    Returns:
        配置好的 PermissionGate 实例
    """
    try:
        from .prompts.roles import ROLES, DEFAULT_ROLE
        role = ROLES.get(role_id, ROLES.get(DEFAULT_ROLE))
    except ImportError:
        role = None
    return PermissionGate(role=role)


# ── 测试 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 测试硬拒绝
    gate = PermissionGate(role=None)
    assert gate.check(ToolAction("bash", "rm -rf /")) == PermissionResult.DENY
    assert gate.check(ToolAction("bash", "dd if=/dev/zero of=/dev/sda")) == PermissionResult.DENY
    assert gate.check(ToolAction("bash", "echo hello")) == PermissionResult.ALLOW

    # 测试硬询问
    assert gate.check(ToolAction("bash", "docker rm container")) == PermissionResult.ASK
    assert gate.check(ToolAction("gateway.restart", "")) == PermissionResult.ASK

    # 测试角色 deny
    from dataclasses import dataclass as dc
    @dc
    class TestRole:
        allowed_tools: list = field(default_factory=lambda: ["read", "bash", "edit"])
        deny_tools: list = field(default_factory=lambda: ["bash"])  # 角色禁止 bash
        ask_tools: list = field(default_factory=lambda: ["edit"])  # 角色需确认 edit

    role = TestRole()
    gate = PermissionGate(role=role)
    assert gate.check(ToolAction("bash", "echo hello")) == PermissionResult.DENY  # 角色 deny
    assert gate.check(ToolAction("edit", "file.py")) == PermissionResult.ASK     # 角色 ask
    assert gate.check(ToolAction("read", "file.py")) == PermissionResult.ALLOW   # 角色 allow

    # 测试不在白名单
    assert gate.check(ToolAction("git", "status")) == PermissionResult.DENY

    print("✅ PermissionGate 所有测试通过")
