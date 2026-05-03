#!/bin/bash

###############################################################################
# Triad Manager - 三层 AI Agent 系统一键部署与启停脚本
# Version: 1.0.0
# Author: Triad Engineering Team
# Date: 2025-01-20
#
# 硬件配置:
#   - 双路 E5-2673v3 (24C/48T)
#   - 魔改 2080Ti 22GB
#   - WSL2 Ubuntu 22.04
#
# 网络环境:
#   - 河南节点，国内网络
#   - Docker Hub / npm / HuggingFace 使用国内镜像源
#
# Usage:
#   ./triad_manager.sh install   # 一键安装环境
#   ./triad_manager.sh start     # 一键启动全站
#   ./triad_manager.sh stop      # 一键停止
#   ./triad_manager.sh status    # 状态查看
#   ./triad_manager.sh restart   # 重启
#   ./triad_manager.sh logs      # 查看日志
###############################################################################

set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

###############################################################################
# 全局常量
###############################################################################

readonly SCRIPT_VERSION="1.0.0"
readonly SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

readonly TRIAD_ROOT="${HOME}/.triad"
readonly TRIAD_APPS="${TRIAD_ROOT}/apps"
readonly TRIAD_MODELS="${TRIAD_ROOT}/models"
readonly TRIAD_LOGS="${TRIAD_ROOT}/logs"
readonly TRIAD_VENVS="${TRIAD_ROOT}/venvs"
readonly TRIAD_CONFIG="${TRIAD_ROOT}/.env"

readonly COMFYUI_DIR="${TRIAD_APPS}/comfyui"
readonly COMFYUI_VENV="${TRIAD_VENVS}/comfyui"
readonly COMFYUI_LOG="${TRIAD_LOGS}/comfyui.log"

readonly LLAMA_MODEL_NAME="qwen-14b-chat-q4_k_m.gguf"
readonly LLAMA_MODEL_URL="https://hf-mirror.com/Qwen/Qwen-14B-Chat-GGUF/resolve/main/${LLAMA_MODEL_NAME}"
readonly LLAMA_MODEL_PATH="${TRIAD_MODELS}/${LLAMA_MODEL_NAME}"

readonly HF_ENDPOINT_URL="https://hf-mirror.com"
readonly NPM_REGISTRY="https://registry.npmmirror.com"
readonly UBUNTU_MIRROR="mirrors.tuna.tsinghua.edu.cn"

###############################################################################
# 颜色输出
###############################################################################

readonly C_RED='\033[0;31m'
readonly C_GREEN='\033[0;32m'
readonly C_YELLOW='\033[1;33m'
readonly C_BLUE='\033[0;34m'
readonly C_CYAN='\033[0;36m'
readonly C_PURPLE='\033[0;35m'
readonly C_BOLD='\033[1m'
readonly C_RESET='\033[0m'

info()    { echo -e "${C_BLUE}[INFO]${C_RESET}  $*"; }
success() { echo -e "${C_GREEN}[OK]${C_RESET}    $*"; }
warn()    { echo -e "${C_YELLOW}[WARN]${C_RESET}  $*" >&2; }
error()   { echo -e "${C_RED}[ERROR]${C_RESET} $*" >&2; }
fatal()   { echo -e "${C_RED}[FATAL]${C_RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${C_BOLD}${C_CYAN}▶ $*${C_RESET}"; }
detail()  { echo -e "${C_PURPLE}  → $*${C_RESET}"; }

###############################################################################
# 错误处理与信号捕获
###############################################################################

cleanup_on_error() {
    local exit_code=$?
    local line_no=$1
    if [[ $exit_code -ne 0 ]]; then
        error "脚本在第 ${line_no} 行失败，退出码: ${exit_code}"
        echo ""
        warn "修复建议:"
        warn "  1. 检查上方错误信息"
        warn "  2. 确认 Docker 服务运行:  sudo systemctl status docker"
        warn "  3. 确认 NVIDIA 驱动:    nvidia-smi"
        warn "  4. 查看日志:             ./${SCRIPT_NAME} logs all"
        warn "  5. 重置环境:             ./${SCRIPT_NAME} install (会保留已下载的模型)"
        echo ""
        exit "${exit_code}"
    fi
}
trap 'cleanup_on_error $LINENO' ERR

cleanup_on_exit() {
    local exit_code=$?
    # 正常退出不做额外处理
    exit "${exit_code}"
}
trap cleanup_on_exit EXIT

###############################################################################
# 工具函数
###############################################################################

cmd_exists() {
    command -v "$1" &>/dev/null
}

require_cmd() {
    local cmd=$1
    local install_hint=${2:-"请安装 ${cmd}"}
    if ! cmd_exists "${cmd}"; then
        fatal "缺少必需命令: ${cmd}\n修复: ${install_hint}"
    fi
}

require_cmds() {
    for cmd in "$@"; do
        require_cmd "${cmd}"
    done
}

dir_exists_or_create() {
    local dir=$1
    if [[ ! -d "${dir}" ]]; then
        detail "创建目录: ${dir}"
        mkdir -p "${dir}" || fatal "无法创建目录: ${dir}"
    fi
}

backup_file() {
    local file=$1
    if [[ -f "${file}" ]]; then
        local backup="${file}.backup.$(date +%Y%m%d_%H%M%S)"
        cp "${file}" "${backup}" || warn "无法备份 ${file}"
        detail "已备份: ${backup}"
    fi
}

wait_for_url() {
    local url=$1
    local max_wait=${2:-30}
    local interval=${3:-2}
    local waited=0

    while ! curl -s --max-time 5 "${url}" &>/dev/null; do
        sleep "${interval}"
        waited=$((waited + interval))
        if [[ ${waited} -ge ${max_wait} ]]; then
            return 1
        fi
    done
    return 0
}

ask_yes_no() {
    local prompt=$1
    local default=${2:-"n"}
    local answer

    while true; do
        if [[ "${default}" == "y" ]]; then
            read -rp "${prompt} [Y/n] " answer
            answer=${answer:-Y}
        else
            read -rp "${prompt} [y/N] " answer
            answer=${answer:-N}
        fi

        case "${answer}" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) warn "请输入 y 或 n" ;;
        esac
    done
}

###############################################################################
# 环境检测函数
###############################################################################

