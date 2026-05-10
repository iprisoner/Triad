"""
code_agent_bridge.py — CheetahClaws 代码执行桥接 (Triad v3.1)

策略: 寄生而非重写。CheetahClaws 已经是一个生产级 AI 编码 Agent，
Triad 不需要再写一套。只需要把代码任务委派给它。

集成方式:
  1. CheetahClaws 作为 Triad 的子模块或 pip 依赖
  2. 共享 API Key (.env)
  3. Triad 的 PermissionGate 包裹 CheetahClaws 的工具调用
  4. 结果通过 hermes_skill.py 返回给 OpenClaw

用法:
  from skills.code_agent_bridge import CodeAgentBridge
  bridge = CodeAgentBridge()
  result = bridge.run("修复 vram_scheduler.py 里的死锁")
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("triad.code_agent_bridge")


class CodeAgentBridge:
    """
    代码 Agent 桥接 — 把代码任务委派给 CheetahClaws。

    三层委派策略（按可用性降级）:
      1. Python import: 直接 import CheetahClaws 的 agent.run()
      2. CLI: cheetahclaws -p "..." (如果已 pip install)
      3. subprocess: python path/to/agent.py "..." (如果本地有源码)

    全部使用 Triad 自己的 API Key（不再需要 Anthropic 注册）。
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.available = self._detect_availability()
        self._agent_state = None

    def _detect_availability(self) -> str:
        """检测 CheetahClaws 可用性"""
        # 1. Python import
        try:
            import agent  # noqa: F401
            logger.info("CodeAgentBridge: CheetahClaws Python import OK")
            return "import"
        except ImportError:
            pass

        # 2. CLI
        try:
            result = subprocess.run(
                ["cheetahclaws", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                logger.info("CodeAgentBridge: cheetahclaws CLI OK")
                return "cli"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # 3. Subprocess from source
        agent_py = Path.home() / ".openclaw/workspace/nano-claude-code-ref/agent.py"
        if agent_py.exists():
            logger.info(f"CodeAgentBridge: source at {agent_py}")
            return "source"

        logger.warning("CodeAgentBridge: CheetahClaws not available")
        return "none"

    def run(self, task: str, working_dir: Optional[str] = None,
            model: Optional[str] = None) -> dict:
        """
        执行代码任务。

        Args:
            task: 自然语言任务描述
            working_dir: 工作目录（默认当前目录）
            model: 指定模型（默认用 Triad 配置的模型）

        Returns:
            {"success": True/False, "output": "...", "model": "...", "tokens": ...}
        """
        if self.available == "none":
            return {
                "success": False,
                "error": "CheetahClaws not available. Install: pip install cheetahclaws",
            }

        cwd = working_dir or str(Path.home() / ".openclaw" / "workspace")

        if self.available == "import":
            return self._run_via_import(task, cwd, model)
        elif self.available == "cli":
            return self._run_via_cli(task, cwd, model)
        else:
            return self._run_via_source(task, cwd, model)

    def _run_via_import(self, task: str, cwd: str, model: Optional[str]) -> dict:
        """通过 Python import 直接调用"""
        import os as _os
        _os.chdir(cwd)

        # 读取 Triad 的 API Key 配置
        from mind.config_manager import config as triad_config

        cc_config = {
            "model": model or "deepseek-v4-pro",
            "max_tokens": 40000,
            "permission_mode": "auto",
            "deepseek_api_key": triad_config.get_api_key("deepseek"),
            "kimi_api_key": triad_config.get_api_key("kimi"),
        }

        import agent as cc_agent
        state = cc_agent.AgentState()

        output_parts = []
        total_tokens = 0

        try:
            for event in cc_agent.run(task, state, cc_config, ""):
                if hasattr(event, "text"):
                    output_parts.append(event.text)
                if hasattr(event, "in_tokens"):
                    total_tokens += event.in_tokens + getattr(event, "out_tokens", 0)
        except Exception as e:
            return {"success": False, "error": str(e)}

        return {
            "success": True,
            "output": "".join(output_parts[-2000:]),  # 最后 2000 字
            "model": cc_config["model"],
            "tokens": total_tokens,
            "engine": "cheetahclaws-import",
        }

    def _run_via_cli(self, task: str, cwd: str, model: Optional[str]) -> dict:
        """通过 cheetahclaws CLI"""
        cmd = ["cheetahclaws", "-p", task]
        if model:
            cmd.extend(["--model", model])

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 分钟超时
            )
            return {
                "success": result.returncode == 0,
                "output": result.stdout[-2000:] + result.stderr[-500:],
                "engine": "cheetahclaws-cli",
                "model": model or "default",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "CheetahClaws timeout (>5min)"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _run_via_source(self, task: str, cwd: str, model: Optional[str]) -> dict:
        """通过源码 subprocess"""
        source_dir = Path.home() / ".openclaw/workspace/nano-claude-code-ref"
        cmd = [sys.executable, str(source_dir / "cheetahclaws.py"), "-p", task]
        if model:
            cmd.extend(["--model", model])

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=300,
            )
            return {
                "success": result.returncode == 0,
                "output": result.stdout[-2000:] + result.stderr[-500:],
                "engine": "cheetahclaws-source",
                "model": model or "default",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "CheetahClaws timeout (>5min)"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Triad Code Agent Bridge")
    parser.add_argument("task", help="代码任务描述")
    parser.add_argument("--dir", default=None, help="工作目录")
    parser.add_argument("--model", default=None, help="模型")
    args = parser.parse_args()

    bridge = CodeAgentBridge()
    print(f"可用性: {bridge.available}")
    print(f"执行: {args.task[:100]}...")
    result = bridge.run(args.task, args.dir, args.model)
    print(json.dumps(result, ensure_ascii=False, indent=2))
