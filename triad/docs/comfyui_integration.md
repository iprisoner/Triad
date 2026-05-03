# Triad ComfyUI 集成手册

> 版本: 1.0.0  
> 目标硬件: 魔改 22GB 2080Ti（双路 E5-2673v3）  
> 通信协议: MCP（Model Context Protocol, JSON-RPC 2.0）

---

## 1. 架构概览

```
┌──────────────┐      ACP (gRPC/WebSocket)       ┌──────────────┐
│  OpenClaw    │ ────────────────────────────────> │ Hermes Agent │
│  (TypeScript)│ <───────────────────────────────── │   (Python)   │
└──────────────┘                                   └──────┬───────┘
                                                          │ MCP (JSON-RPC)
                                                          v
                                            ┌─────────────────────────┐
                                            │  ComfyUI MCP Bridge     │
                                            │  (Python, stdio/WS)     │
                                            └───────────┬─────────────┘
                                                        │
                              ┌─────────────────────────┼─────────────────────────┐
                              │                         │                         │
                              v                         v                         v
                    ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
                    │  VRAMScheduler  │      │  ComfyUIClient  │      │  AssetManager   │
                    │  (显存分时复用)  │      │  (HTTP+WebSocket)│      │  (资产持久化)    │
                    └─────────────────┘      └─────────────────┘      └─────────────────┘
                              │                         │                         │
                              v                         v                         v
                         pynvml / NVML           ComfyUI Server            ~/.triad/memory/
                         LLM Swap Controller       (18188 / ws)             assets/
```

---

## 2. 环境准备

### 2.1 依赖安装

```bash
# Python 3.10+
pip install aiohttp aiofiles websockets Pillow pynvml

# ComfyUI（独立环境，避免与 LLM 环境冲突）
git clone https://github.com/comfyanonymous/ComfyUI.git ~/ComfyUI
cd ~/ComfyUI
pip install -r requirements.txt

# 关键节点包
# - ComfyUI-Manager（自动安装其他节点）
# - ComfyUI-VideoHelperSuite（SVD 视频导出）
# - comfyui_controlnet_aux（ControlNet 预处理）
# - ComfyUI-InstantID（面部一致性）
# - WAS Node Suite（高级图像处理）
```

### 2.2 模型文件放置

```
~/ComfyUI/
├── models/
│   ├── checkpoints/
│   │   ├── sdXL_base.safetensors          # 6.9GB
│   │   └── realisticVision.safetensors    # 2.1GB
│   ├── loras/
│   │   ├── character_style.safetensors    # 144MB
│   │   └── anime_style.safetensors        # 144MB
│   ├── controlnet/
│   │   └── control_v11p_sd15_depth.pth   # 1.4GB
│   ├── ipadapter/
│   │   └── ip-adapter_sd15.bin           # 397MB
│   └── vae/
│       └── sdxl_vae.safetensors          # 335MB
```

### 2.3 魔改 2080Ti 驱动配置

```bash
# 确认 22GB 显存识别正确
nvidia-smi
# 应显示 22528 MiB 总显存

# 如果只有 11GB，检查 NVStraps / MAT 魔改驱动
# 参考: https://github.com/.../nvidia-memory-extension
```

---

## 3. 启动流程

### 3.1 启动顺序

```bash
# 1. 启动 LLM 服务（vLLM，常驻 4GB VRAM）
python -m vllm.entrypoints.openai.api_server \
  --model /models/Qwen2.5-7B-Instruct-GPTQ-Int4 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.18 \
  --port 8000 &

# 2. 启动 ComfyUI（显存由 VRAMScheduler 动态管理）
cd ~/ComfyUI
python main.py \
  --listen 127.0.0.1 \
  --port 18188 \
  --disable-xformers \      # 如果 xformers 导致问题
  &

# 3. 启动 MCP Bridge（stdio 模式，由 Hermes Agent 通过 subprocess 拉起）
python /mnt/agents/output/triad/hand/comfyui_mcp_bridge.py
```

### 3.2 VRAM 分时复用时序

