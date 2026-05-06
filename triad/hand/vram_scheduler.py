"""
vram_scheduler.py
VRAM 极限调度器 — 魔改 22GB 2080Ti 显存分时复用

架构：
  常态（文本/代码任务）：
    ├── 2GB: Embedding 模型（常驻）
    ├── 4GB: 本地 Qwen LLM（常驻，可 swap）
    └── 16GB: 空闲缓冲区

  渲染态（图像/视频任务）：
    ├── 2GB: Embedding（常驻，不可动）
    ├── 0GB: LLM 临时卸载到内存（via vLLM swap / nvoffload）
    └── 20GB: ComfyUI 独占（SDXL Base 6GB + Refiner 4GB + ControlNet 2GB + SVD 8GB）

  渲染完成后：
    ├── 2GB: Embedding（常驻）
    ├── 4GB: LLM 从内存热恢复（约 10-20 秒）
    └── 16GB: 回归空闲缓冲区

调度策略：
  1. 使用 pynvml 实时监控显存占用
  2. 渲染前向 LLM 服务发送 /unload 信号
  3. 渲染完成后 warm-up LLM（预热前几个 token）
  4. 显存不足时自动降级到 --lowvram / --normalvram 模式

作者：Triad System Architect
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

import aiohttp

logger = logging.getLogger("triad.vram_scheduler")

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
    """显存使用状态机"""
    IDLE = auto()           # 常态：LLM 常驻
    RENDERING = auto()      # 渲染态：ComfyUI 独占
    RECOVERING = auto()     # 恢复态：LLM 热恢复中
    EMERGENCY = auto()      # 紧急态：显存不足，强制降级


@dataclass(frozen=True)
class VRAMBudget:
    """显存预算分配（单位：MB）"""
    embedding: int = 2048      # Embedding 模型常驻
    llm: int = 4096            # LLM 常驻
    comfyui_peak: int = 20480  # ComfyUI 渲染峰值
    safety_margin: int = 512   # 安全边距

    @property
    def total(self) -> int:
        return self.embedding + self.llm + self.comfyui_peak + self.safety_margin


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
        """可用于 ComfyUI 的显存（扣除 embedding 和安全边距）"""
        budget = VRAMBudget()
        return max(0, self.free_mb - budget.embedding - budget.safety_margin)


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
            except pynvml.NVMLError as e:
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

        # 获取各进程显存占用（较慢，仅在需要时调用）
        process_allocs: Dict[int, int] = {}
        try:
            procs = pynvml.nvmlDeviceGetComputeRunningProcesses(self._handle)
            for p in procs:
                process_allocs[p.pid] = int(p.usedGpuMemory // 1024 // 1024) if hasattr(p, "usedGpuMemory") else 0
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
        """模拟显存快照（用于 CI / 无 GPU 环境）"""
        return VRAMSnapshot(
            timestamp=time.time(),
            total_mb=22528,   # 22 GB
            free_mb=16384,    # 16 GB 空闲
            used_mb=6144,
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
    # 进程级显存干预（nvoffload / nvidia-smi）
    # ------------------------------------------------------------------

    async def reset_device(self) -> None:
        """极端情况：重置 GPU 上下文（慎用）"""
        if self._handle is None:
            logger.warning("Mock mode: skip GPU reset")
            return
        logger.critical("Requesting GPU context reset — all CUDA apps will crash!")
        # 实际场景下可通过 nvidia-smi --gpu-reset 或发送 SIGTERM 给占用进程

    def close(self) -> None:
        if PYNVML_AVAILABLE and self._handle is not None:
            with contextlib.suppress(Exception):
                pynvml.nvmlShutdown()


# ---------------------------------------------------------------------------
# LLM Swap Controller（vLLM / llama.cpp / TGI 适配）
# ---------------------------------------------------------------------------

class LLMSwapController:
    """
    负责 LLM 的显存卸载（unload）与热恢复（warm-up）。

    支持的 LLM 后端：
      - vLLM：通过 HTTP API /unload 与 /warmup
      - llama.cpp：通过 SIGUSR1 / SIGUSR2 信号
      - 通用：通过进程暂停（SIGSTOP / SIGCONT）
    """

    def __init__(
        self,
        backend_url: str = "http://localhost:18000",
        backend_type: str = "vllm",
        warmup_prompt: str = "你好，世界。",
    ):
        self.backend_url = backend_url.rstrip("/")
        self.backend_type = backend_type
        self.warmup_prompt = warmup_prompt
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    # ------------------------------------------------------------------
    # 卸载
    # ------------------------------------------------------------------

    async def unload(self) -> bool:
        """
        将 LLM 从显存卸载到内存/磁盘，释放 VRAM。
        返回 True 表示成功释放。
        """
        logger.info(f"[{self.backend_type}] Unloading LLM from VRAM ...")
        session = await self._get_session()

        if self.backend_type == "vllm":
            # vLLM 0.4.0+ 支持 /unload 接口（需开启 --enable-lora 或自定义 patch）
            try:
                async with session.post(
                    f"{self.backend_url}/v1/unload",
                    json={"target": "gpu", "destination": "cpu"},
                ) as resp:
                    if resp.status in (200, 202):
                        logger.info("vLLM unloaded successfully")
                        return True
            except Exception as e:
                logger.warning(f"vLLM unload API failed: {e}")

        elif self.backend_type == "llama_cpp":
            # llama.cpp server 支持 SIGUSR1 卸载模型到 RAM
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pkill", "-USR1", "-f", "llama-server",
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                await proc.communicate()
                logger.info("llama.cpp unload signal sent")
                return True
            except Exception as e:
                logger.warning(f"llama.cpp signal failed: {e}")

        elif self.backend_type == "generic":
            # 通用方案：找到 LLM 进程，发送 SIGSTOP（冻结但不释放 VRAM，不推荐）
            # 或尝试通过 nvidia-smi 获取 PID 后优雅关闭
            logger.warning("Generic backend: manual unload not implemented")

        # 兜底：如果 LLM 和 ComfyUI 在同一个进程（罕见），无法单独卸载
        logger.error("LLM unload failed — will proceed with lowvram mode")
        return False

    # ------------------------------------------------------------------
    # 热恢复
    # ------------------------------------------------------------------

    async def warm_up(self, max_retries: int = 3) -> bool:
        """
        将 LLM 从内存热恢复到显存，并预热前几个 token 以消除首 token 延迟。
        """
        logger.info(f"[{self.backend_type}] Warming up LLM ...")
        session = await self._get_session()

        if self.backend_type == "vllm":
            # 发送一个虚拟请求触发权重加载 + KV cache 重建
            for attempt in range(max_retries):
                try:
                    async with session.post(
                        f"{self.backend_url}/v1/completions",
                        json={
                            "model": "default",
                            "prompt": self.warmup_prompt,
                            "max_tokens": 3,
                            "temperature": 0.0,
                        },
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            logger.info(
                                f"Warm-up complete: {data.get('usage', {})}"
                            )
                            return True
                except Exception as e:
                    logger.warning(f"Warm-up attempt {attempt + 1} failed: {e}")
                    await asyncio.sleep(2 ** attempt)

        elif self.backend_type == "llama_cpp":
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pkill", "-USR2", "-f", "llama-server",
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                await proc.communicate()
                await asyncio.sleep(5)  # 给 llama.cpp 留加载时间
                logger.info("llama.cpp warm-up signal sent")
                return True
            except Exception as e:
                logger.warning(f"llama.cpp warm-up failed: {e}")

        logger.error("LLM warm-up failed — LLM may respond slowly until fully loaded")
        return False

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


# ---------------------------------------------------------------------------
# VRAM Scheduler（核心调度器）
# ---------------------------------------------------------------------------

class VRAMScheduler:
    """
    Triad 显存分时复用调度器。

    职责：
      1. 监控 GPU 显存实时状态
      2. 协调 LLM <-> ComfyUI 的显存占用切换
      3. 管理渲染任务队列（优先级 + 显存预算检查）
      4. 异常时自动降级到低显存模式
    """

    def __init__(
        self,
        monitor: Optional[NVMLMonitor] = None,
        llm_controller: Optional[LLMSwapController] = None,
        budget: Optional[VRAMBudget] = None,
        comfyui_lowvram_threshold_mb: int = 14336,  # 14GB 阈值
    ):
        self.monitor = monitor or NVMLMonitor()
        self.llm_controller = llm_controller or LLMSwapController()
        self.budget = budget or VRAMBudget()
        self.lowvram_threshold_mb = comfyui_lowvram_threshold_mb

        # 状态机
        self._state = VRAMState.IDLE
        self._state_lock = asyncio.Lock()
        self._render_queue: asyncio.Queue[RenderTask] = asyncio.Queue()
        self._active_render: Optional[RenderTask] = None

        # 事件订阅
        self._state_listeners: List[Callable[[VRAMState, VRAMState], Coroutine[Any, Any, None]]] = []
        self._progress_listeners: List[Callable[[str, float, str], Coroutine[Any, Any, None]]] = []

        # 统计
        self._stats: Dict[str, Any] = {
            "renders_completed": 0,
            "renders_failed": 0,
            "llm_unloads": 0,
            "llm_warmups": 0,
            "total_render_time_sec": 0.0,
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self.monitor.start_polling()
        logger.info("VRAMScheduler started")

    async def stop(self) -> None:
        await self.monitor.stop_polling()
        await self.llm_controller.close()
        self.monitor.close()
        logger.info("VRAMScheduler stopped")

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
        """
        注册进度回调: (task_id, progress_ratio_0_to_1, status_message)
        """
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
    # 核心调度：申请渲染显存
    # ------------------------------------------------------------------

    async def acquire_render_context(
        self,
        task: RenderTask,
        timeout_sec: float = 60.0,
    ) -> "RenderContext":
        """
        申请进入渲染态。

        流程：
          1. 检查当前显存是否足够
          2. 若不够，尝试卸载 LLM
          3. 若仍不够，标记为 emergency 并降级 ComfyUI 模式
          4. 返回 RenderContext（上下文管理器），退出时自动恢复
        """
        await self._emit_progress(task.task_id, 0.0, "acquiring VRAM ...")
        t0 = time.time()

        async with self._state_lock:
            if self._state == VRAMState.RENDERING:
                raise RuntimeError("Already in rendering state — serialize tasks via queue")

        # 1. 采样显存
        snap = self.monitor.snapshot()
        available = snap.comfyui_available_mb
        needed = task.estimated_vram_mb

        logger.info(
            f"Task {task.task_id}: need {needed}MB, available {available}MB "
            f"(free={snap.free_mb}MB)"
        )

        # 2. 尝试卸载 LLM 释放显存
        if available < needed and self._state != VRAMState.RENDERING:
            await self._set_state(VRAMState.RECOVERING)  # 过渡态
            unloaded = await self.llm_controller.unload()
            if unloaded:
                self._stats["llm_unloads"] += 1
                # 等待显存回落（vLLM 卸载有异步延迟）
                await asyncio.sleep(2.0)
                snap = self.monitor.snapshot()
                available = snap.comfyui_available_mb
                logger.info(f"After LLM unload: available={available}MB")

        # 3. 判断模式
        if available >= needed:
            mode = "normal"
            await self._set_state(VRAMState.RENDERING)
        elif available >= self.lowvram_threshold_mb:
            mode = "lowvram"
            logger.warning(f"Low VRAM mode engaged for task {task.task_id}")
            await self._set_state(VRAMState.RENDERING)
        else:
            mode = "emergency"
            logger.error(f"Emergency mode: only {available}MB free for {needed}MB task")
            await self._set_state(VRAMState.EMERGENCY)

        elapsed = time.time() - t0
        await self._emit_progress(task.task_id, 0.05, f"VRAM acquired ({mode}, {elapsed:.1f}s)")

        return RenderContext(
            scheduler=self,
            task=task,
            mode=mode,
            acquired_at=time.time(),
        )

    async def _emit_progress(self, task_id: str, progress: float, message: str) -> None:
        for cb in self._progress_listeners:
            with contextlib.suppress(Exception):
                await cb(task_id, progress, message)

    # ------------------------------------------------------------------
    # 释放渲染显存（自动恢复 LLM）
    # ------------------------------------------------------------------

    async def _release_render_context(self, task: RenderTask, mode: str) -> None:
        """渲染完成后调用：恢复 LLM，回归常态。"""
        await self._emit_progress(task.task_id, 0.95, "releasing VRAM, recovering LLM ...")

        # 如果之前卸载了 LLM，现在热恢复
        if self._stats["llm_unloads"] > 0:
            await self._set_state(VRAMState.RECOVERING)
            recovered = await self.llm_controller.warm_up()
            if recovered:
                self._stats["llm_warmups"] += 1
            # 给 GPU 驱动一点时间回收碎片化显存
            await asyncio.sleep(1.5)

        await self._set_state(VRAMState.IDLE)
        await self._emit_progress(task.task_id, 1.0, "VRAM released, LLM ready")

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # LLM 推理引用计数（v2.3 新增：兼容 hermes_orchestrator 调用）
    # ------------------------------------------------------------------

    async def begin_llm_inference(self, timeout_sec: float = 5.0) -> bool:
        """
        声明开始一次本地 LLM 推理。
        在 VRAM 切换期间阻塞推理请求，防止显存竞争。
        """
        t0 = time.time()
        while self._state in (VRAMState.CPU_FALLBACK, VRAMState.RENDERING, VRAMState.RECOVERING):
            elapsed = time.time() - t0
            if elapsed >= timeout_sec:
                logger.warning(f"begin_llm_inference 超时 ({timeout_sec}s): VRAM 处于 {self._state.name}")
                return False
            await asyncio.sleep(0.2)
        return True

    async def end_llm_inference(self) -> None:
        """声明结束一次本地 LLM 推理（当前为无操作，由 begin 的 timeout 保护）。"""
        pass

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    def current_snapshot(self) -> VRAMSnapshot:
        return self.monitor.snapshot()

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
# RenderContext — 上下文管理器
# ---------------------------------------------------------------------------

class RenderContext:
    """
    渲染上下文，用于 with 语句确保显存最终释放。

    示例：
        async with await scheduler.acquire_render_context(task) as ctx:
            result = await comfyui_client.queue_prompt(workflow)
            # 渲染期间 ComfyUI 独占 20GB VRAM
        # 退出上下文后，LLM 自动热恢复
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

    async def __aenter__(self) -> "RenderContext":
        logger.info(f"RenderContext entered for {self.task.task_id} (mode={self.mode})")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.scheduler._release_render_context(self.task, self.mode)
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
    # v2.3.1: 仅在直接运行文件时配置日志，避免覆盖全局配置
    if __name__ == "__main__":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    scheduler = VRAMScheduler()
    await scheduler.start()

    # 订阅状态变化
    async def on_state_change(old: VRAMState, new: VRAMState):
        print(f"[STATE] {old.name} -> {new.name}")

    async def on_progress(task_id: str, progress: float, msg: str):
        print(f"[PROGRESS] {task_id}: {progress * 100:.0f}% | {msg}")

    scheduler.add_state_listener(on_state_change)
    scheduler.add_progress_listener(on_progress)

    # 模拟一个 SDXL 角色生成任务
    task = RenderTask(
        task_id="demo_sdxl_001",
        workflow_type="sdxl",
        estimated_vram_mb=10240,
        priority=1,
    )

    print(f"Current snapshot: {scheduler.current_snapshot()}")
    print(f"Recommended args: {scheduler.recommend_comfyui_args()}")

    async with await scheduler.acquire_render_context(task) as ctx:
        print(f"Rendering in mode={ctx.mode} ...")
        await asyncio.sleep(3)  # 模拟 ComfyUI 运行 3 秒
        ctx.result = {"image": "output/demo.png"}

    print(f"Stats: {scheduler.get_stats()}")
    await scheduler.stop()


if __name__ == "__main__":
    asyncio.run(_demo_main())
