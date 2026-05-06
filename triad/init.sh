#!/bin/bash
set -euo pipefail

# =============================================================================
# Triad 系统初始化脚本 — WSL2 Ubuntu 专用
# 职责：环境检测、目录创建、Docker 网络初始化、GPU 检测、配置生成
# 作者：DevOps Engine
# =============================================================================

# --- 严格模式说明 ---
# set -e : 任何命令失败立即退出
# set -u : 使用未定义变量时立即退出
# set -o pipefail : 管道中任一命令失败即整体失败

# --- 颜色定义 ---
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m' # No Color

# --- 日志函数 ---
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${CYAN}${BOLD}[STEP]${NC}  $*"; }
log_fatal() { echo -e "${RED}${BOLD}[FATAL]${NC} $*"; }

# --- 成功/失败标记 ---
success() { echo -e "${GREEN}✓${NC} $*"; }
fail()    { echo -e "${RED}✗${NC} $*"; return 1; }

# --- 致命退出函数 ---
fatal_exit() {
    log_fatal "$1"
    echo -e "${YELLOW}修复指引:${NC} $2"
    exit 1
}

# --- 确认函数 ---
confirm() {
    local msg="$1"
    # v2.3 修复：CI/CD 或管道环境自动跳过确认
    if [[ -n "${CI:-}" ]] || [[ ! -t 0 ]] || [[ ! -t 1 ]]; then
        log_info "Non-interactive mode detected, auto-confirming: ${msg}"
        return 0
    fi
    echo -ne "${YELLOW}[PROMPT]${NC} ${msg} [y/N] "
    read -r answer < /dev/tty || true
    [[ "$answer" =~ ^[Yy]$ ]]
}

# =============================================================================
# 阶段 0: 基础环境信息收集
# =============================================================================
log_step "阶段 0: 收集基础环境信息"

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly HOSTNAME=$(hostname)
readonly DATE_STR=$(date +%Y%m%d_%H%M%S)
readonly OS_INFO=$(uname -a)

log_info "脚本目录: $SCRIPT_DIR"
log_info "主机名:   $HOSTNAME"
log_info "日期:     $DATE_STR"
log_info "内核信息: $OS_INFO"
success "基础信息收集完成"

# =============================================================================
# 阶段 1: 环境检测（WSL2 / Docker / NVIDIA Runtime）
# =============================================================================
log_step "阶段 1: 环境检测"

# --- 1.1 WSL2 环境检测 ---
log_info "检测 WSL2 环境..."
IS_WSL2=false
if echo "$OS_INFO" | grep -qi "WSL2" 2>/dev/null; then
    IS_WSL2=true
    success "uname 确认 WSL2 环境"
elif [[ -d "/mnt/wslg" ]] && [[ -e "/proc/sys/fs/binfmt_misc/WSLInterop" ]]; then
    IS_WSL2=true
    success "/mnt/wslg + WSLInterop 确认 WSL2 环境"
elif grep -qi "microsoft" /proc/version 2>/dev/null; then
    IS_WSL2=true
    success "/proc/version 确认 Microsoft WSL 环境"
else
    log_warn "未检测到 WSL2 特征。如果确实在 WSL2 中运行，请确认 'uname -a' 包含 'WSL2'"
    if ! confirm "继续执行?"; then
        fatal_exit "用户取消" "请确认你在 WSL2 Ubuntu 环境中运行此脚本"
    fi
fi

# 检测 WSL 版本（1 或 2）
WSL_VERSION=2
if [[ -f /proc/sys/kernel/osrelease ]]; then
    if grep -q "WSL2" /proc/sys/kernel/osrelease 2>/dev/null; then
        WSL_VERSION=2
    elif grep -qi "microsoft" /proc/sys/kernel/osrelease 2>/dev/null; then
        WSL_VERSION=1
    fi
fi
log_info "WSL 版本: $WSL_VERSION"

if [[ "$WSL_VERSION" -eq 1 ]]; then
    fatal_exit "检测到 WSL1，不支持" \
        "请升级到 WSL2: wsl --set-version <distro> 2，或 wsl --install --distribution Ubuntu"
fi

# --- 1.2 Docker 可用性检测 ---
log_info "检测 Docker 可用性..."

if ! command -v docker &>/dev/null; then
    fatal_exit "Docker CLI 未安装或未在 PATH 中" \
        "1) Docker Desktop: 设置 → Resources → WSL integration → 启用本发行版\n   2) 或 apt install docker-ce docker-ce-cli containerd.io\n   3) 或 sudo snap install docker"
fi

DOCKER_VERSION=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',') || true
if [[ -z "$DOCKER_VERSION" ]]; then
    fatal_exit "docker --version 返回空，Docker 守护进程可能未运行" \
        "1) Docker Desktop: 确认 Docker Desktop 已启动\n   2) 或 sudo systemctl start docker\n   3) 检查权限: sudo usermod -aG docker \$USER && newgrp docker"
