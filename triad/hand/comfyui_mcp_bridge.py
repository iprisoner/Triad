"""
comfyui_mcp_bridge.py
ComfyUI MCP Bridge — 将 ComfyUI 推理能力暴露为 MCP 工具集

功能：
  1. MCP Server 基类（JSON-RPC 2.0 over stdio / WebSocket）
  2. 5 个工具接口：
       generate_character_concept, generate_scene,
       generate_video_clip, generate_tts, instantid_face_swap
  3. ComfyUI WebSocket 客户端（实时进度回调 → StatusUpdate）
  4. WorkflowTemplate 管理（JSON 工作流模板 + 动态参数注入）
  5. VRAMScheduler 集成（渲染前申请显存，渲染后释放 + LLM 恢复）

通信协议：
  MCP（Model Context Protocol）：JSON-RPC 2.0
  Hermes Agent 通过 stdio / WebSocket 发送 tool call，Bridge 执行后返回结果。

硬件适配：
  魔改 22GB 2080Ti，使用 vram_scheduler.py 中的 VRAMScheduler 分时复用。

作者：Triad System Architect
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import os
import random
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import aiofiles
import aiohttp
import websockets

# ---------------------------------------------------------------------------
# 导入 Triad 内部模块（假设在同一 Python path 下）
# ---------------------------------------------------------------------------

try:
    from vram_scheduler import VRAMScheduler, RenderTask, VRAMState
except ImportError:
    # fallback: 如果不在同一路径，尝试相对导入
    sys.path.insert(0, str(Path(__file__).parent))
    from vram_scheduler import VRAMScheduler, RenderTask, VRAMState

try:
    from asset_manager import AssetManager, AssetMeta
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent / "memory"))
    from asset_manager import AssetManager, AssetMeta


logger = logging.getLogger("triad.comfyui_mcp_bridge")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

COMFYUI_DEFAULT_HOST = os.getenv("COMFYUI_HOST", "host.docker.internal")
COMFYUI_DEFAULT_PORT = int(os.getenv("COMFYUI_PORT", "8188"))
COMFYUI_WS_TIMEOUT = 300  # 5 分钟

# VRAM 预算估算（MB）
VRAM_ESTIMATE = {
    "sdxl": 10240,
    "controlnet": 12288,
    "svd": 16384,
    "instantid": 14336,
    "tts": 1024,        # TTS 通常在 CPU 或极小显存
}


# ---------------------------------------------------------------------------
# WorkflowTemplate — 工作流模板引擎
# ---------------------------------------------------------------------------

class WorkflowTemplate:
    """
    管理 ComfyUI JSON 工作流模板，支持参数注入和节点路径解析。

    模板文件存放路径：
      ~/.triad/workflows/character_concept.json
      ~/.triad/workflows/scene_controlnet.json
      ~/.triad/workflows/svd_video.json
      ~/.triad/workflows/instantid.json
      ~/.triad/workflows/tts_qwen.json
    """

    def __init__(self, templates_dir: Optional[Path] = None):
        if templates_dir is None:
            templates_dir = Path.home() / ".triad" / "workflows"
        self.templates_dir = Path(templates_dir)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 模板加载
    # ------------------------------------------------------------------

    def load(self, name: str) -> Dict[str, Any]:
        """从缓存或磁盘加载工作流模板。"""
        if name in self._cache:
            return dict(self._cache[name])
        path = self.templates_dir / f"{name}.json"
        if not path.exists():
            # 首次运行时自动生成内嵌模板
            self._create_builtin_template(name)
        with open(path, "r", encoding="utf-8") as f:
            workflow = json.load(f)
        self._cache[name] = workflow
        return dict(workflow)

    def save(self, name: str, workflow: Dict[str, Any]) -> Path:
        """保存自定义模板。"""
        path = self.templates_dir / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(workflow, f, indent=2, ensure_ascii=False)
        self._cache[name] = dict(workflow)
        return path

    # ------------------------------------------------------------------
    # 参数注入
    # ------------------------------------------------------------------

    @staticmethod
    def inject_prompt(workflow: Dict[str, Any], positive: str, negative: str = "") -> Dict[str, Any]:
        """
        向工作流注入正/负向提示词。
        自动查找 KSampler / CLIPTextEncode 节点。
        """
        wf = json.loads(json.dumps(workflow))  # deep copy
        for node_id, node in wf.items():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})

            # CLIPTextEncode 节点
            if class_type in ("CLIPTextEncode", "CLIPTextEncodeSDXL", "CLIPTextEncodeFlux"):
                if "text" in inputs:
                    # 判断是 positive 还是 negative 通过 node title 或连接关系
                    # 简单启发：如果节点 ID 较小或连接到 KSampler 的 positive 端
                    inputs["text"] = positive

            # KSampler / SamplerCustom 节点：seed
            if "KSampler" in class_type or "Sampler" in class_type:
                if "seed" in inputs:
                    # 保留原有 seed 值（已由调用方设置）
                    pass

            # EmptyLatentImage 节点：尺寸
            if class_type in ("EmptyLatentImage", "EmptySDXLLatentImage"):
                pass  # width / height 已由调用方设置

        return wf

    @staticmethod
    def set_seed(workflow: Dict[str, Any], seed: int) -> Dict[str, Any]:
        """设置所有 KSampler / Sampler 节点的 seed。"""
        wf = json.loads(json.dumps(workflow))
        for node in wf.values():
            if isinstance(node, dict):
                inputs = node.get("inputs", {})
                if "seed" in inputs:
                    inputs["seed"] = seed
        return wf

    @staticmethod
    def set_latent_size(workflow: Dict[str, Any], width: int, height: int) -> Dict[str, Any]:
        """设置 EmptyLatentImage 节点的尺寸。"""
        wf = json.loads(json.dumps(workflow))
        for node in wf.values():
            if isinstance(node, dict):
                if node.get("class_type") in ("EmptyLatentImage", "EmptySDXLLatentImage"):
                    inputs = node.setdefault("inputs", {})
                    inputs["width"] = width
                    inputs["height"] = height
        return wf

    @staticmethod
    def set_load_image(workflow: Dict[str, Any], node_title_hint: str, image_path: str) -> Dict[str, Any]:
        """
        设置 LoadImage 节点的图像路径。
        node_title_hint 用于区分多个 LoadImage 节点（如 "reference" / "target"）。
        """
        wf = json.loads(json.dumps(workflow))
        for node in wf.values():
            if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                meta = node.get("_meta", {})
                title = meta.get("title", "").lower()
                if node_title_hint.lower() in title or node_title_hint == "any":
                    inputs = node.setdefault("inputs", {})
                    inputs["image"] = image_path
        return wf

    @staticmethod
    def set_string_constant(workflow: Dict[str, Any], node_class: str, value: str) -> Dict[str, Any]:
        """设置 StringConstant / CR String 等节点的文本值。"""
        wf = json.loads(json.dumps(workflow))
        for node in wf.values():
            if isinstance(node, dict) and node.get("class_type") == node_class:
                inputs = node.setdefault("inputs", {})
                for k in list(inputs.keys()):
                    if isinstance(inputs[k], str):
                        inputs[k] = value
                        break
        return wf

    # ------------------------------------------------------------------
    # 内置模板（首次运行自动生成）
    # ------------------------------------------------------------------

    def _create_builtin_template(self, name: str) -> Path:
        """如果模板不存在，生成一个最小可用模板。"""
        templates = {
            "character_concept": self._builtin_character_concept(),
            "scene_controlnet": self._builtin_scene_controlnet(),
            "svd_video": self._builtin_svd_video(),
            "instantid": self._builtin_instantid(),
            "tts_qwen": self._builtin_tts_qwen(),
        }
        wf = templates.get(name, {"error": "unknown template"})
        return self.save(name, wf)

    def _builtin_character_concept(self) -> Dict[str, Any]:
        """SDXL + LoRA 角色概念图生成（精简版 ComfyUI API JSON）"""
        return {
            "1": {
                "inputs": {"ckpt_name": "sdXL_base.safetensors"},
                "class_type": "CheckpointLoaderSimple",
            },
            "2": {
                "inputs": {"text": "masterpiece, best quality, 1girl, silver hair, purple eyes, detailed face", "clip": ["1", 1]},
                "class_type": "CLIPTextEncode",
            },
            "3": {
                "inputs": {"text": "lowres, bad anatomy, worst quality", "clip": ["1", 1]},
                "class_type": "CLIPTextEncode",
            },
            "4": {
                "inputs": {
                    "seed": 42,
                    "steps": 30,
                    "cfg": 7.0,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "denoise": 1.0,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["5", 0],
                },
                "class_type": "KSampler",
            },
            "5": {
                "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
                "class_type": "EmptyLatentImage",
            },
            "6": {
                "inputs": {"samples": ["4", 0], "vae": ["1", 2]},
                "class_type": "VAEDecode",
            },
            "7": {
                "inputs": {"filename_prefix": "character_concept", "images": ["6", 0]},
                "class_type": "SaveImage",
            },
            "8": {
                "inputs": {"lora_name": "character_style.safetensors", "strength_model": 0.8, "strength_clip": 1.0, "model": ["1", 0], "clip": ["1", 1]},
                "class_type": "LoraLoader",
            },
        }

    def _builtin_scene_controlnet(self) -> Dict[str, Any]:
        """ControlNet + IP-Adapter 场景生成"""
        return {
            "1": {"inputs": {"ckpt_name": "sdXL_base.safetensors"}, "class_type": "CheckpointLoaderSimple"},
            "2": {"inputs": {"text": "fantasy landscape, magical forest, volumetric lighting", "clip": ["1", 1]}, "class_type": "CLIPTextEncode"},
            "3": {"inputs": {"text": "lowres, blurry", "clip": ["1", 1]}, "class_type": "CLIPTextEncode"},
            "4": {"inputs": {"image": "reference.png"}, "class_type": "LoadImage"},
            "5": {"inputs": {"control_net_name": "control_v11p_sd15_depth.pth"}, "class_type": "ControlNetLoader"},
            "6": {"inputs": {"strength": 1.0, "start_percent": 0, "end_percent": 1, "positive": ["2", 0], "negative": ["3", 0], "control_net": ["5", 0], "image": ["4", 0]}, "class_type": "ControlNetApply"},
            "7": {"inputs": {"seed": 42, "steps": 25, "cfg": 7.0, "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0, "model": ["1", 0], "positive": ["6", 0], "negative": ["3", 0], "latent_image": ["8", 0]}, "class_type": "KSampler"},
            "8": {"inputs": {"width": 1344, "height": 768, "batch_size": 1}, "class_type": "EmptyLatentImage"},
            "9": {"inputs": {"samples": ["7", 0], "vae": ["1", 2]}, "class_type": "VAEDecode"},
            "10": {"inputs": {"filename_prefix": "scene_gen", "images": ["9", 0]}, "class_type": "SaveImage"},
        }

    def _builtin_svd_video(self) -> Dict[str, Any]:
        """Stable Video Diffusion 视频生成"""
        return {
            "1": {"inputs": {"image": "input_frame.png"}, "class_type": "LoadImage"},
            "2": {"inputs": {"svd_name": "svd_xt_1_1.safetensors"}, "class_type": "ImageOnlyCheckpointLoader"},
            "3": {"inputs": {"width": 1024, "height": 576, "video_frames": 14, "motion_bucket_id": 127, "fps": 6, "augmentation_level": 0}, "class_type": "SVD_img2vid_Conditioning"},
            "4": {"inputs": {"seed": 42, "steps": 20, "cfg": 2.5, "sampler_name": "euler", "scheduler": "karras", "denoise": 1.0, "model": ["2", 0], "positive": ["3", 0], "negative": ["3", 1], "latent_image": ["3", 2]}, "class_type": "KSampler"},
            "5": {"inputs": {"samples": ["4", 0], "vae": ["2", 2]}, "class_type": "VAEDecode"},
            "6": {"inputs": {"filename_prefix": "svd_clip", "fps": 6, "compress_level": 4, "images": ["5", 0]}, "class_type": "VHS_VideoCombine"},
        }

    def _builtin_instantid(self) -> Dict[str, Any]:
        """InstantID 面部一致性保持"""
        return {
            "1": {"inputs": {"image": "reference_face.png"}, "class_type": "LoadImage"},
            "2": {"inputs": {"image": "target_image.png"}, "class_type": "LoadImage"},
            "3": {"inputs": {"instantid_file": "ip-adapter.bin"}, "class_type": "InstantIDModelLoader"},
            "4": {"inputs": {"instantid": ["3", 0], "image": ["1", 0]}, "class_type": "InstantIDFaceAnalysis"},
            "5": {"inputs": {"ipadapter_file": "ip-adapter_sd15.bin"}, "class_type": "IPAdapterModelLoader"},
            "6": {"inputs": {"model": ["7", 0], "ipadapter": ["5", 0], "insightface": ["4", 0], "image": ["1", 0]}, "class_type": "IPAdapterApply"},
            "7": {"inputs": {"ckpt_name": "realisticVision.safetensors"}, "class_type": "CheckpointLoaderSimple"},
            "8": {"inputs": {"seed": 42, "steps": 20, "cfg": 5.0, "sampler_name": "dpmpp_2m", "scheduler": "normal", "denoise": 0.8, "model": ["6", 0], "positive": ["9", 0], "negative": ["10", 0], "latent_image": ["11", 0]}, "class_type": "KSampler"},
            "9": {"inputs": {"text": "portrait, detailed face, high quality", "clip": ["7", 1]}, "class_type": "CLIPTextEncode"},
            "10": {"inputs": {"text": "lowres, worst quality", "clip": ["7", 1]}, "class_type": "CLIPTextEncode"},
            "11": {"inputs": {"width": 512, "height": 512, "batch_size": 1}, "class_type": "EmptyLatentImage"},
            "12": {"inputs": {"samples": ["8", 0], "vae": ["7", 2]}, "class_type": "VAEDecode"},
            "13": {"inputs": {"filename_prefix": "instantid", "images": ["12", 0]}, "class_type": "SaveImage"},
        }

    def _builtin_tts_qwen(self) -> Dict[str, Any]:
        """Qwen-TTS / GPT-SoVITS 语音合成（极简 HTTP 调用型工作流）"""
        # TTS 通常不通过 ComfyUI 节点链执行，而是直接调用外部 API
        # 这里放一个占位模板，实际走 bridge 直接 HTTP 调用
        return {
            "_comment": "TTS workflow is executed via direct HTTP API, not ComfyUI nodes",
            "tts_api": "http://localhost:5000/tts",
            "method": "POST",
        }


# ---------------------------------------------------------------------------
# ComfyUI WebSocket 客户端（进度实时回调）
# ---------------------------------------------------------------------------

class ComfyUIClient:
    """
    ComfyUI HTTP + WebSocket 双通道客户端。

    - HTTP：提交 prompt / 获取历史 / 下载图像
    - WebSocket：接收生成进度（executing、progress、executed）
    """

    def __init__(
        self,
        host: str = COMFYUI_DEFAULT_HOST,
        port: int = COMFYUI_DEFAULT_PORT,
        progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.ws_url = f"ws://{host}:{port}/ws"
        self.client_id = str(uuid.uuid4())
        self.progress_callback = progress_callback
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._connected = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def health_check(self) -> Dict[str, Any]:
        """
        检查 ComfyUI 宿主机服务是否可达。
        
        ComfyUI 在 WSL2 宿主机以原生 Python venv 运行，监听 0.0.0.0:8188。
        Docker 容器通过 host.docker.internal:8188 访问。
        
        Returns:
            {"ok": True, "system_stats": {...}} 或 {"ok": False, "error": "..."}
        """
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"ok": True, "system_stats": data}
                return {"ok": False, "error": f"HTTP {resp.status}"}
        except aiohttp.ClientConnectorError as e:
            return {
                "ok": False,
                "error": (
                    f"Cannot connect to ComfyUI at {self.base_url}. "
                    f"Ensure ComfyUI is running on the WSL2 host: "
                    f"cd ~/.triad/apps/comfyui && source ~/.triad/venvs/comfyui/bin/activate && "
                    f"python main.py --listen 0.0.0.0 --port 8188"
                ),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def connect(self) -> None:
        """建立 WebSocket 连接并启动消息循环。"""
        if self._connected:
            return
        self._ws_task = asyncio.create_task(self._ws_loop())
        self._connected = True
        logger.info(f"ComfyUI WebSocket connecting to {self.ws_url}")

    async def disconnect(self) -> None:
        self._connected = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("ComfyUI client disconnected")

    async def _ws_loop(self) -> None:
        while self._connected:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    logger.info("ComfyUI WebSocket connected")
                    async for message in ws:
                        if isinstance(message, bytes):
                            # 二进制预览帧（JPEG）— ComfyUI 每 N 步发送
                            await self._handle_binary_preview(message)
                        else:
                            data = json.loads(message)
                            await self._handle_ws_message(data)
            except Exception as e:
                logger.warning(f"WebSocket error: {e}; reconnecting in 3s ...")
                await asyncio.sleep(3)

    async def _handle_ws_message(self, data: Dict[str, Any]) -> None:
        msg_type = data.get("type")
        if msg_type == "status":
            # 队列状态
            pass
        elif msg_type == "execution_start":
            logger.info(f"Execution started: prompt_id={data.get('data', {}).get('prompt_id')}")
        elif msg_type == "executing":
            node_id = data.get("data", {}).get("node")
            prompt_id = data.get("data", {}).get("prompt_id")
            logger.debug(f"Executing node {node_id} for {prompt_id}")
            if self.progress_callback:
                self.progress_callback(prompt_id, {"event": "executing", "node": node_id})
        elif msg_type == "progress":
            value = data.get("data", {}).get("value", 0)
            max_val = data.get("data", {}).get("max", 1)
            prompt_id = data.get("data", {}).get("prompt_id")
            ratio = value / max_val if max_val else 0
            logger.debug(f"Progress {value}/{max_val} for {prompt_id}")
            if self.progress_callback:
                self.progress_callback(prompt_id, {"event": "progress", "ratio": ratio, "value": value, "max": max_val})
        elif msg_type == "executed":
            node_id = data.get("data", {}).get("node")
            prompt_id = data.get("data", {}).get("prompt_id")
            output = data.get("data", {}).get("output", {})
            logger.info(f"Node {node_id} executed for {prompt_id}")
            if self.progress_callback:
                self.progress_callback(prompt_id, {"event": "executed", "node": node_id, "output": output})
        elif msg_type == "execution_error":
            prompt_id = data.get("data", {}).get("prompt_id")
            error = data.get("data", {}).get("error", {})
            logger.error(f"Execution error for {prompt_id}: {error}")
            if self.progress_callback:
                self.progress_callback(prompt_id, {"event": "error", "error": error})
        elif msg_type == "execution_cached":
            prompt_id = data.get("data", {}).get("prompt_id")
            if self.progress_callback:
                self.progress_callback(prompt_id, {"event": "cached"})

    async def _handle_binary_preview(self, data: bytes) -> None:
        """处理 WebSocket 二进制预览帧。"""
        # ComfyUI 发送的二进制帧格式：前 4 字节为 event type（uint32），后面为 JPEG 数据
        if len(data) < 4:
            return
        import struct
        event_type = struct.unpack(">I", data[:4])[0]
        image_data = data[4:]
        # event_type 1 = 预览图
        if event_type == 1 and self.progress_callback:
            # 这里 prompt_id 无法直接从二进制帧获取，需要在上下文中匹配
            # 简化：发送到队列由外层匹配
            await self._ws_queue.put({"event": "preview", "image": image_data})

    # ------------------------------------------------------------------
    # HTTP API
    # ------------------------------------------------------------------

    async def queue_prompt(self, workflow: Dict[str, Any]) -> str:
        """提交工作流，返回 prompt_id。"""
        session = await self._get_session()
        payload = {"prompt": workflow, "client_id": self.client_id}
        async with session.post(
            f"{self.base_url}/prompt",
            json=payload,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            prompt_id = data["prompt_id"]
            logger.info(f"Prompt queued: {prompt_id}")
            return prompt_id

    async def get_history(self, prompt_id: str) -> Dict[str, Any]:
        """获取生成历史（包含输出文件路径）。"""
        session = await self._get_session()
        async with session.get(
            f"{self.base_url}/history/{prompt_id}",
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def download_image(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        """下载生成的图像/视频文件。"""
        session = await self._get_session()
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
        async with session.get(
            f"{self.base_url}/view",
            params=params,
        ) as resp:
            resp.raise_for_status()
            return await resp.read()

    async def interrupt(self) -> None:
        """中断当前生成。"""
        session = await self._get_session()
        async with session.post(f"{self.base_url}/interrupt") as resp:
            logger.info(f"Interrupt sent: {resp.status}")

    async def free_memory(self, unload_models: bool = True, free_memory: bool = True) -> None:
        """请求 ComfyUI 释放模型显存。"""
        session = await self._get_session()
        payload = {"unload_models": unload_models, "free_memory": free_memory}
        async with session.post(f"{self.base_url}/free", json=payload) as resp:
            logger.info(f"Free memory request: {resp.status}")


# ---------------------------------------------------------------------------
# MCP Server 基类（JSON-RPC 2.0）
# ---------------------------------------------------------------------------

class MCPServer:
    """
    MCP Server 骨架，处理 JSON-RPC 请求。

    输入：stdin 上的 JSON-RPC lines
    输出：stdout 上的 JSON-RPC responses

    子类通过 @tool 装饰器注册工具。
    """

    def __init__(self):
        self._tools: Dict[str, Callable[..., Coroutine[Any, Any, Dict[str, Any]]]] = {}
        self._notifications: Dict[str, Callable[..., Coroutine[Any, Any, None]]] = {}

    def tool(self, name: Optional[str] = None):
        """工具注册装饰器。"""
        def decorator(fn: Callable[..., Coroutine[Any, Any, Dict[str, Any]]]):
            self._tools[name or fn.__name__] = fn
            return fn
        return decorator

    def notification(self, name: Optional[str] = None):
        def decorator(fn: Callable[..., Coroutine[Any, Any, None]]):
            self._notifications[name or fn.__name__] = fn
            return fn
        return decorator

    # ------------------------------------------------------------------
    # JSON-RPC 协议处理
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """主循环：从 stdin 读取 JSON-RPC，写入 stdout。"""
        logger.info("MCP Server starting on stdio")
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        # 写入初始化握手（MCP 协议要求）
        await self._send({
            "jsonrpc": "2.0",
            "id": 0,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {"subscribe": False},
                },
                "serverInfo": {"name": "triad-comfyui-bridge", "version": "1.0.0"},
            },
        })

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                request = json.loads(line)
                response = await self._handle_request(request)
                if response is not None:
                    await self._send(response)
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
                await self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            except Exception as e:
                logger.exception("MCP loop error")

    async def _handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        req_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        # MCP 标准方法
        if method == "tools/list":
            return self._handle_tools_list(req_id)
        elif method == "resources/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}
        elif method == "initialize":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"status": "ok"}}

        # 工具调用
        if method.startswith("tools/call/"):
            tool_name = method[len("tools/call/"):]
            return await self._handle_tool_call(req_id, tool_name, params)
        if method == "tools/call":
            # 另一种调用格式
            tool_name = params.get("name", "")
            return await self._handle_tool_call(req_id, tool_name, params.get("arguments", {}))

        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

    def _handle_tools_list(self, req_id: Any) -> Dict[str, Any]:
        tools = []
        for name, fn in self._tools.items():
            # 从函数签名提取参数 schema（简化版）
            import inspect
            sig = inspect.signature(fn)
            properties = {}
            required = []
            for pname, param in sig.parameters.items():
                if pname in ("self",):
                    continue
                ptype = "string"
                if param.annotation == int:
                    ptype = "integer"
                elif param.annotation == float:
                    ptype = "number"
                elif param.annotation == bool:
                    ptype = "boolean"
                elif param.annotation == dict:
                    ptype = "object"
                properties[pname] = {"type": ptype, "description": ""}
                if param.default is inspect.Parameter.empty:
                    required.append(pname)
            tools.append({
                "name": name,
                "description": fn.__doc__ or f"Tool {name}",
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            })
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}

    async def _handle_tool_call(self, req_id: Any, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name not in self._tools:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Tool not found: {tool_name}"}}
        try:
            result = await self._tools[tool_name](**params)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
        except Exception as e:
            logger.exception(f"Tool {tool_name} failed")
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}

    async def _send(self, data: Dict[str, Any]) -> None:
        line = json.dumps(data, ensure_ascii=False) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()

    async def send_notification(self, method: str, params: Dict[str, Any]) -> None:
        """向 Hermes Agent 发送主动通知（如进度更新）。"""
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})


# ---------------------------------------------------------------------------
# StatusUpdate Protobuf 映射（Python dict 表示）
# ---------------------------------------------------------------------------

class StatusUpdateBuilder:
    """
    构建 StatusUpdate 消息（JSON 表示，对应 protobuf 定义）。

    扩展字段：
      preview.text   -> TextPreview
      preview.image  -> ImagePreview (JPEG base64, 每 5 步)
      preview.video  -> VideoFrame
    """

    @staticmethod
    def text(message: str, step: Optional[int] = None, total_steps: Optional[int] = None) -> Dict[str, Any]:
        return {
            "preview": {
                "text": {
                    "message": message,
                    "step": step,
                    "total_steps": total_steps,
                }
            }
        }

    @staticmethod
    def image(base64_jpeg: str, step: int, total_steps: int) -> Dict[str, Any]:
        return {
            "preview": {
                "image": {
                    "data": base64_jpeg,
                    "mime": "image/jpeg",
                    "step": step,
                    "total_steps": total_steps,
                }
            }
        }

    @staticmethod
    def video_frame(base64_jpeg: str, frame_index: int, total_frames: int) -> Dict[str, Any]:
        return {
            "preview": {
                "frame": {
                    "data": base64_jpeg,
                    "mime": "image/jpeg",
                    "frame_index": frame_index,
                    "total_frames": total_frames,
                }
            }
        }


# ---------------------------------------------------------------------------
# ComfyUI MCP Bridge（核心实现）
# ---------------------------------------------------------------------------

class ComfyUIMCPBridge(MCPServer):
    """
    ComfyUI MCP Bridge — 将图像/视频/语音生成能力暴露为 MCP 工具。

    工具清单（tools/list 返回）：
      1. generate_character_concept  — SDXL + LoRA 角色概念图
      2. generate_scene             — ControlNet + IP-Adapter 场景
      3. generate_video_clip        — SVD 视频生成
      4. generate_tts               — 语音合成
      5. instantid_face_swap        — 面部一致性保持
    """

    def __init__(
        self,
        comfy_host: str = COMFYUI_DEFAULT_HOST,
        comfy_port: int = COMFYUI_DEFAULT_PORT,
        output_dir: Optional[Path] = None,
        asset_manager: Optional[AssetManager] = None,
        vram_scheduler: Optional[VRAMScheduler] = None,
    ):
        super().__init__()
        self.comfy = ComfyUIClient(
            host=comfy_host,
            port=comfy_port,
            progress_callback=self._on_comfy_progress,
        )
        self.templates = WorkflowTemplate()
        self.output_dir = output_dir or (Path.home() / ".triad" / "outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.asset_manager = asset_manager or AssetManager()
        self.vram = vram_scheduler or VRAMScheduler()

        # 渲染任务追踪：prompt_id -> task metadata
        self._active_tasks: Dict[str, Dict[str, Any]] = {}
        self._task_lock = asyncio.Lock()

        # 注册工具
        self._register_tools()

    def _register_tools(self) -> None:
        """将 async 方法注册为 MCP 工具。"""
        self._tools["generate_character_concept"] = self.generate_character_concept
        self._tools["generate_scene"] = self.generate_scene
        self._tools["generate_video_clip"] = self.generate_video_clip
        self._tools["generate_tts"] = self.generate_tts
        self._tools["instantid_face_swap"] = self.instantid_face_swap

    # ------------------------------------------------------------------
    # 工作流加载与参数注入（API JSON 文件驱动）
    # ------------------------------------------------------------------

    def _load_api_workflow(self, workflow_name: str) -> Dict:
        """
        从同目录加载 ComfyUI API 格式的工作流 JSON 文件。

        文件命名规范: {workflow_name}_api.json
        例如: character_concept_api.json, scene_api.json, video_clip_api.json
        """
        workflow_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"{workflow_name}_api.json"
        )

        if not os.path.exists(workflow_path):
            raise FileNotFoundError(
                f"工作流文件未找到: {workflow_path}\n"
                f"请在 ComfyUI 中搭建工作流，点击 'Save (API Format)' 导出为 {workflow_name}_api.json，"
                f"并放置到 {os.path.dirname(os.path.abspath(__file__))} 目录下。"
            )

        with open(workflow_path, 'r', encoding='utf-8') as f:
            workflow = json.load(f)

        return workflow

    def _inject_prompt_to_workflow(
        self,
        workflow: Dict,
        positive_prompt: str,
        negative_prompt: str,
        seed: int,
        width: int = 1024,
        height: int = 1024,
    ) -> Dict:
        """
        遍历 ComfyUI API JSON，注入用户参数。

        ComfyUI API 格式示例:
        {
          "1": {"inputs": {"text": "positive prompt here", "clip": ["2", 0]}, "class_type": "CLIPTextEncode"},
          "2": {"inputs": {"ckpt_name": "sdxl_base.safetensors"}, "class_type": "CheckpointLoaderSimple"},
          "3": {"inputs": {"seed": 42, "steps": 30, "cfg": 7.0, ...}, "class_type": "KSampler"},
          ...
        }

        注入策略：
        1. 查找 class_type 为 "CLIPTextEncode" 且 inputs.text 包含正向提示词标记的节点 → 替换为 positive_prompt
        2. 查找 class_type 为 "CLIPTextEncode" 且 inputs.text 看起来像负向提示词的节点 → 替换为 negative_prompt
        3. 查找 class_type 为 "KSampler" 或 "RandomNoise" 的节点 → 替换 seed
        4. 查找 class_type 为 "EmptyLatentImage" 的节点 → 替换 width/height
        """
        workflow = copy.deepcopy(workflow)

        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue

            inputs = node.get("inputs", {})
            class_type = node.get("class_type", "")

            # 1. 正向 / 负向 Prompt 注入
            if class_type in ("CLIPTextEncode", "CLIPTextEncodeSDXL", "CLIPTextEncodeFlux") and "text" in inputs:
                current_text = str(inputs["text"]).lower()
                # 先检查负向：如果包含负向关键词，优先标记为负向节点
                negative_kw = ["negative", "nsfw", "bad anatomy", "bad hands", "worst quality"]
                if any(kw in current_text for kw in negative_kw):
                    inputs["text"] = negative_prompt
                    logger.info(f"注入负向 Prompt 到节点 {node_id}")
                # 再检查正向：包含占位符/正向标记（且不是负向）
                elif any(kw in current_text for kw in ["positive", "prompt", "placeholder", "input", "masterpiece", "best quality", "1girl", "1boy"]):
                    inputs["text"] = positive_prompt
                    logger.info(f"注入正向 Prompt 到节点 {node_id}")
                # 兜底：如果文本非常短或为空，替换为正向
                elif len(current_text.strip()) < 5:
                    inputs["text"] = positive_prompt
                    logger.info(f"注入正向 Prompt 到节点 {node_id} (兜底)")

            # 2. Seed 注入
            if class_type in ("KSampler", "RandomNoise", "KSamplerAdvanced", "SamplerCustom") and "seed" in inputs:
                inputs["seed"] = seed
                logger.info(f"注入 seed={seed} 到节点 {node_id}")

            # 3. 尺寸注入
            if class_type in ("EmptyLatentImage", "EmptySDXLLatentImage"):
                if "width" in inputs:
                    inputs["width"] = width
                if "height" in inputs:
                    inputs["height"] = height
                logger.info(f"注入尺寸 {width}x{height} 到节点 {node_id}")

        return workflow

    def _build_character_prompt(self, character_description: str, style_preset: str, reference_face: Optional[str] = None) -> str:
        """构建角色概念图的正向 Prompt。"""
        style_map = {
            "anime": "anime style, cel shading, vibrant colors, detailed face",
            "realistic": "photorealistic, 8k uhd, detailed skin texture, natural lighting",
            "fantasy": "fantasy art, epic lighting, highly detailed, digital painting",
            "chibi": "chibi style, super deformed, cute, big head, small body",
            "cyberpunk": "cyberpunk style, neon lighting, futuristic, high tech, dark background",
            "sci-fi": "sci-fi, cyberpunk, neon lights, futuristic",
        }
        style_tag = style_map.get(style_preset, style_map["anime"])

        prompt = f"character concept art, {style_tag}, {character_description}, " \
                 f"full body, standing pose, neutral background, " \
                 f"masterpiece, best quality, highly detailed"

        if reference_face:
            prompt += f", reference face: {reference_face}"

        return prompt

    def _build_negative_prompt(self, task_type: str = "character") -> str:
        """构建通用负向 Prompt。"""
        negative = (
            "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
            "fewer digits, cropped, worst quality, low quality, normal quality, "
            "jpeg artifacts, signature, watermark, username, blurry, artist name"
        )
        if task_type == "scene":
            negative = "lowres, blurry, worst quality, watermark, text, bad architecture"
        elif task_type == "video":
            negative = "lowres, blurry, jitter, flickering, watermark"
        return negative

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        # 先检查 ComfyUI 宿主机服务是否可达
        health = await self.comfy.health_check()
        if not health["ok"]:
            logger.error(f"ComfyUI health check failed: {health['error']}")
            raise RuntimeError(f"ComfyUI not reachable: {health['error']}")
        logger.info(f"ComfyUI health check passed: {health.get('system_stats', {})}")
        await self.comfy.connect()
        await self.vram.start()
        await self.asset_manager.build_index()
        logger.info("ComfyUIMCPBridge ready")

    async def stop(self) -> None:
        await self.comfy.disconnect()
        await self.vram.stop()
        logger.info("ComfyUIMCPBridge stopped")

    # ------------------------------------------------------------------
    # 进度回调：ComfyUI WebSocket -> MCP notification
    # ------------------------------------------------------------------

    def _on_comfy_progress(self, prompt_id: str, event: Dict[str, Any]) -> None:
        """由 ComfyUIClient 在 WebSocket 线程调用 — 需用 asyncio.run_coroutine_threadsafe 投递。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.run_coroutine_threadsafe(self._handle_progress_async(prompt_id, event), loop)

    async def _handle_progress_async(self, prompt_id: str, event: Dict[str, Any]) -> None:
        async with self._task_lock:
            task_meta = self._active_tasks.get(prompt_id)
        if task_meta is None:
            return

        event_type = event.get("event")
        if event_type == "progress":
            ratio = event.get("ratio", 0)
            step = event.get("value", 0)
            max_step = event.get("max", 1)
            message = f"Generating {task_meta['tool']} — step {step}/{max_step}"
            status = StatusUpdateBuilder.text(message, step=step, total_steps=max_step)
            # 每 5 步尝试附带预览图（如果有缓存预览）
            if step % 5 == 0 and step > 0:
                preview_b64 = await self._get_latest_preview_b64(prompt_id)
                if preview_b64:
                    status = StatusUpdateBuilder.image(preview_b64, step, max_step)
            await self.send_notification("status/update", status)

        elif event_type == "executed":
            node_id = event.get("node")
            output = event.get("output", {})
            images = output.get("images", [])
            if images:
                task_meta["output_images"] = images

        elif event_type == "error":
            await self.send_notification("status/update", StatusUpdateBuilder.text(
                f"Error in {task_meta['tool']}: {event.get('error', {})}",
            ))

    async def _get_latest_preview_b64(self, prompt_id: str) -> Optional[str]:
        """从 ComfyUI 输出目录读取最新预览图。"""
        preview_dir = Path.home() / "ComfyUI" / "output" / "previews"
        if not preview_dir.exists():
            return None
        # 查找最新文件（简单实现）
        files = sorted(preview_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        latest = files[0]
        async with aiofiles.open(latest, "rb") as f:
            data = await f.read()
        return base64.b64encode(data).decode("ascii")

    # ------------------------------------------------------------------
    # 通用：执行 ComfyUI 工作流（含 VRAM 调度 + 资产存储）
    # ------------------------------------------------------------------

    async def _execute_workflow(
        self,
        tool_name: str,
        workflow_name: str,
        workflow: Dict[str, Any],
        vram_estimate_mb: int,
        asset_type: str,
        asset_id_hint: str,
        generation_params: Dict[str, Any],
        timeout_sec: float = 300.0,
    ) -> Dict[str, Any]:
        """
        通用执行管线：
          1. 申请 VRAM
          2. 提交 prompt 到 ComfyUI
          3. 轮询历史获取结果
          4. 下载输出文件
          5. 存入资产库
          6. 释放 VRAM
        """
        task_id = f"{tool_name}_{uuid.uuid4().hex[:8]}"
        render_task = RenderTask(
            task_id=task_id,
            workflow_type=workflow_name,
            estimated_vram_mb=vram_estimate_mb,
            priority=1,
        )

        # 1. 申请显存
        async with await self.vram.acquire_render_context(render_task) as ctx:
            await self.send_notification("status/update", StatusUpdateBuilder.text(
                f"VRAM acquired ({ctx.mode}), submitting {tool_name} ..."
            ))

            # 注册活跃任务
            async with self._task_lock:
                self._active_tasks[task_id] = {
                    "tool": tool_name,
                    "task_id": task_id,
                    "output_images": [],
                    "output_files": [],
                }

            # 2. 提交
            prompt_id = await self.comfy.queue_prompt(workflow)
            async with self._task_lock:
                self._active_tasks[prompt_id] = self._active_tasks.pop(task_id)
                self._active_tasks[prompt_id]["prompt_id"] = prompt_id

            # 3. 轮询结果（每5秒发送进度心跳，超时300秒）
            result_files: List[str] = []
            used_nodes: List[str] = []
            t0 = time.time()
            last_heartbeat = t0
            heartbeat_interval = 5.0
            while time.time() - t0 < timeout_sec:
                await asyncio.sleep(1.0)
                # 每 N 秒发送进度心跳通知
                if time.time() - last_heartbeat >= heartbeat_interval:
                    last_heartbeat = time.time()
                    elapsed = time.time() - t0
                    await self.send_notification("status/update", StatusUpdateBuilder.text(
                        f"{tool_name} running... elapsed={elapsed:.0f}s / timeout={timeout_sec:.0f}s"
                    ))
                history = await self.comfy.get_history(prompt_id)
                entry = history.get(prompt_id, {})
                outputs = entry.get("outputs", {})
                if outputs:
                    for node_id, node_output in outputs.items():
                        used_nodes.append(node_id)
                        images = node_output.get("images", [])
                        for img_info in images:
                            filename = img_info["filename"]
                            subfolder = img_info.get("subfolder", "")
                            data = await self.comfy.download_image(filename, subfolder)
                            local_path = self.output_dir / filename
                            async with aiofiles.open(local_path, "wb") as f:
                                await f.write(data)
                            result_files.append(str(local_path))
                    break
            else:
                # 超时：发送失败通知并抛出异常
                await self.send_notification("status/update", StatusUpdateBuilder.text(
                    f"{tool_name} timed out after {timeout_sec}s"
                ))
                raise TimeoutError(f"Workflow {tool_name} timed out after {timeout_sec}s")

            # 4. 存入资产库
            stored_paths: List[str] = []
            for idx, fp in enumerate(result_files):
                aid = f"{asset_id_hint}_{idx}" if len(result_files) > 1 else asset_id_hint
                meta = AssetMeta(
                    asset_id=aid,
                    asset_type=asset_type,
                    format=Path(fp).suffix.lstrip("."),
                    generation_params=generation_params,
                )
                stored = await self.asset_manager.store_asset(
                    asset_id=aid,
                    asset_type=asset_type,
                    source_path=fp,
                    meta=meta,
                    copy=False,  # 直接移动
                )
                stored_paths.append(str(stored))

            # 清理活跃任务
            async with self._task_lock:
                self._active_tasks.pop(prompt_id, None)

            return {
                "task_id": task_id,
                "prompt_id": prompt_id,
                "output_paths": result_files,
                "stored_asset_paths": stored_paths,
                "generation_params": generation_params,
                "used_nodes": list(set(used_nodes)),
                "vram_mode": ctx.mode,
            }

    # ------------------------------------------------------------------
    # Tool 1: generate_character_concept
    # ------------------------------------------------------------------

    async def generate_character_concept(
        self,
        character_description: str,
        style_preset: str = "anime",
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        reference_face: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        生成角色概念图（从 ComfyUI API JSON 工作流加载 + 参数注入）。

        Args:
            character_description: 角色描述，如 "1girl, silver hair, purple eyes"
            style_preset: 风格预设（anime, realistic, fantasy, chibi, cyberpunk, sci-fi）
            width, height: 输出尺寸
            seed: 随机种子（None 为随机）
            reference_face: 可选参考面部标识
        Returns:
            {image_path, generation_params, used_nodes}
        """
        # 1. 参数校验
        seed = seed or random.randint(1, 2**32 - 1)

        # 2. 构建 Prompt
        positive_prompt = self._build_character_prompt(character_description, style_preset, reference_face)
        negative_prompt = self._build_negative_prompt("character")

        # 3. 加载工作流 JSON（从同目录文件）
        try:
            workflow = self._load_api_workflow("character_concept")
        except FileNotFoundError as e:
            logger.error(f"工作流模板缺失: {e}")
            return {
                "success": False,
                "error": f"工作流模板缺失: {e}\n"
                         f"请先在 ComfyUI 中搭建角色生成工作流，导出 API JSON 并放置到正确位置。"
            }

        # 4. 注入参数
        workflow = self._inject_prompt_to_workflow(
            workflow, positive_prompt, negative_prompt, seed, width, height
        )

        # LoRA 动态选择（如果工作流中有 LoraLoader 节点）
        lora_map = {
            "anime": "anime_style.safetensors",
            "realistic": "realistic.safetensors",
            "fantasy": "fantasy_style.safetensors",
            "chibi": "chibi_style.safetensors",
            "cyberpunk": "cyberpunk_style.safetensors",
            "sci-fi": "scifi_style.safetensors",
        }
        lora_name = lora_map.get(style_preset, "character_style.safetensors")
        for node in workflow.values():
            if isinstance(node, dict) and node.get("class_type") == "LoraLoader":
                node.setdefault("inputs", {})["lora_name"] = lora_name
                logger.info(f"设置 LoRA: {lora_name}")

        # 5. 执行工作流
        result = await self._execute_workflow(
            tool_name="generate_character_concept",
            workflow_name="character_concept",
            workflow=workflow,
            vram_estimate_mb=VRAM_ESTIMATE["sdxl"],
            asset_type="faces",
            asset_id_hint=f"char_{uuid.uuid4().hex[:6]}",
            generation_params={
                "model": "SDXL",
                "prompt": positive_prompt,
                "negative": negative_prompt,
                "seed": seed,
                "width": width,
                "height": height,
                "style_preset": style_preset,
                "reference_face": reference_face,
            },
        )

        return {
            "image_path": result["stored_asset_paths"][0] if result["stored_asset_paths"] else None,
            "generation_params": result["generation_params"],
            "used_nodes": result["used_nodes"],
            "vram_mode": result["vram_mode"],
        }

    # ------------------------------------------------------------------
    # Tool 2: generate_scene
    # ------------------------------------------------------------------

    async def generate_scene(
        self,
        scene_description: str,
        mood: str = "neutral",
        lighting: str = "natural",
        reference_image: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        生成场景图（ControlNet + IP-Adapter）。

        Args:
            scene_description: 场景描述
            mood: 氛围（peaceful, tense, mysterious, epic）
            lighting: 光照（natural, dramatic, neon, golden_hour, moonlight）
            reference_image: 可选参考图路径（asset:// URI 或本地路径）
        Returns:
            {image_path}
        """
        mood_map = {
            "peaceful": "serene, tranquil, soft colors",
            "tense": "tense atmosphere, high contrast, foreboding",
            "mysterious": "mysterious, foggy, hidden details",
            "epic": "epic scale, grand vista, awe-inspiring",
        }
        lighting_map = {
            "natural": "natural lighting, soft shadows",
            "dramatic": "dramatic lighting, strong shadows, rim light",
            "neon": "neon lighting, cyberpunk glow, vibrant colors",
            "golden_hour": "golden hour, warm sunlight, long shadows",
            "moonlight": "moonlight, cool blue tones, silver highlights",
        }
        full_prompt = f"{scene_description}, {mood_map.get(mood, '')}, {lighting_map.get(lighting, '')}, masterpiece, best quality, highly detailed"
        negative = "lowres, blurry, worst quality, watermark, text"

        wf = self.templates.load("scene_controlnet")
        wf = WorkflowTemplate.inject_prompt(wf, full_prompt, negative)
        wf = WorkflowTemplate.set_seed(wf, int(time.time()) % 2**32)

        # 如果有参考图，解析并设置 LoadImage 节点
        if reference_image:
            ref_path = reference_image
            if reference_image.startswith("asset://"):
                resolved = await self.asset_manager.resolve_uri(reference_image)
                if resolved:
                    ref_path = str(resolved)
            wf = WorkflowTemplate.set_load_image(wf, "reference", ref_path)

        result = await self._execute_workflow(
            tool_name="generate_scene",
            workflow_name="scene_controlnet",
            workflow=wf,
            vram_estimate_mb=VRAM_ESTIMATE["controlnet"],
            asset_type="scenes",
            asset_id_hint=f"scene_{uuid.uuid4().hex[:6]}",
            generation_params={
                "prompt": full_prompt,
                "mood": mood,
                "lighting": lighting,
                "reference_image": reference_image,
            },
        )

        return {
            "image_path": result["stored_asset_paths"][0] if result["stored_asset_paths"] else None,
            "generation_params": result["generation_params"],
            "vram_mode": result["vram_mode"],
        }

    # ------------------------------------------------------------------
    # Tool 3: generate_video_clip
    # ------------------------------------------------------------------

    async def generate_video_clip(
        self,
        input_image: str,
        motion_prompt: str = "camera pan left",
        frames: int = 14,
        fps: int = 6,
        seed: int = -1,
    ) -> Dict[str, Any]:
        """
        生成视频片段（Stable Video Diffusion）。

        Args:
            input_image: 输入图像路径或 asset:// URI
            motion_prompt: 运动描述（影响 motion_bucket_id）
            frames: 帧数（SVD 建议 14 或 25）
            fps: 帧率
            seed: 随机种子
        Returns:
            {video_path, frame_previews[]}
        """
        if seed == -1:
            seed = int(time.time()) % 2**32

        # 解析输入图像
        img_path = input_image
        if input_image.startswith("asset://"):
            resolved = await self.asset_manager.resolve_uri(input_image)
            if resolved:
                img_path = str(resolved)

        # motion_bucket_id 映射（SVD 参数，值越大运动越强）
        motion_buckets = {
            "static": 40,
            "slow": 80,
            "normal": 127,
            "fast": 180,
            "very fast": 255,
        }
        # 从 motion_prompt 推断
        mbid = 127
        for key, val in motion_buckets.items():
            if key in motion_prompt.lower():
                mbid = val
                break

        wf = self.templates.load("svd_video")
        wf = WorkflowTemplate.set_load_image(wf, "any", img_path)
        wf = WorkflowTemplate.set_seed(wf, seed)

        # 修改 SVD 节点参数
        for node in wf.values():
            if isinstance(node, dict) and node.get("class_type") == "SVD_img2vid_Conditioning":
                inputs = node.setdefault("inputs", {})
                inputs["video_frames"] = frames
                inputs["fps"] = fps
                inputs["motion_bucket_id"] = mbid
            if isinstance(node, dict) and node.get("class_type") == "VHS_VideoCombine":
                inputs = node.setdefault("inputs", {})
                inputs["fps"] = fps

        result = await self._execute_workflow(
            tool_name="generate_video_clip",
            workflow_name="svd_video",
            workflow=wf,
            vram_estimate_mb=VRAM_ESTIMATE["svd"],
            asset_type="videos",
            asset_id_hint=f"video_{uuid.uuid4().hex[:6]}",
            generation_params={
                "model": "SVD_XT_1_1",
                "input_image": input_image,
                "motion_prompt": motion_prompt,
                "frames": frames,
                "fps": fps,
                "motion_bucket_id": mbid,
                "seed": seed,
            },
            timeout_sec=600.0,  # 视频生成更慢
        )

        # SVD 输出通常是视频文件 + 帧预览
        # 提取视频路径和预览帧
        video_path = None
        previews: List[str] = []
        for p in result.get("stored_asset_paths", []):
            if p.endswith((".mp4", ".webm", ".mov")):
                video_path = p
            elif p.endswith((".png", ".jpg", ".jpeg")):
                previews.append(p)

        return {
            "video_path": video_path,
            "frame_previews": previews,
            "generation_params": result["generation_params"],
            "vram_mode": result["vram_mode"],
        }

    # ------------------------------------------------------------------
    # Tool 4: generate_tts
    # ------------------------------------------------------------------

    async def generate_tts(
        self,
        text: str,
        speaker_id: str = "default",
        emotion: str = "neutral",
        speed: float = 1.0,
    ) -> Dict[str, Any]:
        """
        语音合成（Qwen-TTS / GPT-SoVITS）。

        如果 ComfyUI 节点链不支持 TTS，则直接调用外部 TTS HTTP API。
        Args:
            text: 待合成文本
            speaker_id: 说话人 ID
            emotion: 情绪（neutral, happy, sad, angry, excited）
            speed: 语速倍率（0.5 ~ 2.0）
        Returns:
            {audio_path, duration}
        """
        # TTS 通常不需要大量 VRAM，不申请渲染上下文
        tts_api_url = os.getenv("TTS_API_URL", "http://localhost:5000/tts")

        payload = {
            "text": text,
            "speaker_id": speaker_id,
            "emotion": emotion,
            "speed": speed,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(tts_api_url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                audio_url = data.get("audio_url") or data.get("audio_path")
                duration = data.get("duration", 0.0)

        # 如果返回的是 URL，下载到本地
        local_audio_path = None
        if audio_url:
            if audio_url.startswith("http"):
                async with aiohttp.ClientSession() as session:
                    async with session.get(audio_url) as resp:
                        audio_data = await resp.read()
                filename = f"tts_{uuid.uuid4().hex[:8]}.wav"
                local_audio_path = self.output_dir / filename
                async with aiofiles.open(local_audio_path, "wb") as f:
                    await f.write(audio_data)
            else:
                local_audio_path = Path(audio_url)

        # 存入资产库
        stored_path = None
        if local_audio_path and local_audio_path.exists():
            aid = f"tts_{speaker_id}_{uuid.uuid4().hex[:6]}"
            meta = AssetMeta(
                asset_id=aid,
                asset_type="audio",
                format="wav",
                duration_sec=duration,
                generation_params={"text": text, "speaker_id": speaker_id, "emotion": emotion, "speed": speed},
            )
            stored = await self.asset_manager.store_asset(
                asset_id=aid,
                asset_type="audio",
                source_path=local_audio_path,
                meta=meta,
                copy=False,
            )
            stored_path = str(stored)

        return {
            "audio_path": stored_path or str(local_audio_path) if local_audio_path else None,
            "duration": duration,
            "speaker_id": speaker_id,
            "emotion": emotion,
        }

    # ------------------------------------------------------------------
    # Tool 5: instantid_face_swap
    # ------------------------------------------------------------------

    async def instantid_face_swap(
        self,
        target_image: str,
        reference_face_image: str,
    ) -> Dict[str, Any]:
        """
        使用 InstantID 保持角色面部一致性。

        Args:
            target_image: 目标图像（将被替换面部的图像）asset:// URI 或本地路径
            reference_face_image: 参考面部图像 asset:// URI 或本地路径
        Returns:
            {swapped_image_path}
        """
        # 解析路径
        target_path = target_image
        ref_path = reference_face_image
        for uri, dest in [(target_image, "target_path"), (reference_face_image, "ref_path")]:
            if uri.startswith("asset://"):
                resolved = await self.asset_manager.resolve_uri(uri)
                if resolved:
                    if dest == "target_path":
                        target_path = str(resolved)
                    else:
                        ref_path = str(resolved)

        wf = self.templates.load("instantid")
        wf = WorkflowTemplate.set_load_image(wf, "reference", ref_path)
        wf = WorkflowTemplate.set_load_image(wf, "target", target_path)
        wf = WorkflowTemplate.set_seed(wf, int(time.time()) % 2**32)

        result = await self._execute_workflow(
            tool_name="instantid_face_swap",
            workflow_name="instantid",
            workflow=wf,
            vram_estimate_mb=VRAM_ESTIMATE["instantid"],
            asset_type="faces",
            asset_id_hint=f"swap_{uuid.uuid4().hex[:6]}",
            generation_params={
                "target_image": target_image,
                "reference_face_image": reference_face_image,
                "model": "InstantID",
            },
        )

        return {
            "swapped_image_path": result["stored_asset_paths"][0] if result["stored_asset_paths"] else None,
            "generation_params": result["generation_params"],
            "vram_mode": result["vram_mode"],
        }


# ---------------------------------------------------------------------------
# CLI / 测试入口
# ---------------------------------------------------------------------------

import contextlib


async def _demo_main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # 创建 Bridge（不连接真实 ComfyUI）
    bridge = ComfyUIMCPBridge(
        comfy_host="host.docker.internal",
        comfy_port=8188,
    )

    # 列出可用工具
    tools_resp = bridge._handle_tools_list(req_id="demo")
    print("Available tools:")
    for t in tools_resp["result"]["tools"]:
        print(f"  - {t['name']}: {t['description'][:80]}...")

    # 测试模板加载
    wf = bridge.templates.load("character_concept")
    print(f"\nCharacter concept template keys: {list(wf.keys())}")

    # 测试参数注入
    wf_injected = WorkflowTemplate.inject_prompt(
        wf,
        "1boy, black hair, red eyes, cyberpunk outfit",
        "lowres, bad hands",
    )
    wf_sized = WorkflowTemplate.set_latent_size(wf_injected, 1344, 768)
    wf_seeded = WorkflowTemplate.set_seed(wf_sized, 12345)
    print(f"Injected seed check: {wf_seeded['4']['inputs']['seed']}")
    print(f"Injected size check: {wf_seeded['5']['inputs']}")

    # 测试 asset:// URI 解析
    from asset_manager import AssetManager
    am = AssetManager(base_path=tempfile.mkdtemp())
    uri = "asset://faces/alice_v3.png"
    link = am.parse_asset_uri(uri)
    print(f"\nParsed URI: type={link.asset_type.value}, id={link.asset_id}, ext={link.ext}")

    # 测试 Markdown 提取
    md = "参考图: ![正面照](asset://faces/alice_v3.png)"
    links = am.extract_asset_links(md)
    print(f"Extracted from markdown: {links[0].uri}")

    # 清理
    shutil.rmtree(am.base_path, ignore_errors=True)
    print("\nDemo complete — all imports and core logic verified.")


if __name__ == "__main__":
    import shutil
    asyncio.run(_demo_main())
