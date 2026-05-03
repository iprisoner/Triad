# Triad HPC 本地 LLM 迁移指南: vLLM/Ollama → llama.cpp

> **适用版本**: `docker-compose.hpc.yml` (llama.cpp 重构版)  
> **目标硬件**: 魔改 RTX 2080Ti 22GB / 双路 Xeon E5-2673v3 / 128GB DDR4 ECC  
> **迁移日期**: 2024  

---

## 1. 迁移前后对比表

| 维度 | vLLM + Ollama 版 (旧) | llama.cpp 版 (新) | 说明 |
|------|----------------------|-------------------|------|
| **LLM 后端数量** | 2 个 (`vllm` + `ollama`) | 1 个 (`llama-server`) | 统一后端，降低运维复杂度 |
| **镜像体积** | vLLM ~8GB + Ollama ~3GB | llama.cpp server-cuda ~2GB | 镜像更小，启动更快 |
| **模型格式** | AWQ/Int4 (HF Transformers) | GGUF (Q4_K_M 等) | GGUF 跨平台、单文件、内存映射友好 |
| **典型模型** | `Qwen-14B-Chat-AWQ` | `qwen-14b-chat.Q4_K_M.gguf` | 同精度下 GGUF 文件略小，加载更快 |
| **常驻显存** | ~12GB (vLLM) + ~4GB (Ollama) | ~9GB (llama-server `-ngl 99`) | 量化效率更高，空闲缓冲更大 |
| **渲染态显存** | vLLM/Ollama 需完全停机 | `-ngl 0` 切 CPU offload，**无需停机** | 权重保留在系统内存，GPU 显存瞬间释放 |
| **CPU 推理能力** | vLLM CPU offload 极慢 | llama.cpp 原生优化 CPU 推理 (AVX2/AVX512) | 切换后 CPU 推理可接受 |
| **OpenAI 兼容** | vLLM 原生支持 + Ollama 部分支持 | llama-server `/v1/chat/completions` | 完全兼容，Hermes/OpenClaw 无需改调用逻辑 |
| **并发槽位** | vLLM `max_num_seqs=4` / Ollama `num_parallel=2` | `--slots` + `--cont-batching` | 连续批处理 + 多槽位，等效并发 |
| **Embedding** | Hermes 内嵌 PyTorch (2GB 常驻) | **不变** | BGE-large 仍由 Hermes 独占 2GB |
| **显存调度器** | 脚本停机/启动容器 (延迟高) | `vram_scheduler.py` 外部动态改 `-ngl` 参数 | 秒级切换，服务不中断 |

---

## 2. 显存分区变化说明

### 2.1 22GB VRAM 分配总览 (llama.cpp 版)

| 区域 | vLLM 版 (GB) | llama.cpp 版 (GB) | 变化说明 |
|------|-------------|-------------------|----------|
| **Embedding 常驻** | 2 | 2 | 无变化。Hermes BGE-large fp16，永不卸载 |
| **LLM GPU (常态)** | 12 (vLLM 含 KV Cache) | 9 (Qwen-14B Q4_K_M, `-ngl 99`) | GGUF 量化更高效，省 3GB |
| **LLM CPU (渲染态)** | 0 (权重随容器停机丢失) | 0 (权重在系统内存, `-ngl 0`) | GPU 层数为 0，但权重无需重新加载 |
| **空闲/缓冲** | 6 | 9 | 更多缓冲，减少 CUDA 分配碎片 |
| **ComfyUI 独占** | 8 | 20 | llama.cpp 释放更彻底，渲染可用 VRAM 翻倍 |
| **系统预留** | 2 | 2 | CUDA 上下文/驱动开销 |
| **总计** | **22** | **22** | 重新分配，渲染区大幅扩容 |

### 2.2 渲染场景切换流程