detect_wsl2() {
    if [[ -d "/mnt/wslg" ]] || [[ -f "/proc/sys/fs/binfmt_misc/WSLInterop" ]] || \
       grep -qi microsoft /proc/version 2>/dev/null; then
        echo "WSL2"
        return 0
    fi
    if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
        echo "WSL2"
        return 0
    fi
    echo "native"
    return 1
}

detect_docker() {
    if ! cmd_exists docker; then
        fatal "Docker 未安装\n修复: https://docs.docker.com/engine/install/ubuntu/"
    fi
    if ! docker info &>/dev/null; then
        fatal "Docker 守护进程未运行\n修复: sudo systemctl start docker && sudo systemctl enable docker"
    fi
    success "Docker 运行正常"
}

detect_nvidia_docker() {
    if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q "nvidia"; then
        warn "NVIDIA Docker Runtime 未配置"
        warn "修复: "
        warn "  1. 安装 nvidia-docker2:"
        warn "     distribution=\$(. /etc/os-release;echo \$ID\$VERSION_ID)"
        warn "     curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -"
        warn "     curl -s -L https://nvidia.github.io/nvidia-docker/\$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list"
        warn "     sudo apt update && sudo apt install -y nvidia-docker2"
        warn "  2. 重启 Docker: sudo systemctl restart docker"
        return 1
    fi
    success "NVIDIA Docker Runtime 已配置"
}

detect_nvidia_driver() {
    if ! cmd_exists nvidia-smi; then
        fatal "nvidia-smi 未找到，NVIDIA 驱动可能未安装\n修复: 在 Windows 主机上安装/更新 NVIDIA GPU 驱动"
    fi
    if ! nvidia-smi &>/dev/null; then
        fatal "nvidia-smi 无法连接至 NVIDIA 驱动\n修复: 在 Windows 主机上 wsl --shutdown 后重新启动 WSL2"
    fi
    local gpu_name
    gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    success "检测到 GPU: ${C_BOLD}${gpu_name}${C_RESET}"
}

detect_gpu_memory() {
    local mem_total
    mem_total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')
    echo "${mem_total}"
}

detect_cpu_cores() {
    nproc
}

detect_network() {
    # 检测是否为国内网络
    local ip_country
    ip_country=$(curl -s --max-time 5 "https://ipinfo.io/country" 2>/dev/null || echo "")
    if [[ "${ip_country}" == "CN" ]] || [[ -z "${ip_country}" ]]; then
        echo "cn"
    else
        echo "global"
    fi
}

###############################################################################
# ext4 安全检查
###############################################################################

