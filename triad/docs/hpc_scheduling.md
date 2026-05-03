# Triad HPC 调度指南 — 双路 Xeon E5-2673v3 + 魔改 RTX 2080Ti 22GB

> **版本**: v1.0-hpc  
> **适用系统**: WSL2 Ubuntu 22.04 / 原生 Linux 5.15+  
> **硬件目标**: 2x Intel Xeon E5-2673v3 (Haswell-EP, 24C/48T) + 魔改 NVIDIA RTX 2080Ti 22GB  
> **编排文件**: `../docker-compose.hpc.yml`

---

## 目录

1. [硬件拓扑与 NUMA 映射](#1-硬件拓扑与-numa-映射)
2. [显存分时复用策略](#2-显存分时复用策略)
3. [ClawPod 规格与调度矩阵](#3-clawpod-规格与调度矩阵)
4. [性能调优参数大全](#4-性能调优参数大全)
5. [启动前检查清单](#5-启动前检查清单)
6. [故障排查速查表](#6-故障排查速查表)

---

## 1. 硬件拓扑与 NUMA 映射

### 1.1 物理架构概览

```
+========================================================================================+
|                                    双路 Intel Xeon E5-2673v3                           |
|                                    2x NUMA Nodes / 128GB DDR4-ECC                      |
+========================================================================================+
|                                                                                        |
|  +----------------------------------------+    +----------------------------------------+|
|  |         Socket 0 (NUMA Node 0)         |    |         Socket 1 (NUMA Node 1)         ||
|  |   12 Physical Cores / 24 Threads       |    |   12 Physical Cores / 24 Threads       ||
|  |   Base 2.4GHz / Turbo 3.2GHz           |    |   Base 2.4GHz / Turbo 3.2GHz           ||
|  |   30MB L3 Cache / 4x DDR4-2133         |    |   30MB L3 Cache / 4x DDR4-2133         ||
|  |   ~64GB Local Memory (理论)            |    |   ~64GB Local Memory (理论)            ||
|  +----------------------------------------+    +----------------------------------------+|
|                                                                                        |
|  <-------- QPI/UPI Interconnect (内存一致性通道, ~9.6 GT/s) -------->                  |
|                                                                                        |
|  +----------------------------------------+    +----------------------------------------+|
|  |         PCIe 3.0 x16 Slot              |    |         PCIe 3.0 x16 Slot              ||
|  |   [魔改 RTX 2080Ti 22GB VRAM]          |    |   [可选第二块 GPU / 扩展卡]              ||
|  |   Compute Capability 7.5 (Turing)      |    |                                        ||
|  |   22GB GDDR6 @ 616 GB/s (魔改后)       |    |                                        ||
|  |   Tensor Cores (Int8/FP16)             |    |                                        ||
|  +----------------------------------------+    +----------------------------------------+|
|                                                                                        |
+========================================================================================+
```

### 1.2 Linux CPU ID 与 NUMA 映射表

E5-2673v3 为 **Haswell-EP** 架构，每 Socket 12 核，Hyper-Threading 启用时逻辑核编号如下：

| NUMA Node | 物理核 (Core ID) | 超线程 (SMT ID) | 逻辑 CPU 范围 | Triad 服务分配 |
|:---|:---|:---|:---|:---|
| **Node 0** | 0, 1, 2, 3, 4, 5, 6, 7 | 24, 25, 26, 27, 28, 29, 30, 31 | `0-7,24-31` | **OpenClaw Gateway** (I/O 密集型, 16 线程) |
| **Node 0** | 8, 9, 10, 11 | 32, 33, 34, 35 | `8-11,32-35` | **Hermes** (Python GIL, 8 线程) |
| **Node 1** | 12, 13, 14, 15, 16, 17, 18, 19 | 36, 37, 38, 39, 40, 41, 42, 43 | `12-19,36-43` | **Hermes** (扩展, 16 线程) |
| **Node 1** | 20, 21 | 44, 45 | `20-21,44-45` | **Qdrant** (向量数据库, 4 线程) |
| **Node 1** | 22, 23 | 46, 47 | `22-23,46-47` | **MCP Registry** (注册中心, 4 线程) |

> **注**: 超线程核（SMT）编号 = 物理核编号 + 24。在 `cpuset` 中必须显式写出物理核及其对应的 SMT 核，否则容器只会获得单线程核心。

### 1.3 NUMA 内存带宽拓扑

```
Node 0 本地内存带宽: ~34 GB/s (DDR4-2133 Quad Channel)
Node 1 本地内存带宽: ~34 GB/s (DDR4-2133 Quad Channel)
跨 Node 远程访问带宽: ~17 GB/s (经 QPI/UPI，约减半)

OpenClaw 绑定 Node 0  → 100% 本地内存访问 → 最低网络 IO 延迟
Hermes 跨 Node 0+1   → 混合访问 → Python GIL 单线程执行，带宽非瓶颈
Qdrant 绑定 Node 1   → 隔离向量索引，避免与 Hermes 争用 Node 0 内存通道
```

### 1.4 验证命令

```bash
# 查看 NUMA 拓扑
numactl --hardware

# 预期输出（简化）:
# available: 2 nodes (0-1)
# node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11 24 25 26 27 28 29 30 31 32 33 34 35
# node 0 size: 65536 MB
# node 1 cpus: 12 13 14 15 16 17 18 19 20 21 22 23 36 37 38 39 40 41 42 43 44 45 46 47
# node 1 size: 65536 MB
# node distances:
# node   0   1
#   0:  10  21
#   1:  21  10

# 查看 CPU 详细信息
lscpu | grep -E "NUMA|CPU|Socket|Thread"

# 查看 GPU NUMA 亲和性
nvidia-smi topo -m
# 预期: GPU 0 挂在 Socket 0 的 PCIe 根端口下 (GPU0 <-> CPU0 为 PIX/PHB)
```

---

## 2. 显存分时复用策略

### 2.1 22GB 显存分区总览

魔改 RTX 2080Ti **不支持 NVIDIA MIG (Multi-Instance GPU)** — MIG 是 Ampere (A100) 及以后架构的特性。因此显存分区采用**协作式软隔离 + 动态释放**策略。

```
魔改 RTX 2080Ti 22GB VRAM 分区策略
+================================================================+
|  总 VRAM: 22GB (GDDR6, 616 GB/s)                               |
+================================================================+
|                                                                  |
|  [========] 2GB  常驻区: Hermes Embedding (BGE-large fp16)       |
|          ↑ 常驻内存池，由 PyTorch CUDA Allocator 预分配          |
|          ↓ 仅当 Hermes 重启时释放                                |
|                                                                  |
|  [========] 4GB  弹性区 A: vLLM 主推理 (Qwen-14B-AWQ / DeepSeek) |
|          ↑ gpu_memory_utilization=0.18 硬限制                    |
|          ↓ 空闲 60s 后自动释放 (vLLM --idle-timeout)             |
|                                                                  |
|  [========] 4GB  弹性区 B: Ollama 辅助推理 (Qwen-7B / CodeLlama) |
|          ↑ OLLAMA_KEEP_ALIVE=5m 自动卸载                         |
|          ↓ 模型层可 offload 到 CPU 内存                          |
|                                                                  |
|  [================] 8GB  渲染区: ComfyUI (SDXL/SD1.5)            |
|          ↑ 独占模式 — 启动前强制释放 A/B 区                        |
|          ↓ stop 容器立即释放全部 8GB                               |
|                                                                  |
|  [========] 2GB  动态缓冲: 弹性区 A/B 超售 / 临时借用              |
|          ↑ 当 A 或 B 空闲时，Hermes 可借用做 Embedding 缓存        |
|          ↓ ComfyUI 启动时优先回收                                  |
|                                                                  |
|  [========] 2GB  系统预留: CUDA Context / cuDNN Workspace / 驱动   |
|          ↑ nvidia-smi 显示的 "硬件保留" 部分                       |
|          ↓ 不可压缩                                                |
|                                                                  |
+================================================================+
```

### 2.2 协作式显存管理协议

由于无硬件级隔离，各服务通过以下机制协作：

| 服务 | 显存上限机制 | 释放策略 | 抢占优先级 |
|:---|:---|:---|:---|
| **Hermes Embedding** | `PYTORCH_CUDA_ALLOC_CONF` 限制池大小 | 常驻，不主动释放 | P1 (最高) |
| **vLLM** | `--gpu-memory-utilization 0.18` | 空闲超时释放 | P2 |
| **Ollama** | `OLLAMA_KEEP_ALIVE=5m` + 层卸载 | 5min 空闲后卸载到 CPU | P2 |
| **ComfyUI** | `--highvram` 模式内建管理 | stop 容器完全释放 | P3 (独占时 P0) |

### 2.3 分时复用场景

#### 场景 A: 纯推理模式 (无图像生成)

```
显存占用: 2GB (Embedding) + 4GB (vLLM) + 4GB (Ollama) + 2GB (缓冲) + 2GB (系统)
        = 14GB / 22GB
剩余: 8GB 可供 Hermes 扩展 Embedding 批处理，或临时加载更大模型
```

#### 场景 B: 渲染独占模式 (图像生成)

```bash
# 1. 停止 vLLM 和 Ollama，释放弹性区 A+B
docker compose stop vllm ollama

# 2. 启动 ComfyUI，独占 8GB 渲染区
docker compose --profile render up -d comfyui

# 显存占用: 2GB (Embedding) + 8GB (ComfyUI) + 2GB (缓冲) + 2GB (系统)
#         = 14GB / 22GB
# 剩余: 8GB 供 ComfyUI 高峰期借用 (如 SDXL + ControlNet)
```

#### 场景 C: 混合模式 (轻推理 + 轻渲染)

```
Hermes Embedding:  2GB (常驻)
Ollama (轻推理):  2GB (弹性区 B 减半，Qwen-7B 半精度)
ComfyUI (SD1.5):  4GB (渲染区减半)
缓冲 + 系统:      4GB
────────────────────────────
总计:             12GB / 22GB (安全余量 10GB)
```

### 2.4 显存监控脚本

创建 `/usr/local/bin/triad-vram-watch`：

```bash
#!/bin/bash
# Triad VRAM 监控脚本 — 实时显示各服务显存占用

while true; do
    clear
    echo "=== Triad GPU VRAM Monitor === $(date) ==="
    nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv
    echo ""
    echo "--- Container GPU Processes ---"
    nvidia-smi pmon -s um
    echo ""
    echo "--- Docker Stats (GPU containers) ---"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" triad-hermes triad-vllm triad-ollama triad-comfyui 2>/dev/null || true
    sleep 2
done
```

### 2.5 显存紧急回收脚本

创建 `/usr/local/bin/triad-vram-reclaim`：

```bash
#!/bin/bash
# 紧急回收显存 — 按优先级停止服务

NEED_GB=${1:-8}  # 默认回收 8GB

echo "[Triad] 请求回收 ${NEED_GB}GB 显存..."

# P3: 停止 ComfyUI (释放 8GB)
if docker ps | grep -q triad-comfyui; then
    echo "  → 停止 ComfyUI (释放 ~8GB)"
    docker stop triad-comfyui
fi

# P2: 停止 vLLM (释放 4GB)
if [ "$NEED_GB" -ge 4 ] && docker ps | grep -q triad-vllm; then
    echo "  → 停止 vLLM (释放 ~4GB)"
    docker stop triad-vllm
fi

# P2: 停止 Ollama (释放 4GB)
if [ "$NEED_GB" -ge 4 ] && docker ps | grep -q triad-ollama; then
    echo "  → 停止 Ollama (释放 ~4GB)"
    docker stop triad-ollama
fi

# 强制清空 PyTorch 缓存 (Hermes 内)
docker exec triad-hermes python -c "import torch; torch.cuda.empty_cache()" 2>/dev/null || true

echo "[Triad] 显存回收完成. 当前状态:"
nvidia-smi
```

---

## 3. ClawPod 规格与调度矩阵

### 3.1 容器规格对照表

| 规格 | 名称 | CPU | 内存 | GPU VRAM | 典型任务 | 超时 |
|:---|:---|:---|:---|:---|:---|:---|
| **small** | `clawpod-small` | 1 核 | 2GB | 无 | 代码嵌入、轻量 lint、文本预处理 | 5 min |
| **medium** | `clawpod-medium` | 2 核 | 4GB | 512MB | AST 解析、静态分析、中等数据处理 | 10 min |
| **large** | `clawpod-large` | 8 核 | 16GB | 4GB (借用弹性区) | 本地 LLM 推理、参数微调、大规模数据处理 | 30 min |

### 3.2 动态调度决策树

```
OpenClaw 接收任务
      │
      ▼
  任务类型分析
      │
      ├── 代码嵌入 / 向量生成 ──→ clawpod-small ──→ Hermes Embedding API (GPU 常驻 2GB)
      │
      ├── 代码分析 / Lint / AST ──→ clawpod-medium ──→ 纯 CPU 或借用 512MB VRAM
      │
      ├── 本地 LLM 推理 (轻量) ──→ clawpod-medium ──→ 调用 Ollama API (弹性区 B)
      │
      ├── 本地 LLM 推理 (重量) ──→ clawpod-large ──→ 调用 vLLM API (弹性区 A)
      │                              └─→ 若 vLLM 未启动，触发 `docker compose up vllm`
      │
      ├── 图像生成 / SD 工作流 ──→ clawpod-large ──→ 调用 ComfyUI API
      │                              └─→ 触发显存回收: 停止 vLLM + Ollama
      │                              └─→ 启动 ComfyUI (profile render)
      │
      └── MCP 代码执行 (Claude Code) ──→ clawpod-medium ──→ Registry 发现可用工具
```

### 3.3 动态启动命令

OpenClaw 通过 Docker API 或 CLI 动态创建 ClawPod：

```bash
# Small: 嵌入任务
docker compose -f docker-compose.hpc.yml run --rm \
  --name "clawpod-task-$(uuidgen | cut -d- -f1)" \
  clawpod-small \
  python /shared/scripts/generate_embeddings.py

# Medium: 代码分析
docker compose -f docker-compose.hpc.yml run --rm \
  --name "clawpod-task-$(uuidgen | cut -d- -f1)" \
  -e TASK_ID="analyze-123" \
  -e MODEL_API_URL="http://ollama:11434" \
  clawpod-medium \
  node /shared/scripts/static-analysis.js

# Large: 本地 LLM 推理 (确保 vLLM 已启动)
docker compose -f docker-compose.hpc.yml run --rm \
  --name "clawpod-task-$(uuidgen | cut -d- -f1)" \
  -e TASK_ID="infer-456" \
  -e LOCAL_VLLM_URL="http://vllm:8000/v1" \
  clawpod-large \
  python /shared/scripts/batch_inference.py
```

### 3.4 并发限制与资源池

```
Node 0 总逻辑核: 24 (0-11 物理, 24-35 SMT)
  └─ OpenClaw:    16 核 (0-7,24-31)
  └─ Hermes:       8 核 (8-11,32-35)

Node 1 总逻辑核: 24 (12-23 物理, 36-47 SMT)
  └─ Hermes:      16 核 (12-19,36-43)
  └─ Qdrant:       4 核 (20-21,44-45)
  └─ Registry:     4 核 (22-23,46-47)
  └─ vLLM/Ollama:  8 核 (20-23,44-47) [与 Qdrant/Registry 共享核心，
                                            因它们负载极低]

并发 ClawPod 上限:
  - small (1核):  最多 8 个并发 (保留 8 核心给系统/突发)
  - medium (2核): 最多 4 个并发
  - large (8核):  最多 1 个并发 ( exclusive 模式 )
```

---

## 4. 性能调优参数大全

### 4.1 WSL2 层调优 (`%USERPROFILE%\.wslconfig`)

```ini
# Windows 用户目录下的 .wslconfig 文件
# 路径: C:\Users\<Username>\.wslconfig

[wsl2]
# 内存上限 — 为 Windows 宿主机保留 8GB，防止宿主机 OOM 导致 WSL2 崩溃
memory=120GB

# 处理器上限 — 分配 46 核，留 2 核给 Windows 宿主调度
processors=46

# 禁用 WSL2 自动回收内存 (使用 systemd 替代方案)
# 显式关闭以避免 Docker 容器被意外影响
swap=0

# 禁用 localhost 端口转发 (如需桥接网络)
localhostForwarding=true

# 嵌套虚拟化 (如需在 ClawPod 内运行 KVM)
nestedVirtualization=false

# 启用巨型页支持 (对 Qdrant/向量检索有帮助)
pageReporting=true

# 磁盘大小 — 确保 VHD 不会成为瓶颈 (NVMe 直通时此选项影响较小)
# 使用 wsl --mount 直接挂载物理 NVMe 分区时忽略
```

> **应用方式**: 修改后执行 `wsl --shutdown`，重新打开 WSL2。

### 4.2 Linux 内核参数调优 (`/etc/sysctl.d/99-triad-hpc.conf`)

```bash
# =============================================================================
# Triad HPC 内核参数 — 针对 Docker 容器密集 IO 与 NUMA 优化
# 应用: sudo sysctl --system 或重启
# =============================================================================

# --- 虚拟内存 ---
# 禁用过度交换 (128GB 物理内存充足)
vm.swappiness=1
vm.vfs_cache_pressure=50

# 增加文件句柄与 inode 缓存
vm.dirty_ratio=40
vm.dirty_background_ratio=10
vm.dirty_expire_centisecs=3000
vm.dirty_writeback_centisecs=500

# --- 网络栈 (OpenClaw WebSocket 高并发) ---
net.core.somaxconn=65535
net.core.netdev_max_backlog=65535
net.ipv4.tcp_max_syn_backlog=65535
net.ipv4.ip_local_port_range=1024 65535
net.ipv4.tcp_tw_reuse=1
net.netfilter.nf_conntrack_max=1000000

# --- 进程与 cgroup ---
kernel.pid_max=4194304
kernel.threads-max=4194304
kernel.msgmax=65536
kernel.msgmnb=65536

# --- NUMA 优化 ---
# 优先在本地 NUMA 节点分配内存，减少跨 Node 访问
vm.zone_reclaim_mode=1

# --- 容器安全与性能 ---
# 允许非特权用户创建更多命名空间 (用于 rootless ClawPod)
kernel.unprivileged_userns_clone=1
```

### 4.3 Docker Daemon 配置 (`/etc/docker/daemon.json`)

```json
{
  "exec-opts": ["native.cgroupdriver=systemd"],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  },
  "storage-driver": "overlay2",
  "storage-opts": [
    "overlay2.override_kernel_check=true"
  ],
  "live-restore": true,
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 1048576,
      "Soft": 1048576
    }
  },
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  },
  "default-runtime": "runc",
  "features": {
    "buildkit": true
  }
}
```

> **关键说明**: `nvidia` runtime 在 `daemon.json` 中注册，但 **默认 runtime 仍为 `runc`**。仅在需要 GPU 的服务中通过 `runtime: nvidia` 显式调用，避免无 GPU 容器引入 CUDA 初始化开销。

### 4.4 NVIDIA Container Toolkit 配置

```bash
# 1. 安装 NVIDIA Container Toolkit (WSL2 已预装时跳过)
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
  sudo tee /etc/apt/sources.list.d/nvidia-docker.list
sudo apt update && sudo apt install -y nvidia-container-toolkit

# 2. 配置 Docker 使用 nvidia runtime
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 3. 验证 (应能看到 GPU 信息)
docker run --rm --runtime=nvidia nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi
```

### 4.5 魔改 2080Ti 22GB 特殊驱动配置

魔改 2080Ti (22GB) 通常使用 **BIOS 级显存扩容** (双面 GDDR6) 或 **驱动补丁**。WSL2 下需要确保 Windows 侧驱动已正确识别 22GB。

```bash
# WSL2 内验证显存识别
nvidia-smi
# 预期输出: 22731 MiB (或接近 22GB 的数值)

# 若仍显示 11GB，需在 Windows 侧更新魔改驱动，
# 或确认 WSL2 的 dxgkrnl 正确映射了显存 BAR。
```

### 4.6 PyTorch / CUDA 环境变量速查

| 变量 | 值 | 作用 |
|:---|:---|:---|
| `CUDA_VISIBLE_DEVICES` | `"0"` | 限定容器只看到 GPU 0 |
| `NVIDIA_VISIBLE_DEVICES` | `"0"` | NVIDIA Docker 运行时设备过滤 |
| `PYTORCH_CUDA_ALLOC_CONF` | `"max_split_size_mb:512,expandable_segments:True"` | 限制分配块大小，启用可扩展段减少碎片 |
| `TORCH_CUDA_ARCH_LIST` | `"7.5"` | 仅编译 Turing SM_75 内核，缩短 JIT 时间 |
| `CUDA_MODULE_LOADING` | `"LAZY"` | 延迟加载 CUDA 内核，降低启动内存峰值 |
| `VLLM_ATTENTION_BACKEND` | `"FLASH_ATTN"` | Turing 支持 FlashAttention-2 (需安装 flash-attn) |
| `OLLAMA_FLASH_ATTENTION` | `"1"` | Ollama 启用 Flash Attention |
| `OLLAMA_KEEP_ALIVE` | `"5m"` | 5 分钟空闲后模型卸载到 CPU |

### 4.7 NUMA 绑定强化脚本

对于原生 Linux (非 WSL2，WSL2 的 NUMA 支持有限)，使用 `numactl` 强化绑定：

```bash
#!/bin/bash
# /usr/local/bin/triad-numa-bind.sh
# 用法: 在 docker-compose 的 command 前加 numactl 前缀

# OpenClaw 强化绑定 (原生 Linux)
NUMACTL_OPENCLAW="numactl --cpunodebind=0 --membind=0 --physcpubind=0-7,24-31"

# Hermes 强化绑定 (跨 Node，优先本地)
NUMACTL_HERMES="numactl --interleave=all --physcpubind=8-11,32-35,12-19,36-43"

# vLLM 绑定 Node 1 (GPU 通常挂在 Socket 0，但 Node 1 内存充足)
NUMACTL_VLLM="numactl --cpunodebind=1 --membind=1 --physcpubind=20-23,44-47"

echo "NUMA binding configured. Use in docker-compose command:"
echo "  command: ${NUMACTL_OPENCLAW} node /app/dist/index.js"
```

> **WSL2 限制**: WSL2 当前 (2024) 的 NUMA 支持不完整，`numactl --hardware` 可能只显示单 Node。此时依赖 Docker `cpuset` 做最佳努力调度即可。

### 4.8 Docker Compose 启动命令矩阵

| 模式 | 命令 | 启动服务 |
|:---|:---|:---|
| **核心基础** | `docker compose -f docker-compose.hpc.yml up -d` | openclaw, hermes, qdrant, registry |
| **HPC 完整** | `docker compose -f docker-compose.hpc.yml --profile hpc-full up -d` | 全部 (含 LLM + 渲染 + ClawPod) |
| **本地 LLM** | `docker compose -f docker-compose.hpc.yml --profile local-llm up -d` | 核心 + vLLM + Ollama |
| **图像渲染** | `docker compose -f docker-compose.hpc.yml --profile render up -d` | 核心 + ComfyUI |
| **调试前端** | `docker compose -f docker-compose.hpc.yml --profile debug up -d` | 核心 + text-generation-webui |
| **ClawPod 任务** | `docker compose -f docker-compose.hpc.yml run --rm clawpod-medium [cmd]` | 单次任务容器 |

### 4.9 内存大页 (HugePages) 配置 (可选，Qdrant/Hermes 受益)

```bash
# 启用 2MB 透明大页
sudo sysctl vm.nr_hugepages=32768  # 64GB 大页池

echo 'vm.nr_hugepages=32768' | sudo tee /etc/sysctl.d/99-hugepages.conf

# Docker Compose 中为 Hermes/Qdrant 添加:
#   devices:
#     - /dev/hugepages:/dev/hugepages
```

---

## 5. 启动前检查清单

```bash
#!/bin/bash
# triad-pre-flight-check.sh

PASS="\033[32m[PASS]\033[0m"
FAIL="\033[31m[FAIL]\033[0m"
WARN="\033[33m[WARN]\033[0m"

echo "=== Triad HPC Pre-flight Check ==="

# 1. CPU 拓扑
echo ""
echo "[1/8] CPU Topology..."
if lscpu | grep -q "NUMA node(s):.*2"; then
    echo -e "  $PASS NUMA 双节点检测正常"
else
    echo -e "  $FAIL NUMA 节点数异常 (预期 2)"
fi

# 2. 内存
echo ""
echo "[2/8] Memory..."
MEM_GB=$(free -g | awk '/^Mem:/{print $2}')
if [ "$MEM_GB" -ge 120 ]; then
    echo -e "  $PASS 物理内存 ${MEM_GB}GB >= 120GB"
else
    echo -e "  $WARN 物理内存 ${MEM_GB}GB < 120GB，可能限制并发"
fi

# 3. GPU 显存
echo ""
echo "[3/8] GPU VRAM..."
VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')
VRAM_GB=$((VRAM / 1024))
if [ "$VRAM_GB" -ge 21 ]; then
    echo -e "  $PASS GPU 显存 ${VRAM_GB}GB >= 21GB (22GB 魔改识别正常)"
else
    echo -e "  $FAIL GPU 显存 ${VRAM_GB}GB < 21GB — 驱动未识别魔改容量！"
    echo "        请在 Windows 侧更新魔改驱动后重启 WSL2: wsl --shutdown"
fi

# 4. NVIDIA Docker Runtime
echo ""
echo "[4/8] NVIDIA Docker Runtime..."
if docker info 2>/dev/null | grep -q "nvidia"; then
    echo -e "  $PASS nvidia runtime 已注册"
else
    echo -e "  $FAIL nvidia runtime 未注册 — 执行 nvidia-ctk runtime configure --runtime=docker"
fi

# 5. Docker cpuset 支持
echo ""
echo "[5/8] Docker CPU 限制..."
if docker run --rm --cpuset-cpus="0" busybox true 2>/dev/null; then
    echo -e "  $PASS cpuset 功能正常"
else
    echo -e "  $FAIL cpuset 功能异常 — 检查 cgroup v2 挂载"
fi

# 6. WSL2 .wslconfig
echo ""
echo "[6/8] WSL2 Config..."
if [ -f /mnt/c/Users/*/\.wslconfig ] 2>/dev/null || [ -f "$(wslpath "$USERPROFILE")/.wslconfig" ] 2>/dev/null; then
    echo -e "  $PASS .wslconfig 文件存在"
else
    echo -e "  $WARN .wslconfig 未找到 — 建议配置 memory=120GB processors=46"
fi

# 7. 内核参数
echo ""
echo "[7/8] Kernel Parameters..."
SWAPPINESS=$(sysctl -n vm.swappiness)
if [ "$SWAPPINESS" -le 10 ]; then
    echo -e "  $PASS vm.swappiness=${SWAPPINESS} (已优化)"
else
    echo -e "  $WARN vm.swappiness=${SWAPPINESS} — 建议设为 1"
fi

# 8. 磁盘空间
echo ""
echo "[8/8] Disk Space..."
DISK_AVAIL=$(df / | tail -1 | awk '{print $4}')
DISK_AVAIL_GB=$((DISK_AVAIL / 1024 / 1024))
if [ "$DISK_AVAIL_GB" -ge 100 ]; then
    echo -e "  $PASS 根分区剩余 ${DISK_AVAIL_GB}GB"
else
    echo -e "  $FAIL 根分区剩余 ${DISK_AVAIL_GB}GB < 100GB — 模型缓存可能不足"
fi

echo ""
echo "=== Pre-flight Check Complete ==="
```

---

## 6. 故障排查速查表

| 症状 | 根因 | 修复 |
|:---|:---|:---|
| `docker compose up` 报错 `runtime "nvidia"` 不存在 | NVIDIA Container Toolkit 未注册 | `nvidia-ctk runtime configure --runtime=docker && systemctl restart docker` |
| `nvidia-smi` 显示 11GB 而非 22GB | WSL2 映射了错误的驱动 | Windows 侧安装魔改驱动 → `wsl --shutdown` → 重启 WSL2 |
| Hermes 启动后 PyTorch CUDA OOM | 显存碎片化 | `docker exec triad-hermes python -c "import torch; torch.cuda.empty_cache()"` |
| vLLM 加载模型后显存超 4GB | AWQ 量化模型尺寸估算不准 | 降低 `--max-model-len` 或换用 GPTQ 量化 |
| ComfyUI 启动时 CUDA OutOfMemory | 弹性区 A/B 未释放 | 执行 `triad-vram-reclaim 8` 停止 vLLM + Ollama |
| OpenClaw WebSocket 连接数上不去 | 文件句柄限制 | `ulimit -n 1048576` 或在 daemon.json 配置 default-ulimits |
| WSL2 内 `numactl` 只显示 1 节点 | WSL2 当前不支持完整 NUMA | 正常限制，依赖 Docker `cpuset` 即可 |
| ClawPod 启动极慢 | OverlayFS 在 VHD 上性能差 | 将 Docker data-root 迁移到 `/mnt/wsl/docker` (ext4 挂载点) |
| Qdrant 搜索延迟高 | 向量索引未预热 / 缺 HugePages | 启用透明大页 `echo always > /sys/kernel/mm/transparent_hugepage/enabled` |
| 双路 CPU 负载不均衡 | Docker 默认调度器未感知 NUMA | 所有服务已显式配置 `cpuset`，检查 `docker stats` |

---

## 附录 A: 魔改 2080Ti 22GB 技术背景

魔改 2080Ti 通常采用以下方案之一：

1. **BIOS 级显存扩容**: 将原有 11GB (1GBx11) GDDR6 替换为 2GBx11 颗粒，通过修改显卡 BIOS 和 VBIOS 识别更大的显存容量。
2. **驱动级破解**: 在 NVIDIA 驱动层修改显存报告上限。

在 WSL2 环境下，由于 GPU 通过 **dxgkrnl** (DirectX Graphics Kernel) 虚拟化通道透传，显存大小由 **Windows 宿主驱动** 报告给 WSL2 Linux 内核。因此：

- **Windows 侧驱动必须正确识别 22GB**，WSL2 内才能看到 22GB。
- 若 Windows 侧显示 11GB，WSL2 内无论如何配置都不可能超过 11GB。
- 魔改显存的带宽保持原 2080Ti 水平 (~616 GB/s)，22GB 版本的延迟特性与 11GB 原版基本一致。

---

## 附录 B: 模型量化与显存需求对照

| 模型 | 量化方案 | 权重显存 | 激活/Cache | 总需求 | 分配区域 |
|:---|:---|:---|:---|:---|:---|
| BGE-large (Embedding) | fp16 | ~1.3GB | ~0.2GB | **~1.5GB** | 常驻区 2GB |
| Qwen-14B-Chat | AWQ Int4 | ~3.8GB | ~1.0GB | **~4.8GB** | 弹性区 A 4GB (需调低 max_len) |
| DeepSeek-7B | GPTQ Int4 | ~2.0GB | ~1.0GB | **~3.0GB** | 弹性区 A 4GB |
| Qwen-7B-Chat | GGUF Q4_K_M | ~1.5GB | ~0.5GB | **~2.0GB** | 弹性区 B 4GB |
| CodeLlama-7B | GGUF Q4_0 | ~1.3GB | ~0.5GB | **~1.8GB** | 弹性区 B 4GB |
| SDXL Base | fp16 | ~6.4GB | ~1.5GB | **~8.0GB** | 渲染区 8GB |
| SD 1.5 | fp16 | ~2.2GB | ~1.0GB | **~3.2GB** | 渲染区 8GB (余量可用 ControlNet) |

> **Qwen-14B-AWQ 的 4GB 限制**: 官方 AWQ 量化权重约 3.8GB，但 vLLM 的 KV Cache 与激活需要额外空间。若 strict 限制 4GB 内，建议 `--max-model-len 2048` 或换用 **DeepSeek-7B-GPTQ**。

---

*文档版本: v1.0-hpc | 最后更新: 2024 | Triad Infrastructure Team*