```text
[常态]   Embedding(2GB) + LLM GPU(9GB) + 空闲(9GB) + 系统(2GB) = 22GB
            ↓  vram_scheduler.py 检测到 ComfyUI 启动请求
[渲染态] Embedding(2GB) + LLM CPU(0GB) + ComfyUI(20GB) + 系统(2GB) = 22GB
            ↓  ComfyUI 任务完成，vram_scheduler.py 恢复
[常态]   Embedding(2GB) + LLM GPU(9GB) + 空闲(9GB) + 系统(2GB) = 22GB
```

**关键优势**: llama-server 在 `-ngl 0` 时仍监听 `8080` 端口，WebUI/API 不中断；请求由 CPU 推理处理，延迟增加但可用性保持。ComfyUI 完成后 `vram_scheduler` 通过 `docker compose exec` 发送 `SIGHUP` 或直接重启容器带上 `-ngl 99`，模型权重已在系统内存，重新热加载到 GPU 只需秒级。

---

## 3. 模型下载指引 (GGUF 格式)

### 3.1 推荐模型与量化策略

| 模型 | 量化 | 文件大小 | VRAM(-ngl 99) | 适用场景 |
|------|------|----------|---------------|----------|
| **Qwen2.5-14B-Instruct** | Q4_K_M | ~9.0 GB | ~9.5 GB | 中文综合对话，首选 |
| **Qwen2.5-14B-Instruct** | Q5_K_M | ~10.5 GB | ~11.0 GB | 精度更高，若 ComfyUI 占用小可用 |
| **Llama-3.1-8B-Instruct** | Q4_K_M | ~4.7 GB | ~5.2 GB | 英文为主，显存极宽裕 |
| **DeepSeek-Coder-V2-Lite** | Q4_K_M | ~8.5 GB | ~9.0 GB | 代码场景 |

> **硬件匹配**: 2080Ti 22GB 在常态下给 LLM 留 9GB，因此 **Qwen-14B Q4_K_M** 是最均衡的选择。若需要更高精度且暂时不用 ComfyUI，可临时上 Q5_K_M。

### 3.2 下载与放置

```bash
# 创建模型目录
mkdir -p ~/.triad/models

# 方式 1: 从 HuggingFace 直接下载 (需安装 huggingface-cli)
pip install huggingface-hub
huggingface-cli download \
  Qwen/Qwen2.5-14B-Instruct-GGUF \
  qwen2.5-14b-instruct-q4_k_m.gguf \
  --local-dir ~/.triad/models

# 方式 2: 从 modelscope 镜像下载 (国内加速)
wget -O ~/.triad/models/qwen-14b-chat.Q4_K_M.gguf \
  https://modelscope.cn/models/qwen/Qwen-14B-Chat-GGUF/resolve/master/qwen-14b-chat.Q4_K_M.gguf

# 方式 3: 手动从 HF/Mega 等站点下载后放置
# 文件必须放在 ~/.triad/models/ 下，且 docker-compose 中 command 的 -m 路径要匹配
```

### 3.3 文件名映射

`docker-compose.hpc.yml` 中 llama-server 默认 command 使用:
```yaml
-m /models/qwen-14b-chat.Q4_K_M.gguf
```

若下载的文件名不同，请**同时修改宿主机文件名**或**修改 compose 中的 command**。建议保持命名一致，避免混淆。

### 3.4 校验文件完整性

```bash
cd ~/.triad/models
sha256sum -c qwen-14b-chat.Q4_K_M.gguf.sha256  # 若官方提供校验文件
ls -lh qwen-14b-chat.Q4_K_M.gguf                # 确认大小约 8.5-9.5 GB
```

---

## 4. NUMA/CPU 线程调优建议

### 4.1 硬件拓扑回顾

- **CPU**: 2x Intel Xeon E5-2673v3 (每颗 12C/24T，共 24C/48T)
- **NUMA Nodes**: 2 (每 Node 64GB 内存)
- **GPU**: 单卡 RTX 2080Ti 22GB，挂在 NUMA Node 1 (通常 PCIe 拓扑更近)

### 4.2 llama-server 的 NUMA 感知