check_filesystem() {
    step "检查文件系统"

    dir_exists_or_create "${TRIAD_ROOT}"

    local fs_type
    fs_type=$(df -T "${TRIAD_ROOT}" | awk 'NR==2 {print $2}')
    detail "文件系统类型: ${fs_type}"

    if [[ "${fs_type}" != "ext4" ]]; then
        warn "推荐在 ext4 文件系统上运行以获得最佳性能"
        warn "当前文件系统: ${fs_type}"
        if ! ask_yes_no "是否继续?"; then
            fatal "用户中止"
        fi
    fi

    case "${TRIAD_ROOT}" in
        /mnt/*)
            fatal "禁止将 Triad 根目录放在 /mnt/ 下（NTFS 跨界挂载）\n修复: TRIAD_ROOT 必须指向 WSL2 原生 ext4 分区，例如 ~/"
            ;;
    esac

    success "文件系统检查通过"
}

###############################################################################
# 国内镜像源配置
###############################################################################

configure_mirrors() {
    step "配置国内镜像源"

    local net_type
    net_type=$(detect_network)

    if [[ "${net_type}" == "global" ]]; then
        info "检测到非国内网络，跳过镜像源配置"
        return 0
    fi

    info "检测到国内网络，配置镜像源..."

    # --- Ubuntu apt 清华源 ---
    if [[ -f /etc/apt/sources.list ]]; then
        detail "配置 Ubuntu apt 清华源..."
        backup_file /etc/apt/sources.list
        sudo sed -i "s/archive.ubuntu.com/${UBUNTU_MIRROR}/g" /etc/apt/sources.list || warn "sed archive.ubuntu.com 失败"
        sudo sed -i "s/security.ubuntu.com/${UBUNTU_MIRROR}/g" /etc/apt/sources.list || warn "sed security.ubuntu.com 失败"
        success "apt 源已更新"
    fi

    # --- Docker 阿里云镜像加速 ---
    detail "配置 Docker 阿里云镜像加速..."
    local docker_daemon="/etc/docker/daemon.json"
    sudo mkdir -p /etc/docker

    # 注意：用户需要替换 <your_id> 为实际的阿里云加速器 ID
    local aliyun_mirror="https://docker.mirrors.ustc.edu.cn"
    # 备选：阿里云加速器需要用户自己注册获取 ID
    # aliyun_mirror="https://<your_id>.mirror.aliyuncs.com"

    if [[ -f "${docker_daemon}" ]]; then
        backup_file "${docker_daemon}"
    fi

    sudo tee "${docker_daemon}" <<EOF
{
  "registry-mirrors": [
    "${aliyun_mirror}",
    "https://docker.mirrors.ustc.edu.cn",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ],
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  }
}
EOF

    # 尝试重启 Docker，但 WSL2 中 systemctl 可能不可用
    if cmd_exists systemctl && systemctl is-active --quiet docker 2>/dev/null; then
        detail "重启 Docker 服务..."
        sudo systemctl restart docker || warn "systemctl restart docker 失败"
    elif cmd_exists service && service docker status &>/dev/null; then
        detail "重启 Docker 服务 (service)..."
        sudo service docker restart || warn "service docker restart 失败"
    else
        warn "无法自动重启 Docker，请手动执行: sudo systemctl restart docker"
    fi
    success "Docker 镜像加速已配置"

    # --- npm 淘宝源 ---
    if cmd_exists npm; then
        detail "配置 npm 淘宝镜像源..."
        npm config set registry "${NPM_REGISTRY}" || warn "npm 源配置失败"
        success "npm 源已更新"
    else
        warn "npm 未安装，跳过 npm 镜像配置"
    fi

    success "国内镜像源配置完成"
}

###############################################################################
# Docker 镜像拉取
###############################################################################

pull_docker_images() {
    step "拉取 Docker 镜像"

    detect_docker
    detect_nvidia_docker

    local compose_file="${SCRIPT_DIR}/docker-compose.hpc.yml"
    if [[ ! -f "${compose_file}" ]]; then
        warn "未找到 ${compose_file}，尝试在当前目录查找..."
        compose_file="docker-compose.hpc.yml"
        if [[ ! -f "${compose_file}" ]]; then
            # 尝试在 triad/ 子目录
            compose_file="${SCRIPT_DIR}/triad/docker-compose.hpc.yml"
            if [[ ! -f "${compose_file}" ]]; then
                warn "未找到 docker-compose.hpc.yml，跳过 Docker 镜像拉取"
                warn "请将 docker-compose.hpc.yml 放置在正确位置后重试"
                return 1
            fi
        fi
    fi

    detail "使用 compose 文件: ${compose_file}"
    docker compose -f "${compose_file}" pull || fatal "Docker 镜像拉取失败\n修复: 检查网络连接和 Docker 镜像源配置"
    success "Docker 镜像拉取完成"
}

###############################################################################
# Web UI 生产构建
###############################################################################

build_webui() {
    step "构建 Web UI"

    require_cmd npm "curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs"

    local webui_dir="${SCRIPT_DIR}/triad/webui"
    if [[ ! -d "${webui_dir}" ]]; then
        webui_dir="${SCRIPT_DIR}/webui"
        if [[ ! -d "${webui_dir}" ]]; then
            warn "未找到 webui 目录，跳过 Web UI 构建"
            warn "预期路径: ${SCRIPT_DIR}/triad/webui 或 ${SCRIPT_DIR}/webui"
            return 1
        fi
    fi

    detail "进入目录: ${webui_dir}"
    cd "${webui_dir}" || fatal "无法进入 ${webui_dir}"

    detail "安装 npm 依赖..."
    npm install || fatal "npm install 失败\n修复: npm cache clean --force && npm install"

    detail "生产构建..."
    npm run build || fatal "npm run build 失败"

    # 复制 dist/ 到 OpenClaw 静态文件目录
    local openclaw_static="${SCRIPT_DIR}/triad/openclaw/static"
    if [[ ! -d "${openclaw_static}" ]]; then
        openclaw_static="${SCRIPT_DIR}/openclaw/static"
    fi
    if [[ -d "${openclaw_static}" ]]; then
        detail "复制 dist 到 OpenClaw 静态目录..."
        rm -rf "${openclaw_static}/dist"
        cp -r dist "${openclaw_static}/" || warn "复制 dist 到 ${openclaw_static} 失败"
    else
        warn "未找到 OpenClaw 静态目录，dist 保留在 ${webui_dir}/dist"
    fi

    success "Web UI 构建完成"
}

###############################################################################
# GGUF 模型下载
###############################################################################

download_model() {
    step "下载 GGUF 模型"

    dir_exists_or_create "${TRIAD_MODELS}"

    if [[ -f "${LLAMA_MODEL_PATH}" ]]; then
        local file_size
        file_size=$(du -h "${LLAMA_MODEL_PATH}" | cut -f1)
        success "模型已存在: ${LLAMA_MODEL_PATH} (大小: ${file_size})"
        return 0
    fi

    info "目标模型: ${LLAMA_MODEL_NAME}"
    info "保存路径: ${LLAMA_MODEL_PATH}"

    export HF_ENDPOINT="${HF_ENDPOINT_URL}"

    # 尝试使用 huggingface-cli
    if cmd_exists huggingface-cli || pip install huggingface-hub 2>/dev/null; then
        detail "使用 huggingface-cli 下载..."
        if huggingface-cli download \
            "Qwen/Qwen-14B-Chat-GGUF" \
            "${LLAMA_MODEL_NAME}" \
            --local-dir "${TRIAD_MODELS}" \
            --local-dir-use-symlinks False 2>/dev/null; then
            success "模型下载完成 (huggingface-cli)"
            return 0
        else
            warn "huggingface-cli 下载失败，尝试备选方案..."
        fi
    fi

    # 备选方案 1: wget
    if cmd_exists wget; then
        detail "使用 wget 从 hf-mirror.com 下载..."
        if wget --show-progress \
            "${LLAMA_MODEL_URL}" \
            -O "${LLAMA_MODEL_PATH}.tmp" 2>/dev/null; then
            mv "${LLAMA_MODEL_PATH}.tmp" "${LLAMA_MODEL_PATH}"
            success "模型下载完成 (wget)"
            return 0
        else
            rm -f "${LLAMA_MODEL_PATH}.tmp"
            warn "wget 下载失败"
        fi
    fi

    # 备选方案 2: aria2c (支持断点续传)
    if cmd_exists aria2c; then
        detail "使用 aria2c 从 hf-mirror.com 下载（支持断点续传）..."
        if aria2c -x 4 -s 4 \
            "${LLAMA_MODEL_URL}" \
            -d "${TRIAD_MODELS}" \
            -o "${LLAMA_MODEL_NAME}" 2>/dev/null; then
            success "模型下载完成 (aria2c)"
            return 0
        else
            warn "aria2c 下载失败"
        fi
    fi

    # 备选方案 3: curl
    if cmd_exists curl; then
        detail "使用 curl 从 hf-mirror.com 下载..."
        if curl -L --progress-bar \
            "${LLAMA_MODEL_URL}" \
            -o "${LLAMA_MODEL_PATH}.tmp" 2>/dev/null; then
            mv "${LLAMA_MODEL_PATH}.tmp" "${LLAMA_MODEL_PATH}"
            success "模型下载完成 (curl)"
            return 0
        else
            rm -f "${LLAMA_MODEL_PATH}.tmp"
            warn "curl 下载失败"
        fi
    fi

    # 全部失败
    fatal "模型下载全部失败\n\n手动下载指引:\n  wget ${LLAMA_MODEL_URL} -O ${LLAMA_MODEL_PATH}\n\n或:\n  aria2c -x 4 -s 4 ${LLAMA_MODEL_URL} -d ${TRIAD_MODELS} -o ${LLAMA_MODEL_NAME}\n\n或:\n  curl -L ${LLAMA_MODEL_URL} -o ${LLAMA_MODEL_PATH}"
}

###############################################################################
# ComfyUI 安装
###############################################################################

install_comfyui() {
    step "安装 ComfyUI"

    local comfyui_script="${SCRIPT_DIR}/scripts/install_comfyui.sh"
    if [[ ! -f "${comfyui_script}" ]]; then
        comfyui_script="${SCRIPT_DIR}/install_comfyui.sh"
        if [[ ! -f "${comfyui_script}" ]]; then
            warn "未找到 install_comfyui.sh，跳过 ComfyUI 安装"
            warn "预期路径: ${SCRIPT_DIR}/scripts/install_comfyui.sh"
            return 1
        fi
    fi

    detail "执行安装脚本: ${comfyui_script}"
    bash "${comfyui_script}" || fatal "ComfyUI 安装失败\n修复: 检查 install_comfyui.sh 日志输出"
    success "ComfyUI 安装完成"
}

###############################################################################
# 生成 .env 文件
###############################################################################

generate_env() {
    step "生成环境配置文件"

    local gpu_mem
    gpu_mem=$(detect_gpu_memory)
    local cpu_cores
    cpu_cores=$(detect_cpu_cores)
    local uid gid
    uid=$(id -u)
    gid=$(id -g)

    dir_exists_or_create "${TRIAD_ROOT}"

    backup_file "${TRIAD_CONFIG}"

    cat > "${TRIAD_CONFIG}" <<EOF
# Triad 环境配置文件
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')
# 脚本版本: ${SCRIPT_VERSION}

# 用户配置
UID=${uid}
GID=${gid}

# 硬件配置
GPU_MEMORY=${gpu_mem}
CPU_CORES=${cpu_cores}

# 路径配置
TRIAD_ROOT=${TRIAD_ROOT}
LLAMA_MODEL_PATH=${LLAMA_MODEL_PATH}

# 服务配置
COMFYUI_HOST=host.docker.internal
COMFYUI_PORT=18188

# 网络配置
HF_ENDPOINT=${HF_ENDPOINT_URL}

# Docker 配置
DOCKER_COMPOSE_FILE=${SCRIPT_DIR}/docker-compose.hpc.yml

# 日志配置
LOG_DIR=${TRIAD_LOGS}
EOF

    success "环境配置已写入: ${TRIAD_CONFIG}"
    detail "GPU 显存: ${gpu_mem} MiB"
    detail "CPU 核心: ${cpu_cores}"
    detail "用户 UID: ${uid}, GID: ${gid}"
}

###############################################################################
# 安装完成提示
###############################################################################

print_install_complete() {
    local gpu_mem
    gpu_mem=$(detect_gpu_memory)

    echo ""
    echo -e "${C_GREEN}${C_BOLD}╔═══════════════════════════════════════════════════════════╗${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}║                 Triad 环境安装完成                        ║${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}╠═══════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_GREEN}║  模型路径: ${LLAMA_MODEL_PATH}${C_RESET}"
    echo -e "${C_GREEN}║  配置文件: ${TRIAD_CONFIG}${C_RESET}"
    echo -e "${C_GREEN}║  ComfyUI:  ${COMFYUI_DIR}${C_RESET}"
    echo -e "${C_GREEN}║  日志目录: ${TRIAD_LOGS}${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}╠═══════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_GREEN}║  服务地址:                                               ║${C_RESET}"
    echo -e "${C_GREEN}║    Web UI:      http://localhost:8080/panel               ║${C_RESET}"
    echo -e "${C_GREEN}║    Gateway:     ws://localhost:8080/ws/tasks            ║${C_RESET}"
    echo -e "${C_GREEN}║    llama-srv:  http://localhost:18000/v1/chat/completions║${C_RESET}"
    echo -e "${C_GREEN}║    ComfyUI:    http://localhost:18188                   ║${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}╠═══════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_GREEN}║  硬件信息:                                               ║${C_RESET}"
    echo -e "${C_GREEN}║    GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1 | sed 's/^[[:space:]]*//')${C_RESET}"
    echo -e "${C_GREEN}║    VRAM: ${gpu_mem} MiB${C_RESET}"
    echo -e "${C_GREEN}║    CPU: $(detect_cpu_cores) 核心${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}╠═══════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_GREEN}║  下一步: ./${SCRIPT_NAME} start                           ║${C_RESET}"
    echo -e "${C_GREEN}${C_BOLD}╚═══════════════════════════════════════════════════════════╝${C_RESET}"
    echo ""
}

