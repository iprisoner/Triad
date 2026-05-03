# ComfyUI 宿主机原生部署手册

## 概述

本手册描述如何在 WSL2 Ubuntu 宿主机上以原生 Python venv 方式部署 ComfyUI，替代原有的 Docker 容器方案。MCP Bridge（在 Docker 内）通过 `host.docker.internal:18188` 访问宿主机 ComfyUI。

## 为何从 Docker 迁移到宿主机原生

| 问题 | Docker 方案 | 宿主机原生方案 |
|------|------------|--------------|
| 镜像体积 | 10GB+ (含 PyTorch + CUDA + 所有节点) | ~200MB (仅 ComfyUI 本体) |
| PyTorch CUDA 兼容性 | Docker 内 CUDA 版本与宿主机驱动错位风险 | 直接使用宿主机 PyTorch + NVIDIA 驱动 |
| 自定义节点安装 | 需重建 Docker 镜像或复杂卷挂载 | `git clone` 到 `custom_nodes/` 即可 |
| xformers / 加速库 | WSL2 内 Docker 中经常编译失败 | 宿主机直接 pip 安装，稳定可用 |
| 魔改 2080Ti 22GB | Docker 内可能无法识别非标准 VRAM | 宿主机直接识别全部 22GB |
| 模型管理 | Docker 卷映射复杂 | 软链接到 `~/.triad/models/comfyui/` |
| 启动速度 | Docker 容器冷启动 30-60s | venv 激活后秒启 |

## 部署架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           WSL2 Ubuntu 22.04 (宿主机)                         │
│                                                                             │
│   ~/.triad/                                                                 │
│   ├── venvs/comfyui/          ← Python 3.11 venv                          │
│   │   ├── bin/python                                                          │
│   │   └── lib/python3.11/site-packages/ (PyTorch cu121, etc.)               │
│   ├── apps/comfyui/            ← ComfyUI 本体 (git clone)                   │
│   │   ├── main.py                                                             │
│   │   ├── custom_nodes/        ← ComfyUI-Manager, InstantID, etc.          │
│   │   ├── models/              ← 软链接到 ~/.triad/models/comfyui/         │
│   │   └── output/              ← 生成结果输出                                │
│   └── models/comfyui/          ← 模型集中存储                              │
│       ├── checkpoints/ (SDXL, SD1.5)                                       │
│       ├── loras/                                                            │
│       ├── controlnet/                                                         │
│       ├── vae/                                                              │
│       └── insightface/ (InstantID 面部模型)                                  │
│                                                                             │
│   python main.py --listen 0.0.0.0 --port 18188 --highvram                   │
│        ▲                                                                    │
│        └────────────────────── 监听 0.0.0.0:18188 ────────────────────────────┘
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                           Docker 容器网络层                                  │
│                                                                             │
│   triad-hermes (Docker)                                                     │
│   └── comfyui_mcp_bridge.py ──► http://host.docker.internal:18188/prompt   │
│                                  host.docker.internal ──► WSL2 宿主机 127.0.0.1│
│                                                                             │
│   triad-clawpod-* (Docker)                                                  │
│   └── 如需访问 ComfyUI ───────► host.docker.internal:18188                  │
│                                                                             │
│   所有容器配置 extra_hosts:                                                 │
│   - "host.docker.internal:host-gateway"                                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 快速部署

### 步骤 1: 一键安装

```bash
cd /path/to/triad
bash scripts/install_comfyui.sh
```

此脚本会自动完成：
1. 创建 `~/.triad/venvs/comfyui/` Python venv
2. 安装 PyTorch CUDA 12.1 (`torch`, `torchvision`, `torchaudio`)
3. 克隆 ComfyUI 到 `~/.triad/apps/comfyui/`
4. 安装 ComfyUI 核心依赖 (`requirements.txt`)
5. 创建模型目录软链接到 `~/.triad/models/comfyui/`
6. 安装常用自定义节点 (Manager, InstantID, ControlNet-Aux, IPAdapter+, etc.)

### 步骤 2: 放置模型文件

将下载的模型文件放入对应目录：

```bash
# 检查点模型 (SDXL Base, SD1.5, etc.)
cp sdXL_base.safetensors ~/.triad/models/comfyui/checkpoints/
cp realisticVisionV51.safetensors ~/.triad/models/comfyui/checkpoints/

# LoRA
cp anime_style.safetensors ~/.triad/models/comfyui/loras/

# ControlNet
cp control_v11p_sd15_depth.pth ~/.triad/models/comfyui/controlnet/
cp control_v11p_sd15_canny.pth ~/.triad/models/comfyui/controlnet/

# VAE
cp sdxl_vae.safetensors ~/.triad/models/comfyui/vae/

# InstantID 所需 (自动下载或手动放置)
# ~/.insightface/models/ 目录将在首次运行时自动创建
```

