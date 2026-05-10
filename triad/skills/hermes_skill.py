#!/usr/bin/env python3
"""
hermes_skill.py — Hermes 认知编排层 OpenClaw 集成模块

改造目标：
  - 把 Hermes（模型路由/蜂群/技能进化/小说评估）包装成 OpenClaw 可调用的工具
  - 同时保留原有的独立运行能力
  - 新增 CLI 接口，OpenClaw 通过 exec 调用
  - 新增 REST API，OpenClaw 通过 web_fetch 调用

架构变化 (v3.0):
  Before: Hermes → 自己写的 Gateway → WebUI
  After:  OpenClaw Gateway → Hermes Skill → WebUI

用法:
  # CLI 模式（OpenClaw exec 调用）
  python hermes_skill.py route "帮我写一段代码" --strategy REASONING
  python hermes_skill.py swarm "调研 Rust vs Go" --agents researcher,writer,reviewer
  python hermes_skill.py evaluate novel --text "第一章内容..."
  python hermes_skill.py crystallize --task-id "task-001"

  # REST 模式（OpenClaw web_fetch 调用）
  python hermes_skill.py serve --port 19000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# 将 triad 根目录加入 sys.path，确保内部导入正常
TRIAD_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRIAD_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hermes_skill")


# ═══════════════════════════════════════════════════════════════════════════
# 1. 模型路由工具
# ═══════════════════════════════════════════════════════════════════════════

async def route_task(prompt: str, strategy: str = "AUTO") -> dict:
    """
    调用 Hermes ModelRouter，返回路由决策。

    OpenClaw 调用:
      exec: python hermes_skill.py route "<prompt>" --strategy REASONING
      web_fetch: POST /api/route {"prompt":"...","strategy":"AUTO"}
    """
    from mind.model_router import ModelRouter, RouteStrategy

    try:
        strategy_enum = RouteStrategy[strategy.upper()]
    except KeyError:
        strategy_enum = RouteStrategy.AUTO

    router = ModelRouter()
    decision = router.route(prompt, strategy_enum)

    return {
        "success": True,
        "strategy": decision.strategy.name,
        "primary_vendor": decision.primary.vendor,
        "primary_model": decision.primary.model_id,
        "estimated_tokens": decision.estimated_input_tokens,
        "will_truncate": decision.will_truncate,
        "context_summary": decision.context_summary[:200],
    }


async def execute_with_route(
    prompt: str,
    strategy: str = "AUTO",
    max_tokens: int = 4096,
) -> dict:
    """
    端到端：路由 + 执行，返回 LLM 响应。

    OpenClaw 调用:
      exec: python hermes_skill.py execute "<prompt>" --strategy CREATIVE
    """
    from mind.model_router import ModelRouter, RouteStrategy

    try:
        strategy_enum = RouteStrategy[strategy.upper()]
    except KeyError:
        strategy_enum = RouteStrategy.AUTO

    router = ModelRouter()
    decision = router.route(prompt, strategy_enum)
    response = await router.execute(decision, prompt)

    return {
        "success": True,
        "content": response.content,
        "vendor": response.vendor,
        "model": response.model_id,
        "usage": response.usage,
        "latency_ms": response.latency_ms,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 2. 蜂群调度工具
# ═══════════════════════════════════════════════════════════════════════════

async def run_swarm(
    task_description: str,
    agents: str = "researcher,writer,reviewer",
    aggregation: str = "concat",
    max_output_tokens: int = 6000,
) -> dict:
    """
    启动蜂群：多个 Agent 并发执行，结果聚合。

    agents: 逗号分隔的 Agent 类型，可选:
      researcher(default), researcher(deep), writer(default), writer(tech),
      reviewer(default), reviewer(code), reviewer(logic),
      coder(default), coder(frontend), coder(backend)

    OpenClaw 调用:
      exec: python hermes_skill.py swarm "调研 Rust vs Go" --agents researcher,writer,reviewer
    """
    from mind.swarm_orchestrator import (
        SwarmExecutor, SwarmTask, TemporaryAgent, AggregationMode,
    )
    from mind.model_router import ModelRouter

    router = ModelRouter()

    # 解析 agent 规格
    agent_factories = {
        "researcher": SwarmExecutor.create_researcher,
        "writer": SwarmExecutor.create_writer,
        "reviewer": SwarmExecutor.create_reviewer,
        "coder": SwarmExecutor.create_coder,
    }

    swarm_agents = []
    for spec in agents.split(","):
        spec = spec.strip()
        if "(" in spec and spec.endswith(")"):
            base, variant_s = spec.split("(", 1)
            variant = variant_s.rstrip(")")
        else:
            base = spec
            variant = "default"

        factory = agent_factories.get(base)
        if factory:
            agent = factory(variant)
            swarm_agents.append(agent)
        else:
            logger.warning(f"未知 Agent 类型: {base}")

    if not swarm_agents:
        swarm_agents = [
            SwarmExecutor.create_researcher("default"),
            SwarmExecutor.create_writer("default"),
        ]

    executor = SwarmExecutor(
        model_router=router,
        streaming_reporter=None,  # 蜂群模式下不上报，减小开销
        max_concurrent=3,
    )

    agg_map = {
        "concat": AggregationMode.CONCAT,
        "join": AggregationMode.JOIN,
        "best": AggregationMode.BEST,
        "merge": AggregationMode.MERGE,
    }
    agg_mode = agg_map.get(aggregation.lower(), AggregationMode.CONCAT)

    task = SwarmTask(
        task_id=f"swarm-{os.urandom(4).hex()}",
        description=task_description,
        agents=swarm_agents,
        parallel_limit=min(3, len(swarm_agents)),
        aggregation_mode=agg_mode,
        max_output_tokens=max_output_tokens,
    )

    result = await executor.execute_swarm(task)

    return {
        "success": True,
        "aggregated_content": result.aggregated_content,
        "agent_count": result.agent_count,
        "success_count": result.success_count,
        "failed_count": result.failed_count,
        "total_tokens": result.total_tokens,
        "total_latency_ms": result.total_latency_ms,
        "individual_results": [
            {
                "agent": r.agent_name,
                "model": r.model_used,
                "tokens": r.prompt_tokens + r.completion_tokens,
                "success": r.success,
                "error": r.error,
            }
            for r in result.individual_results
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. VRAM 调度工具
# ═══════════════════════════════════════════════════════════════════════════

async def get_vram_status() -> dict:
    """获取当前 VRAM 状态"""
    try:
        from hand.vram_scheduler_llama import VRAMScheduler
        scheduler = VRAMScheduler()
        return scheduler.get_status()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def switch_vram_mode(mode: str) -> dict:
    """
    手动切换 VRAM 模式。

    mode: "gpu" | "cpu"
    OpenClaw 调用:
      exec: python hermes_skill.py vram switch --mode cpu
    """
    from hand.vram_scheduler_llama import VRAMScheduler, RenderTask

    scheduler = VRAMScheduler()
    await scheduler.start()

    if mode == "cpu":
        # 切换到 CPU 模式：先获取渲染上下文触发切换
        task = RenderTask(
            task_id=f"manual-switch-{os.urandom(4).hex()}",
            workflow_type="manual",
            estimated_vram_mb=20480,
            priority=10,
        )
        ctx = await scheduler.acquire_render_memory(task)
        # 保持在渲染态（不自动释放）
        return {
            "success": True,
            "mode": "CPU_FALLBACK",
            "state": scheduler.state.name,
            "status": scheduler.get_status(),
        }
    elif mode == "gpu":
        # 恢复 GPU：释放所有渲染上下文
        status = await scheduler.get_status()
        return {
            "success": True,
            "mode": "GPU",
            "status": status,
        }
    else:
        return {"success": False, "error": f"Unknown mode: {mode}"}


# ═══════════════════════════════════════════════════════════════════════════
# 4. 技能进化工具
# ═══════════════════════════════════════════════════════════════════════════

def list_skills() -> dict:
    """列出所有已结晶的技能"""
    from mind.skill_crystallizer import SkillCrystallizer

    crystallizer = SkillCrystallizer()
    try:
        recipes = crystallizer.list_recipes()
        return {
            "success": True,
            "count": len(recipes),
            "skills": [
                {
                    "name": r.name,
                    "tags": r.tags,
                    "score_threshold": r.score_threshold,
                    "created_at": r.created_at,
                }
                for r in recipes
            ],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def evolve_skill(recipe_id: str, adjustments: dict) -> dict:
    """
    进化已有配方。

    OpenClaw 调用:
      exec: python hermes_skill.py evolve <recipe_id> \
              --temperature-delta 0.1 --extra-tools search_web
    """
    from mind.skill_crystallizer import SkillCrystallizer

    crystallizer = SkillCrystallizer()
    try:
        new_recipe = crystallizer.evolve_recipe(recipe_id, adjustments)
        crystallizer.save_recipe(new_recipe)
        return {
            "success": True,
            "name": new_recipe.name,
            "version": new_recipe.version,
            "evolved_from": new_recipe.evolved_from,
        }
    except FileNotFoundError:
        return {"success": False, "error": f"Recipe {recipe_id} not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# 5. 小说评估工具
# ═══════════════════════════════════════════════════════════════════════════

def evaluate_novel(
    text: str,
    characters: Optional[list] = None,
    use_llm: bool = False,
) -> dict:
    """
    评估小说文本质量（4 维评分）。

    OpenClaw 调用:
      exec: python hermes_skill.py evaluate novel --text-file chapter.txt
    """
    from mind.novel_curator import create_novel_curator

    curator = create_novel_curator(use_local_first=True)

    import asyncio
    loop = asyncio.get_event_loop()

    result = loop.run_until_complete(
        curator.evaluate(
            chapter_text=text,
            characters=characters or [],
            use_llm=use_llm,
        )
    )

    return {
        "success": True,
        "text_id": result.text_id,
        "overall_score": result.overall_score,
        "dimensions": {
            d.value: {
                "score": s.score,
                "comments": s.comments[:3],
                "violations": s.violations[:5],
            }
            for d, s in result.dimension_scores.items()
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# 6. 配置管理工具（关键：模型自主调参入口）
# ═══════════════════════════════════════════════════════════════════════════

def get_config() -> dict:
    """读取 Triad 当前配置"""
    from mind.config_manager import ConfigManager
    config = ConfigManager()
    return {
        "success": True,
        "config": config._config,
        "api_keys_configured": [
            k for k, v in config._config.get("api_keys", {}).items() if v
        ],
    }


def update_config(key: str, value: str) -> dict:
    """
    修改 Triad 配置（直接写 .env）。

    OpenClaw 调用:
      exec: python hermes_skill.py config set LLAMA_NGL 0
    """
    env_path = Path.home() / ".triad" / ".env"

    if not env_path.exists():
        return {"success": False, "error": ".env 不存在"}

    lines = env_path.read_text().splitlines()
    updated = False
    new_lines = []

    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(new_lines) + "\n")
    return {
        "success": True,
        "key": key,
        "value": value,
        "file": str(env_path),
    }


def manage_provider(
    action: str,
    provider_id: str,
    **kwargs,
) -> dict:
    """
    管理模型提供商（增删改查启停）。

    OpenClaw 调用:
      exec: python hermes_skill.py provider add --id kimi --name Kimi \
              --base-url https://api.moonshot.cn/v1
      exec: python hermes_skill.py provider toggle --id kimi
    """
    from mind.model_registry import ModelRegistry, ProviderConfig

    registry = ModelRegistry()

    if action == "add":
        provider = ProviderConfig(
            id=provider_id,
            name=kwargs.get("name", provider_id),
            base_url=kwargs.get("base_url", ""),
            api_key=kwargs.get("api_key", ""),
            context_window=int(kwargs.get("context_window", 4096)),
            tags=kwargs.get("tags", []),
            enabled=True,
            description=kwargs.get("description", ""),
        )
        ok = registry.add(provider)
        return {"success": ok, "provider": provider_id, "action": "added"}

    elif action == "toggle":
        ok = registry.toggle(provider_id)
        status = registry.get(provider_id)
        return {
            "success": ok,
            "provider": provider_id,
            "enabled": status.enabled if status else None,
        }

    elif action == "delete":
        ok = registry.delete(provider_id)
        return {"success": ok, "provider": provider_id, "action": "deleted"}

    elif action == "list":
        providers = registry.list()
        return {
            "success": True,
            "count": len(providers),
            "providers": [
                {"id": p.id, "name": p.name, "enabled": p.enabled, "tags": p.tags}
                for p in providers
            ],
        }

    else:
        return {"success": False, "error": f"Unknown action: {action}"}


# ═══════════════════════════════════════════════════════════════════════════
# 7. 记忆系统工具 (v3.0 P0)
# ═══════════════════════════════════════════════════════════════════════════

def get_memory_stats() -> dict:
    """记忆系统统计"""
    from mind.memory_system import MemorySystem
    mem = MemorySystem()
    return {"success": True, **mem.get_stats()}


def search_memory(query: str, category: str = None, limit: int = 20) -> dict:
    """搜索记忆事实"""
    from mind.memory_system import MemorySystem
    mem = MemorySystem()
    facts = mem.search_facts(query, category, limit)
    return {
        "success": True,
        "count": len(facts),
        "facts": [{"category": f.category, "subject": f.subject, "predicate": f.predicate, "obj": f.obj} for f in facts],
    }


def summarize_conversation(task_id: str, messages: list = None) -> dict:
    """压缩对话为摘要"""
    from mind.memory_system import MemorySystem
    mem = MemorySystem()
    if messages is None:
        summary = mem.load_summary(task_id)
        if summary:
            return {"success": True, "task_id": task_id, "summary": summary.summary, "topics": summary.key_topics}
        return {"success": False, "error": "Summary not found"}
    summary = mem.summarize_conversation(task_id, messages)
    return {"success": True, "task_id": task_id, "summary": summary.summary, "topics": summary.key_topics}


# ═══════════════════════════════════════════════════════════════════════════
# 8. REST API 服务（可选，OpenClaw web_fetch 调用）
# ═══════════════════════════════════════════════════════════════════════════

async def serve_api(port: int = 19000):
    """启动轻量 HTTP API 服务，供 OpenClaw 通过 web_fetch 调用"""
    import aiohttp
    from aiohttp import web

    async def handle_route(request: web.Request) -> web.Response:
        data = await request.json()
        result = await route_task(
            data.get("prompt", ""),
            data.get("strategy", "AUTO"),
        )
        return web.json_response(result)

    async def handle_swarm(request: web.Request) -> web.Response:
        data = await request.json()
        result = await run_swarm(
            data.get("task", ""),
            data.get("agents", "researcher,writer,reviewer"),
        )
        return web.json_response(result)

    async def handle_vram(request: web.Request) -> web.Response:
        result = await get_vram_status()
        return web.json_response(result)

    async def handle_skills(request: web.Request) -> web.Response:
        result = list_skills()
        return web.json_response(result)

    async def handle_config(request: web.Request) -> web.Response:
        if request.method == "GET":
            result = get_config()
        else:
            data = await request.json()
            result = update_config(data["key"], data["value"])
        return web.json_response(result)

    app = web.Application()
    app.router.add_post("/api/route", handle_route)
    app.router.add_post("/api/swarm", handle_swarm)
    app.router.add_get("/api/vram", handle_vram)
    app.router.add_get("/api/skills", handle_skills)
    app.router.add_route("*", "/api/config", handle_config)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"Hermes Skill API listening on http://0.0.0.0:{port}")
    logger.info("Endpoints: /api/route /api/swarm /api/vram /api/skills /api/config /health")

    # 保持运行
    await asyncio.Event().wait()


# ═══════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Hermes Skill — OpenClaw 集成 CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # route
    p_route = subparsers.add_parser("route", help="路由决策")
    p_route.add_argument("prompt")
    p_route.add_argument("--strategy", default="AUTO")

    # execute
    p_exec = subparsers.add_parser("execute", help="路由+执行")
    p_exec.add_argument("prompt")
    p_exec.add_argument("--strategy", default="AUTO")

    # swarm
    p_swarm = subparsers.add_parser("swarm", help="蜂群调度")
    p_swarm.add_argument("task")
    p_swarm.add_argument("--agents", default="researcher,writer,reviewer")
    p_swarm.add_argument("--aggregation", default="concat")

    # vram
    p_vram = subparsers.add_parser("vram", help="VRAM 操作")
    p_vram.add_argument("action", choices=["status", "switch"])
    p_vram.add_argument("--mode", choices=["gpu", "cpu"])

    # config
    p_config = subparsers.add_parser("config", help="配置管理")
    p_config.add_argument("action", choices=["get", "set"])
    p_config.add_argument("key", nargs="?", default="")
    p_config.add_argument("value", nargs="?", default="")

    # provider
    p_prov = subparsers.add_parser("provider", help="模型供应商管理")
    p_prov.add_argument("action", choices=["add", "toggle", "delete", "list"])
    p_prov.add_argument("--id", default="")
    p_prov.add_argument("--name", default="")
    p_prov.add_argument("--base-url", default="")
    p_prov.add_argument("--api-key", default="")
    p_prov.add_argument("--context-window", type=int, default=4096)
    p_prov.add_argument("--tags", nargs="*", default=[])
    p_prov.add_argument("--description", default="")

    # evaluate
    p_eval = subparsers.add_parser("evaluate", help="评估")
    p_eval.add_argument("type", choices=["novel"])
    p_eval.add_argument("--text", default="")
    p_eval.add_argument("--text-file", default="")

    # skills
    p_skills = subparsers.add_parser("skills", help="技能管理")
    p_skills.add_argument("action", choices=["list"])

    # evolve
    p_evolve = subparsers.add_parser("evolve", help="配方进化")
    p_evolve.add_argument("recipe_id")
    p_evolve.add_argument("--temperature-delta", type=float, default=0.0)
    p_evolve.add_argument("--extra-tools", nargs="*", default=[])

    # serve
    p_serve = subparsers.add_parser("serve", help="启动 REST API 服务")
    p_serve.add_argument("--port", type=int, default=19000)

    # memory (v3.0)
    p_mem = subparsers.add_parser("memory", help="记忆管理")
    p_mem.add_argument("action", choices=["stats", "search", "summarize", "compact"])
    p_mem.add_argument("query", nargs="?", default="")
    p_mem.add_argument("--category", default=None)
    p_mem.add_argument("--limit", type=int, default=20)
    p_mem.add_argument("--task-id", default="")

    # manager (v3.0)
    p_mgr = subparsers.add_parser("manager", help="Manager-Executor 层级调度")
    p_mgr.add_argument("task", nargs="?", default="")

    # code (v3.1)
    p_code = subparsers.add_parser("code", help="代码任务 — 委派给 CheetahClaws")
    p_code.add_argument("task", nargs="?", default="")
    p_code.add_argument("--dir", default=None)
    p_code.add_argument("--model", default=None)

    args = parser.parse_args()

    if args.command == "serve":
        asyncio.run(serve_api(args.port))
    elif args.command == "route":
        result = asyncio.run(route_task(args.prompt, args.strategy))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "execute":
        result = asyncio.run(execute_with_route(args.prompt, args.strategy))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "swarm":
        result = asyncio.run(run_swarm(args.task, args.agents, args.aggregation))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "vram":
        if args.action == "status":
            result = asyncio.run(get_vram_status())
        else:
            result = asyncio.run(switch_vram_mode(args.mode))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "config":
        if args.action == "get":
            print(json.dumps(get_config(), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(update_config(args.key, args.value), ensure_ascii=False, indent=2))
    elif args.command == "provider":
        result = manage_provider(
            args.action,
            args.id,
            name=args.name,
            base_url=args.base_url,
            api_key=args.api_key,
            context_window=args.context_window,
            tags=args.tags,
            description=args.description,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "evaluate":
        text = args.text
        if args.text_file:
            text = Path(args.text_file).read_text()
        result = evaluate_novel(text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "skills":
        print(json.dumps(list_skills(), ensure_ascii=False, indent=2))
    elif args.command == "evolve":
        adjustments = {
            "temperature_delta": args.temperature_delta,
            "extra_tools": args.extra_tools,
        }
        print(json.dumps(evolve_skill(args.recipe_id, adjustments), ensure_ascii=False, indent=2))
    elif args.command == "memory":
        if args.action == "stats":
            print(json.dumps(get_memory_stats(), ensure_ascii=False, indent=2))
        elif args.action == "search":
            result = search_memory(args.query, args.category, args.limit)
            if result["facts"]:
                for f in result["facts"]:
                    print(f"[{f['category']}] {f['subject']} {f['predicate']} {f['obj']}")
            else:
                print("未找到匹配的事实")
        elif args.action == "summarize":
            result = summarize_conversation(args.task_id or "latest")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == "compact":
            from mind.memory_system import MemorySystem
            n = MemorySystem().compact_conversations()
            print(json.dumps({"success": True, "compacted": n}, ensure_ascii=False))
    elif args.command == "manager" and args.task:
        from mind.manager_executor import ManagerExecutor
        mex = ManagerExecutor(router=None, reporter=None)
        result = asyncio.run(mex.execute(args.task))
        print(json.dumps({
            "success": True,
            "task_id": result.task_id,
            "subtasks": len(result.plan.subtasks),
            "aggregation": result.plan.aggregation_strategy,
            "success_count": result.success_count,
            "failed_count": result.failed_count,
            "total_tokens": result.total_tokens,
            "cost_saved_pct": result.cost_saved_pct,
            "final_output": result.final_output[:500],
        }, ensure_ascii=False, indent=2))
    elif args.command == "code" and args.task:
        from skills.code_agent_bridge import CodeAgentBridge
        bridge = CodeAgentBridge()
        result = bridge.run(args.task, args.dir, args.model)
        print(result["output"] if result.get("success") else f"Error: {result.get('error')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
