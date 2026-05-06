"""
vram_scheduler_llama.py
VRAM 极限调度器 — llama.cpp (llama-server) 版

架构（22GB 2080Ti 魔改显存分时复用）：
  常态（文本/代码任务）：
    ├── 2GB:  Embedding 模型（BGE-large fp16，常驻，永不卸载）
    ├── 9GB:  Qwen-14B Q4_K_M（llama-server -ngl 99，全部层 GPU）
    ├── 9GB:  空闲 / ComfyUI 预热缓存
    └── 2GB:  CUDA Context / 驱动开销

  渲染态（图像/视频任务）：
    ├── 2GB:  Embedding（常驻）
    ├── 0GB:  LLM 切到 CPU 模式（-ngl 0），权重保留在 RAM via mmap
    └── 20GB: ComfyUI 独占（SDXL + ControlNet + SVD）

  渲染完成后：
    ├── 2GB:  Embedding
    ├── 9GB:  LLM GPU 恢复（-ngl 99，mmap 热映射 <2s）
    └── 11GB: 空闲回归

状态机：
  IDLE -> CPU_FALLBACK -> RENDERING -> RECOVERING -> IDLE

CPU 亲和性动态跷跷板（NUMA 修复）：
  GPU 模式（常态）：
    - Docker 容器 cpus=0.125（4 核：20-23,44-47）
    - llama-server -ngl 99 -t 4（4 控制线程，GPU 做 heavy lifting）
  CPU_FALLBACK 模式：
    - Docker 容器 cpus=0.67（32 核：0-31）
    - llama-server -ngl 0 -t 32（32 线程并行 CPU 计算）

公共 API：
  - VRAMScheduler.acquire_render_memory(task) -> RenderContext
  - VRAMScheduler.release_render_memory(ctx)
  - VRAMScheduler.get_status() -> dict

作者：Triad System Architect (llama.cpp migration + NUMA fix)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Literal, Optional, Union

import aiohttp

logger = logging.getLogger("triad.vram_scheduler_llama")

# ---------------------------------------------------------------------------
# 依赖适配
# ---------------------------------------------------------------------------

try:
    import pynvml

    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    logger.warning("pynvml not installed; falling back to mock VRAM monitoring")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class VRAMState(Enum):
    """显存使用状态机（llama.cpp 版，新增 CPU_FALLBACK）"""

    IDLE = auto()           # 常态：LLM 在 GPU 全速运行
    CPU_FALLBACK = auto()   # 过渡态：LLM 切到 CPU，显存释放中
    RENDERING = auto()      # 渲染态：ComfyUI 独占显存
    RECOVERING = auto()     # 恢复态：LLM GPU 模式热恢复中
    EMERGENCY = auto()      # 紧急态：显存不足，强制降级


@dataclass(frozen=True)
class VRAMBudget:
    """显存预算分配（单位：MB） — llama.cpp 版"""

    embedding: int = 2048       # 2GB 常驻 Embedding（永不卸载）
    llm_gpu: int = 9216         # 9GB LLM GPU 常驻（-ngl 99）
    comfyui_peak: int = 20480   # 20GB ComfyUI 渲染峰值
    safety_margin: int = 2048   # 2GB 系统预留 / 安全边距

    @property
    def total(self) -> int:
        """总预算（应 ≈ 22528 MB = 22 GB）"""
        return self.embedding + self.llm_gpu + self.comfyui_peak + self.safety_margin


@dataclass
class VRAMSnapshot:
    """单次显存采样"""

    timestamp: float
    total_mb: int
    free_mb: int
    used_mb: int
    process_allocations: Dict[int, int] = field(default_factory=dict)

    @property
    def comfyui_available_mb(self) -> int:
        """可用于 ComfyUI 的显存（仅扣除安全边距）。

        说明：llama-server 切到 CPU 模式后，free_mb 已不包含 LLM GPU 占用，
        因此只需再扣除 safety_margin 即可。
        """
        budget = VRAMBudget()
        return max(0, self.free_mb - budget.safety_margin)


@dataclass
class RenderTask:
    """渲染任务描述"""

    task_id: str
    workflow_type: str          # "sdxl", "controlnet", "svd", "instantid", "tts"
    estimated_vram_mb: int
    priority: int = 0           # 越大越优先
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# NVML 监控核心
# ---------------------------------------------------------------------------

class NVMLMonitor:
    """
    基于 pynvml 的显存监控器。
    支持单 GPU（魔改 2080Ti 22GB）场景。
    """

    def __init__(self, device_index: int = 0, polling_interval_ms: int = 500):
        self.device_index = device_index
        self.polling_interval_ms = polling_interval_ms
        self._handle: Optional[Any] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._listeners: List[Callable[[VRAMSnapshot], None]] = []
        self._lock = asyncio.Lock()

        if PYNVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
                self._handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
                logger.info(f"NVML initialized for GPU {device_index}")
            except Exception as e:
                logger.error(f"NVML init failed: {e}; using mock mode")
                self._handle = None
        else:
            self._handle = None

    # ------------------------------------------------------------------
    # 采样 API
    # ------------------------------------------------------------------

    def snapshot(self) -> VRAMSnapshot:
        """获取当前显存快照（线程安全，阻塞约 5-10ms）"""
        if self._handle is None:
            return self._mock_snapshot()

        info = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        total = int(info.total // 1024 // 1024)
        used = int(info.used // 1024 // 1024)
        free = int(info.free // 1024 // 1024)

        process_allocs: Dict[int, int] = {}
        try:
            procs = pynvml.nvmlDeviceGetComputeRunningProcesses(self._handle)
            for p in procs:
                process_allocs[p.pid] = (
                    int(p.usedGpuMemory // 1024 // 1024)
                    if hasattr(p, "usedGpuMemory")
                    else 0
                )
        except Exception:
            pass

        return VRAMSnapshot(
            timestamp=time.time(),
            total_mb=total,
            free_mb=free,
            used_mb=used,
            process_allocations=process_allocs,
        )

    def _mock_snapshot(self) -> VRAMSnapshot:
        """模拟显存快照（用于 CI / 无 GPU 环境）

        常态假设：
          - 22 GB 总量
          - 13 GB 已用（2GB embed + 9GB llm + 2GB 系统）
          - 9 GB 空闲
        """
        return VRAMSnapshot(
            timestamp=time.time(),
            total_mb=22528,
            free_mb=9216,
            used_mb=13312,
            process_allocations={},
        )

    # ------------------------------------------------------------------
    # 异步轮询
    # ------------------------------------------------------------------

    def add_listener(self, callback: Callable[[VRAMSnapshot], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[VRAMSnapshot], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    async def start_polling(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("VRAM polling started")

    async def stop_polling(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("VRAM polling stopped")

    async def _poll_loop(self) -> None:
        while self._running:
            snap = await asyncio.get_running_loop().run_in_executor(
                None, self.snapshot
            )
            for cb in self._listeners:
                with contextlib.suppress(Exception):
                    cb(snap)
            await asyncio.sleep(self.polling_interval_ms / 1000.0)

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def close(self) -> None:
        if PYNVML_AVAILABLE and self._handle is not None:
            with contextlib.suppress(Exception):
                pynvml.nvmlShutdown()

    async def reset_device(self) -> None:
        """极端情况：重置 GPU 上下文（慎用）"""
        if self._handle is None:
            logger.warning("Mock mode: skip GPU reset")
            return
        logger.critical("Requesting GPU context reset — all CUDA apps will crash!")


# ---------------------------------------------------------------------------
# CpuAffinityManager（Docker 容器 CPU 亲和性动态管理器）
# ---------------------------------------------------------------------------

class CpuAffinityManager:
    """
    Docker 容器 CPU 亲和性动态管理器。

    核心机制（NUMA 跷跷板）：
      - GPU 模式：llama-server 仅需控制线程，收缩到 4 核（20-23,44-47）
      - CPU_FALLBACK 模式：释放 32 核（0-31），让 llama.cpp 并行计算

    NUMA 拓扑（48 逻辑核双路 EPYC）：
      - Node 0: 0-11, 24-35（前 12 物理核 + SMT）
      - Node 1: 12-23, 36-47（后 12 物理核 + SMT）

    绑核策略：
      - GPU 模式（IDLE）：20-23,44-47 = Node 1 的末尾 4 核（远离 GPU PCIe 根复用器）
      - CPU_FALLBACK：0-31 = Node 0 全核 + Node 1 前 8 核（最大化 LLC 命中）

    宿主机模式回退：检测不到 Docker 容器时静默 pass，兼容裸机部署。
    """

    GPU_MODE_CPUS: str = "0.125"
    GPU_MODE_CPUSET: str = "20-23,44-47"
    GPU_MODE_THREADS: int = 4

    CPU_FALLBACK_CPUS: str = "0.67"
    CPU_FALLBACK_CPUSET: str = "0-31"
    CPU_FALLBACK_THREADS: int = 32

    def __init__(self, container_name: Optional[str] = None):
        self.container_name = container_name or os.getenv(
            "LLAMA_CONTAINER_NAME", "triad-llama-server"
        )
        self.container_id: Optional[str] = None
        self.has_docker: bool = False
        self._check_docker_access()

    def _check_docker_access(self) -> None:
        """检查是否有 docker update 权限，并缓存容器 ID。"""
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", f"name={self.container_name}", "-q"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.container_id = result.stdout.strip() or None
            self.has_docker = bool(self.container_id)
            if self.has_docker:
                logger.info(
                    f"CpuAffinityManager: found container {self.container_name} "
                    f"(id={self.container_id[:12]})"
                )
            else:
                logger.info(
                    f"CpuAffinityManager: container {self.container_name} not found, "
                    f"running in host mode (no docker updates)"
                )
        except Exception as e:
            logger.warning(f"CpuAffinityManager: docker check failed: {e}")
            self.container_id = None
            self.has_docker = False

    def expand_to_cpu_fallback(self) -> None:
        """
        CPU_FALLBACK：释放 32 核（cpus=0.67, cpuset-cpus=0-31）。
        必须在启动 CPU 模式 llama-server 之前调用，确保容器已有足够核心。
        """
        if not self.has_docker or not self.container_id:
            logger.debug("CpuAffinityManager: skip expand (host mode)")
            return

        cmd = [
            "docker", "update",
            f"--cpus={self.CPU_FALLBACK_CPUS}",
            f"--cpuset-cpus={self.CPU_FALLBACK_CPUSET}",
            self.container_id,
        ]
        logger.info(
            f"CpuAffinityManager: EXPAND -> {self.CPU_FALLBACK_CPUS} cpus, "
            f"cpuset={self.CPU_FALLBACK_CPUSET} (CPU_FALLBACK)"
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error(
                f"CpuAffinityManager: docker update expand failed: {result.stderr.strip()}"
            )
        else:
            logger.info("CpuAffinityManager: expand succeeded")

    def shrink_to_gpu_mode(self) -> None:
        """
        GPU 模式：收缩回 4 核（cpus=0.125, cpuset-cpus=20-23,44-47）。
        应在 GPU 模式 llama-server 健康启动之后调用，释放核心给其他任务。
        """
        if not self.has_docker or not self.container_id:
            logger.debug("CpuAffinityManager: skip shrink (host mode)")
            return

        cmd = [
            "docker", "update",
            f"--cpus={self.GPU_MODE_CPUS}",
            f"--cpuset-cpus={self.GPU_MODE_CPUSET}",
            self.container_id,
        ]
        logger.info(
            f"CpuAffinityManager: SHRINK -> {self.GPU_MODE_CPUS} cpus, "
            f"cpuset={self.GPU_MODE_CPUSET} (GPU mode)"
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error(
                f"CpuAffinityManager: docker update shrink failed: {result.stderr.strip()}"
            )
        else:
            logger.info("CpuAffinityManager: shrink succeeded")

    def refresh_container(self) -> None:
        """刷新容器状态（用于容器重启后重新检测）。"""
        self._check_docker_access()


# ---------------------------------------------------------------------------
# LlamaCppProcessManager（llama-server 进程管理器）
# ---------------------------------------------------------------------------

class LlamaCppProcessManager:
    """
    llama-server 进程管理器：通过 SIGTERM + 重启实现 GPU/CPU 跷跷板。

    NUMA 修复后的核心机制：
      - GPU 模式：-ngl 99 -t 4（4 控制线程，GPU 做 heavy lifting）
      - CPU 模式：-ngl 0 -t 32（32 线程并行 CPU 计算）
      - 切换时先调整 Docker CPU 亲和性，再启停进程，健康检查通过 /health 或 /v1/models
      - 恢复 GPU 模式时先启动进程，确认健康后再收缩 CPU 亲和性
    """

    def __init__(
        self,
        model_path: str,
        host: str = "0.0.0.0",
        port: int = 18000,
        gpu_layers: int = 99,
        n_threads: int = 32,
        ctx_size: int = 8192,
        metrics: bool = True,
        cpu_manager: Optional[CpuAffinityManager] = None,
    ):
        self.model_path = model_path
        self.host = host
        self.port = port
        self.gpu_layers = gpu_layers
        self.n_threads = n_threads
        self.ctx_size = ctx_size
        self.metrics = metrics
        self.cpu_manager = cpu_manager or CpuAffinityManager()

        self.process: Optional[asyncio.subprocess.Process] = None
        self.current_mode: Literal["gpu", "cpu", "stopped"] = "stopped"
        self._session: Optional[aiohttp.ClientSession] = None
        self._mock_mode: bool = False

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    def _detect_mock_mode(self) -> None:
        """若模型路径不存在且 llama-server 不在 PATH，则启用 mock 模式。"""
        if self._mock_mode:
            return
        model_exists = Path(self.model_path).exists()
        server_in_path = shutil.which("llama-server") is not None
        if not model_exists and not server_in_path:
            logger.warning(
                f"Model {self.model_path} not found and llama-server not in PATH; "
                "enabling mock mode for testing"
            )
            self._mock_mode = True

    async def _wait_for_healthy(self, timeout: int) -> bool:
        """轮询 /health 或 /v1/models 确认 llama-server 就绪。"""
        session = await self._get_session()
        urls = [
            f"http://{self.host}:{self.port}/health",
            f"http://{self.host}:{self.port}/v1/models",
        ]
        for _ in range(timeout * 2):
            for url in urls:
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=2)
                    ) as resp:
                        if resp.status == 200:
                            logger.info(f"llama-server healthy ({url})")
                            return True
                except Exception:
                    pass
            await asyncio.sleep(0.5)
        return False

    async def _graceful_shutdown(self) -> None:
        """优雅关闭：SIGTERM → wait → 确认显存释放。"""
        if self.process is None:
            self.current_mode = "stopped"
            return

        pid = self.process.pid
        logger.info(f"Graceful shutdown llama-server (PID {pid}) ...")
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning(f"SIGTERM timeout for PID {pid}, sending SIGKILL")
            self.process.kill()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error(f"SIGKILL timeout for PID {pid}, orphan process may remain")

        # 额外等待 NVIDIA 驱动释放显存上下文
        await asyncio.sleep(2)
        self.process = None
        self.current_mode = "stopped"

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def start_gpu_mode(self) -> bool:
        """
        启动 llama-server，全部层在 GPU（-ngl 99），仅 4 控制线程（-t 4）。

        NUMA 跷跷板顺序：
          1. 启动 llama-server GPU 模式进程
          2. 等待健康检查通过
          3. 收缩 Docker CPU 亲和性到 4 核（释放核心给其他任务）
        """
        self._detect_mock_mode()
        if self.current_mode == "gpu" and self.process is not None:
            logger.info("llama-server already in GPU mode")
            # 即使已经在 GPU 模式，也确保 CPU 亲和性正确
            self.cpu_manager.shrink_to_gpu_mode()
            return True

        if self.process:
            await self._graceful_shutdown()

        if self._mock_mode:
            logger.info("[Mock] llama-server GPU mode started")
            self.current_mode = "gpu"
            self.cpu_manager.shrink_to_gpu_mode()
            return True

        cmd = [
            "llama-server",
            "-m", self.model_path,
            "--host", self.host,
            "--port", str(self.port),
            "-ngl", str(self.gpu_layers),
            "-c", str(self.ctx_size),
            "-t", str(self.cpu_manager.GPU_MODE_THREADS),
        ]
        if self.metrics:
            cmd.append("--metrics")

        logger.info(f"Starting llama-server (GPU): {' '.join(cmd)}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("llama-server executable not found, falling back to mock mode")
            self._mock_mode = True
            self.current_mode = "gpu"
            self.cpu_manager.shrink_to_gpu_mode()
            return True

        self.current_mode = "gpu"
        healthy = await self._wait_for_healthy(timeout=30)
        if not healthy:
            logger.error("llama-server GPU mode failed health check")
            await self._graceful_shutdown()
            return False

        # NUMA 跷跷板第 3 步：健康启动后再收缩 CPU 亲和性
        self.cpu_manager.shrink_to_gpu_mode()
        return True

    async def switch_to_cpu_mode(self) -> bool:
        """
        切到 CPU 模式：SIGTERM 当前进程，重启为 -ngl 0 -t 32。

        NUMA 跷跷板顺序（关键）：
          1. 先扩展 Docker CPU 亲和性到 32 核（给容器更多核心）
          2. 然后启动 CPU 模式 llama-server（-ngl 0 -t 32）
          3. 等待健康检查通过

        必须先扩展再启动，否则 32 个 OpenMP 线程在 4 核上会导致
        上下文切换灾难（速度从 8 tok/s 暴跌到 1 tok/s，甚至死锁）。
        """
        self._detect_mock_mode()
        if self.current_mode == "cpu" and self.process is not None:
            logger.info("llama-server already in CPU mode")
            return True

        await self._graceful_shutdown()

        # NUMA 跷跷板第 1 步：先扩展 CPU 亲和性
        self.cpu_manager.expand_to_cpu_fallback()

        if self._mock_mode:
            logger.info("[Mock] llama-server CPU mode started")
            self.current_mode = "cpu"
            return True

        cmd = [
            "llama-server",
            "-m", self.model_path,
            "--host", self.host,
            "--port", str(self.port),
            "-ngl", "0",
            "-c", str(self.ctx_size),
            "-t", str(self.cpu_manager.CPU_FALLBACK_THREADS),
        ]
        if self.metrics:
            cmd.append("--metrics")

        logger.info(f"Starting llama-server (CPU): {' '.join(cmd)}")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("llama-server executable not found, falling back to mock mode")
            self._mock_mode = True
            self.current_mode = "cpu"
            return True

        self.current_mode = "cpu"
        healthy = await self._wait_for_healthy(timeout=30)
        if not healthy:
            logger.error("llama-server CPU mode failed health check")
            await self._graceful_shutdown()
            return False
        return True

    async def close(self) -> None:
        """关闭管理器并终止 llama-server 进程。"""
        await self._graceful_shutdown()
        if self._session and not self._session.closed:
            await self._session.close()


# ---------------------------------------------------------------------------
# VRAM Scheduler（核心调度器）
# ---------------------------------------------------------------------------

class VRAMScheduler:
    """
    Triad 显存分时复用调度器 — llama.cpp 版。

    职责：
      1. 监控 GPU 显存实时状态（NVML）
      2. 通过 LlamaCppProcessManager 将 llama-server 在 GPU/CPU 模式间切换
      3. 管理渲染任务队列（优先级 + 显存预算检查）
      4. 状态机：IDLE → CPU_FALLBACK → RENDERING → RECOVERING → IDLE
    """

    def __init__(
        self,
        monitor: Optional[NVMLMonitor] = None,
        llm_manager: Optional[LlamaCppProcessManager] = None,
        budget: Optional[VRAMBudget] = None,
        comfyui_lowvram_threshold_mb: int = 14336,
        wait_for_vram_target: bool = True,
    ):
        self.monitor = monitor or NVMLMonitor()

        # 若外部未传入 llm_manager，构造默认实例并自动绑定 CpuAffinityManager
        if llm_manager is None:
            cpu_mgr = CpuAffinityManager()
            llm_manager = LlamaCppProcessManager(
                model_path=os.getenv("LLAMA_MODEL_PATH", "/mnt/models/qwen-14b-q4_k_m.gguf"),
                host=os.getenv("LLAMA_HOST", "0.0.0.0"),
                port=int(os.getenv("LLAMA_PORT", "18000")),
                gpu_layers=int(os.getenv("LLAMA_NGL", "99")),
                n_threads=int(os.getenv("LLAMA_THREADS", "32")),
                ctx_size=int(os.getenv("LLAMA_CTX_SIZE", "8192")),
                cpu_manager=cpu_mgr,
            )
        self.llm_manager = llm_manager

        self.budget = budget or VRAMBudget()
        self.lowvram_threshold_mb = comfyui_lowvram_threshold_mb
        self.wait_for_vram_target = wait_for_vram_target

        # 状态机
        self._state = VRAMState.IDLE
        self._state_lock = asyncio.Lock()
        self._render_queue: asyncio.Queue[RenderTask] = asyncio.Queue()
        self._active_render: Optional["RenderContext"] = None

        # ★★★ 地雷 1 修复：推理引用计数 + 读者-写者锁 ★★★
        # 活跃 LLM 推理计数（多个推理可同时运行，VRAM 切换必须等待全部完成）
        self._llm_inference_counter: int = 0
        self._llm_counter_lock = asyncio.Lock()
        self._llm_counter_cv = asyncio.Condition(self._llm_counter_lock)
        # 全局 VRAM 切换互斥锁（保证同时只有一个渲染任务在进行切换）
        self._vram_switch_lock = asyncio.Lock()

        # 事件订阅
        self._state_listeners: List[
            Callable[[VRAMState, VRAMState], Coroutine[Any, Any, None]]
        ] = []
        self._progress_listeners: List[
            Callable[[str, float, str], Coroutine[Any, Any, None]]
        ] = []

        # 统计
        self._stats: Dict[str, Any] = {
            "renders_completed": 0,
            "renders_failed": 0,
            "llm_cpu_fallbacks": 0,
            "llm_gpu_recoveries": 0,
            "total_render_time_sec": 0.0,
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self.monitor.start_polling()
        logger.info("VRAMScheduler (llama.cpp) started")

    async def stop(self) -> None:
        await self.monitor.stop_polling()
        await self.llm_manager.close()
        self.monitor.close()
        logger.info("VRAMScheduler (llama.cpp) stopped")

    # ------------------------------------------------------------------
    # 状态机操作
    # ------------------------------------------------------------------

    @property
    def state(self) -> VRAMState:
        return self._state

    def add_state_listener(
        self,
        callback: Callable[[VRAMState, VRAMState], Coroutine[Any, Any, None]],
    ) -> None:
        self._state_listeners.append(callback)

    def add_progress_listener(
        self,
        callback: Callable[[str, float, str], Coroutine[Any, Any, None]],
    ) -> None:
        """注册进度回调: (task_id, progress_ratio_0_to_1, status_message)"""
        self._progress_listeners.append(callback)

    async def _set_state(self, new_state: VRAMState) -> None:
        async with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return
            self._state = new_state
            logger.info(f"VRAM state transition: {old_state.name} -> {new_state.name}")
            for cb in self._state_listeners:
                with contextlib.suppress(Exception):
                    await cb(old_state, new_state)

    # ------------------------------------------------------------------
    # 推理引用计数 API（地雷 1 修复：读者-写者锁模式）
    # ------------------------------------------------------------------

    async def begin_llm_inference(self, timeout_sec: float = 30.0) -> bool:
        """
        声明开始一次本地 LLM 推理。

        在 VRAM 切换期间（CPU_FALLBACK / RENDERING / RECOVERING），
        推理请求会阻塞等待，直到切换完成，避免 Connection Refused。

        Args:
            timeout_sec: 等待 VRAM 就绪的最长时间（秒）

        Returns:
            True 表示推理可以安全开始，False 表示超时放弃
        """
        t0 = time.time()
        async with self._llm_counter_cv:
            while self._state in (VRAMState.CPU_FALLBACK, VRAMState.RENDERING, VRAMState.RECOVERING):
                elapsed = time.time() - t0
                if elapsed >= timeout_sec:
                    logger.warning(
                        f"begin_llm_inference 超时 ({timeout_sec}s): "
                        f"VRAM 仍处于 {self._state.name} 状态，拒绝推理请求"
                    )
                    return False
                logger.info(
                    f"推理请求排队: VRAM 正在 {self._state.name}，"
                    f"已等待 {elapsed:.1f}s..."
                )
                try:
                    await asyncio.wait_for(
                        self._llm_counter_cv.wait(),
                        timeout=timeout_sec - elapsed,
                    )
                except asyncio.TimeoutError:
                    logger.warning("begin_llm_inference 等待超时")
                    return False
            self._llm_inference_counter += 1
            logger.debug(
                f"LLM 推理开始，活跃计数: {self._llm_inference_counter}"
            )
            return True

    async def end_llm_inference(self) -> None:
        """
        声明结束一次本地 LLM 推理。

        减少引用计数，并通知所有等待 VRAM 切换的协程。
        """
        async with self._llm_counter_cv:
            self._llm_inference_counter -= 1
            if self._llm_inference_counter < 0:
                logger.warning("LLM 推理计数出现负值，归零修正")
                self._llm_inference_counter = 0
            self._llm_counter_cv.notify_all()
            logger.debug(
                f"LLM 推理结束，活跃计数: {self._llm_inference_counter}"
            )

    # ------------------------------------------------------------------
    # 核心调度：申请渲染显存（公共 API）
    # ------------------------------------------------------------------

    async def acquire_render_memory(
        self,
        task: Union[str, RenderTask],
        timeout_sec: float = 60.0,
    ) -> "RenderContext":
        # 兼容旧 API：如果传入字符串，自动包装为 RenderTask
        if isinstance(task, str):
            task = RenderTask(
                task_id=task,
                workflow_type="default",
                estimated_vram_mb=20480,
                priority=0,
            )
        """
        申请进入渲染态（llama.cpp 版）。

        流程：
          1. 状态 IDLE → CPU_FALLBACK：将 llama-server 切到 -ngl 0
          2. 确认 llama-server CPU 模式健康运行
          3. 等待显存释放到目标值
          4. 状态 CPU_FALLBACK → RENDERING（或 EMERGENCY）
          5. 返回 RenderContext，退出时自动恢复 GPU 模式
        """
        await self._emit_progress(task.task_id, 0.0, "acquiring VRAM (llama.cpp CPU_FALLBACK strategy) ...")
        t0 = time.time()

        async with self._vram_switch_lock:
            # ★★★ 地雷 1 修复：等待所有活跃 LLM 推理完成 ★★★
            t_wait = time.time()
            async with self._llm_counter_cv:
                while self._llm_inference_counter > 0:
                    logger.info(
                        f"VRAM 切换等待: {self._llm_inference_counter} 个 LLM 推理仍在运行，"
                        f"已等待 {time.time() - t_wait:.1f}s..."
                    )
                    try:
                        await asyncio.wait_for(
                            self._llm_counter_cv.wait(),
                            timeout=timeout_sec,
                        )
                    except asyncio.TimeoutError:
                        logger.error(
                            f"VRAM 切换超时: {self._llm_inference_counter} 个推理仍未完成，"
                            f"取消渲染任务 {task.task_id}"
                        )
                        raise RuntimeError(
                            f"VRAM deadlock: {self._llm_inference_counter} LLM inference(s) "
                            f"still active after {timeout_sec}s timeout"
                        )
            logger.info(
                f"VRAM 切换安全: 所有 LLM 推理已完成，"
                f"总等待 {(time.time() - t_wait):.2f}s"
            )

            # 状态检查
            async with self._state_lock:
                if self._state in (VRAMState.RENDERING, VRAMState.CPU_FALLBACK):
                    raise RuntimeError(
                        f"Already in {self._state.name} state — serialize render tasks via queue"
                    )

            # Step 1: 将 LLM 从 GPU 切到 CPU（不中断服务，仅降速）
            await self._set_state(VRAMState.CPU_FALLBACK)
            cpu_ok = await self.llm_manager.switch_to_cpu_mode()
            if not cpu_ok:
                logger.error("Failed to switch llama-server to CPU mode")
                await self._set_state(VRAMState.EMERGENCY)
                raise RuntimeError("LLM CPU_FALLBACK failed: cannot release GPU VRAM")

            self._stats["llm_cpu_fallbacks"] += 1

            # Step 2: 等待显存达标（llama-server CPU 模式几乎瞬间释放）
            target_free_mb = task.estimated_vram_mb + self.budget.safety_margin
            if self.wait_for_vram_target:
                vram_ok = await self._wait_for_free_vram(target_free_mb, timeout=timeout_sec)
                if not vram_ok:
                    logger.warning(
                        f"Timeout waiting for {target_free_mb}MB free VRAM, proceeding with caution"
                    )

            # Step 3: 判断模式并进入 RENDERING
            snap = self.monitor.snapshot()
            available = snap.comfyui_available_mb
            needed = task.estimated_vram_mb

            logger.info(
                f"Task {task.task_id}: need {needed}MB, available ~{available}MB "
                f"(free={snap.free_mb}MB, llm_mode={self.llm_manager.current_mode})"
            )

            if available >= needed:
                mode = "normal"
            elif available >= self.lowvram_threshold_mb:
                mode = "lowvram"
                logger.warning(f"Low VRAM mode engaged for task {task.task_id}")
            else:
                mode = "emergency"
                logger.error(f"Emergency mode: only {available}MB free for {needed}MB task")

            if mode == "emergency":
                await self._set_state(VRAMState.EMERGENCY)
            else:
                await self._set_state(VRAMState.RENDERING)

            elapsed = time.time() - t0
            await self._emit_progress(task.task_id, 0.05, f"VRAM acquired ({mode}, {elapsed:.1f}s)")

            ctx = RenderContext(
                scheduler=self,
                task=task,
                mode=mode,
                acquired_at=time.time(),
            )
            self._active_render = ctx
            return ctx

    async def _wait_for_free_vram(self, target_free_mb: int, timeout: float = 60.0) -> bool:
        """轮询等待显存空闲达到目标值。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            snap = self.monitor.snapshot()
            if snap.free_mb >= target_free_mb:
                return True
            await asyncio.sleep(0.5)
        return False

    # ------------------------------------------------------------------
    # 核心调度：释放渲染显存（公共 API）
    # ------------------------------------------------------------------

    async def release_render_memory(self, ctx: "RenderContext") -> None:
        """
        释放渲染显存，将 llama-server 恢复为 GPU 模式。

        状态流转: RENDERING/EMERGENCY/CPU_FALLBACK → RECOVERING → IDLE

        NUMA 跷跷板顺序（关键）：
          1. 启动 GPU 模式 llama-server 进程（-ngl 99 -t 4）
          2. 等待健康检查通过
          3. 收缩 Docker CPU 亲和性到 4 核（释放核心给其他任务）
        """
        if ctx._released:
            logger.debug(f"RenderContext {ctx.task.task_id} already released, skipping")
            return
        ctx._released = True

        await self._emit_progress(ctx.task.task_id, 0.95, "releasing VRAM, recovering LLM to GPU (-ngl 99) ...")

        if self._state in (VRAMState.RENDERING, VRAMState.EMERGENCY, VRAMState.CPU_FALLBACK):
            await self._set_state(VRAMState.RECOVERING)
        elif self._state == VRAMState.IDLE:
            logger.warning("release_render_memory called while already IDLE")
            return

        # 恢复 llama-server GPU 模式（mmap 热映射，通常 <2s）
        recovered = await self.llm_manager.start_gpu_mode()
        if recovered:
            self._stats["llm_gpu_recoveries"] += 1
            await asyncio.sleep(1.0)  # 给驱动一点缓冲
        else:
            logger.error("Failed to recover llama-server to GPU mode; LLM remains in CPU mode")

        await self._set_state(VRAMState.IDLE)
        self._active_render = None
        await self._emit_progress(ctx.task.task_id, 1.0, "VRAM released, LLM GPU ready")

    # ------------------------------------------------------------------
    # 兼容旧 API 与查询接口
    # ------------------------------------------------------------------

    async def acquire_render_context(
        self,
        task: Union[str, RenderTask],
        timeout_sec: float = 60.0,
    ) -> "RenderContext":
        """兼容旧版 API，等同于 acquire_render_memory。"""
        return await self.acquire_render_memory(task, timeout_sec)

    async def _release_render_context(self, task: RenderTask, mode: str) -> None:
        """兼容旧版内部 API。"""
        ctx = RenderContext(
            scheduler=self,
            task=task,
            mode=mode,
            acquired_at=0.0,
        )
        await self.release_render_memory(ctx)

    def get_stats(self) -> Dict[str, Any]:
        """返回统计信息（兼容旧版）。"""
        return dict(self._stats)

    def get_status(self) -> Dict[str, Any]:
        """
        返回调度器完整状态（公共 API）。

        包含：当前状态、LLM 模式、显存快照、统计、推荐参数等。
        """
        snap = self.monitor.snapshot()
        cpu_mgr = getattr(self.llm_manager, "cpu_manager", None)
        return {
            "state": self._state.name,
            "llm_mode": self.llm_manager.current_mode,
            "cpu_affinity": {
                "has_docker": getattr(cpu_mgr, "has_docker", False),
                "container_name": getattr(cpu_mgr, "container_name", None),
                "container_id": getattr(cpu_mgr, "container_id", None),
                "gpu_mode_cpuset": getattr(cpu_mgr, "GPU_MODE_CPUSET", None),
                "cpu_fallback_cpuset": getattr(cpu_mgr, "CPU_FALLBACK_CPUSET", None),
            } if cpu_mgr else None,
            "snapshot": {
                "total_mb": snap.total_mb,
                "free_mb": snap.free_mb,
                "used_mb": snap.used_mb,
                "comfyui_available_mb": snap.comfyui_available_mb,
            },
            "stats": dict(self._stats),
            "active_render": self._active_render.task.task_id if self._active_render else None,
            "recommend_comfyui_args": self.recommend_comfyui_args(),
            "recommend_svd_args": self.recommend_svd_args(),
        }

    def current_snapshot(self) -> VRAMSnapshot:
        return self.monitor.snapshot()

    async def _emit_progress(self, task_id: str, progress: float, message: str) -> None:
        for cb in self._progress_listeners:
            with contextlib.suppress(Exception):
                await cb(task_id, progress, message)

    # ------------------------------------------------------------------
    # ComfyUI 参数推荐
    # ------------------------------------------------------------------

    def recommend_comfyui_args(self) -> List[str]:
        """
        根据当前显存状态，推荐 ComfyUI 启动参数。
        返回如 ["--normalvram"] 或 ["--lowvram", "--cpu-vae"]
        """
        snap = self.monitor.snapshot()
        free = snap.comfyui_available_mb

        if free >= 18000:
            return []  # 默认高显存模式
        elif free >= 12000:
            return ["--normalvram"]
        elif free >= 6000:
            return ["--lowvram"]
        else:
            return ["--lowvram", "--cpu-vae", "--disable-xformers"]

    def recommend_svd_args(self) -> List[str]:
        """SVD 对显存极度敏感，单独推荐参数。"""
        snap = self.monitor.snapshot()
        free = snap.comfyui_available_mb
        if free >= 20000:
            return ["--batch-size", "1"]
        elif free >= 14000:
            return ["--batch-size", "1", "--lowvram"]
        else:
            return ["--batch-size", "1", "--lowvram", "--cpu-vae"]


