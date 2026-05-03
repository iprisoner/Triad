# Triad WSL2 部署手册

> **版本**: v1.0  
> **适用环境**: WSL2 + Ubuntu 22.04/24.04 + Docker  
> **更新日期**: 2025  
> **维护者**: DevOps Engine

---

## 目录

1. [快速开始](#快速开始)
2. [架构概览](#架构概览)
3. [已知陷阱与解决方案](#已知陷阱与解决方案)
   - 陷阱 1: NTFS 跨界挂载
   - 陷阱 2: Hyper-V 虚拟交换机子网冲突
   - 陷阱 3: Docker Desktop vs 原生 Docker
   - 陷阱 4: GPU 透传（WSL2 特殊）
   - 陷阱 5: Windows 访问 WSL2 内 Docker 服务
   - 陷阱 6: Docker Socket 权限
   - 陷阱 7: 9P 文件系统性能
4. [脚本使用指南](#脚本使用指南)
5. [故障排查速查表](#故障排查速查表)
6. [进阶配置](#进阶配置)
7. [附录](#附录)

---

## 快速开始

```bash
# 1. 克隆仓库并进入目录
cd /path/to/triad

# 2. 运行初始化（会自动检测环境、创建目录、配置网络）
bash init.sh

# 3. 配置 WSL2 网关（让 Windows 浏览器能访问）
bash bridge/wsl2_gateway.sh setup

# 4. 启动 Triad
docker compose up -d

# 5. 在 Windows 浏览器打开
#    http://localhost:8080  → ClawPanel 前端
#    http://localhost:8000  → Triad API
```

---

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    Windows 宿主机                              │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────┐  │
│  │ 浏览器       │   │ PowerShell  │   │ netsh portproxy     │  │
│  │ localhost   │←──│ 防火墙规则  │←──│ 127.0.0.1:8080     │  │
│  │ :8080       │   │             │   │    ↓                │  │
│  └─────────────┘   └─────────────┘   │ WSL2_IP:8080        │  │
│                                      └─────────────────────┘  │
│                          │                                    │
│                    Hyper-V 虚拟交换机                           │
│                          │                                    │
└──────────────────────────┼────────────────────────────────────┘
                           │
┌──────────────────────────┼────────────────────────────────────┐
│                    WSL2  Ubuntu                              │
│  ┌───────────────────────┐│┌────────────────────────────────┐  │
│  │  eth0 (172.x.x.x)    │││  docker0 / triad-bridge        │  │
│  │                       │││  172.20.0.0/16                 │  │
│  │  ←── WSL2_IP ────────┘│└──→ 172.20.0.1 (gateway)       │  │
│  │                       │  │                                │  │
│  │  ~/.triad/            │  │  ┌─────────┐ ┌─────────┐    │  │
│  │  ├── memory/          │  │  │ 容器 A  │ │ 容器 B  │    │  │
│  │  ├── audit/           │  │  │:8000    │ │:8080    │    │  │
│  │  └── logs/            │  │  └────┬────┘ └────┬────┘    │  │
│  │       (ext4/btrfs)    │  │       └────┬────┘          │  │
│  └───────────────────────┘  │            │                │  │
│                             │    bind mount 挂载            │  │
│                             │    ~/.triad → /app/data       │  │
│                             └────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**核心设计原则**:
- 所有状态数据（memory、audit、logs）必须存储在 **WSL2 的 ext4/btrfs 文件系统**上
- Docker 使用自定义 bridge 网络 `triad-bridge`（`172.20.0.0/16`）
- 容器通过 bind mount 访问宿主目录，因此文件系统类型直接影响权限有效性
- Windows 浏览器通过 `localhost:PORT` 访问，底层由 `netsh portproxy` 转发到 WSL2 IP

---

## 已知陷阱与解决方案

### 陷阱 1: NTFS 跨界挂载（最严重）

#### 症状
- 容器启动时报 `Permission Denied`
- `chown 1000:1000 /app/data` 在容器内失败
- `chmod 700` 在 host 执行后，容器内看到的权限仍是 `777` 或不可写
- 日志中出现 `Operation not permitted` 或 `Read-only file system`

#### 根本原因
WSL2 的 `/mnt/c/`、`/mnt/d/` 等路径是 **9P 协议挂载**的 Windows NTFS 分区：

```bash
$ mount | grep 9p
none on /mnt/c type 9p (rw,noatime,dirsync,aname=drvfs;path=C:\;uid=1000;gid=1000;...)
```

9P 文件系统的问题：
1. **不支持 Unix 权限位**: `chmod`、`chown` 完全无效或仅模拟
2. **UID/GID 映射问题**: 容器内 UID 1000 映射到 Windows 的未知用户
3. **执行位丢失**: 脚本文件在 NTFS 上没有 `+x` 权限
4. **大小写敏感问题**: NTFS 默认大小写不敏感，影响某些 Linux 工具

#### 检测方法
```bash
# 方法 1: 查看 mount
df -T ~/.triad
# 正确输出应包含 ext4 或 btrfs
# 危险输出: 9p、fuse、fuseblk、ntfs、vfat

# 方法 2: 尝试 chown（在 9P 上会失败）
touch /mnt/c/test_chown && sudo chown 9999:9999 /mnt/c/test_chown 2>&1
# 9P 上: "chown: changing ownership of '/mnt/c/test_chown': Operation not permitted"

# 方法 3: 查看 wsl.conf 的 mount 配置
cat /etc/wsl.conf | grep -A5 "\[automount\]"
```

#### 解决方案

**方案 A: 强制使用 WSL2 ext4 路径（推荐）**

```bash
# 永远不要这样
TRIAD_ROOT=/mnt/c/Users/Alice/triad    # ❌ 死亡陷阱

# 永远要这样
TRIAD_ROOT="$HOME/.triad"              # ✅ 在 /home/<user> 下，ext4 文件系统
export TRIAD_ROOT=/var/lib/triad       # ✅ 也是 Linux 原生路径
```

**方案 B: 如果必须通过 /mnt/ 访问（不推荐用于状态数据）**

在 `/etc/wsl.conf` 中配置：
```ini
[automount]
enabled = true
mountFsTab = false
root = /mnt/
options = "metadata,umask=22,fmask=11"
```

然后重启 WSL2：
```bash
wsl --shutdown
# 重新打开 WSL2
```

> ⚠️ 注意：即使配置了 `metadata`，9P 的权限模拟仍然**不完全可靠**，容器内仍然可能遇到边缘 case。生产环境务必使用原生 ext4/btrfs 路径。

#### 修复命令速查
```bash
# 确认文件系统类型
df -T ~/.triad

# 确认不在 /mnt/ 下
[[ "$TRIAD_ROOT" == /mnt/* ]] && echo "危险！" || echo "安全"

# 如需迁移数据
mkdir -p "$HOME/.triad"
cp -a /mnt/c/old_triad/* "$HOME/.triad/"
chmod -R 700 "$HOME/.triad"
```

---

### 陷阱 2: Hyper-V 虚拟交换机子网冲突

#### 症状
- Docker 网络创建失败：`Error response from daemon: Pool overlaps with other one on this address space`
- `docker network create` 报子网已被占用
- 容器无法获取 IP，或获取到冲突网段的 IP 导致网络不通
- Windows 宿主机与 WSL2 之间网络间歇性断开

#### 根本原因
WSL2 使用 Hyper-V 虚拟交换机，其虚拟网卡会占用一个子网（通常是 `172.x.x.x/20`）。如果 Docker 自定义网络的子网与 Hyper-V 分配的网段重叠，就会产生冲突。

常见冲突场景：
```
Hyper-V WSL 子网:    172.20.0.0/20  (WSL2 eth0 可能在这个范围)
Docker 自定义网络:    172.20.0.0/16  ← 完全包含在 Hyper-V 子网内！
```

#### 检测方法
```bash
# 查看 WSL2 的 eth0 IP
ip addr show eth0 | grep 'inet '
# 例如: 172.20.15.3/20

# 查看现有 Docker 网络
docker network ls
docker network inspect bridge | grep Subnet

# 查看所有占用 172.20.x.x 的接口
ip addr show | grep 'inet 172\.20\.'
```

#### 解决方案

**init.sh 已内置自动调整机制**：如果检测到 `172.20.x.x` 已被占用，会自动尝试 `172.21` - `172.30` 范围内的备用子网。

**手动调整**：
```bash
# 1. 检查当前 Docker 网络
docker network inspect triad-bridge | grep Subnet

# 2. 如果冲突，删除旧网络（先断开所有容器）
docker network disconnect triad-bridge <container_name>
docker network rm triad-bridge

# 3. 使用不冲突的子网重建
docker network create \
  --driver bridge \
  --subnet=172.30.0.0/16 \
  --gateway=172.30.0.1 \
  triad-bridge

# 4. 更新 .env 文件中的 TRIAD_SUBNET
```

**预防配置（docker-compose.yml）**：
```yaml
networks:
  triad-bridge:
    driver: bridge
    ipam:
      config:
        - subnet: ${TRIAD_SUBNET:-172.30.0.0/16}
          gateway: ${TRIAD_GATEWAY:-172.30.0.1}
```

---

### 陷阱 3: Docker Desktop vs 原生 Docker

#### 症状
- `docker info` 无响应或报错 `Cannot connect to the Docker daemon`
- `docker compose` 与 `docker-compose` 命令行为不一致
- Docker Socket 路径在不同环境下变化
- 容器内无法访问 Docker API（Docker-in-Docker 场景）

#### 根本原因
| 模式 | Socket 路径 | 管理工具 | WSL2 集成 |
|------|-----------|---------|----------|
| Docker Desktop | `/var/run/docker.sock` → 转发到 Windows | Desktop UI | 自动集成 |
| 原生 Docker (apt) | `/var/run/docker.sock` | systemd | 手动安装 |
| Rootless Docker | `~/.docker/run/docker.sock` | rootlesskit | 需手动配置 |

WSL2 中使用 Docker Desktop 时，`/var/run/docker.sock` 实际上是一个指向 Windows 侧 Docker Desktop 的代理 socket。这在绝大多数情况下工作正常，但：
1. 某些 Go 编写的工具对转发 socket 兼容性不佳
2. 权限映射可能有微妙差异
3. Docker Desktop 更新后 socket 可能短暂不可用

#### 检测方法
```bash
# 检测 Docker 版本与来源
docker --version
# Docker Desktop: "Docker version 24.0.7, build afdd53b"
# 原生 Docker: "Docker version 24.0.7, build 24.0.7-0ubuntu..."

# 检测 socket 类型
ls -la /var/run/docker.sock
# 如果是符号链接，追踪目标
readlink -f /var/run/docker.sock

# 检测 Docker Desktop 集成
docker info 2>/dev/null | grep -i "desktop\|docker desktop"

# 检测 docker compose 插件
docker compose version
docker-compose version  # standalone
```

#### 解决方案

**方案 A: 确认 Docker Desktop WSL 集成已启用**

在 Windows 侧：
1. 打开 Docker Desktop → Settings → Resources → WSL Integration
2. 启用你的 Ubuntu 发行版
3. 点击 Apply & Restart

或在 PowerShell（管理员）：
```powershell
# Docker Desktop CLI 控制（如果安装了）
& "C:\Program Files\Docker\Docker\DockerCli.exe" -SwitchDaemon
```

**方案 B: 安装原生 Docker（不依赖 Docker Desktop）**

```bash
# 1. 卸载 Docker Desktop 的 WSL 集成（可选）
# 2. 安装原生 Docker
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 3. 将当前用户加入 docker 组
sudo usermod -aG docker $USER
newgrp docker

# 4. 验证
docker run hello-world
```

**方案 C: 处理 socket 权限问题**

```bash
# 如果 /var/run/docker.sock 权限不对
sudo chmod 666 /var/run/docker.sock    # 临时（不安全）

# 正确做法
sudo chown root:docker /var/run/docker.sock
sudo chmod 660 /var/run/docker.sock
sudo usermod -aG docker $USER
newgrp docker
```

**方案 D: 配置 DOCKER_HOST 环境变量**

```bash
# 如果 socket 不在标准位置
export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
# 或 TCP（不推荐用于本地）
export DOCKER_HOST=tcp://localhost:2375
```

---

### 陷阱 4: GPU 透传（WSL2 特殊）

#### 症状
- `nvidia-smi` 在 WSL2 中可运行，但 Docker 容器内报错：`CUDA unavailable`
- `docker run --gpus all nvidia/cuda:11.0-base nvidia-smi` 报 `could not select device driver`
- 容器启动时失败：`Unknown runtime specified nvidia`
- GPU 显存显示异常（比如显示 24GB 但实际无法分配）

#### 根本原因
WSL2 的 GPU 支持是 **Windows 宿主机驱动直接透传**，不需要在 Linux 内安装 NVIDIA 驱动。但 Docker 使用 GPU 需要 NVIDIA Container Toolkit。

三种常见配置状态：

| 状态 | nvidia-smi (WSL2) | Docker `--gpus` | 说明 |
|------|-------------------|----------------|------|
| A | ✅ 工作 | ✅ 工作 | 理想状态，Docker Desktop 4.9+ |
| B | ✅ 工作 | ❌ 失败 | 缺少 nvidia-container-toolkit |
| C | ❌ 失败 | ❌ 失败 | Windows 侧驱动问题 |

#### 检测方法
```bash
# 1. WSL2 侧 nvidia-smi
nvidia-smi
# 应该显示与 Windows 宿主机相同的 GPU

# 2. Docker 运行时检测
docker info | grep -i nvidia
# 正确应显示 "nvidia" runtime

# 3. Docker GPU 测试
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
# 如果失败，说明 toolkit 未配置

# 4. 检查 nvidia-container-toolkit
nvidia-ctk --version
# 或
dpkg -l | grep nvidia-container

# 5. 检查 daemon.json
cat /etc/docker/daemon.json | grep nvidia
```

#### 解决方案

**Docker Desktop 用户（推荐路径）**

Docker Desktop 4.9+ 在 WSL2 中**自动**支持 NVIDIA GPU：
1. 确保 Windows 宿主机已安装 NVIDIA 驱动（从 nvidia.com 下载）
2. 确保 Docker Desktop 版本 >= 4.9
3. 在 Docker Desktop → Settings → Resources → WSL Integration 中启用你的发行版
4. **不要在 WSL2 内安装 NVIDIA Linux 驱动**（会冲突！）

验证：
```bash
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

**原生 Docker 用户（高级）**

```bash
# 1. 添加 NVIDIA 仓库
sudo apt update && sudo apt install -y curl

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 2. 安装 toolkit
sudo apt update
sudo apt install -y nvidia-container-toolkit

# 3. 配置 Docker 运行时
sudo nvidia-ctk runtime configure --runtime=docker

# 4. 重启 Docker
sudo systemctl restart docker

# 5. 验证
sudo docker run --rm --runtime=nvidia --gpus all nvidia/cuda:12.0-base nvidia-smi
```

**重要：WSL2 不需要 Linux NVIDIA 驱动**

```bash
# ❌ 错误：在 WSL2 内安装 nvidia-driver-XXX
sudo apt install nvidia-driver-535    # 不要这样做！

# ✅ 正确：只在 Windows 宿主机安装驱动
# WSL2 通过 GPU-PV（GPU Paravirtualization）直接使用 Windows 驱动
```

**显存检测与魔改提示**

`init.sh` 会解析 `nvidia-smi` 输出确认显存。注意：
- WSL2 中 `nvidia-smi` 显示的是 Windows 宿主机的**物理显存**
- 如果 Windows 宿主机有多个 GPU，WSL2 通常只能看到主 GPU
- 显存 "魔改"（如 4090 24GB 刷成 48GB）会在 `nvidia-smi` 中直接体现，`init.sh` 会如实记录

---

### 陷阱 5: Windows 访问 WSL2 内 Docker 服务

#### 症状
- Windows 浏览器访问 `http://localhost:8080` 无响应
- `curl http://localhost:8080` 在 WSL2 内成功，Windows PowerShell 中失败
- 防火墙拦截连接，或连接超时

#### 根本原因
WSL2 是一个独立的虚拟网络命名空间。虽然较新的 Windows 11 支持 `localhost` 互通，但实际可靠性因版本而异：

| Windows 版本 | localhostForwarding | 稳定性 |
|-------------|---------------------|--------|
| Windows 10 20H2 | 部分支持 | 差，经常失效 |
| Windows 11 21H2 | 默认启用 | 中等 |
| Windows 11 22H2+ | 默认启用 | 较好 |
| Windows 10 + WSL1 | 直接共享网络栈 | 好（但 WSL1 不推荐） |

即使 `localhostForwarding=true`，Docker 的 `-p 8080:8080` 绑定到 `0.0.0.0` 后，Windows 侧有时仍然无法直接 `localhost:8080` 访问。

#### 解决方案

**方案 A: `wsl2_gateway.sh` 推荐的 netsh portproxy（最可靠）**

```bash
# 在 WSL2 内运行
bash bridge/wsl2_gateway.sh setup
```

这会在 Windows 侧执行：
```powershell
# PowerShell（管理员）
netsh interface portproxy add v4tov4 listenport=8080 listenaddress=127.0.0.1 connectport=8080 connectaddress=<WSL2_IP>
```

Windows 侧验证：
```powershell
# 查看转发规则
netsh interface portproxy show v4tov4

# 测试端口
Test-NetConnection -ComputerName 127.0.0.1 -Port 8080
```

**方案 B: 直接访问 WSL2 IP**

```bash
# 在 WSL2 内获取 IP
ip addr show eth0 | grep 'inet '
# 例如: 172.20.15.3

# 在 Windows 浏览器直接访问
# http://172.20.15.3:8080
```

缺点：每次 WSL2 重启后 IP 会变化。

**方案 C: 配置 .wslconfig 的 localhostForwarding**

在 Windows 用户目录创建 `C:\Users\<用户名>\.wslconfig`：
```ini
[wsl2]
localhostForwarding=true
```

然后重启 WSL2：
```powershell
wsl --shutdown
```

**方案 D: 使用 host.docker.internal（容器内访问宿主机反向场景）**

如果问题是容器访问 Windows 侧服务：
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
environment:
  - WINDOWS_HOST=host.docker.internal
```

#### 防火墙配置

`wsl2_gateway.sh` 会自动配置 Windows 防火墙，但如果没有管理员权限：

```powershell
# 手动以管理员运行 PowerShell
New-NetFirewallRule -DisplayName "Triad-WSL2-8080" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080
New-NetFirewallRule -DisplayName "Triad-WSL2-8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000
New-NetFirewallRule -DisplayName "Triad-WSL2-16333" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 16333
```

---

### 陷阱 6: Docker Socket 权限

#### 症状
- `docker ps` 报错 `permission denied while trying to connect to the Docker daemon`
- 非 root 用户无法运行任何 docker 命令
- 容器内挂载 `/var/run/docker.sock` 后，内部用户无法读写

#### 根本原因
Docker socket 默认属于 `root:docker`，权限 `660`。用户必须在 `docker` 组内。

#### 解决方案

```bash
# 1. 将用户加入 docker 组
sudo usermod -aG docker $USER

# 2. 刷新组权限（不重新登录）
newgrp docker

# 3. 验证
docker ps
```

**容器内访问 Docker socket**:

如果容器需要访问 Docker API（如 Portainer、ClawPanel 的容器管理功能）：

```yaml
services:
  clawpanel:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    user: "${UID}:${GID}"  # 确保容器内 UID/GID 与宿主机 docker 组匹配
```

更安全的方案：使用 Docker socket 代理：
```yaml
services:
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      - CONTAINERS=1
      - NETWORKS=1
      - POST=0  # 禁止写操作

  clawpanel:
    environment:
      - DOCKER_HOST=tcp://docker-socket-proxy:2375
```

---

### 陷阱 7: 9P 文件系统性能

#### 症状
- Docker build 或 `npm install` 在 `/mnt/c/` 下极慢
- 文件 I/O 密集型操作（如向量数据库写入）性能低下
- `df` 显示 `none` 作为设备名，类型为 `9p`

#### 根本原因
9P 是网络文件系统协议，用于 WSL2 访问 Windows 分区。其性能特性：
- 跨边界文件操作延迟高（每次系统调用都需跨越 VM 边界）
- 小文件操作尤其慢（`npm install` 可慢 10-50 倍）
- 无内核页缓存优化
- 大文件顺序读写尚可，随机读写和元数据操作很差

#### 解决方案

**黄金法则：状态数据、代码、Docker 构建缓存全部放在 WSL2 ext4 上**

```bash
# ❌ 错误
cd /mnt/c/Projects/triad && docker compose up -d

# ✅ 正确
cd ~/triad && docker compose up -d
# 或
cd /var/lib/triad && docker compose up -d
```

**如果必须跨边界工作**：

在 `wsl.conf` 中启用 `metadata` 和优化挂载选项：
```ini
[automount]
enabled = true
options = "metadata,umask=22,fmask=11,case=dir"
```

**使用 VS Code Remote-WSL**：
- 在 Windows 侧用 VS Code，通过 Remote-WSL 插件连接
- 代码实际保存在 WSL2 的 ext4 上
- 获得 Windows 编辑体验 + Linux 文件系统性能

---

## 脚本使用指南

### init.sh

**用途**: 环境检测、目录初始化、网络配置、GPU 检测、配置生成

```bash
# 基本用法
bash init.sh

# 自定义 TRIAD_ROOT
TRIAD_ROOT=/var/lib/triad bash init.sh

# 执行流程
# 阶段 1: 检测 WSL2 / Docker / Compose / NVIDIA Runtime
# 阶段 2: 创建 ~/.triad/ 目录结构（严格检查 ext4/btrfs）
# 阶段 3: 创建/检查 triad-bridge Docker 网络（自动解决子网冲突）
# 阶段 4: 检测 GPU（nvidia-smi）并判断显存容量
# 阶段 5: 生成 .env 和 docker-compose.override.wsl2.yml
```

**退出条件**（脚本会立即失败并给出修复指引）：
- 不在 WSL2 环境（可强制继续）
- Docker 不可用
- `TRIAD_ROOT` 落在 `/mnt/` 下
- 文件系统类型为 `9p`、`fuse`、`ntfs`、`vfat`
- Docker 守护进程无响应

### wsl2_gateway.sh

**用途**: 配置 Windows 到 WSL2 的端口转发

```bash
# 配置默认端口 (8080, 8000, 16333, 16379)
bash bridge/wsl2_gateway.sh setup

# 配置指定端口
bash bridge/wsl2_gateway.sh setup 8080 3000 9090

# 自动检测 Docker 容器端口并配置
bash bridge/wsl2_gateway.sh auto

# 查看当前状态
bash bridge/wsl2_gateway.sh status

# 测试端口连通性
bash bridge/wsl2_gateway.sh test

# 清理端口转发
bash bridge/wsl2_gateway.sh clean

# 启用 WSL2 localhost 互通（Windows 11）
bash bridge/wsl2_gateway.sh enable-localhost

# 环境变量控制
WSL2_AUTO_CLEAN=true bash bridge/wsl2_gateway.sh setup
WSL2_GATEWAY_PORTS="8080 3000" bash bridge/wsl2_gateway.sh setup
```

**需要管理员权限的操作**（脚本会自动尝试，失败时给出手动命令）：
- `netsh interface portproxy add/delete`
- `New-NetFirewallRule / Remove-NetFirewallRule`

**每次 WSL2 重启后**：
```bash
# WSL2 IP 会变化，需要重新配置网关
wsl --shutdown
# 重新打开 WSL2
bash bridge/wsl2_gateway.sh setup
```

---

## 故障排查速查表

| 症状 | 可能原因 | 快速诊断 | 修复命令 |
|------|---------|---------|---------|
| `init.sh` 报 "TRIAD_ROOT 位于 /mnt/" | 状态目录在 NTFS 上 | `df -T ~/.triad` | `export TRIAD_ROOT=$HOME/.triad` |
| `init.sh` 报 "文件系统类型 9p" | 路径通过软链指向 /mnt/ | `mount \| grep 9p` | 确保路径在 ext4 上 |
| `docker network create` 失败 | 子网冲突 | `ip addr \| grep 172.20` | 改用 `172.30.0.0/16` |
| `docker info` 无响应 | Docker 未运行/权限不足 | `sudo docker info` | `sudo usermod -aG docker $USER` |
| 容器内 Permission Denied | bind mount 权限/文件系统 | `ls -la /app/data` 容器内 | `chmod 700` host 侧；确认非 9P |
| Windows 浏览器打不开 | 端口转发未配置 | `wsl2_gateway.sh status` | `wsl2_gateway.sh setup` |
| `nvidia-smi` 在容器内失败 | toolkit 未配置 | `docker info \| grep nvidia` | 安装 nvidia-container-toolkit |
| GPU 显存显示不对 | WSL2 透传显示宿主机信息 | Windows 侧也运行 nvidia-smi | 这是正常行为，显存以物理为准 |
| 构建速度极慢 | 在 /mnt/ 下执行 Docker | `pwd` | `cd ~ && docker build ...` |
| `localhost:8080` 时通时不通 | WSL2 localhostForwarding 不稳定 | 检查 Windows 版本 | 使用 `wsl2_gateway.sh` 的 netsh 方案 |

---

## 进阶配置

### 自定义 WSL2 资源配置

在 Windows 用户目录创建 `C:\Users\<用户名>\.wslconfig`：

```ini
[wsl2]
# 内存上限（防止 WSL2 吞掉所有宿主机内存）
memory=16GB
processors=8

# localhost 互通（Windows 11 22H2+）
localhostForwarding=true

# 禁用页面报告（减少内存回收卡顿）
pageReporting=false

# 交换文件大小
swap=4GB

# 嵌套虚拟化（如果你要在 WSL2 里再跑 VM）
nestedVirtualization=false

# 是否允许 WSL2 使用 Windows 防火墙规则
firewall=true
```

配置后执行：
```powershell
wsl --shutdown
# 等待 8 秒后重新打开 WSL2
```

### 固定 WSL2 IP（高级）

WSL2 IP 每次重启会变化。如需固定：

```bash
# 在 /etc/wsl-daemon.conf 或启动脚本中设置静态 IP
# 注意：这需要禁用 WSL2 的 DHCP，不建议新手操作

# 替代方案：使用 Windows 侧脚本自动更新 portproxy
# 已包含在 wsl2_gateway.sh 中，每次重启后运行即可
```

### Docker BuildKit 跨边界优化

如果必须在 `/mnt/` 下构建：

```bash
# 强制 BuildKit 使用 WSL2 内的临时目录
export DOCKER_BUILDKIT=1
DOCKER_BUILDKIT=1 docker build \
  --build-arg BUILDKIT_INLINE_CACHE=1 \
  -t myimage .
```

### 日志与监控

```bash
# 查看 Triad 所有服务日志
docker compose logs -f --tail=100

# 限制日志大小（防止 9P 日志目录膨胀）
# docker-compose.yml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

---

## 附录

### A. WSL2 网络架构详解

```
Windows 宿主机
  ├─ 物理网卡 (Wi-Fi / 以太网)
  ├─ Hyper-V 虚拟交换机 (WSL)
  │    └─ WSL2 虚拟网卡 (vEthernet WSL)
  │         └─ 子网: 172.x.x.x/20 (由 Windows 分配)
  │              └─ WSL2 VM
  │                   ├─ eth0 (172.x.x.x/20)
  │                   ├─ docker0 (bridge)
  │                   │    └─ vethxxxx (容器网卡)
  │                   └─ triad-bridge (自定义 bridge)
  │                        └─ 子网: 172.20.0.0/16
```

**数据包流向**（Windows 浏览器 → Triad API）：
1. Windows 浏览器 `http://localhost:8000`
2. Windows TCP/IP 栈 → `netsh portproxy` 规则匹配
3. 目标被改写为 `WSL2_IP:8000`
4. 数据包进入 Hyper-V 虚拟交换机
5. 到达 WSL2 `eth0`
6. Docker 的 `-p 8000:8000` 规则匹配
7. 数据包进入 `triad-bridge` 网络
8. 送达容器内的服务进程

### B. 文件系统对比

| 特性 | ext4 (WSL2) | 9P (/mnt/c) | 影响 |
|------|-------------|-------------|------|
| Unix 权限 | ✅ 完整 | ❌ 模拟/无效 | Docker 权限依赖 |
| 硬链接 | ✅ 支持 | ⚠️ 有限 | 某些构建工具依赖 |
| 符号链接 | ✅ 完整 | ⚠️ Windows 风格 | 兼容性差异 |
| 大小写敏感 | ✅ 敏感 | ❌ 默认不敏感 | 编译缓存问题 |
| 文件锁 | ✅ flock | ⚠️ 有限 | 数据库文件可能损坏 |
| 性能 | ✅ 原生 | ❌ 慢 5-50x | I/O 密集型任务 |
| inotify | ✅ 支持 | ❌ 不支持 | 热重载、文件监听失效 |

### C. 相关资源

- [WSL2 网络问题官方文档](https://learn.microsoft.com/en-us/windows/wsl/networking)
- [Docker Desktop WSL2 Backend](https://docs.docker.com/desktop/wsl/)
- [NVIDIA Container Toolkit on WSL2](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)
- [WSL2 文件系统性能](https://devblogs.microsoft.com/commandline/whats-new-for-wsl-in-insiders-preview-build-18945/)
- [WSL2 高级设置配置](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)

---

> **注意**: 本手册基于 WSL2 + Docker 的当前行为编写。WSL2 和 Docker Desktop 更新频繁，某些行为可能随版本变化。遇到问题时，请先确认 Windows、WSL2、Docker 的版本。
