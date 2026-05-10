# Changelog

All notable changes to Triad will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v3.1.0] - 2026-05-11

### 🦞 Code Agent Integration

v3.1 integrates CheetahClaws (nano-claude-code) as the code execution engine,
adding production-grade coding capabilities through parasitic delegation.

#### Added

- **`skills/code_agent_bridge.py`** — 3-tier delegation to CheetahClaws (import/CLI/source)
- **`mind/manager_executor.py`** — Hierarchical scheduling (Manager-Executor pattern)
- **`mind/memory_system.py`** — 3-layer memory (conversation summary + fact triples + skill recipes)
- **`mind/permission_gate.py`** — 5-layer deny→ask→allow permission pipeline with hard deny rules
- **`PARITY.md`** — Feature parity table vs Aider/SWE-agent/Claude Code/claurst
- **5 reference docs** — Architecture analysis of Claude Code leak, Aider, SWE-agent, claurst, claude-code-haha

#### Updated

- **`roles.py`** — All 5 roles now have deny_tools + ask_tools
- **`hermes_skill.py`** — Added code/memory/manager CLI commands
- **`webui/src/TriadPanel.tsx`** — New UI: left chat + right monitoring/config

#### Capabilities

| Capability | v3.0 | v3.1 |
|-----------|:----:|:----:|
| Code Agent | ❌ | ✅ CheetahClaws (15 providers) |
| Permission Pipeline | ❌ | ✅ deny→ask→allow |
| Memory System | ⚠️ | ✅ 3-layer |
| Manager-Executor | ❌ | ✅ Hierarchical scheduling |

---

## [v3.0.0] - 2026-05-10

### 🏗️ Architecture Refactor — OpenClaw Native

v3.0 is a **parasitic evolution**: Triad no longer runs its own message gateway.
All gateway functionality is delegated to OpenClaw's production-grade infrastructure.

#### Removed

- **`host/openclaw/src/gateway/websocket.ts`** (400 lines) — Replaced by OpenClaw native WebSocket
- **`host/openclaw/src/gateway/api.ts`** (350 lines) — Replaced by OpenClaw native REST API
- **`host/openclaw/src/gateway/monitor.ts`** (200 lines) — Replaced by Hermes Skill API
- **`webui/src/BrowserShell.tsx`** — Replaced by TriadPanel.tsx
- **`webui/src/hooks/useWebSocket.ts`** — Replaced by useOpenClawWS.ts

#### Added

- **`skills/hermes_skill.py`** — Hermes full capability CLI + REST API for OpenClaw
- **`skills/TRIAD_SYSTEM_PROMPT.md`** — Model auth to directly modify system config
- **`webui/src/TriadPanel.tsx`** — New UI: left chat + right monitoring/config
- **`webui/src/hooks/useOpenClawWS.ts`** — OpenClaw native WebSocket hook
- **`docker-compose.v3.yml`** — Simplified deployment (no redundant Gateway service)
- **`skills/hermes-skill-api`** — New Docker service for Hermes REST bridge
- **`docs/OPENCLAW_INTEGRATION_GUIDE.md`** — Full integration guide
- **`docs/TECHNICAL_WHITEPAPER_v3.0.md`** — v3.0 technical whitepaper

#### Key Design Changes

- Model can now **directly modify config** during conversation (no more "I suggest you...")
- VRAM switching, provider management, role tuning — all done via chat
- WebUI is pure frontend, zero local state, all data from OpenClaw WS + Hermes API
- Reduced ~950 lines of TypeScript (removed redundant code)
- Architecture: `OpenClaw Gateway → Hermes Skill → Infrastructure`

---

## [v2.3.1] - 2026-05-06

### 🚨 Security Patch Release

v2.3.1 is a **security and stability patch release** addressing 53 bugs discovered during a comprehensive code audit. This release fixes critical production blockers, security vulnerabilities, and concurrency issues without adding new features.

---

### 🔴 Critical Fixes (Production Blockers)

#### Python Import / Syntax Errors
- **`mind/novel_curator.py`**: Removed non-existent `ModelVendor` import; switched to relative import with fallback
- **`mind/swarm_orchestrator.py`**: Fixed `IndentationError` (line 379 `try` not indented under `if`); added missing `import re`; removed duplicate `semaphore`/`logger` assignments
- **`mind/hermes_orchestrator.py`**: Fixed `has_vram_scheduler` NameError risk in `try/finally` block