# ---------------------------------------------------------------------------
# RenderContext — 异步上下文管理器
# ---------------------------------------------------------------------------

class RenderContext:
    """
    渲染上下文，用于 ``async with`` 语句确保显存最终释放。

    示例：
        async with await scheduler.acquire_render_memory(task) as ctx:
            result = await comfyui_client.queue_prompt(workflow)
            # 渲染期间 ComfyUI 独占 ~20GB VRAM，LLM 在 CPU 模式慢速运行
        # 退出上下文后，llama-server 自动恢复 -ngl 99 GPU 模式
    """

    def __init__(
        self,
        scheduler: VRAMScheduler,
        task: RenderTask,
        mode: str,
        acquired_at: float,
    ):
        self.scheduler = scheduler
        self.task = task
        self.mode = mode          # "normal", "lowvram", "emergency"
        self.acquired_at = acquired_at
        self.result: Optional[Any] = None
        self._released = False

    async def __aenter__(self) -> "RenderContext":
        logger.info(f"RenderContext entered for {self.task.task_id} (mode={self.mode})")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self._released:
            await self.scheduler.release_render_memory(self)
        total_time = time.time() - self.acquired_at
        self.scheduler._stats["total_render_time_sec"] += total_time
        if exc_type is not None:
            self.scheduler._stats["renders_failed"] += 1
            logger.error(f"Render task {self.task.task_id} failed: {exc}")
        else:
            self.scheduler._stats["renders_completed"] += 1
        logger.info(f"RenderContext exited for {self.task.task_id} ({total_time:.1f}s)")