###############################################################################
# INSTALL 命令
###############################################################################

cmd_install() {
    echo -e "${C_BOLD}${C_PURPLE}"
    echo "  ████████╗██████╗ ██╗ █████╗ ██████╗ "
    echo "  ╚══██╔══╝██╔══██╗██║██╔══██╗██╔══██╗"
    echo "     ██║   ██████╔╝██║███████║██║  ██║"
    echo "     ██║   ██╔══██╗██║██╔══██║██║  ██║"
    echo "     ██║   ██║  ██║██║██║  ██║██████╔╝"
    echo "     ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚═════╝ "
    echo -e "  三层 AI Agent 系统 - 一键安装${C_RESET}"
    echo ""

    info "脚本版本: ${SCRIPT_VERSION}"
    info "Triad 根目录: ${TRIAD_ROOT}"

    local wsl_type
    wsl_type=$(detect_wsl2)
    if [[ "${wsl_type}" == "WSL2" ]]; then
        success "检测到 WSL2 环境"
    else
        warn "未检测到 WSL2，确认在原生 Linux 上运行"
    fi

    # 预检查
    require_cmds curl grep awk sed sudo tee mkdir cp rm mv chmod date du
    detect_nvidia_driver

    # 执行安装步骤
    check_filesystem
    configure_mirrors
    pull_docker_images
    build_webui
    download_model
    install_comfyui
    generate_env

    print_install_complete
}

###############################################################################
# START 命令
###############################################################################