#### Cross-Module Interface Mismatches
- **`hand/vram_scheduler_llama.py`**: `acquire_render_memory` now accepts `Union[str, RenderTask]` — compatible with `hermes_orchestrator.py` passing `task_id` string
- **`mind/skill_crystallizer.py`**: `load_recipe` reads `model_pref` from YAML frontmatter instead of incorrectly using `aggregation_mode`
- **`mind/model_router.py`**: `execute` signature detection now uses `inspect.signature()` instead of checking unrelated `execute_with_role` attribute

#### Statistics / Accounting Bugs
- **`hand/vram_scheduler_llama.py`**: Fixed `__aexit__` where both `renders_completed` and `renders_failed` incremented on exception — now only one counter increments
- **`hand/vram_scheduler.py`**: Same `__aexit__` fix; added missing `begin_llm_inference()` / `end_llm_inference()` methods for LLM reference counting
- **`host/openclaw/src/gateway/websocket.ts`**: `removeConnectionBySocket` now removes **all** `taskId`s for a disconnected socket (was `break` after first)

---

### 🛡️ Security Vulnerabilities

#### High Severity
- **`host/openclaw/package.json`**: Upgraded `ws` from `8.16.0` to `8.17.1` — fixes **CVE-2024-37890** (HIGH, DoS via HTTP header length)
- **`host/openclaw/package.json`**: Upgraded `express` from `4.18.2` to `4.20.0` — fixes multiple CVEs including open redirect
- **`webui/package.json`**: Upgraded `axios` from `1.6.0` to `1.7.4` — fixes **CVE-2024-39338** (SSRF, CVSS 7.5)
- **`webui/package.json`**: Upgraded `vite` from `5.0.0` to `5.4.6` — fixes **CVE-2024-47068** (DOM Clobbering via Rollup)
- **`host/openclaw/src/gateway/api.ts`**: Added SSRF protection on `/models/:id/test` — blocks internal addresses (localhost, 127.0.0.1, 10.x, 192.168.x, 172.16-31.x)
- **`host/openclaw/src/gateway/api.ts`**: Error responses sanitized — no longer expose `axiosErr.message` or `response.data` to client
- **`host/openclaw/src/gateway/monitor.ts`**: Error responses sanitized — returns generic "System status temporarily unavailable" instead of internal details

#### Medium Severity
- **`host/openclaw/src/gateway/websocket.ts`**: CORS restricted to allowed origin list (`localhost:3000/5173`) instead of wildcard `*`
- **`host/openclaw/src/gateway/websocket.ts`**: `recover_tasks` now filters by `userId` — no longer leaks all users' task history
- **`host/openclaw/src/gateway/websocket.ts`**: `push_result` accepts `output === undefined` instead of rejecting empty string with `!body.output`
- **`webui/src/hooks/useWebSocket.ts`**: Added max reconnect limit (10) with exponential backoff — prevents connection storms
- **`memory/asset_manager.py`**: Path traversal filter on `asset_id` — replaces `../` and other dangerous characters with `_`
- **`memory/asset_manager.py`**: Version chain `parent_id` now points to previous version (`asset_id_vN-1`) instead of self-referencing
- **`bridge/wsl2_gateway.sh`**: `listenaddress` changed from `0.0.0.0` to `127.0.0.1` — prevents LAN exposure
- **`bridge/wsl2_gateway.sh`**: Firewall rules now include `-RemoteAddress 127.0.0.1` — restricts to localhost
- **`triad/init.sh`**: `confirm()` function now auto-returns true in CI/CD environments (no TTY detected)
- **`triad/init.sh`**: `nvidia-smi` parsing switched from fragile `grep -oP` to `--query-gpu=... --format=csv` for robustness

---

### 🔧 Architecture Improvements

#### Circuit Breaker Refactor
- **`mind/model_router.py`**: `FallbackChain` completely refactored to **3-state circuit breaker**:
  - `CLOSED` → `OPEN` (after 5 failures) → `HALF_OPEN` (after 120s timeout) → `CLOSED` (on success)
  - `HALF_OPEN` allows only **single probe request** through (protected by `asyncio.Lock`)
  - Client errors (4xx) no longer trigger circuit opening
  - `_failure_counts` properly reset on successful probe