当 `vram_scheduler` 将 llama-server 切换为 `-ngl 0` (纯 CPU) 时，权重将全部载入系统内存。**这是 llama.cpp 相比 vLLM 的巨大优势**: vLLM 的 CPU offload 性能极差且实现复杂，而 llama.cpp 原生为 CPU 推理优化。

在纯 CPU 模式下，建议通过 `vram_scheduler` 的启动逻辑注入 NUMA 亲和参数:

```bash
# 在宿主机脚本中，启动 CPU 模式 llama-server 时:
numactl --cpunodebind=1 --membind=1 \
  docker compose exec llama-server \
  llama-server -m /models/... -ngl 0 --host 0.0.0.0 --port 8080
```

或更简洁地，在 `docker-compose.hpc.yml` 中通过 `cpuset` 已绑定到 `20-23,44-47` (Node 1 的后段核心)。对于 **纯 CPU 大负载推理**，可考虑将 cpuset 扩展为 Node 1 全核心:

```yaml
# 若常态 GPU 推理由 GPU 承担大部分计算，CPU 辅助即可，保持 cpuset 不变。
# 若长时间运行 -ngl 0 纯 CPU 模式，可临时调整 cpuset: "12-23,36-47" (Node 1 全核心)
```

### 4.3 llama.cpp CPU 线程参数

当 `vram_scheduler` 以外部进程注入 command 时，建议追加以下 CPU 调优参数:

```bash
llama-server \
  -m /models/qwen-14b-chat.Q4_K_M.gguf \
  -ngl 0                    # 纯 CPU 模式 (渲染态)
  -t 32                     # 使用 32 线程 (留 16 线程给系统/ComfyUI)
  --host 0.0.0.0 --port 8080
```

常态 GPU 模式无需特别调整线程数，GPU 承担矩阵运算，CPU 仅做预处理和调度。

### 4.4 内存带宽优化

- **系统内存充足**: 128GB 足够容纳 Qwen-14B Q4_K_M (~9GB 权重) + 32K 上下文缓存 + ComfyUI 模型卸载到内存的缓冲。
- **透明大页 (THP)**: 建议在宿主机开启，提升 llama.cpp 顺序内存访问性能:
  ```bash
  echo always > /sys/kernel/mm/transparent_hugepage/enabled
  ```
- **内存绑定**: WSL2 下 `numactl` 可能不可用，依赖 `cpuset` 的隐式 NUMA 倾向即可。原生 Linux 强烈建议显式 `--membind=1`。

---

## 5. 故障排查

### 5.1 llama-server 启动失败 / 立即退出

**症状**: `docker compose --profile local-llm up -d` 后，`triad-llama-server` 状态为 `Restarting` 或 `Exited`。

**排查步骤**:

1. **查看日志**
   ```bash
   docker logs triad-llama-server --tail 100
   ```

2. **常见原因与修复**

   | 错误日志关键词 | 原因 | 修复 |
   |----------------|------|------|
   | `failed to load model` / `cannot open file` | GGUF 文件不存在或路径错误 | 确认 `~/.triad/models/qwen-14b-chat.Q4_K_M.gguf` 存在，且宿主机路径正确展开 |
   | `CUDA out of memory` | 显存不足 (有其他进程占用) | 执行 `nvidia-smi` 检查占用；确认 Hermes 未超限 (2GB)；必要时改 `-ngl 50` 减少 GPU 层数 |
   | `ggml_cuda_init: failed to initialize CUDA` | NVIDIA Container Toolkit 未正确配置 | 运行 `nvidia-ctk runtime configure --runtime=docker && systemctl restart docker` |
   | `port 8080 is already in use` | 宿主机或其他容器占用了 8080 | `lsof -i :8080` 排查；修改 compose 端口映射，或终止占用进程 |
   | `illegal instruction` / `SIGILL` | CPU 不支持 AVX2 (但 E5-2673v3 支持 AVX2) | 若镜像为 AVX512 构建而 CPU 不支持，换用 `ghcr.io/ggerganov/llama.cpp:server` (无 CUDA 通用版) |