cmd_start() {
    echo -e "${C_BOLD}${C_PURPLE}"
    echo "  ████████╗██████╗ ██╗ █████╗ ██████╗ "
    echo "  ╚══██╔══╝██╔══██╗██║██╔══██╗██╔══██╗"
    echo "     ██║   ██████╔╝██║███████║██║  ██║"
    echo "     ██║   ██╔══██╗██║██╔══██║██║  ██║"
    echo "     ██║   ██║  ██║██║██║  ██║██████╔╝"
    echo "     ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚═════╝ "
    echo -e "  三层 AI Agent 系统 - 一键启动${C_RESET}"
    echo ""

    # 环境检查
    detect_docker
    detect_nvidia_driver

    if [[ ! -f "${TRIAD_CONFIG}" ]]; then
        fatal "未找到 ${TRIAD_CONFIG}\n修复: 先运行 ./${SCRIPT_NAME} install"
    fi

    # 加载 .env
    set -a
    # shellcheck source=/dev/null
    source "${TRIAD_CONFIG}"
    set +a

    # 创建日志目录
    dir_exists_or_create "${TRIAD_LOGS}"

    # --- 1. 检查并启动 ComfyUI ---
    step "检查 ComfyUI 状态"
    local comfyui_ready=false
    if curl -s --max-time 3 "http://localhost:18188/system_stats" &>/dev/null; then
        success "ComfyUI 已在运行 (localhost:18188)"
        comfyui_ready=true
    else
        warn "ComfyUI 未启动，尝试自动拉起..."

        if [[ ! -d "${COMFYUI_DIR}" ]]; then
            fatal "未找到 ComfyUI 目录: ${COMFYUI_DIR}\n修复: ./${SCRIPT_NAME} install"
        fi

        if [[ ! -d "${COMFYUI_VENV}" ]]; then
            fatal "未找到 ComfyUI 虚拟环境: ${COMFYUI_VENV}\n修复: ./${SCRIPT_NAME} install"
        fi

        detail "激活虚拟环境: ${COMFYUI_VENV}"
        # shellcheck source=/dev/null
        source "${COMFYUI_VENV}/bin/activate" || fatal "无法激活 ComfyUI 虚拟环境"

        cd "${COMFYUI_DIR}" || fatal "无法进入 ${COMFYUI_DIR}"

        detail "启动 ComfyUI (nohup)..."
        nohup python main.py \
            --listen 0.0.0.0 \
            --port 18188 \
            > "${COMFYUI_LOG}" 2>&1 &

        local comfyui_pid=$!
        detail "ComfyUI PID: ${comfyui_pid}"

        # 等待启动
        detail "等待 ComfyUI 就绪 (最多 60 秒)..."
        local waited=0
        while ! curl -s --max-time 2 "http://localhost:18188/system_stats" &>/dev/null; do
            sleep 2
            waited=$((waited + 2))
            if ! kill -0 "${comfyui_pid}" 2>/dev/null; then
                fatal "ComfyUI 进程已退出\n日志: ${COMFYUI_LOG}\n修复: cat ${COMFYUI_LOG}"
            fi
            if [[ ${waited} -ge 60 ]]; then
                fatal "ComfyUI 启动超时 (60 秒)\n日志: ${COMFYUI_LOG}\n修复: cat ${COMFYUI_LOG}"
            fi
            echo -n "."
        done
        echo ""
        success "ComfyUI 启动成功 (PID: ${comfyui_pid})"
        comfyui_ready=true
    fi

    # --- 2. 启动 Docker 后端 ---
    step "启动 Docker 后端"

    local compose_file="${SCRIPT_DIR}/docker-compose.hpc.yml"
    if [[ ! -f "${compose_file}" ]]; then
        compose_file="${SCRIPT_DIR}/triad/docker-compose.hpc.yml"
        if [[ ! -f "${compose_file}" ]]; then
            compose_file="docker-compose.hpc.yml"
        fi
    fi

    if [[ ! -f "${compose_file}" ]]; then
        fatal "未找到 docker-compose.hpc.yml"
    fi

    detail "Compose 文件: ${compose_file}"
    detail "启动配置: hpc-full..."

    cd "$(dirname "${compose_file}")" || fatal "无法进入 compose 文件目录"

    docker compose -f "${compose_file}" --profile hpc-full up -d || \
        fatal "Docker 容器启动失败\n修复: docker compose -f ${compose_file} logs"

    success "Docker 容器已启动"

    # --- 3. 等待容器就绪 ---
    step "等待服务就绪"

    detail "等待 llama-server 就绪..."
    local llama_ready=false
    local llama_wait=0
    while [[ ${llama_wait} -lt 60 ]]; do
        if curl -s --max-time 2 "http://localhost:18000/health" &>/dev/null; then
            llama_ready=true
            break
        fi
        sleep 2
        llama_wait=$((llama_wait + 2))
        echo -n "."
    done
    echo ""

    if [[ "${llama_ready}" == true ]]; then
        success "llama-server 就绪"
    else
        warn "llama-server 健康检查未响应 (可能仍在加载模型)"
    fi

    # --- 4. WSL2 网关绑定 ---
    step "WSL2 网关绑定"

    local bridge_script="${SCRIPT_DIR}/bridge/wsl2_gateway.sh"
    if [[ ! -f "${bridge_script}" ]]; then
        bridge_script="${SCRIPT_DIR}/wsl2_gateway.sh"
    fi

    if [[ -f "${bridge_script}" ]]; then
        detail "执行: ${bridge_script} setup 8080 50051"
        bash "${bridge_script}" setup 8080 50051 || warn "WSL2 网关绑定脚本执行失败"
    else
        warn "未找到 WSL2 网关绑定脚本: ${bridge_script}"
        warn "跳过网关绑定 (WSL2 自动端口转发通常已足够)"
    fi

    # --- 5. 打印启动成功面板 ---
    print_start_panel
}

###############################################################################
# 启动成功面板
###############################################################################