### 步骤 3: 启动 ComfyUI

```bash
# 激活 venv
source ~/.triad/venvs/comfyui/bin/activate

# 进入 ComfyUI 目录
cd ~/.triad/apps/comfyui

# 启动 (高显存模式，适合 2080Ti 22GB)
python main.py --listen 0.0.0.0 --port 18188 --preview-method auto --highvram

# 如果显存不足，使用标准模式
# python main.py --listen 0.0.0.0 --port 18188 --preview-method auto --normalvram

# 如果仍然 OOM，使用低显存模式 (模型分片加载)
# python main.py --listen 0.0.0.0 --port 18188 --preview-method auto --lowvram
```

启动参数说明：
| 参数 | 说明 |
|------|------|
| `--listen 0.0.0.0` | 监听所有接口，允许 Docker 容器访问 |
| `--port 18188` | 默认端口，与 MCP Bridge 配置一致 |
| `--preview-method auto` | 自动选择预览方法 |
| `--highvram` | 高显存模式，模型常驻 VRAM (推荐 22GB) |
| `--normalvram` | 标准模式，需要时加载模型 (推荐 12-16GB) |
| `--lowvram` | 低显存模式，模型分片加载 (推荐 8GB) |
| `--disable-xformers` | 禁用 xformers (WSL2 下可能不稳定) |

### 步骤 4: 验证 Docker 容器可访问

```bash
# 从 Docker 容器内部测试连通性
docker run --rm --add-host=host.docker.internal:host-gateway alpine \
    sh -c "apk add --no-cache curl && curl -s http://host.docker.internal:18188/system_stats"

# 或进入 triad-hermes 容器测试
docker compose -f docker-compose.hpc.yml exec hermes \
    sh -c "apt-get update -qq && apt-get install -y -qq curl && curl -s http://host.docker.internal:18188/system_stats"
```

### 步骤 5: 启动 Triad Docker 服务

```bash
# 启动核心服务 + 本地 LLM
docker compose -f docker-compose.hpc.yml --profile hpc-full up -d

# 或仅启动核心服务 (ComfyUI 已在宿主机运行)
docker compose -f docker-compose.hpc.yml up -d
```

## 日常运维

### 启动顺序 (重要)

```
1. WSL2 宿主机: 启动 ComfyUI
   source ~/.triad/venvs/comfyui/bin/activate
   cd ~/.triad/apps/comfyui
   python main.py --listen 0.0.0.0 --port 18188 --highvram

2. WSL2 宿主机: 启动 Triad Docker 服务
   docker compose -f docker-compose.hpc.yml --profile hpc-full up -d

3. Docker 容器: MCP Bridge 自动连接 host.docker.internal:18188
```

### 停止顺序

```
1. Docker: docker compose -f docker-compose.hpc.yml down
2. 宿主机: Ctrl+C 停止 ComfyUI (或 kill 进程)
```

### 更新 ComfyUI

```bash
cd ~/.triad/apps/comfyui
git pull --ff-only

# 更新自定义节点
cd custom_nodes
for d in */; do
    cd "$d" && git pull --ff-only 2>/dev/null; cd ..
done

# 更新后重启 ComfyUI
```

### 安装新自定义节点

```bash
cd ~/.triad/apps/comfyui/custom_nodes
git clone <节点仓库URL>
cd <节点目录>
pip install -r requirements.txt  # 如果在 venv 内
# 或
~/.triad/venvs/comfyui/bin/pip install -r requirements.txt  # 指定 venv pip
```

## 故障排查

### 问题 1: Docker 容器无法连接 host.docker.internal:18188

**症状**: MCP Bridge 报错 `Cannot connect to ComfyUI at http://host.docker.internal:18188`

**排查步骤**:
```bash
# 1. 确认 ComfyUI 在宿主机运行
curl http://localhost:18188/system_stats

# 2. 确认 Docker 容器能解析 host.docker.internal
docker run --rm --add-host=host.docker.internal:host-gateway alpine \
    ping -c 1 host.docker.internal

# 3. 检查 docker-compose.hpc.yml 中的 extra_hosts 配置
# 确保服务定义包含:
#   extra_hosts:
#     - "host.docker.internal:host-gateway"

# 4. WSL2 特定: 检查 /etc/hosts
cat /etc/hosts | grep host.docker.internal
# 应包含: 172.17.0.1 host.docker.internal (或类似)
# 如果没有，Docker 20.10+ 应自动注入
```

