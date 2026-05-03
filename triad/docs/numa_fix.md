# NUMA 拓扑修复文档 — vram_scheduler_llama.py CPU 亲和性动态跷跷板

> **问题根源**：Docker cpuset-cpus 限制（8 核）与 llama-server `-t 32`（32 线程）严重矛盾，导致 32 个 OpenMP 线程在 8 个逻辑核上疯狂上下文切换，推理速度从 ~5 tok/s 暴跌到 ~1 tok/s，甚至触发死锁。
>
> **解决方案**：`CpuAffinityManager` 在状态切换时通过 `docker update` 动态调整容器 CPU 亲和性，实现 NUMA 跷跷板。

---

## 1. NUMA 拓扑说明

本系统运行于 **双路 AMD EPYC 48 逻辑核** 平台，NUMA 拓扑如下：

| NUMA Node | 逻辑核范围 | 物理核数 | SMT 配对 |
|-----------|-----------|---------|---------|
| **Node 0** | `0-11, 24-35` | 12 | core0→thread0+thread24 |
| **Node 1** | `12-23, 36-47` | 12 | core12→thread12+thread36 |
| **总计** | `0-47` | 24 | 48 逻辑核 |

**关键观察**：
- GPU（2080Ti 22GB）通常挂载在 **Node 0** 的 PCIe 根复用器上
- 远离 GPU 的核心（Node 1 末尾）用于控制线程，避免与 GPU DMA 争用 LLC/内存带宽
- CPU 推理时需要尽可能多的核心 + 共享 LLC，因此优先使用 `0-31`（Node 0 全量 + Node 1 前半）

---

## 2. 容器绑核策略

### 2.1 GPU 模式（常态 IDLE）

| 参数 | 值 | 说明 |
|------|-----|------|
| `--cpus` | `0.125` | 4 核配额（cgroup v2 cpu.max 限制） |
| `--cpuset-cpus` | `20-23,44-47` | Node 1 末尾 4 核（远离 GPU PCIe） |
| `llama-server -t` | `4` | 4 控制线程，GPU 做 heavy lifting |

**策略理由**：
- llama-server GPU 模式下，CPU 仅负责 prompt 预处理、KV-cache 管理、token 采样
- 4 线程已足够处理控制逻辑，多余线程只会浪费调度开销
- 绑到 Node 1 末尾避免与 GPU 内存拷贝争用 Node 0 的内存控制器

### 2.2 CPU_FALLBACK 模式

| 参数 | 值 | 说明 |
|------|-----|------|
| `--cpus` | `0.67` | 32 核配额 |
| `--cpuset-cpus` | `0-31` | Node 0 全量(24核) + Node 1 前半(8核) |
| `llama-server -t` | `32` | 32 OpenMP 线程并行矩阵乘法 |

**策略理由**：
- llama.cpp 的 GGML 使用 OpenMP 并行，`t` 线程直接对应 BLAS 并行度
- `0-31` 覆盖 Node 0 全部逻辑核（含 SMT）+ Node 1 前 8 核，最大化 LLC 命中
- 32 线程 ≈ 32 逻辑核，避免超订（oversubscription）导致的上下文切换

### 2.3 切换时序（关键）

```
IDLE --(acquire)--> CPU_FALLBACK:
  1. docker update --cpus=0.67 --cpuset-cpus=0-31   <-- 先扩展!
  2. llama-server -ngl 0 -t 32                      <-- 后启动
  3. 健康检查通过 → 进入 RENDERING

RENDERING --(release)--> IDLE:
  1. llama-server -ngl 99 -t 4                       <-- 先启动 GPU 模式
  2. 健康检查通过
  3. docker update --cpus=0.125 --cpuset-cpus=20-23,44-47  <-- 后收缩!
```

**为什么必须先扩展再启动？**

若顺序颠倒（先启动 `-t 32` 再扩展 cpuset），32 个 OpenMP 线程会在容器原有的 4 核上瞬时被创建，即使后续扩展 cpuset，OpenMP 线程绑定已固定（部分实现会重新探测，但不可依赖）。这会导致：
- 32 线程在 4 核上瞬态并发 → 调度风暴
- 推理速度暴跌到 ~1 tok/s
- 极端情况下触发 futex 死锁

---

## 3. 线程数与核心数对照表