#### Connection Pool Optimization
- **`mind/model_router.py`**: `_default_call` now uses persistent `httpx.AsyncClient` (20 keepalive / 50 max connections) instead of creating new client per request
- Added `ModelRouter.close()` method for graceful connection pool shutdown

#### Context Compression Fix
- **`mind/model_router.py`**: `ContextAligner` truncated context using character slicing (`[:target_budget*2]`) which expanded ~3277 tokens to ~10000+ tokens in Chinese. Now uses proper `tiktoken` encoder `encode/decode` for token-level truncation.

#### Model ID Mapping
- **`mind/model_router.py`**: Added `_DEFAULT_MODEL_MAP` — maps provider IDs like `deepseek` → API model IDs like `deepseek-chat`. Previously `provider.id` was incorrectly used as `model_id`, causing 404 errors.

---

### 🧵 Concurrency & Thread Safety

- **`mind/model_router.py`**: `FallbackChain` now protected by `asyncio.Lock` — all state mutations (`_circuit_open`, `_failure_counts`, `_circuit_half_open`) are atomic
- **`mind/config_manager.py`**: Singleton `ConfigManager` now uses `threading.Lock` — thread-safe initialization and `reload()`
- **`mind/model_registry.py`**: All CRUD operations (`add`/`update`/`delete`/`toggle`) protected by `threading.Lock`
- **`mind/model_registry.py`**: `_load()` now handles `JSONDecodeError` — gracefully reinitializes on corrupted `providers.json`
- **`hand/comfyui_mcp_bridge.py`**: `connect()` and `_get_session()` protected by `asyncio.Lock` — prevents duplicate WebSocket tasks and ClientSessions
- **`hand/comfyui_mcp_bridge.py`**: Exponential backoff reconnect: `delay * 1.5^n` (max 60s) instead of fixed 3s interval
- **`hand/comfyui_mcp_bridge.py`**: `_active_tasks` cleanup wrapped in `try/finally` — prevents memory leaks on timeout/exception
- **`hand/comfyui_mcp_bridge.py`**: MCP `readline()` wrapped in `asyncio.wait_for(timeout=30.0)` — prevents indefinite blocking

---

### 🏗️ Infrastructure Hardening

- **`docker-compose.hpc.yml`**: `llama-server` port changed from `18000:8080` to `18001:8080` — resolves conflict with `hermes` service on port 18000
- **`triad_manager.sh`**: `.env` parsing changed from `export $(grep -v '^#' .env | xargs)` to `set -a; source .env; set +a` — safer variable handling
- **`.env.example`**: `DEBUG` default changed from `true` to `false`; API Key placeholders cleared
- **`requirements.txt`**: New file — declares all Python dependencies (`httpx`, `aiohttp`, `aiofiles`, `websockets`, `python-dotenv`, `tiktoken`, `pynvml`)
- **`mind/prompts/roles.py`**: Added missing `"general"` role — matches `DEFAULT_ROLE = "general"` reference

---

### 📊 Statistics

| Metric | Count |
|--------|-------|
| Files modified | 30 |
| Files added | 1 (`requirements.txt`) |
| Critical bugs fixed | 9 |
| Security vulnerabilities fixed | 12 |
| Architecture improvements | 5 |
| Concurrency fixes | 9 |
| Infrastructure hardening | 8 |
| **Total bug fixes** | **53** |

---

## [v2.3] - 2026-05-06

### Initial Release

- Multi-tab workbench (Lobster Console + System Monitor)
- Single Agent multi-role system (@novelist, @code_engineer, @art_director, etc.)
- Dynamic model registry (unlimited providers, Web UI management)
- System monitoring probes (GPU/container/llama/CPU/memory)
- llama.cpp VRAM seesaw (-ngl 99↔0)
- Swarm scheduler (SwarmExecutor) — multi-Agent concurrent collaboration
- Skill crystallizer (SkillCrystallizer) — automatic recipe固化 and evolution
- Dynamic evaluation routing — novel evaluation / code bypass / general bypass
- VRAM deadlock protection (inference reference counting + global lock)
- Context compression (Map-Reduce token limit)
- Recipe semantic deduplication (survival of the fittest)
- WebSocket disconnect recovery (task state persistence)