fi
success "Docker 版本: $DOCKER_VERSION"

# Docker Desktop vs 原生 Docker 检测
DOCKER_SOCKET_TYPE="unknown"
if [[ -S "/var/run/docker.sock" ]]; then
    DOCKER_SOCKET_TYPE="unix"
    log_info "检测到 Unix socket: /var/run/docker.sock"
elif [[ -S "/run/user/$(id -u)/docker.sock" ]]; then
    DOCKER_SOCKET_TYPE="user"
    log_info "检测到用户 socket: /run/user/$(id -u)/docker.sock"
elif [[ -n "${DOCKER_HOST:-}" ]]; then
    DOCKER_SOCKET_TYPE="tcp"
    log_info "检测到 DOCKER_HOST=$DOCKER_HOST"
fi

# Docker Compose 检测
log_info "检测 Docker Compose..."
COMPOSE_VERSION=""
if docker compose version &>/dev/null; then
    COMPOSE_VERSION=$(docker compose version --short 2>/dev/null || docker compose version 2>/dev/null | head -1)
    success "Docker Compose (Plugin) 可用: $COMPOSE_VERSION"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_VERSION=$(docker-compose --version 2>/dev/null | awk '{print $3}' | tr -d ',')
    success "docker-compose (Standalone) 可用: $COMPOSE_VERSION"
else
    fatal_exit "Docker Compose 未安装" \
        "1) Docker Desktop 自带 compose plugin\n   2) 或: sudo apt install docker-compose-plugin\n   3) 或: pip install docker-compose"
fi

# Docker 守护进程响应检测
log_info "检测 Docker 守护进程响应..."
if ! docker info &>/dev/null; then
    fatal_exit "Docker 守护进程无响应" \
        "1) sudo systemctl start docker\n   2) 或确认 Docker Desktop 已启动\n   3) 检查权限: sudo usermod -aG docker \$USER，然后重新登录\n   4) 临时测试: sudo docker info"
fi
success "Docker 守护进程正常响应"

# --- 1.3 NVIDIA Docker Runtime 检测 ---
log_info "检测 NVIDIA Docker Runtime..."
NVIDIA_RUNTIME_AVAILABLE=false
if docker info 2>/dev/null | grep -q "nvidia"; then
    NVIDIA_RUNTIME_AVAILABLE=true
    success "Docker 已配置 nvidia runtime"
else
    log_warn "Docker 未配置 nvidia runtime"
fi

# 检查 nvidia-container-toolkit
if command -v nvidia-container-toolkit &>/dev/null || [[ -f /usr/bin/nvidia-container-toolkit ]]; then
    success "nvidia-container-toolkit 已安装"
else
    log_warn "nvidia-container-toolkit 未找到，GPU 容器可能无法使用"
fi

success "阶段 1 环境检测通过"

# =============================================================================
# 阶段 2: 目录创建与权限（严格检查）
# =============================================================================
log_step "阶段 2: 目录创建与权限（严格检查）"

# --- 2.1 设置 TRIAD_ROOT ---
TRIAD_ROOT="${TRIAD_ROOT:-$HOME/.triad}"
log_info "TRIAD_ROOT 设定为: $TRIAD_ROOT"