```
时间轴 ───────────────────────────────────────────────>

[常态运行]
  ├─ Embedding: 2GB (常驻)
  ├─ vLLM Qwen: 4GB (常驻)
  └─ 空闲: 16GB

        │ Hermes 收到图像生成请求
        v
[申请渲染]
  ├─ vLLM /unload 信号 ──→ VRAM 释放 4GB
  ├─ 等待 2s (显存回落)
  ├─ ComfyUI 启动 / 加载模型
  └─ 可用: 20GB

        │ ComfyUI 执行 SDXL 推理
        │ (6GB Base + 4GB Refiner + 2GB ControlNet = 12GB)
        v
[渲染中]
  ├─ Embedding: 2GB (常驻)
  ├─ ComfyUI: 12-16GB
  └─ 安全边距: 2GB

        │ 渲染完成，下载输出
        v
[释放恢复]
  ├─ ComfyUI /free 释放显存
  ├─ vLLM warm_up() ──→ 预热 3 tokens
  ├─ 等待 10-20s (权重加载)
  └─ 回归常态
```

---

## 4. MCP 工具调用协议

### 4.1 初始化握手

```json
// Hermes Agent → Bridge
{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}

// Bridge → Hermes Agent
{"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "triad-comfyui-bridge", "version": "1.0.0"}}}
```

### 4.2 工具列表

```json
// Hermes Agent → Bridge
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

// Bridge → Hermes Agent
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "generate_character_concept",
        "description": "SDXL + LoRA 角色概念图生成",
        "inputSchema": {
          "type": "object",
          "properties": {
            "character_description": {"type": "string"},
            "style_preset": {"type": "string"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "seed": {"type": "integer"}
          },
          "required": ["character_description"]
        }
      },
      {
        "name": "generate_scene",
        "description": "ControlNet + IP-Adapter 场景生成",
        "inputSchema": {
          "type": "object",
          "properties": {
            "scene_description": {"type": "string"},
            "mood": {"type": "string"},
            "lighting": {"type": "string"},
            "reference_image": {"type": "string"}
          },
          "required": ["scene_description"]
        }
      },
      {
        "name": "generate_video_clip",
        "description": "SVD 视频片段生成",
        "inputSchema": {
          "type": "object",
          "properties": {
            "input_image": {"type": "string"},
            "motion_prompt": {"type": "string"},
            "frames": {"type": "integer"},
            "fps": {"type": "integer"},
            "seed": {"type": "integer"}
          },
          "required": ["input_image", "motion_prompt"]
        }
      },
      {
        "name": "generate_tts",
        "description": "语音合成",
        "inputSchema": {
          "type": "object",
          "properties": {
            "text": {"type": "string"},
            "speaker_id": {"type": "string"},
            "emotion": {"type": "string"},
            "speed": {"type": "number"}
          },
          "required": ["text"]
        }
      },
      {
        "name": "instantid_face_swap",
        "description": "InstantID 面部一致性保持",
        "inputSchema": {
          "type": "object",
          "properties": {
            "target_image": {"type": "string"},
            "reference_face_image": {"type": "string"}
          },
          "required": ["target_image", "reference_face_image"]
        }
      }
    ]
  }
}
```

### 4.3 工具调用示例：角色概念图

```json
// Hermes Agent → Bridge
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "generate_character_concept",
    "arguments": {
      "character_description": "1girl, silver hair, purple eyes, slender build, fantasy clothing",
      "style_preset": "anime",
      "width": 1024,
      "height": 1024,
      "seed": 42
    }
  }
}

// Bridge → Hermes Agent（实时通知）
{"jsonrpc": "2.0", "method": "status/update", "params": {"preview": {"text": {"message": "VRAM acquired (normal), submitting generate_character_concept ..."}}}}
{"jsonrpc": "2.0", "method": "status/update", "params": {"preview": {"image": {"data": "...base64...", "step": 5, "total_steps": 30}}}}
{"jsonrpc": "2.0", "method": "status/update", "params": {"preview": {"image": {"data": "...base64...", "step": 10, "total_steps": 30}}}}

// Bridge → Hermes Agent（最终结果）
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [{
      "type": "text",
      "text": "{\"image_path\": \"/home/user/.triad/memory/assets/faces/char_a1b2c3.png\", \"generation_params\": {...}, \"used_nodes\": [\"7\", \"6\", \"4\"], \"vram_mode\": \"normal\"}"
    }]
  }
}
```