# ---------------------------------------------------------------------------
# CLI / 测试入口
# ---------------------------------------------------------------------------

async def _demo_main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # 使用不存在的 mock 模型路径以在无 llama-server 环境运行演示
    cpu_mgr = CpuAffinityManager()
    manager = LlamaCppProcessManager(
        model_path="/mnt/models/demo-qwen-14b-q4_k_m.gguf",
        host="0.0.0.0",
        port=18000,
        gpu_layers=99,
        n_threads=32,
        ctx_size=8192,
        cpu_manager=cpu_mgr,
    )
    scheduler = VRAMScheduler(llm_manager=manager)
    await scheduler.start()

    # 订阅状态变化与进度
    async def on_state_change(old: VRAMState, new: VRAMState) -> None:
        print(f"\n>>> [STATE] {old.name} -> {new.name}\n")

    async def on_progress(task_id: str, progress: float, msg: str) -> None:
        print(f"[PROGRESS] {task_id}: {progress * 100:.0f}% | {msg}")

    scheduler.add_state_listener(on_state_change)
    scheduler.add_progress_listener(on_progress)

    print("=" * 60)
    print("Triad VRAM Scheduler (llama.cpp edition + NUMA fix) Demo")
    print("=" * 60)
    print(f"Initial status:\n{json.dumps(scheduler.get_status(), indent=2, ensure_ascii=False)}")
    print(f"ComfyUI args (idle): {scheduler.recommend_comfyui_args()}")
    print(f"SVD args (idle): {scheduler.recommend_svd_args()}")
    print(f"\nNUMA CPU Affinity:")
    print(f"  GPU  mode:  {CpuAffinityManager.GPU_MODE_CPUS} cpus, cpuset={CpuAffinityManager.GPU_MODE_CPUSET}, threads={CpuAffinityManager.GPU_MODE_THREADS}")
    print(f"  CPU  mode:  {CpuAffinityManager.CPU_FALLBACK_CPUS} cpus, cpuset={CpuAffinityManager.CPU_FALLBACK_CPUSET}, threads={CpuAffinityManager.CPU_FALLBACK_THREADS}")

    # ------------------------------------------------------------------
    # 演示 1：async with 方式（推荐）
    # 状态机预期：IDLE -> CPU_FALLBACK -> RENDERING -> RECOVERING -> IDLE
    # ------------------------------------------------------------------
    task = RenderTask(
        task_id="demo_sdxl_llama_001",
        workflow_type="sdxl",
        estimated_vram_mb=18432,  # 18GB 峰值，接近 20GB 预算
        priority=1,
    )

    print("\n--- Demo 1: async with render context ---")
    ctx = await scheduler.acquire_render_memory(task, timeout_sec=30.0)
    async with ctx:
        print(
            f"Inside render: mode={ctx.mode}, "
            f"llm_mode={scheduler.llm_manager.current_mode}, "
            f"state={scheduler.state.name}"
        )
        await asyncio.sleep(2)  # 模拟 ComfyUI 工作 2 秒
        ctx.result = {"image": "output/demo_llama.png", "vram_peak_mb": 18432}

    print(f"\nAfter render: {json.dumps(scheduler.get_status(), indent=2, ensure_ascii=False)}")

    # ------------------------------------------------------------------
    # 演示 2：显式 acquire / release 方式（公共 API）
    # ------------------------------------------------------------------
    task2 = RenderTask(
        task_id="demo_instantid_002",
        workflow_type="instantid",
        estimated_vram_mb=12288,
        priority=2,
    )

    print("\n--- Demo 2: manual acquire / release ---")
    ctx2 = await scheduler.acquire_render_memory(task2)
    print(
        f"Manual acquire: state={scheduler.state.name}, "
        f"llm={scheduler.llm_manager.current_mode}"
    )
    await asyncio.sleep(1)
    await scheduler.release_render_memory(ctx2)
    print(
        f"Manual release: state={scheduler.state.name}, "
        f"llm={scheduler.llm_manager.current_mode}"
    )

    # ------------------------------------------------------------------
    # 演示 3：低显存阈值触发 lowvram 模式
    # ------------------------------------------------------------------
    task3 = RenderTask(
        task_id="demo_lowvram_003",
        workflow_type="svd",
        estimated_vram_mb=21504,  # 超过 22GB，会触发 emergency
        priority=0,
    )
    print("\n--- Demo 3: lowvram / emergency mode ---")
    ctx3 = await scheduler.acquire_render_memory(task3, timeout_sec=5.0)
    print(f"Emergency acquire result: mode={ctx3.mode}")
    async with ctx3:
        await asyncio.sleep(0.5)

    print("\n--- Final stats ---")
    print(f"get_status(): {json.dumps(scheduler.get_status(), indent=2, ensure_ascii=False)}")
    print(f"get_stats(): {json.dumps(scheduler.get_stats(), indent=2, ensure_ascii=False)}")

    await scheduler.stop()
    print("\nDemo finished.")


if __name__ == "__main__":
    asyncio.run(_demo_main())
