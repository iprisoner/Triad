"""
Hermes -> OpenClaw Gateway 状态回传总线

非阻塞的异步报告器，使用 httpx 异步客户端通过 REST API
向 OpenClaw Gateway 推送任务状态、进度、预览帧、模型信息及最终结果。

关键设计：
- 非阻塞：所有 report_* 方法内部使用 asyncio.create_task 包装 _post_with_retry，
  立即返回，不等待 HTTP 响应，避免拖慢 Hermes 主流程。
- 容错：Gateway 不可用时，自动指数退避重试 3 次后静默失败。
- 连接复用：复用 httpx.AsyncClient 会话，避免频繁建立 TCP 连接。
- 并发安全：使用 asyncio.Lock 保护会话创建过程。
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class StreamingReporter:
    """
    Hermes 向 OpenClaw Gateway 推送任务状态的异步报告器。

    非阻塞设计：使用 asyncio.create_task 在后台发送 HTTP 请求，
    失败时不影响 Hermes 主流程的执行进度。
    """

    def __init__(self, gateway_url: str = "http://host.docker.internal:18080") -> None:
        """
        初始化 StreamingReporter。

        Args:
            gateway_url: OpenClaw Gateway 的基地址，默认通过 Docker 内部网络访问。
        """
        self.gateway_url = gateway_url.rstrip("/")
        self._session: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> httpx.AsyncClient:
        """
        获取或创建一个 httpx 异步会话。

        使用 asyncio.Lock 保证并发安全，避免在并发场景下创建多个客户端实例。

        Returns:
            已就绪的 httpx.AsyncClient 实例。
        """
        if self._session is None or self._session.is_closed:
            async with self._lock:
                # 双重检查，防止在锁等待期间已有其他协程完成创建
                if self._session is None or self._session.is_closed:
                    self._session = httpx.AsyncClient(
                        timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0),
                        limits=httpx.Limits(max_connections=10),
                    )
        return self._session

    async def _post_with_retry(
        self,
        endpoint: str,
        payload: dict[str, Any],
        max_retries: int = 3,
    ) -> bool:
        """
        向 Gateway 发送 POST 请求，带指数退避重试机制。

        Args:
            endpoint: API 端点路径（如 ``/api/internal/push_status``）。
            payload: JSON 请求体。
            max_retries: 最大重试次数（包含首次请求）。

        Returns:
            当任意一次请求收到 HTTP 200 时返回 True；否则返回 False。
        """
        url = f"{self.gateway_url}{endpoint}"
        session = await self._get_session()

        for attempt in range(max_retries):
            try:
                resp = await session.post(url, json=payload)
                if resp.status_code == 200:
                    return True
                logger.warning(
                    "Gateway 返回非 200: %d, body: %s",
                    resp.status_code,
                    resp.text[:200],
                )
            except Exception as exc:
                logger.warning(
                    "上报失败 (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    exc,
                )
                if attempt < max_retries - 1:
                    # 指数退避：1s, 2s, 4s
                    await asyncio.sleep(2 ** attempt)
        return False

    async def report_stage(
        self,
        task_id: str,
        stage: str,
        message: str,
        progress: Optional[float] = None,
    ) -> bool:
        """
        推送阶段性状态（非阻塞）。

        将任务进度以 fire-and-forget 方式发送到 Gateway，
        立即返回 True 表示后台上报任务已启动。

        Args:
            task_id: 任务唯一标识。
            stage: 当前阶段名称（如 ``PLANNING``, ``RENDERING``）。
            message: 人类可读的状态描述。
            progress: 可选的进度百分比（0.0 ~ 1.0）。

        Returns:
            始终返回 True，表示后台上报任务已成功调度。
        """
        payload: dict[str, Any] = {
            "taskId": task_id,
            "stage": stage,
            "message": message,
        }
        if progress is not None:
            payload["progress"] = progress

        asyncio.create_task(self._post_with_retry("/api/internal/push_status", payload))
        return True

    async def report_image_preview(
        self,
        task_id: str,
        base64_data: str,
        step: int,
        total_steps: int = 50,
    ) -> bool:
        """
        推送图像预览帧（非阻塞）。

        在图像生成过程中逐步推送当前渲染帧的 Base64 数据及进度。

        Args:
            task_id: 任务唯一标识。
            base64_data: 图像的 Base64 编码字符串。
            step: 当前渲染步数。
            total_steps: 总渲染步数，默认 50。

        Returns:
            始终返回 True，表示后台上报任务已成功调度。
        """
        payload: dict[str, Any] = {
            "taskId": task_id,
            "stage": "RENDERING",
            "message": f"渲染中：Step {step}/{total_steps}",
            "progress": step / total_steps,
            "preview": {
                "type": "image",
                "data": base64_data,
            },
        }

        asyncio.create_task(self._post_with_retry("/api/internal/push_status", payload))
        return True

    async def report_model_info(
        self,
        task_id: str,
        vendor: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
    ) -> bool:
        """
        推送模型调用信息（非阻塞）。

        上报 LLM / VLM 的供应商、模型名及 Token 消耗，便于 Gateway 统计与计费。

        Args:
            task_id: 任务唯一标识。
            vendor: 模型供应商（如 ``openai``, ``anthropic``）。
            model: 具体模型名（如 ``gpt-4o``）。
            tokens_in: 输入 Token 数。
            tokens_out: 输出 Token 数。

        Returns:
            始终返回 True，表示后台上报任务已成功调度。
        """
        payload: dict[str, Any] = {
            "taskId": task_id,
            "stage": "ANALYZING",
            "message": f"使用 {vendor}/{model} 处理中...",
            "modelInfo": {
                "vendor": vendor,
                "model": model,
                "tokensIn": tokens_in,
                "tokensOut": tokens_out,
            },
        }

        asyncio.create_task(self._post_with_retry("/api/internal/push_status", payload))
        return True

    async def report_vram(
        self,
        task_id: str,
        state: str,
        embedding_mb: int,
        llm_mb: int,
        free_mb: int,
        other_mb: int = 0,
    ) -> bool:
        """
        推送 VRAM 状态（非阻塞）。

        上报 GPU 显存使用分布，帮助 Gateway 进行资源调度与监控。

        Args:
            task_id: 任务唯一标识。
            state: 显存状态摘要（如 ``normal``, ``warning``, ``critical``）。
            embedding_mb: Embedding 模型占用的显存（MB）。
            llm_mb: LLM 占用的显存（MB）。
            free_mb: 剩余可用显存（MB）。
            other_mb: 其他占用的显存（MB，v3.0 中 ComfyUI 已移除）。

        Returns:
            始终返回 True，表示后台上报任务已成功调度。
        """
        payload: dict[str, Any] = {
            "taskId": task_id,
            "stage": "ANALYZING",
            "message": f"VRAM 状态: {state}",
            "vramInfo": {
                "state": state,
                "embeddingMb": embedding_mb,
                "llmMb": llm_mb,
                "freeMb": free_mb,
            },
        }

        asyncio.create_task(self._post_with_retry("/api/internal/push_status", payload))
        return True

    async def report_result(
        self,
        task_id: str,
        status: str,
        output: str,
        tool_log: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        """
        推送最终结果（非阻塞）。

        当任务完成或失败时，向 Gateway 发送最终 Markdown 输出及可选的工具调用日志。

        Args:
            task_id: 任务唯一标识。
            status: 最终状态，应为 ``success`` 或 ``failed``。
            output: Markdown 格式的最终输出内容。
            tool_log: 可选的工具调用日志列表，用于调试与审计。

        Returns:
            始终返回 True，表示后台上报任务已成功调度。
        """
        payload: dict[str, Any] = {
            "taskId": task_id,
            "status": status,
            "output": output,
        }
        if tool_log is not None:
            payload["toolLog"] = tool_log

        asyncio.create_task(self._post_with_retry("/api/internal/push_result", payload))
        return True

    async def close(self) -> None:
        """
        关闭底层 httpx 会话，释放所有连接资源。

        应在 Hermes 生命周期结束时调用（如应用关闭钩子中）。
        """
        if self._session is not None:
            await self._session.aclose()
            self._session = None


async def _demo() -> None:
    """内部演示：模拟向 Gateway 上报 3 条状态及最终结果。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    reporter = StreamingReporter()
    task_id = "demo-task-001"

    # 1. 上报任务开始
    await reporter.report_stage(
        task_id=task_id,
        stage="PLANNING",
        message="正在分析用户需求并制定执行计划...",
        progress=0.1,
    )

    # 2. 上报模型调用信息
    await reporter.report_model_info(
        task_id=task_id,
        vendor="openai",
        model="gpt-4o",
        tokens_in=1024,
        tokens_out=512,
    )

    # 3. 上报 VRAM 状态
    await reporter.report_vram(
        task_id=task_id,
        state="normal",
        embedding_mb=512,
        llm_mb=2048,
        comfyui_mb=1024,
        free_mb=14336,
    )

    # 4. 上报渲染进度（模拟图像生成过程中的 3 个中间帧）
    for step in (10, 25, 40):
        await reporter.report_image_preview(
            task_id=task_id,
            base64_data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",  # 1x1 透明 PNG
            step=step,
            total_steps=50,
        )

    # 5. 上报最终结果
    await reporter.report_result(
        task_id=task_id,
        status="success",
        output="任务已完成！\n\n这是 **Markdown** 格式的输出示例。",
        tool_log=[
            {"tool": "planner", "input": "用户需求", "output": "执行计划"},
            {"tool": "image_generator", "input": "prompt", "output": "image_url"},
        ],
    )

    # 等待后台任务完成（给 create_task 留出时间）
    logger.info("等待后台上报任务落盘...")
    await asyncio.sleep(3)

    await reporter.close()
    logger.info("演示结束，Reporter 已关闭。")


if __name__ == "__main__":
    asyncio.run(_demo())