**解决方案**:
```bash
# 手动添加 hosts 条目 (临时)
echo "127.0.0.1 host.docker.internal" | sudo tee -a /etc/hosts

# 或在 docker-compose 中显式配置 (已包含在本项目配置中)
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### 问题 2: PyTorch 无法识别 GPU

**症状**: `torch.cuda.is_available()` 返回 `False`

**排查步骤**:
```bash
source ~/.triad/venvs/comfyui/bin/activate
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"
nvidia-smi
```

**解决方案**:
```bash
# 确认安装的是 cu121 版本而非 CPU 版本
pip uninstall torch torchvision torchaudio -y
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# 确认 NVIDIA 驱动在 WSL2 内可见
nvidia-smi
# 应显示 GPU 信息和驱动版本
```

### 问题 3: ComfyUI 启动时 CUDA OutOfMemory

**症状**: `RuntimeError: CUDA out of memory`

**解决方案**:
```bash
# 使用更低的显存模式
python main.py --listen 0.0.0.0 --port 18188 --normalvram

# 或低显存模式
python main.py --listen 0.0.0.0 --port 18188 --lowvram

# 同时确保 llama-server 已切换到 CPU 模式 (-ngl 0) 释放显存
```

### 问题 4: 自定义节点导入失败

**症状**: ComfyUI 启动日志显示 `IMPORT FAILED` 某些节点

**排查步骤**:
```bash
# 查看详细错误日志
cd ~/.triad/apps/comfyui
python main.py --listen 0.0.0.0 --port 18188 2>&1 | tee comfyui.log

# 常见原因：缺少依赖
cd custom_nodes/<失败节点目录>
cat requirements.txt
pip install -r requirements.txt
```

### 问题 5: 模型文件找不到

**症状**: `CheckpointLoaderSimple: 模型文件不存在`

**排查步骤**:
```bash
# 确认软链接正确
ls -la ~/.triad/apps/comfyui/models/checkpoints/
# 应显示软链接指向 ~/.triad/models/comfyui/checkpoints/

# 确认模型文件存在
ls -la ~/.triad/models/comfyui/checkpoints/
```

## 环境变量速查

| 变量 | 设置位置 | 值 | 说明 |
|------|---------|-----|------|
| `COMFYUI_HOST` | `docker-compose.hpc.yml` (hermes, clawpod-*) | `host.docker.internal` | MCP Bridge 连接目标 |
| `COMFYUI_PORT` | `docker-compose.hpc.yml` (hermes, clawpod-*) | `18188` | ComfyUI 监听端口 |
| `CUDA_VISIBLE_DEVICES` | `docker-compose.hpc.yml` | `0` | 指定 GPU 设备 |
| `extra_hosts` | `docker-compose.hpc.yml` | `host.docker.internal:host-gateway` | DNS 解析保障 |

## 性能调优建议

### 魔改 2080Ti 22GB 最佳实践

```bash
# 推荐启动命令 (22GB 高显存)
python main.py \
    --listen 0.0.0.0 \
    --port 18188 \
    --preview-method auto \
    --highvram \
    --disable-xformers \
    --fp16-vae \
    --bf16-unet
```

### 与 llama-server 共存时的启动策略

```bash
# 1. 确保 llama-server 在 CPU 模式 (-ngl 0) 或已停止
#    (由 vram_scheduler.py 自动管理)

# 2. 启动 ComfyUI 高显存模式
source ~/.triad/venvs/comfyui/bin/activate
cd ~/.triad/apps/comfyui
python main.py --listen 0.0.0.0 --port 18188 --highvram

# 3. 渲染完成后，停止 ComfyUI (释放显存给 llama-server GPU 模式)
#    Ctrl+C 或 kill <pid>
```

## 参考

- [ComfyUI 官方仓库](https://github.com/comfyanonymous/ComfyUI)
- [ComfyUI-Manager 节点管理](https://github.com/ltdrdata/ComfyUI-Manager)
- [ComfyUI-InstantID 面部一致性](https://github.com/cubiq/ComfyUI-InstantID)
- [Triad HPC 调度文档](hpc_scheduling.md)
- [Triad VRAM 调度器](../hand/vram_scheduler.py)