---

## 5. VRAM 调度详解

### 5.1 状态机

```
           ┌─────────────┐
     ┌─────│    IDLE     │<──────────────────────┐
     │     │ (LLM常驻)   │                       │
     │     └──────┬──────┘                       │
     │            │ 收到渲染任务                    │
     │            v                                │
     │     ┌─────────────┐    卸载失败+显存不足      │
     │     │ RECOVERING  │────────────────>┐       │
     │     │ (LLM卸载中)  │                  │       │
     │     └──────┬──────┘                  │       │
     │            │ 卸载成功                  │       │
     │            v                          v       │
     │     ┌─────────────┐           ┌─────────────┐│
     │     │  RENDERING  │           │  EMERGENCY  ││
     │     │ (ComfyUI独占)│           │ (--lowvram) ││
     │     └──────┬──────┘           └──────┬──────┘│
     │            │ 渲染完成                  │ 渲染完成│
     │            v                          v       │
     │     ┌─────────────┐           ┌─────────────┐│
     └─────│ RECOVERING  │<──────────│ RECOVERING  │┘
           │ (LLM热恢复)   │           │ (LLM热恢复)   │
           └─────────────┘           └─────────────┘
```

### 5.2 VRAM 预算表（22GB 2080Ti）

| 状态 | Embedding | LLM | ComfyUI | 安全边距 | 空闲 |
|------|-----------|-----|---------|----------|------|
| IDLE | 2GB | 4GB | 0GB | 0.5GB | 15.5GB |
| RENDERING | 2GB | 0GB | 19.5GB | 0.5GB | 0GB |
| RECOVERING | 2GB | 0~4GB | 0~19.5GB | 0.5GB | 变 |

### 5.3 降级策略

```python
# vram_scheduler.py 自动决策
if available_vram >= needed:
    mode = "normal"       # 全精度推理
elif available_vram >= 12000:
    mode = "lowvram"      # 模型逐层加载（--lowvram）
else:
    mode = "emergency"    # CPU 解码 + 分块推理

# ComfyUI 启动参数映射
args_map = {
    "normal": [],
    "lowvram": ["--lowvram"],
    "emergency": ["--lowvram", "--cpu-vae", "--disable-xformers"],
}
```

---

## 6. 工作流模板系统

### 6.1 模板目录

```
~/.triad/workflows/
├── character_concept.json      # SDXL + LoRA
├── scene_controlnet.json     # ControlNet + IP-Adapter
├── svd_video.json            # Stable Video Diffusion
├── instantid.json             # InstantID 面部保持
└── tts_qwen.json              # TTS API 占位
```

### 6.2 模板参数注入

```python
from comfyui_mcp_bridge import WorkflowTemplate

# 1. 加载模板
wf = WorkflowTemplate().load("character_concept")

# 2. 注入提示词
wf = WorkflowTemplate.inject_prompt(
    wf,
    positive="masterpiece, best quality, 1girl, silver hair",
    negative="lowres, bad anatomy"
)

# 3. 设置尺寸
wf = WorkflowTemplate.set_latent_size(wf, width=1344, height=768)

# 4. 设置种子
wf = WorkflowTemplate.set_seed(wf, seed=12345)

# 5. 设置参考图（ControlNet / IP-Adapter）
wf = WorkflowTemplate.set_load_image(wf, "reference", "/path/to/ref.png")
```

### 6.3 自定义工作流导入

从 ComfyUI 前端导出 API JSON（Save(API Format)），放置到 `~/.triad/workflows/my_custom.json`：

```python
# Bridge 自动识别并加载
templates = WorkflowTemplate()
wf = templates.load("my_custom")
```

---

## 7. WebSocket 进度回调

### 7.1 ComfyUI 原生消息类型