| 运行模式 | 容器核数 | llama-server -t | 线程:核心比 | 预期行为 |
|---------|---------|----------------|------------|---------|
| GPU（修改前） | 8 | 32 | **4:1** | ❌ 严重超订，上下文切换灾难 |
| GPU（修改后） | 4 | 4 | **1:1** | ✅ 控制线程刚好够用 |
| CPU_FALLBACK | 32 | 32 | **1:1** | ✅ OpenMP 满负荷无超订 |
| CPU（错误调参） | 32 | 48 | **1.5:1** | ⚠️ 轻微超订，SMT 争用 |
| CPU（极端错误） | 8 | 48 | **6:1** | ❌ 死锁或 0.5 tok/s |

**经验法则**：
- GPU 模式：`-t` 取 `cpuset-cpus` 核心数的 **1~2 倍**（控制逻辑轻量）
- CPU 模式：`-t` 取 `cpuset-cpus` 逻辑核数的 **0.8~1.0 倍**（避免超订）

---

## 4. 性能基准预期

基于 **Qwen-14B Q4_K_M @ 2080Ti 22GB + 双路 EPYC 48 核** 实测估算：

| 模式 | 推理速度 | 显存占用 | 功耗 | 适用场景 |
|------|---------|---------|------|---------|
| **GPU 全速** (`-ngl 99 -t 4`) | **~25 tok/s** | ~9 GB | ~200W | 常态文本/代码 |
| **CPU_FALLBACK** (`-ngl 0 -t 32`) | **~8-12 tok/s** | ~0.5 GB | ~150W | 渲染态后台推理 |
| **CPU 错误调参** (`-ngl 0 -t 48@8核`) | **~1-2 tok/s** | ~0.5 GB | ~180W | 灾难态 |
| **GPU 恢复** (`mmap` 热映射) | < **2 秒** 恢复 25 tok/s | 逐步增长到 9GB | 阶梯上升 | 渲染完成 |

**关键指标说明**：
- `~25 tok/s`：FP16 KV-cache，prompt 预处理 GPU 全 offload，batch=1
- `~8-12 tok/s`：Q4_K_M 权重纯 CPU 解码，32 线程 AMX/AVX512，受内存带宽限制
- `< 2 秒`：mmap 热映射，权重已在 RAM，GPU 层重新加载仅迁移 KV-cache

---

## 5. 环境变量配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLAMA_CONTAINER_NAME` | `triad-llama-server` | Docker 容器名称，`CpuAffinityManager` 检测目标 |
| `LLAMA_MODEL_PATH` | `/mnt/models/qwen-14b-q4_k_m.gguf` | GGUF 模型路径 |
| `LLAMA_HOST` | `127.0.0.1` | llama-server 监听地址 |
| `LLAMA_PORT` | `8000` | llama-server 监听端口 |
| `LLAMA_NGL` | `99` | GPU offload 层数 |
| `LLAMA_THREADS` | `32` | CPU 模式线程数（GPU 模式自动取 4） |
| `LLAMA_CTX_SIZE` | `8192` | 上下文长度 |

---

## 6. 宿主机模式回退

当 `CpuAffinityManager` 检测不到 Docker 容器（`docker ps` 返回空）时：
- `has_docker = False`
- `expand_to_cpu_fallback()` 和 `shrink_to_gpu_mode()` 均静默 `return`
- llama-server 线程数仍按策略设置（`-t 4` 或 `-t 32`），但不受 cgroup 限制

这允许同一代码库在以下场景运行：
1. **Docker 容器**（生产环境）：自动 NUMA 跷跷板
2. **systemd 服务**（裸机部署）：仅线程数策略生效
3. **CI/测试环境**（mock 模式）：完全无 Docker 依赖

---

## 7. 调试命令

```bash
# 查看当前容器 CPU 限制
docker inspect triad-llama-server --format '{{.HostConfig.CpusetCpus}} {{.HostConfig.CpuQuota}} {{.HostConfig.CpuPeriod}}'

# 实时查看容器内线程绑定
docker exec triad-llama-server ps -eLo pid,psr,comm | grep llama-server

# 查看 llama-server 日志中的线程数确认
docker logs triad-llama-server | grep -i "threads\|n_threads"

# 手动测试 NUMA 跷跷板
docker update --cpus=0.67 --cpuset-cpus=0-31 triad-llama-server
docker update --cpus=0.125 --cpuset-cpus=20-23,44-47 triad-llama-server
```

---

## 8. 参考

- [Docker run reference — CPU limit](https://docs.docker.com/engine/reference/run/#cpu-limit-constraint)
- [cgroups v2 cpu controller](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#cpu)
- [llama.cpp server README — threading](https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md)
- [AMD EPYC 7003 NUMA topology guide](https://www.amd.com/system/files/documents/amd-epyc-7003-tg-hpc-workload-performance-tuning-guide.pdf)