print_start_panel() {
    # 获取容器状态
    local openclaw_status hermes_status llama_status qdrant_status registry_status
    openclaw_status=$(docker ps --filter "name=openclaw" --format "{{.Status}}" 2>/dev/null || echo "未找到")
    hermes_status=$(docker ps --filter "name=hermes" --format "{{.Status}}" 2>/dev/null || echo "未找到")
    llama_status=$(docker ps --filter "name=llama-server" --format "{{.Status}}" 2>/dev/null || echo "未找到")
    qdrant_status=$(docker ps --filter "name=qdrant" --format "{{.Status}}" 2>/dev/null || echo "未找到")
    registry_status=$(docker ps --filter "name=registry" --format "{{.Status}}" 2>/dev/null || echo "未找到")

    # 状态图标
    local icon_openclaw icon_hermes icon_llama icon_qdrant icon_registry
    if [[ -n "${openclaw_status}" && "${openclaw_status}" != "未找到" ]]; then
        icon_openclaw="🟢"
    else
        icon_openclaw="🔴"
    fi
    if [[ -n "${hermes_status}" && "${hermes_status}" != "未找到" ]]; then
        icon_hermes="🟢"
    else
        icon_hermes="🔴"
    fi
    if [[ -n "${llama_status}" && "${llama_status}" != "未找到" ]]; then
        icon_llama="🟢"
    else
        icon_llama="🔴"
    fi
    if [[ -n "${qdrant_status}" && "${qdrant_status}" != "未找到" ]]; then
        icon_qdrant="🟢"
    else
        icon_qdrant="🔴"
    fi
    if [[ -n "${registry_status}" && "${registry_status}" != "未找到" ]]; then
        icon_registry="🟢"
    else
        icon_registry="🔴"
    fi

    # 获取 nvidia-smi 显存信息
    local gpu_mem_total gpu_mem_used
    gpu_mem_total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ' || echo "22000")
    gpu_mem_used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ' || echo "0")

    # 估算显存分配 (简化显示)
    local llama_mem comfy_mem sys_mem free_mem
    # llama-server 大约使用模型大小的 70% 显存
    # Qwen-14B Q4_K_M 约 8.5GB，GPU offload 后约 9GB
    llama_mem=9216
    comfy_mem=2048
    sys_mem=2048
    free_mem=$((gpu_mem_total - llama_mem - comfy_mem - sys_mem))
    if [[ ${free_mem} -lt 0 ]]; then free_mem=0; fi

    local llama_bar_len=$((llama_mem * 40 / gpu_mem_total))
    local comfy_bar_len=$((comfy_mem * 40 / gpu_mem_total))
    local free_bar_len=$((free_mem * 40 / gpu_mem_total))
    local sys_bar_len=$((40 - llama_bar_len - comfy_bar_len - free_bar_len))
    if [[ ${sys_bar_len} -lt 0 ]]; then sys_bar_len=0; fi

    # 构建进度条
    local bar=""
    for ((i=0; i<comfy_bar_len; i++)); do bar+="█"; done
    for ((i=0; i<llama_bar_len; i++)); do bar+="▓"; done
    for ((i=0; i<free_bar_len; i++)); do bar+="░"; done
    for ((i=0; i<sys_bar_len; i++)); do bar+="▒"; done

    echo ""
    echo -e "${C_PURPLE}${C_BOLD}╔══════════════════════════════════════════════════════════════╗${C_RESET}"
    echo -e "${C_PURPLE}${C_BOLD}║              🟣 Triad Control Panel 启动成功               ║${C_RESET}"
    echo -e "${C_PURPLE}${C_BOLD}╠══════════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_PURPLE}║  Web UI:      http://localhost:8080/panel                   ║${C_RESET}"
    echo -e "${C_PURPLE}║  Gateway:     ws://localhost:8080/ws/tasks                  ║${C_RESET}"
    echo -e "${C_PURPLE}║  llama-srv:   http://localhost:18000/v1/chat/completions     ║${C_RESET}"
    echo -e "${C_PURPLE}║  ComfyUI:     http://localhost:18188                        ║${C_RESET}"
    echo -e "${C_PURPLE}${C_BOLD}╠══════════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_PURPLE}║  VRAM 分配 ( ${gpu_mem_total} MiB )${C_RESET}"
    echo -e "${C_PURPLE}║    [${C_CYAN}2GB Emb${C_RESET}]${C_GREEN}[${bar}]${C_RESET}"
    echo -e "${C_PURPLE}║    ${C_CYAN}█${C_RESET}=ComfyUI(${comfy_mem}MB) ${C_GREEN}▓${C_RESET}=LLM(${llama_mem}MB) ░=空闲(${free_mem}MB) ▒=系统(${sys_mem}MB)${C_RESET}"
    echo -e "${C_PURPLE}${C_BOLD}╠══════════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_PURPLE}║  Docker 容器:                                              ║${C_RESET}"
    echo -e "${C_PURPLE}║    openclaw     ${icon_openclaw} ${openclaw_status}${C_RESET}"
    echo -e "${C_PURPLE}║    hermes       ${icon_hermes} ${hermes_status}${C_RESET}"
    echo -e "${C_PURPLE}║    llama-server ${icon_llama} ${llama_status} (GPU 模式, -ngl 99)${C_RESET}"
    echo -e "${C_PURPLE}║    qdrant       ${icon_qdrant} ${qdrant_status}${C_RESET}"
    echo -e "${C_PURPLE}║    registry     ${icon_registry} ${registry_status}${C_RESET}"
    echo -e "${C_PURPLE}${C_BOLD}╚══════════════════════════════════════════════════════════════╝${C_RESET}"
    echo ""
}

###############################################################################
# STOP 命令
###############################################################################

cmd_stop() {
    step "停止 Triad 全站服务"

    # 1. 优雅停止 Docker 容器
    detail "停止 Docker 容器..."
    local compose_file="${SCRIPT_DIR}/docker-compose.hpc.yml"
    if [[ ! -f "${compose_file}" ]]; then
        compose_file="${SCRIPT_DIR}/triad/docker-compose.hpc.yml"
        if [[ ! -f "${compose_file}" ]]; then
            compose_file="docker-compose.hpc.yml"
        fi
    fi

    if [[ -f "${compose_file}" ]]; then
        cd "$(dirname "${compose_file}")" || warn "无法进入 compose 目录"
        docker compose -f "${compose_file}" --profile hpc-full down --timeout 30 || warn "docker compose down 部分失败"
        success "Docker 容器已停止"
    else
        warn "未找到 docker-compose.hpc.yml，跳过 Docker 停止"
    fi

    # 2. 停止 ComfyUI
    detail "停止 ComfyUI..."
    local comfyui_pids
    comfyui_pids=$(pgrep -f "python.*main.py.*18188" 2>/dev/null || true)
    if [[ -n "${comfyui_pids}" ]]; then
        detail "找到 ComfyUI PID: ${comfyui_pids}"
        echo "${comfyui_pids}" | while read -r pid; do
            if [[ -n "${pid}" ]]; then
                detail "发送 SIGTERM 到 PID ${pid}..."
                kill -TERM "${pid}" 2>/dev/null || true
            fi
        done

        # 等待进程退出
        local wait_count=0
        while pgrep -f "python.*main.py.*18188" &>/dev/null && [[ ${wait_count} -lt 15 ]]; do
            sleep 1
            wait_count=$((wait_count + 1))
            echo -n "."
        done
        echo ""

        if pgrep -f "python.*main.py.*18188" &>/dev/null; then
            warn "ComfyUI 未能在 15 秒内退出，发送 SIGKILL..."
            pkill -9 -f "python.*main.py.*18188" 2>/dev/null || true
        else
            success "ComfyUI 已停止"
        fi
    else
        info "ComfyUI 未在运行"
    fi

    # 3. 等待 llama-server 优雅关闭
    detail "确保 llama-server 优雅关闭..."
    local llama_pids
    llama_pids=$(docker ps --filter "name=llama-server" --format "{{.ID}}" 2>/dev/null || true)
    if [[ -n "${llama_pids}" ]]; then
        detail "等待 llama-server 写入记忆总线..."
        sleep 3
    fi

    # 4. 停止 Web UI 开发服务器
    detail "停止 Web UI 开发服务器..."
    local webui_pids
    webui_pids=$(pgrep -f "vite.*dev" 2>/dev/null || true)
    if [[ -n "${webui_pids}" ]]; then
        echo "${webui_pids}" | while read -r pid; do
            kill -TERM "${pid}" 2>/dev/null || true
        done
        success "Web UI 开发服务器已停止"
    else
        info "Web UI 开发服务器未在运行"
    fi

    success "Triad 全站已停止"
}