# --- 2.2 严格禁止 NTFS 跨界挂载 ---
log_info "检查 TRIAD_ROOT 是否在 /mnt/ 下..."
if [[ "$TRIAD_ROOT" == /mnt/* ]]; then
    fatal_exit "TRIAD_ROOT 位于 /mnt/ 下: $TRIAD_ROOT" \
        "这是 Windows NTFS 跨界挂载，chown/chmod 完全失效，容器内会出现 Permission Denied。\n修复方案:\n   1) 改用 WSL2 Linux 原生 ext4 路径，例如: export TRIAD_ROOT=\$HOME/.triad\n   2) 或在 ~/.bashrc 中设置: export TRIAD_ROOT=/home/<user>/.triad\n   3) 然后重新运行本脚本"
fi
success "TRIAD_ROOT 不在 /mnt/ 下"

# --- 2.3 解析真实路径并重新检查 ---
mkdir -p "$TRIAD_ROOT" 2>/dev/null || true
if [[ ! -d "$TRIAD_ROOT" ]]; then
    fatal_exit "无法创建目录: $TRIAD_ROOT" \
        "检查父目录权限: ls -ld $(dirname "$TRIAD_ROOT")\n   或: sudo mkdir -p $TRIAD_ROOT && sudo chown \$(id -u):\$(id -g) $TRIAD_ROOT"
fi

# 获取文件系统挂载点信息
REAL_PATH=$(cd "$TRIAD_ROOT" && pwd -P)
log_info "真实路径: $REAL_PATH"

# --- 2.4 文件系统类型检测（关键陷阱防护） ---
log_info "检测文件系统类型..."
FS_TYPE=$(df -T "$TRIAD_ROOT" 2>/dev/null | awk 'NR==2 {print $2}')
if [[ -z "$FS_TYPE" ]]; then
    # 备用方法
    FS_TYPE=$(stat -f -c %T "$TRIAD_ROOT" 2>/dev/null || true)
fi
log_info "检测到文件系统类型: $FS_TYPE"

case "$FS_TYPE" in
    ext4|btrfs|tmpfs)
        success "文件系统类型 '${FS_TYPE}' 受支持（Linux 原生）"
        ;;
    9p|fuse*|fuseblk|ntfs|vfat|exfat)
        fatal_exit "文件系统类型 '${FS_TYPE}' 不受支持" \
            "这是 NTFS 跨界挂载或 9P 文件系统，Docker bind mount 会导致:\n   - chown 完全失效\n   - 容器内 Permission Denied\n   - 无法执行 Unix 权限位\n修复方案:\n   1) 确认 TRIAD_ROOT 在 WSL2 的 ext4 虚拟磁盘上: export TRIAD_ROOT=/home/\$USER/.triad\n   2) 检查 df -h 输出，确保路径挂载为 ext4 而非 9p\n   3) 切勿将 Triad 状态目录放在 /mnt/c/、/mnt/d/ 等 Windows 盘符下"
        ;;
    *)
        log_warn "未知文件系统类型: ${FS_TYPE}"
        if ! confirm "文件系统 '${FS_TYPE}' 未经验证，继续执行?"; then
            exit 1
        fi
        ;;
esac

# 额外保险：通过 mount 命令再次确认
if mount | grep -E "^9p.*on ${REAL_PATH}" &>/dev/null || mount | grep "type 9p" | grep -q "$REAL_PATH"; then
    fatal_exit "mount 命令检测到 9P 文件系统" \
        "即使路径不在 /mnt/ 下，也可能通过软链挂载到 9P。请检查: mount | grep 9p\n修复: 确保目录在 WSL2 ext4 根文件系统上"
fi

# --- 2.5 创建目录结构 ---
log_info "创建 Triad 目录结构..."

readonly DIRS=(
    "$TRIAD_ROOT/memory/facts"
    "$TRIAD_ROOT/memory/skills"
    "$TRIAD_ROOT/memory/episodes"
    "$TRIAD_ROOT/memory/vectors"
    "$TRIAD_ROOT/audit"
    "$TRIAD_ROOT/logs"
    "$TRIAD_ROOT/tmp"
    "$TRIAD_ROOT/config"
    "$TRIAD_ROOT/secrets"
)

for dir in "${DIRS[@]}"; do
    if mkdir -p "$dir"; then
        success "创建目录: $dir"
    else
        fatal_exit "无法创建目录: $dir" \
            "检查权限: ls -ld $(dirname "$dir")\n   执行: chmod 755 $(dirname "$dir")"
    fi
done

# --- 2.6 设置权限 ---
log_info "设置目录权限 (chmod 700)..."
for dir in "$TRIAD_ROOT" "${DIRS[@]}"; do
    if chmod 700 "$dir"; then
        success "权限设置: $dir"
    else
        # 在 9P/NTFS 上 chmod 会静默失败或报 Operation not permitted
        if [[ "$FS_TYPE" == "9p" ]] || [[ "$FS_TYPE" == "fuse*" ]]; then
            fatal_exit "chmod 700 在 '${FS_TYPE}' 上失败: $dir" \
                "再次确认文件系统类型: df -T $dir\n   然后移动到 ext4/btrfs 路径下"
        else
            log_warn "chmod 700 失败: $dir (可能是 9P 检测遗漏)"
        fi
    fi
done

# 验证权限
log_info "验证权限设置..."
PERM_CHECK=$(stat -c %a "$TRIAD_ROOT" 2>/dev/null || stat -f %Lp "$TRIAD_ROOT" 2>/dev/null || echo "???")
if [[ "$PERM_CHECK" == "700" ]]; then
    success "权限验证通过: $TRIAD_ROOT = 700"
else
    log_warn "权限验证结果: $PERM_CHECK（期望 700）。在某些文件系统上 stat 输出可能不同，请手动检查: ls -ld $TRIAD_ROOT"
fi

success "阶段 2 目录创建与权限设置完成"

# =============================================================================
# 阶段 3: Docker 网络初始化
# =============================================================================
log_step "阶段 3: Docker 网络初始化"

# --- 3.1 定义网络参数 ---
TRIAD_SUBNET="172.20.0.0/16"
TRIAD_GATEWAY="172.20.0.1"
TRIAD_NETWORK_NAME="triad-bridge"

# --- 3.2 检查 WSL2 虚拟网卡子网冲突 ---
log_info "检查 WSL2 虚拟网卡与子网冲突..."
WSL_ETH0_IP=$(ip addr show eth0 2>/dev/null | grep 'inet ' | awk '{print $2}')
log_info "WSL2 eth0 IP: ${WSL_ETH0_IP:-未获取}"

# 检查现有接口是否占用 172.20.x.x
log_info "扫描现有网络接口..."
CONFLICT_FOUND=false
while read -r line; do
    if echo "$line" | grep -q "172\.20\." ; then
        log_warn "发现现有接口占用 172.20.x.x: $line"
        CONFLICT_FOUND=true
    fi
done < <(ip addr show 2>/dev/null | grep 'inet ' || true)

# 如果冲突，自动调整子网
if [[ "$CONFLICT_FOUND" == true ]]; then
    log_warn "172.20.0.0/16 与现有网络冲突，尝试自动调整..."
    # 尝试备用子网
    for ALT_PREFIX in 172.21 172.22 172.23 172.24 172.25 172.26 172.27 172.28 172.29 172.30; do
        ALT_SUBNET="${ALT_PREFIX}.0.0/16"
        ALT_GATEWAY="${ALT_PREFIX}.0.1"
        if ! ip addr show 2>/dev/null | grep "inet " | grep -q "${ALT_PREFIX}\."; then
            TRIAD_SUBNET="$ALT_SUBNET"
            TRIAD_GATEWAY="$ALT_GATEWAY"
            log_info "已自动调整子网为: $TRIAD_SUBNET (网关: $TRIAD_GATEWAY)"
            break
        fi
    done
fi

# --- 3.3 检查现有 Docker 网络冲突 ---
log_info "检查现有 Docker 网络..."
if docker network ls --format '{{.Name}} {{.Driver}} {{.Scope}}' 2>/dev/null | grep -q "^${TRIAD_NETWORK_NAME} "; then
    log_warn "Docker 网络 '${TRIAD_NETWORK_NAME}' 已存在"
    EXISTING_SUBNET=$(docker network inspect "$TRIAD_NETWORK_NAME" 2>/dev/null \
        | grep -oP '"Subnet":\s*"\K[^"]+' || true)
    log_info "现有子网: ${EXISTING_SUBNET:-未获取}"
    
    if [[ -n "$EXISTING_SUBNET" ]] && [[ "$EXISTING_SUBNET" != "$TRIAD_SUBNET" ]]; then
        log_warn "现有子网 (${EXISTING_SUBNET}) 与目标 (${TRIAD_SUBNET}) 不一致"
        if confirm "删除并重建网络 '${TRIAD_NETWORK_NAME}'?"; then
            log_info "删除现有网络..."
            # 先尝试断开所有容器
            mapfile -t CONNECTED_CONTAINERS < <(docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' "$TRIAD_NETWORK_NAME" 2>/dev/null || true)
            if [[ "${#CONNECTED_CONTAINERS[@]}" -gt 0 ]] && [[ -n "${CONNECTED_CONTAINERS[0]}" ]]; then
                for container in "${CONNECTED_CONTAINERS[@]}"; do
                    if [[ -n "$container" ]]; then
                        log_info "断开容器: $container"
                        docker network disconnect -f "$TRIAD_NETWORK_NAME" "$container" 2>/dev/null || true
                    fi
                done
            fi
            docker network rm "$TRIAD_NETWORK_NAME" 2>/dev/null || true
            success "已删除现有网络"
        else
            log_warn "保留现有网络，使用其子网配置"
            TRIAD_SUBNET="$EXISTING_SUBNET"
            TRIAD_GATEWAY=$(docker network inspect "$TRIAD_NETWORK_NAME" 2>/dev/null \
                | grep -oP '"Gateway":\s*"\K[^"]+' | head -1 || echo "${TRIAD_SUBNET%.*/*}.1")
        fi
    else
        success "现有网络子网与目标一致，无需重建"
    fi
fi

# --- 3.4 创建/确认 Docker 网络 ---
if ! docker network ls --format '{{.Name}}' 2>/dev/null | grep -q "^${TRIAD_NETWORK_NAME}$"; then
    log_info "创建 Docker 网络: ${TRIAD_NETWORK_NAME} (${TRIAD_SUBNET})..."
    if docker network create \
        --driver bridge \
        --subnet="$TRIAD_SUBNET" \
        --gateway="$TRIAD_GATEWAY" \
        --label "project=triad" \
        --label "created_by=init.sh" \
        --label "created_at=$DATE_STR" \
        "$TRIAD_NETWORK_NAME"; then
        success "网络创建成功: ${TRIAD_NETWORK_NAME} (${TRIAD_SUBNET})"
    else
        fatal_exit "Docker 网络创建失败" \
            "1) 检查子网是否冲突: docker network ls\n   2) 手动创建: docker network create --driver bridge --subnet=${TRIAD_SUBNET} ${TRIAD_NETWORK_NAME}\n   3) 检查 Docker 守护进程权限\n   4) 如果是子网冲突，脚本已自动尝试调整，请检查上方日志"
    fi
else
    success "Docker 网络已就绪: ${TRIAD_NETWORK_NAME}"
fi

# --- 3.5 网络验证 ---
log_info "验证网络配置..."
NETWORK_INSPECT=$(docker network inspect "$TRIAD_NETWORK_NAME" 2>/dev/null)
if echo "$NETWORK_INSPECT" | grep -q "$TRIAD_SUBNET"; then
    success "子网验证通过: $TRIAD_SUBNET"
else
    log_warn "子网验证未通过，请手动检查: docker network inspect ${TRIAD_NETWORK_NAME}"
fi

success "阶段 3 Docker 网络初始化完成"

# =============================================================================
# 阶段 4: GPU 支持检测
# =============================================================================
log_step "阶段 4: GPU 支持检测"

GPU_AVAILABLE=false
GPU_MEMORY_MB=0
GPU_MODEL="none"
GPU_COUNT=0
WSL_GPU_WORKAROUND=false

# --- 4.1 检测 NVIDIA GPU ---
log_info "检测 NVIDIA GPU..."
if command -v nvidia-smi &>/dev/null; then
    NVIDIA_SMI_OUTPUT=$(nvidia-smi 2>/dev/null || true)
    if [[ -n "$NVIDIA_SMI_OUTPUT" ]]; then
        GPU_AVAILABLE=true
        GPU_COUNT=$(echo "$NVIDIA_SMI_OUTPUT" | grep -cE "^\|[[:space:]]+[0-9]+[[:space:]]+.*NVIDIA" || echo "0")
        GPU_MODEL=$(echo "$NVIDIA_SMI_OUTPUT" | grep -oP "(NVIDIA|GeForce|Quadro|Tesla|Titan)\s+[A-Za-z0-9\-]+" | head -1 || echo "unknown")
        
        # 解析显存（MB）
        # 尝试多种格式：MiB、MB、GiB
        GPU_MEM_MIB=$(echo "$NVIDIA_SMI_OUTPUT" | grep -oP "\d+\s*MiB" | head -1 | grep -oP "\d+" || true)
        GPU_MEM_GIB=$(echo "$NVIDIA_SMI_OUTPUT" | grep -oP "\d+\s*GiB" | head -1 | grep -oP "\d+" || true)
        
        if [[ -n "$GPU_MEM_GIB" ]] && [[ "$GPU_MEM_GIB" -gt 0 ]]; then
            GPU_MEMORY_MB=$((GPU_MEM_GIB * 1024))
        elif [[ -n "$GPU_MEM_MIB" ]] && [[ "$GPU_MEM_MIB" -gt 0 ]]; then
            GPU_MEMORY_MB=$GPU_MEM_MIB
        else
            # 备用解析：从表格中提取
            GPU_MEMORY_MB=$(echo "$NVIDIA_SMI_OUTPUT" | grep -oP "\d+\s*MiB /\s*\K\d+" | head -1 || echo "0")
        fi
        
        # WSL2 特殊：nvidia-smi 可能显示的是 Windows 宿主机的 GPU
        if [[ "$IS_WSL2" == true ]]; then
            WSL_GPU_WORKAROUND=true
            log_info "WSL2 GPU 透传模式: nvidia-smi 显示的是宿主机 GPU 信息"
        fi
        
        success "检测到 GPU: ${GPU_MODEL} (${GPU_MEMORY_MB} MiB) x${GPU_COUNT}"
    else
        log_warn "nvidia-smi 存在但无法获取输出（驱动问题）"
    fi
else
    log_warn "nvidia-smi 未安装"
fi

# --- 4.2 检测 NVIDIA Container Toolkit ---
log_info "检测 NVIDIA Container Toolkit..."
NVIDIA_CTK_AVAILABLE=false
if command -v nvidia-ctk &>/dev/null || [[ -x /usr/bin/nvidia-ctk ]]; then
    NVIDIA_CTK_AVAILABLE=true
    success "nvidia-ctk 已安装"
else
    log_warn "nvidia-ctk 未安装"
fi

# --- 4.3 WSL2 GPU 特殊配置检查 ---
if [[ "$IS_WSL2" == true ]] && [[ "$GPU_AVAILABLE" == true ]]; then
    log_info "检查 WSL2 GPU 集成..."
    # WSL2 中，Docker Desktop 4.9+ 自带 nvidia 支持
    if docker info 2>/dev/null | grep -q "nvidia"; then
        success "Docker 已配置 nvidia runtime（WSL2 集成正常）"
    else
        log_warn "Docker 未配置 nvidia runtime"
        log_info "WSL2 GPU 使用建议:"
        echo -e "   ${CYAN}1) Docker Desktop 用户:${NC} 确保版本 >= 4.9，在 Settings → Resources → WSL Integration 中启用"
        echo -e "   ${CYAN}2) 原生 Docker 用户:${NC} 安装 nvidia-container-toolkit 并配置 /etc/docker/daemon.json"
        echo -e "   ${YELLOW}修复命令:${NC}"
        echo -e "      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg"
        echo -e "      curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list"
        echo -e "      sudo apt update && sudo apt install -y nvidia-container-toolkit"
        echo -e "      sudo nvidia-ctk runtime configure --runtime=docker"
        echo -e "      sudo systemctl restart docker"
    fi
fi

# --- 4.4 魔改显存检测（>20GB） ---
log_info "检查显存容量..."
if [[ "$GPU_AVAILABLE" == true ]]; then
    if [[ "$GPU_MEMORY_MB" -gt 20000 ]]; then
        success "显存 > 20GB (${GPU_MEMORY_MB} MiB)，满足大模型运行要求"
    elif [[ "$GPU_MEMORY_MB" -gt 8000 ]]; then
        log_warn "显存 ${GPU_MEMORY_MB} MiB（8-20GB），建议运行 7B/13B 级别模型"
    elif [[ "$GPU_MEMORY_MB" -gt 4000 ]]; then
        log_warn "显存 ${GPU_MEMORY_MB} MiB（4-8GB），建议运行量化版小模型"
    elif [[ "$GPU_MEMORY_MB" -gt 0 ]]; then
        log_warn "显存 ${GPU_MEMORY_MB} MiB（<4GB），仅适合推理最小模型或 CPU offload"
    fi
else
    log_warn "未检测到 GPU，将生成 CPU-only 配置"
fi

# --- 4.5 CPU-only 降级配置提示 ---
if [[ "$GPU_AVAILABLE" == false ]]; then
    log_warn "==================== CPU-ONLY 模式 ===================="
    log_warn "未检测到 NVIDIA GPU，Triad 将运行在 CPU-only 模式"
    log_warn "性能影响: LLM 推理速度降低 10-50 倍，embedding 计算显著变慢"
    log_warn "修复方案:"
    echo -e "   ${YELLOW}1) WSL2 + NVIDIA GPU:${NC}"
    echo -e "      - 确保 Windows 宿主机已安装 NVIDIA 驱动（WSL2 不需要单独安装驱动）"
    echo -e "      - 确认 nvidia-smi 在 PowerShell 中可运行"
    echo -e "      - 确保 CUDA for WSL 已安装: https://developer.nvidia.com/cuda/wsl"
    echo -e "   ${YELLOW}2) 纯 CPU 运行（不推荐用于生产）:${NC}"
    echo -e "      - 增加 CPU 核心数分配（Docker --cpus）"
    echo -e "      - 使用量化模型（GGUF Q4_0）"
    echo -e "      - 启用 llama.cpp 的 OpenBLAS 支持"
    log_warn "======================================================"
fi

success "阶段 4 GPU 检测完成"

# =============================================================================
# 阶段 5: 配置文件生成
# =============================================================================
log_step "阶段 5: 配置文件生成"

# --- 5.1 收集配置参数 ---
readonly UID_VAL=$(id -u)
readonly GID_VAL=$(id -g)
readonly CPU_CORES=$(nproc)
readonly TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
readonly TOTAL_MEM_MB=$((TOTAL_MEM_KB / 1024))

# GPU 显存默认值
if [[ "$GPU_AVAILABLE" == true ]] && [[ "$GPU_MEMORY_MB" -gt 0 ]]; then
    GPU_MEMORY_CONFIG=$GPU_MEMORY_MB
else
    GPU_MEMORY_CONFIG=0
fi

# Docker Compose 文件路径
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"

# --- 5.2 生成 .env 文件 ---
ENV_FILE="${SCRIPT_DIR}/.env"
log_info "生成环境配置文件: $ENV_FILE"

cat > "$ENV_FILE" << EOF
# =============================================================================
# Triad 系统自动生成配置
# 生成时间: $(date -Iseconds)
# 生成脚本: init.sh
# =============================================================================

# --- 身份与权限 ---
UID=${UID_VAL}
GID=${GID_VAL}
TRIAD_USER=$(whoami)

# --- 路径配置 ---
TRIAD_ROOT=${TRIAD_ROOT}
TRIAD_MEMORY_DIR=${TRIAD_ROOT}/memory
TRIAD_AUDIT_DIR=${TRIAD_ROOT}/audit
TRIAD_LOGS_DIR=${TRIAD_ROOT}/logs
TRIAD_TMP_DIR=${TRIAD_ROOT}/tmp

# --- 网络配置 ---
TRIAD_NETWORK_NAME=${TRIAD_NETWORK_NAME}
TRIAD_SUBNET=${TRIAD_SUBNET}
TRIAD_GATEWAY=${TRIAD_GATEWAY}

# --- 硬件配置 ---
GPU_ENABLED=${GPU_AVAILABLE}
GPU_MEMORY=${GPU_MEMORY_CONFIG}
GPU_MODEL=${GPU_MODEL}
GPU_COUNT=${GPU_COUNT}
CPU_CORES=${CPU_CORES}
TOTAL_MEMORY_MB=${TOTAL_MEM_MB}

# --- WSL2 特定 ---
IS_WSL2=${IS_WSL2}
WSL_VERSION=${WSL_VERSION}
WSL_GPU_WORKAROUND=${WSL_GPU_WORKAROUND}

# --- 运行时模式 ---
# 可选: gpu | cpu | hybrid
TRIAD_RUNTIME_MODE=$([[ "$GPU_AVAILABLE" == true ]] && echo "gpu" || echo "cpu")

# --- 服务端口（宿主机映射） ---
CLAWPANEL_PORT=18080
API_PORT=18000
VECTOR_DB_PORT=16333
REDIS_PORT=16379

# --- 模型配置 ---
# 根据显存自动选择默认模型
EOF

# 根据显存追加模型建议
if [[ "$GPU_MEMORY_CONFIG" -gt 22000 ]]; then
    cat >> "$ENV_FILE" << 'EOF'
DEFAULT_MODEL=llama3:70b
EMBEDDING_MODEL=nomic-embed-text
# 显存充足，可运行 70B 级全精度模型
EOF
elif [[ "$GPU_MEMORY_CONFIG" -gt 12000 ]]; then
    cat >> "$ENV_FILE" << 'EOF'
DEFAULT_MODEL=llama3:70b-q4
EMBEDDING_MODEL=nomic-embed-text
# 显存中等，建议运行量化版 70B 或全精度 13B
EOF
elif [[ "$GPU_MEMORY_CONFIG" -gt 8000 ]]; then
    cat >> "$ENV_FILE" << 'EOF'
DEFAULT_MODEL=llama3:8b
EMBEDDING_MODEL=nomic-embed-text
# 显存有限，建议运行 8B 级别模型
EOF
else
    cat >> "$ENV_FILE" << 'EOF'
DEFAULT_MODEL=phi3:mini
EMBEDDING_MODEL=all-minilm
# CPU/低显存模式，使用最小模型
EOF
fi

success ".env 文件生成: $ENV_FILE"

# --- 5.3 生成 docker-compose.override.yml（WSL2 优化） ---
OVERRIDE_FILE="${SCRIPT_DIR}/docker-compose.override.wsl2.yml"
log_info "生成 WSL2 Docker Compose 覆盖配置: $OVERRIDE_FILE"

if [[ "$GPU_AVAILABLE" == true ]]; then
    cat > "$OVERRIDE_FILE" << EOF
# WSL2 GPU 覆盖配置
# 用法: docker compose -f docker-compose.yml -f docker-compose.override.wsl2.yml up -d
services:
  agent-core:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility

  inference-engine:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
EOF
else
    cat > "$OVERRIDE_FILE" << 'EOF'
# WSL2 CPU-only 覆盖配置
services:
  agent-core:
    deploy:
      resources:
        limits:
          cpus: '4.0'
          memory: 8G
    environment:
      - LLAMA_CUDA=0
      - OLLAMA_CPU_ONLY=1

  inference-engine:
    deploy:
      resources:
        limits:
          cpus: '4.0'
          memory: 8G
    environment:
      - LLAMA_CUDA=0
      - OLLAMA_CPU_ONLY=1
EOF
fi

success "WSL2 override 配置生成: $OVERRIDE_FILE"

# --- 5.4 生成 WSL2 网关脚本入口 ---
GATEWAY_SCRIPT="${SCRIPT_DIR}/bridge/wsl2_gateway.sh"
if [[ -f "$GATEWAY_SCRIPT" ]]; then
    log_info "WSL2 网关脚本已存在: $GATEWAY_SCRIPT"
    chmod +x "$GATEWAY_SCRIPT"
else
    log_warn "WSL2 网关脚本未找到: $GATEWAY_SCRIPT"
    log_warn "请确保 wsl2_gateway.sh 已部署到 ${SCRIPT_DIR}/bridge/"
fi

# --- 5.5 写入系统元数据 ---
META_FILE="${TRIAD_ROOT}/.triad_meta"
cat > "$META_FILE" << EOF
{
  "initialized_at": "$(date -Iseconds)",
  "hostname": "$HOSTNAME",
  "wsl2": $IS_WSL2,
  "wsl_version": $WSL_VERSION,
  "docker_version": "$DOCKER_VERSION",
  "compose_version": "$COMPOSE_VERSION",
  "docker_socket_type": "$DOCKER_SOCKET_TYPE",
  "triad_root": "$TRIAD_ROOT",
  "filesystem_type": "$FS_TYPE",
  "network": {
    "name": "$TRIAD_NETWORK_NAME",
    "subnet": "$TRIAD_SUBNET",
    "gateway": "$TRIAD_GATEWAY"
  },
  "gpu": {
    "available": $GPU_AVAILABLE,
    "model": "$GPU_MODEL",
    "memory_mb": $GPU_MEMORY_CONFIG,
    "count": $GPU_COUNT,
    "wsl_workaround": $WSL_GPU_WORKAROUND
  },
  "cpu": {
    "cores": $CPU_CORES,
    "memory_mb": $TOTAL_MEM_MB
  },
  "uid": $UID_VAL,
  "gid": $GID_VAL
}
EOF
chmod 600 "$META_FILE"
success "系统元数据写入: $META_FILE"

# =============================================================================
# 阶段 6: 最终汇总与后续指引
# =============================================================================
log_step "阶段 6: 初始化完成汇总"

echo ""
echo -e "${GREEN}${BOLD}==============================================${NC}"
echo -e "${GREEN}${BOLD}     Triad 系统初始化完成${NC}"
echo -e "${GREEN}${BOLD}==============================================${NC}"
echo ""
echo -e "${BOLD}环境信息:${NC}"
echo -e "  WSL2:          ${IS_WSL2} (版本 ${WSL_VERSION})"
echo -e "  Docker:        ${DOCKER_VERSION}"
echo -e "  Compose:       ${COMPOSE_VERSION}"
echo -e "  Socket 类型:   ${DOCKER_SOCKET_TYPE}"
echo ""
echo -e "${BOLD}目录结构:${NC}"
echo -e "  TRIAD_ROOT:    ${TRIAD_ROOT}"
echo -e "  文件系统:      ${FS_TYPE}"
echo -e "  权限:          700 (drwx------)"
echo ""
echo -e "${BOLD}网络配置:${NC}"
echo -e "  网络名:        ${TRIAD_NETWORK_NAME}"
echo -e "  子网:          ${TRIAD_SUBNET}"
echo -e "  网关:          ${TRIAD_GATEWAY}"
echo ""
echo -e "${BOLD}硬件配置:${NC}"
echo -e "  GPU 可用:      ${GPU_AVAILABLE}"
echo -e "  GPU 型号:      ${GPU_MODEL}"
echo -e "  GPU 显存:      ${GPU_MEMORY_CONFIG} MiB"
echo -e "  CPU 核心:      ${CPU_CORES}"
echo -e "  总内存:        ${TOTAL_MEM_MB} MiB"
echo ""
echo -e "${BOLD}生成文件:${NC}"
echo -e "  .env                    → ${SCRIPT_DIR}/.env"
echo -e "  docker-compose.override → ${OVERRIDE_FILE}"
echo -e "  元数据                  → ${META_FILE}"
echo ""
echo -e "${BOLD}后续步骤:${NC}"
echo -e "  ${CYAN}1) 配置 WSL2 网关（Windows 访问）:${NC}"
echo -e "     bash ${GATEWAY_SCRIPT}"
echo -e "  ${CYAN}2) 启动 Triad 服务:${NC}"
echo -e "     cd ${SCRIPT_DIR} && docker compose up -d"
echo -e "  ${CYAN}3) 查看服务状态:${NC}"
echo -e "     docker compose ps"
echo -e "  ${CYAN}4) 查看日志:${NC}"
echo -e "     docker compose logs -f"
echo ""
echo -e "${BOLD}已知陷阱速查:${NC}"
echo -e "  • ${YELLOW}NTFS 跨界挂载${NC}: 已检查（${FS_TYPE}）"
echo -e "  • ${YELLOW}Hyper-V 子网冲突${NC}: 已检查（${TRIAD_SUBNET}）"
echo -e "  • ${YELLOW}Docker Socket${NC}: 已检测（${DOCKER_SOCKET_TYPE}）"
echo -e "  • ${YELLOW}GPU 透传${NC}: ${GPU_AVAILABLE} (${GPU_MODEL})"
echo ""
echo -e "${GREEN}${BOLD}==============================================${NC}"
echo ""

# 如果无 GPU，额外醒目提示
if [[ "$GPU_AVAILABLE" == false ]]; then
    echo -e "${YELLOW}${BOLD}⚠️  WARNING: CPU-ONLY MODE ⚠️${NC}"
    echo -e "${YELLOW}大语言模型推理将极慢。建议配置 NVIDIA GPU 后重新运行 init.sh${NC}"
    echo ""
fi

log_info "init.sh 执行完毕，退出码: 0"
exit 0