3. **手动测试启动**
   ```bash
   docker run --rm -it --gpus all \
     -v ~/.triad/models:/models:ro \
     ghcr.io/ggerganov/llama.cpp:server-cuda \
     llama-server -m /models/qwen-14b-chat.Q4_K_M.gguf --host 0.0.0.0 -ngl 99
   ```

### 5.2 `-ngl` (gpu-layers) 无效 / 全部权重仍在 CPU

**症状**: `nvidia-smi` 显示 llama-server 显存占用接近 0GB，或 `-ngl 99` 时显存占用远低于预期。

**排查步骤**:

1. **确认镜像为 CUDA 版本**
   ```bash
   docker inspect ghcr.io/ggerganov/llama.cpp:server-cuda | grep -i cuda
   ```
   若误用 CPU 镜像 (`server` 而非 `server-cuda`)，则无 GPU 加速。

2. **确认 NVIDIA Runtime 生效**
   ```bash
   docker exec triad-llama-server nvidia-smi
   ```
   若提示 `nvidia-smi not found` 或无法列出 GPU，说明 `runtime: nvidia` 未生效。检查 `/etc/docker/daemon.json` 是否包含:
   ```json
   "runtimes": {
     "nvidia": {
       "path": "nvidia-container-runtime",
       "runtimeArgs": []
     }
   }
   ```

3. **CUDA 架构兼容性**
   RTX 2080Ti 为 Turing (SM_75)。llama.cpp 的 `server-cuda` 镜像通常包含多架构 binary (SM_52+)。若自行编译，务必确保:
   ```bash
   cmake -B build -DLLAMA_CUDA=ON -DLLAMA_CUDA_NVCC_ARCH_FLAGS="7.5"
   ```

4. **日志确认 offload 层数**
   llama-server 启动日志会输出 `offloaded X/Y layers to GPU`。若 `X=0`，则 `-ngl` 未生效。检查是否使用了 `--n-gpu-layers` 的短形式 `-ngl` (正确)。

### 5.3 健康检查持续失败

**症状**: `docker ps` 显示 `health: starting` 长时间不转 `healthy`。

**原因与修复**:
- **模型加载超时**: Q4_K_M 在 NVMe 上通常 10-30 秒加载完成。若存储为 HDD 或网络存储，可能超过 `start_period: 60s`。延长 healthcheck 的 `start_period` 到 `180s`。
- **端口未监听**: llama-server 默认只监听 `127.0.0.1`，compose 中已用 `--host 0.0.0.0` 修复。若手动覆盖 command 时遗漏此参数，健康检查从容器内 `localhost` 访问也会失败。
- **curl 不存在**: `ghcr.io/ggerganov/llama.cpp:server-cuda` 基于轻量镜像，**可能未内置 curl**。若健康检查失败提示 `curl: not found`，请将 healthcheck 改为:
  ```yaml
  healthcheck:
    test: ["CMD-SHELL", "wget -qO- http://localhost:8080/health || exit 1"]
  ```
  或使用 llama-server 内置状态端口。若官方镜像确实缺少 curl/wget，建议在宿主机健康检查或构建自定义镜像:
  ```dockerfile
  FROM ghcr.io/ggerganov/llama.cpp:server-cuda
  RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
  ```

### 5.4 性能异常 (GPU 模式推理慢)

1. **确认连续批处理生效**: 启动日志应包含 `cont_batching = true`。若使用旧版 llama.cpp 镜像，可能不支持 `--cont-batching`。升级镜像或移除该参数。
2. **确认 KV Cache 尺寸合理**: `-c 8192` 对 Q4_K_M 约占用额外 1-2GB VRAM。若显存紧张导致层数被隐式削减，性能会骤降。可通过 `nvidia-smi` 实时监控。
3. **PCIe 带宽**: 2080Ti 插在 PCIe 3.0 x16 上通常足够。若误插 x8 或 x4，GGUF 首次加载和上下文切换会慢。