###############################################################################
# STATUS 命令
###############################################################################

cmd_status() {
    step "Triad 系统状态"

    # 1. Docker 容器状态
    echo ""
    echo -e "${C_BOLD}${C_BLUE}📦 Docker 容器状态${C_RESET}"
    echo -e "${C_BLUE}─────────────────────────────${C_RESET}"
    if docker info &>/dev/null; then
        docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "(openclaw|hermes|llama|qdrant|registry|NAMES)" || echo "无相关容器运行"
    else
        warn "Docker 未运行"
    fi

    # 2. nvidia-smi 显存
    echo ""
    echo -e "${C_BOLD}${C_GREEN}🧠 GPU 显存 (nvidia-smi)${C_RESET}"
    echo -e "${C_GREEN}─────────────────────────────${C_RESET}"
    if cmd_exists nvidia-smi && nvidia-smi &>/dev/null; then
        local gpu_name gpu_used gpu_total
        gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1 | sed 's/^[[:space:]]*//')
        gpu_used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -n1 | tr -d ' ')
        gpu_total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')
        local used_pct
        used_pct=$((gpu_used * 100 / gpu_total))

        echo -e "GPU 0: ${C_BOLD}${gpu_name}${C_RESET}"
        echo -e "  Used: ${gpu_used}MB / ${gpu_total}MB (${used_pct}%)"
        echo ""
        echo "  Processes:"
        nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader | \
            while IFS=',' read -r pid proc_name mem; do
                if [[ -n "${pid}" ]]; then
                    echo -e "    ${proc_name}  PID ${pid}  ${mem}"
                fi
            done
    else
        warn "nvidia-smi 不可用"
    fi

    # 3. ComfyUI 状态
    echo ""
    echo -e "${C_BOLD}${C_CYAN}🎨 ComfyUI 状态${C_RESET}"
    echo -e "${C_CYAN}─────────────────────────────${C_RESET}"
    local comfyui_pid
    comfyui_pid=$(pgrep -f "python.*main.py.*18188" | head -n1 || true)
    if [[ -n "${comfyui_pid}" ]]; then
        local comfyui_cpu comfyui_mem
        comfyui_cpu=$(ps -p "${comfyui_pid}" -o %cpu= 2>/dev/null | tr -d ' ' || echo "N/A")
        comfyui_mem=$(ps -p "${comfyui_pid}" -o %mem= 2>/dev/null | tr -d ' ' || echo "N/A")
        echo -e "Host: localhost:18188  ${C_GREEN}🟢 就绪${C_RESET}"
        echo "PID: ${comfyui_pid}"
        echo "CPU: ${comfyui_cpu}%"
        echo "MEM: ${comfyui_mem}%"
    else
        echo -e "Host: localhost:18188  ${C_RED}🔴 未运行${C_RESET}"
    fi

    # 4. llama-server 状态
    echo ""
    echo -e "${C_BOLD}${C_PURPLE}🧠 llama-server 状态${C_RESET}"
    echo -e "${C_PURPLE}─────────────────────────────${C_RESET}"
    local llama_health llama_mode llama_speed
    llama_health=$(curl -s --max-time 3 "http://localhost:18000/health" -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")

    if [[ "${llama_health}" == "200" ]]; then
        echo -e "Health: ${C_GREEN}🟢 /health 返回 200${C_RESET}"
        # 尝试获取更多信息
        local props
        props=$(curl -s --max-time 3 "http://localhost:18000/props" 2>/dev/null || echo "")
        if echo "${props}" | grep -q "cuda"; then
            llama_mode="GPU (-ngl 99)"
        else
            llama_mode="CPU"
        fi
        echo "Mode: ${llama_mode}"
    else
        # 尝试 8080 端口
        llama_health=$(curl -s --max-time 3 "http://localhost:18000/health" -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
        if [[ "${llama_health}" == "200" ]]; then
            echo -e "Health: ${C_GREEN}🟢 /health 返回 200 (端口 8080)${C_RESET}"
            echo "Mode: GPU (-ngl 99)"
        else
            echo -e "Health: ${C_RED}🔴 /health 无响应 (HTTP ${llama_health})${C_RESET}"
        fi
    fi

    # 5. Web UI 状态
    echo ""
    echo -e "${C_BOLD}${C_YELLOW}🌐 Web UI${C_RESET}"
    echo -e "${C_YELLOW}─────────────────────────────${C_RESET}"
    local webui_status
    webui_status=$(curl -s --max-time 3 -o /dev/null -w "%{http_code}" "http://localhost:8080/panel" 2>/dev/null || echo "000")
    if [[ "${webui_status}" == "200" || "${webui_status}" == "301" || "${webui_status}" == "302" ]]; then
        echo -e "Status: ${C_GREEN}🟢 http://localhost:8080/panel${C_RESET}"
    else
        echo -e "Status: ${C_RED}🔴 http://localhost:8080/panel (HTTP ${webui_status})${C_RESET}"
    fi

    # 6. 系统负载
    echo ""
    echo -e "${C_BOLD}${C_BLUE}📊 系统负载${C_RESET}"
    echo -e "${C_BLUE}─────────────────────────────${C_RESET}"
    local loadavg
    loadavg=$(cat /proc/loadavg | awk '{print $1, $2, $3}')
    echo "Load average: ${loadavg}"
    if cmd_exists free; then
        local mem_info
        mem_info=$(free -h | grep "Mem:" | awk '{print "Total: " $2, "Used: " $3, "Free: " $4}')
        echo "Memory: ${mem_info}"
    fi

    echo ""
}

###############################################################################
# RESTART 命令
###############################################################################

cmd_restart() {
    step "重启 Triad"
    cmd_stop
    echo ""
    sleep 2
    cmd_start
}

###############################################################################
# LOGS 命令
###############################################################################

cmd_logs() {
    local service=${1:-"all"}

    case "${service}" in
        openclaw)
            step "OpenClaw 日志"
            docker logs --tail 100 -f "openclaw" 2>/dev/null || warn "OpenClaw 容器未运行"
            ;;
        hermes)
            step "Hermes 日志"
            docker logs --tail 100 -f "hermes" 2>/dev/null || warn "Hermes 容器未运行"
            ;;
        llama)
            step "llama-server 日志"
            docker logs --tail 100 -f "llama-server" 2>/dev/null || warn "llama-server 容器未运行"
            ;;
        comfyui)
            step "ComfyUI 日志"
            if [[ -f "${COMFYUI_LOG}" ]]; then
                tail -n 100 -f "${COMFYUI_LOG}"
            else
                warn "未找到 ComfyUI 日志: ${COMFYUI_LOG}"
            fi
            ;;
        all|*)
            step "所有服务日志"
            echo -e "${C_BOLD}${C_BLUE}─── Docker 容器日志 (最近 50 行) ───${C_RESET}"
            local compose_file="${SCRIPT_DIR}/docker-compose.hpc.yml"
            if [[ ! -f "${compose_file}" ]]; then
                compose_file="${SCRIPT_DIR}/triad/docker-compose.hpc.yml"
                if [[ ! -f "${compose_file}" ]]; then
                    compose_file="docker-compose.hpc.yml"
                fi
            fi
            if [[ -f "${compose_file}" ]]; then
                cd "$(dirname "${compose_file}")" || true
                docker compose -f "${compose_file}" logs --tail 50 2>/dev/null || warn "Docker compose logs 失败"
            fi
            echo ""
            echo -e "${C_BOLD}${C_CYAN}─── ComfyUI 日志 (最近 50 行) ───${C_RESET}"
            if [[ -f "${COMFYUI_LOG}" ]]; then
                tail -n 50 "${COMFYUI_LOG}"
            else
                warn "未找到 ComfyUI 日志"
            fi
            ;;
    esac
}