| 消息类型 | 触发时机 | Bridge 处理 |
|----------|----------|-------------|
| `execution_start` | prompt 开始执行 | 发送状态通知 |
| `executing` | 节点开始执行 | 记录节点耗时 |
| `progress` | KSampler 每步 | 计算 ratio，每 5 步发 ImagePreview |
| `executed` | 节点执行完毕 | 提取输出文件列表 |
| `execution_cached` | 命中缓存 | 直接返回结果 |
| `execution_error` | 执行错误 | 发送 error 通知 |
| 二进制帧 | 预览图 JPEG | 缓存，供 progress 消息附带 |

### 7.2 二进制预览帧格式

```python
# ComfyUI WebSocket 二进制帧
# 前 4 字节: event_type (uint32, big-endian)
# event_type == 1: 预览图 (JPEG)
# 后续字节: JPEG 数据

import struct
header = data[:4]
event_type = struct.unpack(">I", header)[0]
if event_type == 1:
    jpeg_data = data[4:]
    # 转换为 base64 供 StatusUpdate.image 使用
```

---

## 8. 故障排查

### 8.1 ComfyUI 连接失败

```bash
# 检查 ComfyUI 是否监听正确端口
curl http://127.0.0.1:18188/system_stats
# 应返回 GPU / 内存统计

# 检查 WebSocket
wscat -c ws://127.0.0.1:18188/ws
```

### 8.2 VRAM 不足（OOM）

```bash
# 1. 查看当前显存占用
python -c "from vram_scheduler import NVMLMonitor; m=NVMLMonitor(); print(m.snapshot())"

# 2. 手动释放 ComfyUI 显存
curl -X POST http://127.0.0.1:18188/free -H "Content-Type: application/json" -d '{"unload_models":true,"free_memory":true}'

# 3. 手动卸载 vLLM
curl -X POST http://0.0.0.0:18000/v1/unload
```

### 8.3 模型加载失败

```bash
# 检查模型路径
cd ~/ComfyUI && python main.py --verbose
# 查看日志中的模型扫描路径

# 确认文件名与工作流模板中的 `ckpt_name` 一致
ls ~/ComfyUI/models/checkpoints/
```

### 8.4 工作流节点缺失

```
Error: Node type 'SVD_img2vid_Conditioning' not found
```

解决方案：通过 ComfyUI-Manager 安装缺失节点包，或修改模板使用替代节点。

---

## 9. 性能调优

### 9.1 2080Ti 22GB 专项优化

```bash
# 启动 ComfyUI 时使用以下参数
python main.py \
  --normalvram \              # 22GB 足够 normal 模式
  --cuda-malloc \             # 启用 CUDA 内存池
  --disable-smart-memory \    # 禁用智能内存（避免与 vram_scheduler 冲突）
  --listen 127.0.0.1 \
  --port 18188
```

### 9.2 SVD 视频生成参数

| 帧数 | 分辨率 | 显存占用 | 耗时 |
|------|--------|----------|------|
| 14 | 1024x576 | ~14GB | ~45s |
| 25 | 1024x576 | ~18GB | ~90s |
| 14 | 512x320 | ~8GB | ~20s |

建议：显存紧张时使用 512x320 低分辨率，Hermes Agent 可自动降级。

### 9.3 LLM 热恢复优化

```python
# vram_scheduler.py
# warm_up() 中发送的预热提示词影响恢复速度
warmup_prompt = "System prompt initialization."  # 短提示词 = 更快加载
# 预热 3 个 token 已足够触发权重加载
```

---

## 10. 附录

### A. 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `COMFYUI_HOST` | `127.0.0.1` | ComfyUI 监听地址 |
| `COMFYUI_PORT` | `18188` | ComfyUI 端口 |
| `TTS_API_URL` | `http://localhost:5000/tts` | TTS 服务地址 |
| `TRIAD_ASSETS_PATH` | `~/.triad/memory/assets` | 资产库存储路径 |
| `TRIAD_WORKFLOWS_PATH` | `~/.triad/workflows` | 工作流模板路径 |

### B. 日志级别

```python
import logging
logging.getLogger("triad.vram_scheduler").setLevel(logging.DEBUG)
logging.getLogger("triad.comfyui_mcp_bridge").setLevel(logging.DEBUG)
```

### C. 调试模式启动 Bridge

```bash
# 不连接真实 ComfyUI，仅测试模板和协议
python -m hand.comfyui_mcp_bridge --dry-run
```