### 5.5 vram_scheduler 切换后模型不响应

**症状**: vram_scheduler 从 `-ngl 99` 切到 `-ngl 0` 后，API 返回 502 或超时。

**原因**:
- llama-server 不支持运行时热切换 `-ngl` 参数。必须**重启容器**或通过 `docker compose exec` 杀掉原进程并启动新进程。
- `vram_scheduler.py` 的实现应遵循:
  ```python
  # 1. 发送 SIGTERM 给 llama-server 主进程 (优雅保存上下文槽位)
  docker compose kill -s SIGTERM llama-server
  # 2. 使用新 command 重启 (Docker Compose 会自动使用更新后的 command)
  # 或者通过 docker compose run 一个临时容器接管端口 (复杂)
  ```
  最简单可靠的方式是:
  ```bash
  # CPU 模式 (ComfyUI 渲染期)
  docker compose stop llama-server
  docker compose run -d --service-ports --name triad-llama-server \
    llama-server llama-server -m /models/... -ngl 0 -c 8192 --host 0.0.0.0
  
  # GPU 模式 (渲染结束)
  docker compose stop llama-server
  docker compose up -d llama-server
  ```

> **建议**: 在 `vram_scheduler.py` 中实现状态机，确保端口 8080 在任何时刻至多被一个 llama-server 实例持有，避免请求打到旧进程。

---

## 6. 快速验证清单

完成迁移后，按以下顺序验证:

```bash
# 1. 核心服务启动 (无 LLM)
docker compose up -d
# 验证: openclaw(8080), hermes(8000/19000), qdrant(16333), registry(18500) 均 healthy

# 2. 本地 LLM 模式启动
docker compose --profile local-llm up -d
# 验证: llama-server 健康，且 nvidia-smi 显示 ~9GB 显存占用

# 3. 模型对话测试
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen-14b-chat.Q4_K_M.gguf","messages":[{"role":"user","content":"你好"}]}'

# 4. ComfyUI 渲染共存测试 (若使用 vram_scheduler)
# 触发调度器切到 CPU 模式 → 启动 comfyui --profile render
# 验证: nvidia-smi 中 llama-server 显存占用 ~0GB，ComfyUI 可用 ~20GB

# 5. 恢复测试
# ComfyUI 停止后，vram_scheduler 恢复 -ngl 99
# 验证: llama-server 再次 healthy，显存回到 ~9GB，对话正常
```

---

## 附录 A: 备选镜像方案

若 `ghcr.io/ggerganov/llama.cpp:server-cuda` 在特定网络环境拉取失败，可使用以下备选:

| 镜像 | 标签 | 说明 |
|------|------|------|
| `ghcr.io/ggerganov/llama.cpp:server-cuda` | `server-cuda` | **首选**, 官方 CUDA 构建 |
| `ghcr.io/abetlen/llama-cpp-python:latest` | `latest` | 社区维护，含 Python 绑定，体积较大 |
| 自建 (见下) | `triad/llama-server:cuda12.1` | 网络隔离环境推荐 |

**自建 Dockerfile** (网络受限环境):
```dockerfile
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/ggerganov/llama.cpp /llama.cpp \
    && cd /llama.cpp \
    && cmake -B build -DLLAMA_CUDA=ON -DLLAMA_CUDA_NVCC_ARCH_FLAGS="7.5" \
    && cmake --build build --config Release -j$(nproc)
EXPOSE 8080
ENTRYPOINT ["/llama.cpp/build/bin/llama-server"]
```

构建命令:
```bash
docker build -t triad/llama-server:cuda12.1 -f Dockerfile.llama .
# 然后将 compose 中 image 替换为 triad/llama-server:cuda12.1
```

---

## 附录 B: 相关文档索引

- `docs/hpc_scheduling.md` — NUMA 拓扑、内核参数、WSL2 配置
- `docs/vram_scheduler.md` — vram_scheduler.py 设计文档与状态机实现
- `docker-compose.hpc.yml` — 本指南对应的运行配置