###############################################################################
# 帮助信息
###############################################################################

usage() {
    cat <<EOF

${C_BOLD}${C_PURPLE}Triad Manager v${SCRIPT_VERSION}${C_RESET}
三层 AI Agent 系统一键部署与启停工具

${C_BOLD}用法:${C_RESET}
  ./${SCRIPT_NAME} [command] [options]

${C_BOLD}命令:${C_RESET}
  ${C_GREEN}install${C_RESET}   一键安装环境（首次部署）
  ${C_GREEN}start${C_RESET}     一键启动全站服务
  ${C_GREEN}stop${C_RESET}      一键停止全站服务
  ${C_GREEN}status${C_RESET}    查看系统状态
  ${C_GREEN}restart${C_RESET}   重启全站服务
  ${C_GREEN}logs${C_RESET}      查看日志 [service]

${C_BOLD}logs 子命令:${C_RESET}
  ${C_CYAN}openclaw${C_RESET}   OpenClaw 容器日志
  ${C_CYAN}hermes${C_RESET}     Hermes 容器日志
  ${C_CYAN}llama${C_RESET}      llama-server 容器日志
  ${C_CYAN}comfyui${C_RESET}    ComfyUI 本地日志
  ${C_CYAN}all${C_RESET}        所有日志 (默认)

${C_BOLD}示例:${C_RESET}
  ./${SCRIPT_NAME} install            # 首次安装
  ./${SCRIPT_NAME} start              # 启动所有服务
  ./${SCRIPT_NAME} stop               # 停止所有服务
  ./${SCRIPT_NAME} status             # 查看状态
  ./${SCRIPT_NAME} restart            # 重启
  ./${SCRIPT_NAME} logs llama         # 查看 llama-server 日志
  ./${SCRIPT_NAME} logs all           # 查看所有日志

${C_BOLD}环境变量:${C_RESET}
  TRIAD_ROOT          默认: ${TRIAD_ROOT}
  HF_ENDPOINT         默认: ${HF_ENDPOINT_URL}
  NPM_REGISTRY        默认: ${NPM_REGISTRY}

${C_BOLD}国内镜像源:${C_RESET}
  Ubuntu apt:  清华源 (${UBUNTU_MIRROR})
  Docker:      中科大 / 网易云 / 百度云 镜像加速
  npm:         淘宝源 (${NPM_REGISTRY})
  HuggingFace: hf-mirror.com

${C_BOLD}硬件要求:${C_RESET}
  - WSL2 Ubuntu 22.04+
  - NVIDIA GPU (CUDA 12+)
  - ext4 文件系统 (推荐)
  - Docker + NVIDIA Container Runtime

${C_BOLD}故障排除:${C_RESET}
  1. Docker 未运行:   sudo systemctl start docker
  2. NVIDIA 驱动:     wsl --shutdown (Windows PowerShell)
  3. 显存不足:        调低 ngl 层数或换用更小模型
  4. 模型下载失败:    wget ${LLAMA_MODEL_URL}
  5. ComfyUI 失败:    cat ${COMFYUI_LOG}

EOF
}

###############################################################################
# 主入口
###############################################################################

main() {
    # 无参数时显示帮助
    if [[ $# -eq 0 ]]; then
        usage
        exit 1
    fi

    local command=$1
    shift || true

    case "${command}" in
        install)
            cmd_install
            ;;
        start)
            cmd_start
            ;;
        stop)
            cmd_stop
            ;;
        status)
            cmd_status
            ;;
        restart)
            cmd_restart
            ;;
        logs)
            cmd_logs "${1:-all}"
            ;;
        help|--help|-h)
            usage
            exit 0
            ;;
        *)
            error "未知命令: ${command}"
            usage
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"
