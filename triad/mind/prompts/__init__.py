"""prompts 包 — Triad 多角色 System Prompt 与配置中心

导出：
- RoleConfig : 角色配置数据类
- ROLES      : 角色注册表字典
- DEFAULT_ROLE : 兜底角色标识
"""

from .roles import DEFAULT_ROLE, ROLES, RoleConfig

__all__ = ["RoleConfig", "ROLES", "DEFAULT_ROLE"]
