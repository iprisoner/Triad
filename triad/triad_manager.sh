#!/bin/bash
###############################################################################
# triad_manager.sh v2.2
# 一键部署脚本：原生 Ubuntu 22.04 (WSL2) + 单卡魔改 RTX 2080Ti 22GB
# 功能：安装 / 启动 / 停止 / 状态 / 日志 / 更新
# 作者：Triad Dev Team
# 日期：2025-01
###############################################################################

# 严格模式与全局配置
set -euo pipefail
IFS=$'\n\t'

# 脚本元信息
readonly SCRIPT_VERSION="2.2"
readonly SCRIPT_NAME="triad_manager.sh"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly TRIAD_ROOT="${SCRIPT_DIR}"

# 数据盘挂载点（双 NVMe 优化：模型与大数据放数据盘）
readonly DATA_DISK="/mnt/data"
readonly USE_DATA_DISK=1

# 服务端口配置（全部大端口号 +10000）
readonly OPENCLAW_PORT=18080
readonly HERMES_PORT=18000
readonly LLAMA_PORT=18000
readonly QDRANT_HTTP_PORT=16333
readonly QDRANT_GRPC_PORT=16334
readonly EMBEDDING_API_PORT=19000
readonly MCP_SERVER_PORT=18500
readonly BIND_ADDRESS="0.0.0.0"

# 模型配置（用户自行管理模型文件，不在脚本中硬编码路径）
readonly MODEL_NAME="Qwen3.6-27B"
# 启动 llama-server 时请使用 -m 参数指定你的模型路径
# 例如：llama-server -m /mnt/f/AI_Models/Qwen3.6-27b.gguf --host 0.0.0.0 --port 18000 -ngl 999 ...
readonly LLAMA_CPP_REPO="https://ghproxy.com/https://github.com/ggerganov/llama.cpp.git"

# 国内镜像源配置
readonly PIP_INDEX_URL="https://mirrors.aliyun.com/pypi/simple/"
readonly PIP_TRUSTED_HOST="mirrors.aliyun.com"
readonly NPM_REGISTRY="https://registry.npmmirror.com"

# 安装标记文件
readonly INSTALL_FLAG="${TRIAD_ROOT}/.triad_installed"
readonly ENV_FILE="${TRIAD_ROOT}/.env"

# 日志配置
readonly LOG_DIR="${TRIAD_ROOT}/logs"
readonly INSTALL_LOG="${LOG_DIR}/install.log"
readonly RUN_LOG="${LOG_DIR}/runtime.log"

# 颜色常量与输出函数
# 颜色定义
readonly C_RESET='\033[0m'
readonly C_RED='\033[0;31m'
readonly C_GREEN='\033[0;32m'
readonly C_YELLOW='\033[0;33m'
readonly C_BLUE='\033[0;34m'
readonly C_CYAN='\033[0;36m'
readonly C_BOLD='\033[1m'

# 输出函数：信息
info() { echo -e "${C_BLUE}[INFO]${C_RESET} $1"; }

# 输出函数：成功
ok() { echo -e "${C_GREEN}[OK]${C_RESET} $1"; }

# 输出函数：警告
warn() { echo -e "${C_YELLOW}[WARN]${C_RESET} $1"; }

# 输出函数：错误
err() { echo -e "${C_RED}[ERROR]${C_RESET} $1" >&2; }

# 输出函数：步骤标题
step() {
    local num="$1"
    local title="$2"
    echo -e "${C_BOLD}${C_CYAN} 步骤 ${num}: ${title}${C_RESET}"
}

# 输出函数：分隔线
line() {
echo -e "${C_CYAN}---${C_RESET}"
}

# 输出函数：高亮文本
highlight() { echo -e "${C_BOLD}${C_YELLOW}$1${C_RESET}"; }

# 日志与错误处理
# 确保日志目录存在
init_log_dir() { [[ -d "${LOG_DIR}" ]] || mkdir -p "${LOG_DIR}"; }

# 日志记录函数（同时输出到控制台和日志文件）
log_write() {
    local level="$1"
    local msg="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[${timestamp}] [${level}] ${msg}" >> "${INSTALL_LOG}"
}

# 致命错误处理：停止并报告
fatal() {
    local msg="$1"
    local code="${2:-1}"
    err "${msg}"
    log_write "FATAL" "${msg}"
    echo -e "${C_RED}安装中断，请检查上述错误信息后重试。${C_RESET}"
    exit "${code}"
}

# 命令执行包装器：失败时自动退出
run_cmd() {
    local desc="$1"
    shift
    info "执行: ${desc} ..."
    log_write "EXEC" "${desc}: $*"
    if "$@"; then
        ok "${desc} 完成"
        log_write "OK" "${desc}"
        return 0
    else
        fatal "${desc} 失败 (命令: $*)"
    fi
}

# 检查命令是否存在
check_cmd() {
    local cmd="$1"
    local pkg="${2:-${cmd}}"
    if ! command -v "${cmd}" &>/dev/null; then
        warn "命令 ${cmd} 未找到，将尝试安装 ${pkg} ..."
        return 1
    fi
    return 0
}

# 检查目录或创建
check_or_mkdir() { local dir="$1"; if [[ ! -d "${dir}" ]]; then info "创建目录: ${dir}"; mkdir -p "${dir}" || fatal "无法创建目录: ${dir}"; fi; }

# 检查文件是否存在
file_exists() { [[ -f "$1" ]]; }

# 获取数据盘路径（如果不存在则使用脚本目录）
get_data_path() {
    local subpath="$1"
    if [[ "${USE_DATA_DISK}" -eq 1 ]] && [[ -d "${DATA_DISK}" ]]; then
        echo "${DATA_DISK}/triad/${subpath}"
    else
        echo "${TRIAD_ROOT}/${subpath}"
    fi
}

# 系统检查函数
# 检查是否在 WSL2 环境中
check_wsl2() {
    info "检查 WSL2 环境 ..."
    if [[ ! -f /proc/sys/fs/binfmt_misc/WSLInterop ]] && [[ ! -f /run/WSLInterop ]]; then
        warn "未检测到 WSLInterop，可能不是 WSL2 环境"
    else
        ok "检测到 WSL2 环境"
    fi
    # 检查内核版本（WSL2 通常为 5.x）
    local kernel
    kernel=$(uname -r)
    info "当前内核版本: ${kernel}"
    if [[ "${kernel}" == *microsoft* ]] || [[ "${kernel}" == *Microsoft* ]]; then
        ok "确认运行在 WSL2 内核上"
    else
        warn "内核名称不包含 'microsoft'，请确认是 WSL2"
    fi
}

# 检查 Ubuntu 版本
check_ubuntu_version() {
    info "检查操作系统版本 ..."
    if [[ ! -f /etc/os-release ]]; then
        fatal "无法读取 /etc/os-release"
    fi
    source /etc/os-release
    info "检测到操作系统: ${NAME} ${VERSION_ID}"
    if [[ "${ID}" != "ubuntu" ]]; then
        warn "当前不是 Ubuntu 系统，脚本针对 Ubuntu 22.04 优化"
    fi
    if [[ "${VERSION_ID}" != "22.04" ]]; then
        warn "当前 Ubuntu 版本为 ${VERSION_ID}，建议升级到 22.04 以获得最佳兼容性"
    else
        ok "Ubuntu 22.04 确认"
    fi
}

# 检查硬件配置（CPU、内存、GPU）
check_hardware() {
    info "检查硬件配置 ..."
    # CPU 信息
    local cpu_count
    cpu_count=$(nproc)
    info "逻辑 CPU 核心数: ${cpu_count}"
    if [[ "${cpu_count}" -lt 8 ]]; then
        warn "CPU 核心数较少 (${cpu_count})，编译过程可能较慢"
    fi

    # 内存信息
    local mem_gb
    mem_gb=$(free -g | awk '/^Mem:/{print $2}')
    info "系统内存: ${mem_gb}GB"
    if [[ "${mem_gb}" -lt 32 ]]; then
        warn "内存不足 32GB，当前 ${mem_gb}GB，可能影响大模型加载"
    fi

    # GPU 信息
    info "检查 NVIDIA GPU ..."
    if ! command -v nvidia-smi &>/dev/null; then
        fatal "未找到 nvidia-smi，请先安装 NVIDIA 驱动"
    fi
    local gpu_info
    gpu_info=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || true)
    if [[ -z "${gpu_info}" ]]; then
        fatal "无法获取 GPU 信息，请检查 NVIDIA 驱动是否正确安装"
    fi
    info "检测到 GPU: ${gpu_info}"
    # 检查是否为 2080Ti 22GB
    if [[ "${gpu_info}" == *"2080 Ti"* ]] || [[ "${gpu_info}" == *"2080Ti"* ]]; then
        ok "检测到 RTX 2080Ti（魔改 22GB）"
    else
        warn "未检测到 2080Ti，当前 GPU: ${gpu_info}"
    fi
    # 显存检查
    local vmem
    vmem=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d ' ')
    info "显存大小: ${vmem} MiB"
    if [[ "${vmem}" -lt 22000 ]]; then
        warn "显存不足 22GB (${vmem} MiB)，可能无法加载大模型"
    else
        ok "显存充足 (${vmem} MiB)"
    fi
}

# 检查 NVMe 数据盘
check_nvme_disk() {
    info "检查 NVMe 数据盘挂载点 ..."
    if [[ -d "${DATA_DISK}" ]]; then
        ok "数据盘挂载点存在: ${DATA_DISK}"
        local avail
        avail=$(df -BG "${DATA_DISK}" | tail -1 | awk '{print $4}' | tr -d 'G')
        info "数据盘可用空间: ${avail}GB"
        if [[ "${avail}" -lt 100 ]]; then
            warn "数据盘可用空间不足 100GB，请注意存储管理"
        fi
        # 创建 triad 数据目录
        check_or_mkdir "${DATA_DISK}/triad"
    else
        warn "数据盘挂载点 ${DATA_DISK} 不存在，将使用脚本目录作为数据存储"
        warn "建议挂载第二块 NVMe SSD 到 ${DATA_DISK} 以获得最佳性能"
        check_or_mkdir "${TRIAD_ROOT}/data"
    fi
}

# 国内镜像源配置
# 配置 APT 国内源（阿里云 / 清华源）
config_apt_mirror() {
    step "2.1" "配置 APT 国内镜像源"
    local sources_file="/etc/apt/sources.list"
    local backup_file="/etc/apt/sources.list.bak.$(date +%Y%m%d%H%M%S)"

    # 备份原有源
    if [[ ! -f "${backup_file}" ]]; then
        info "备份原 apt 源列表到 ${backup_file}"
        sudo cp "${sources_file}" "${backup_file}" || warn "无法备份 apt 源"
    fi

    # 检测 Ubuntu 版本并写入对应源
    local version_id
    version_id=$(source /etc/os-release && echo "${VERSION_ID}")
    info "正在为 Ubuntu ${version_id} 配置阿里云镜像源 ..."

    # 使用阿里云源
    local mirror_content
    mirror_content="# 阿里云 Ubuntu 镜像源
# 由 triad_manager.sh 自动生成
deb https://mirrors.aliyun.com/ubuntu/ jammy main restricted universe multiverse
deb https://mirrors.aliyun.com/ubuntu/ jammy-updates main restricted universe multiverse
deb https://mirrors.aliyun.com/ubuntu/ jammy-backports main restricted universe multiverse
deb https://mirrors.aliyun.com/ubuntu/ jammy-security main restricted universe multiverse
"
    # 如果 VERSION_ID 不是 22.04，使用更通用的方式
    if [[ "${version_id}" == "22.04" ]]; then
        echo "${mirror_content}" | sudo tee "${sources_file}" >/dev/null
    else
        # 使用 sed 替换域名
        sudo sed -i 's|archive.ubuntu.com|mirrors.aliyun.com|g' "${sources_file}" || true
        sudo sed -i 's|security.ubuntu.com|mirrors.aliyun.com|g' "${sources_file}" || true
    fi

    run_cmd "更新 apt 索引" sudo apt-get update
    ok "APT 国内镜像源配置完成"
}

# 配置 pip 国内镜像源
config_pip_mirror() {
    step "2.2" "配置 pip 国内镜像源"
    local pip_conf_dir="${HOME}/.config/pip"
    local pip_conf="${pip_conf_dir}/pip.conf"
    check_or_mkdir "${pip_conf_dir}"

    info "写入 pip 配置文件: ${pip_conf}"
    cat > "${pip_conf}" <<EOF
[global]
index-url = ${PIP_INDEX_URL}
trusted-host = ${PIP_TRUSTED_HOST}
timeout = 120
retries = 5

[install]
use-pep517 = true
EOF
    ok "pip 国内镜像源配置完成 (${PIP_INDEX_URL})"
}

# 配置 npm 国内镜像源
config_npm_mirror() {
    step "2.3" "配置 npm 国内镜像源"
    if ! command -v npm &>/dev/null; then
        warn "npm 尚未安装，将在 Node.js 安装后配置"
        return
    fi
    info "设置 npm 淘宝镜像源 ..."
    npm config set registry "${NPM_REGISTRY}" || warn "npm  registry 设置失败"
    # 安装 cnpm 作为备用
    if ! command -v cnpm &>/dev/null; then
        info "安装 cnpm ..."
        npm install -g cnpm --registry="${NPM_REGISTRY}" || warn "cnpm 安装失败"
    fi
    ok "npm 国内镜像源配置完成 (${NPM_REGISTRY})"
}

# 配置 Docker 国内镜像源
config_docker_mirror() {
    step "2.4" "配置 Docker 国内镜像源"
    local daemon_dir="/etc/docker"
    local daemon_file="${daemon_dir}/daemon.json"
    check_or_mkdir "${daemon_dir}"

    info "写入 Docker 守护进程配置 ..."
    sudo tee "${daemon_file}" >/dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://registry.docker-cn.com",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com",
    "https://docker.mirrors.ustc.edu.cn",
    "https://cr.console.aliyun.com"
  ],
  "exec-opts": ["native.cgroupdriver=systemd"],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  },
  "storage-driver": "overlay2"
}
EOF
    ok "Docker 国内镜像源配置完成"
}

# Docker 与 nvidia-container-toolkit 安装
install_docker() {
    step "3" "安装原生 Docker CE 与 nvidia-container-toolkit"
    if command -v docker &>/dev/null; then
        local docker_version
        docker_version=$(docker --version)
        ok "Docker 已安装: ${docker_version}"
    else
        info "开始安装 Docker CE ..."
        # 卸载旧版本（如果存在）
        sudo apt-get remove -y docker docker-engine docker.io containerd runc || true

        # 安装依赖包
        run_cmd "安装 Docker 依赖" sudo apt-get install -y ca-certificates curl gnupg lsb-release

        # 添加 Docker 官方 GPG 密钥（通过国内镜像）
        info "添加 Docker GPG 密钥 ..."
        sudo install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg || \
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        sudo chmod a+r /etc/apt/keyrings/docker.gpg

        # 添加 Docker APT 仓库
        info "添加 Docker APT 仓库 ..."
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $(lsb_release -cs) stable" | \
            sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

        run_cmd "更新 apt 索引（Docker 仓库）" sudo apt-get update
        run_cmd "安装 Docker CE 组件" sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

        # 将当前用户加入 docker 组
        info "将当前用户加入 docker 组 ..."
        sudo usermod -aG docker "${USER}" || warn "无法将用户加入 docker 组"

        # 启动 Docker 服务
        run_cmd "启动 Docker 服务" sudo systemctl start docker
        run_cmd "设置 Docker 开机自启" sudo systemctl enable docker

        ok "Docker CE 安装完成"
    fi

    # 验证 Docker 运行状态
    if ! sudo systemctl is-active --quiet docker; then
        fatal "Docker 服务未能正常启动"
    fi

    # 安装 nvidia-container-toolkit（让 Docker 容器使用 GPU）
    install_nvidia_container_toolkit
}

# 安装 nvidia-container-toolkit
install_nvidia_container_toolkit() {
    info "检查 nvidia-container-toolkit ..."
    if command -v nvidia-ctk &>/dev/null; then
        ok "nvidia-container-toolkit 已安装"
    else
        info "开始安装 nvidia-container-toolkit ..."
        # 添加 NVIDIA Container Toolkit 仓库
        info "添加 NVIDIA Container Toolkit 仓库 ..."
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
            sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
            sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
            sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

        run_cmd "更新 apt 索引（NVIDIA 仓库）" sudo apt-get update
        run_cmd "安装 nvidia-container-toolkit" sudo apt-get install -y nvidia-container-toolkit

        # 配置 Docker 使用 nvidia 运行时
        info "配置 Docker 使用 nvidia 运行时 ..."
        sudo nvidia-ctk runtime configure --runtime=docker || warn "nvidia-ctk runtime 配置失败"
        run_cmd "重启 Docker 服务" sudo systemctl restart docker

        ok "nvidia-container-toolkit 安装完成"
    fi

    # 验证 GPU 容器可用性
    info "验证 Docker GPU 支持 ..."
    if sudo docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi &>/dev/null; then
        ok "Docker GPU 支持验证通过"
    else
        warn "Docker GPU 验证未通过，请检查 nvidia-driver 和 nvidia-container-toolkit"
    fi
}

# Python 3.10 与依赖安装
install_python_env() {
    step "4" "安装 Python 3.10 环境与依赖"
    # 安装 Python 3.10
    if ! command -v python3.10 &>/dev/null; then
        info "安装 Python 3.10 ..."
        run_cmd "安装 Python 3.10 包" sudo apt-get install -y python3.10 python3.10-venv python3.10-dev python3-pip
    else
        ok "Python 3.10 已安装"
    fi

    # 确保 pip 最新
    info "升级 pip ..."
    python3.10 -m pip install --upgrade pip --index-url "${PIP_INDEX_URL}" || \
        python3.10 -m pip install --upgrade pip

    # 安装全局 Python 依赖（系统级常用包）
    info "安装全局 Python 依赖 ..."
    local global_pkgs=("requests" "tqdm" "numpy" "psutil" "pyyaml")
    python3.10 -m pip install "${global_pkgs[@]}" --index-url "${PIP_INDEX_URL}" || warn "部分全局包安装失败"

    # 创建项目虚拟环境
    local venv_dir="${TRIAD_ROOT}/venv"
    if [[ ! -d "${venv_dir}" ]]; then
        info "创建项目虚拟环境: ${venv_dir}"
        python3.10 -m venv "${venv_dir}"
    fi

    # 安装项目根目录依赖
    local req_file="${TRIAD_ROOT}/requirements.txt"
    if [[ -f "${req_file}" ]]; then
        info "安装根目录 requirements.txt 依赖 ..."
        "${venv_dir}/bin/pip" install -r "${req_file}" --index-url "${PIP_INDEX_URL}" || warn "部分 requirements 安装失败"
    else
        warn "未找到根目录 requirements.txt，跳过"
    fi

    # 安装 Hermes (mind/) Python 依赖
    local hermes_req="${TRIAD_ROOT}/mind/requirements.txt"
    if [[ -f "${hermes_req}" ]]; then
        info "安装 Hermes (mind/) Python 依赖 ..."
        "${venv_dir}/bin/pip" install -r "${hermes_req}" --index-url "${PIP_INDEX_URL}" || warn "Hermes 部分依赖安装失败"
    else
        warn "未找到 Hermes requirements.txt，跳过"
    fi

    ok "Python 3.10 环境配置完成"
}

# Node.js 18 与 npm 依赖安装
install_node_env() {
    step "5" "安装 Node.js 18 与 npm 依赖"
    if ! command -v node &>/dev/null || [[ "$(node -v | cut -d'v' -f2 | cut -d'.' -f1)" != "18" ]]; then
        info "安装 Node.js 18 LTS ..."
        # 卸载旧版本
        sudo apt-get remove -y nodejs npm || true
        sudo rm -f /etc/apt/sources.list.d/nodesource.list || true

        # 使用 Nodesource 脚本安装 Node.js 18（通过国内加速）
        local setup_script
        setup_script=$(curl -fsSL https://deb.nodesource.com/setup_18.x || \
                       curl -fsSL https://mirrors.aliyun.com/nodesource/setup_18.x || true)
        if [[ -n "${setup_script}" ]]; then
            echo "${setup_script}" | sudo -E bash -
        else
            # 手动添加仓库
            sudo apt-get install -y ca-certificates curl gnupg
            sudo mkdir -p /etc/apt/keyrings
            curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
            local node_major=18
            echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${node_major}.x nodistro main" | sudo tee /etc/apt/sources.list.d/nodesource.list
            run_cmd "更新 apt 索引（NodeSource）" sudo apt-get update
        fi

        run_cmd "安装 Node.js 18" sudo apt-get install -y nodejs
    else
        ok "Node.js 18 已安装: $(node -v)"
    fi

    # 验证 npm
    if ! command -v npm &>/dev/null; then
        fatal "npm 未正确安装"
    fi
    ok "npm 版本: $(npm -v)"

    # 配置国内镜像
    config_npm_mirror

    # 安装项目 npm 依赖
    local pkg_file="${TRIAD_ROOT}/package.json"
    if [[ -f "${pkg_file}" ]]; then
        info "安装项目根目录 npm 依赖 ..."
        pushd "${TRIAD_ROOT}" >/dev/null || fatal "无法进入项目目录"
        npm install --registry="${NPM_REGISTRY}" || warn "npm install 部分失败"
        popd >/dev/null || true
    else
        warn "未找到根目录 package.json，跳过"
    fi

    # 安装 OpenClaw Gateway npm 依赖
    local openclaw_dir="${TRIAD_ROOT}/host/openclaw"
    if [[ -f "${openclaw_dir}/package.json" ]]; then
        info "安装 OpenClaw Gateway npm 依赖 ..."
        pushd "${openclaw_dir}" >/dev/null || true
        npm install --registry="${NPM_REGISTRY}" || warn "OpenClaw npm install 部分失败"
        npm run build 2>/dev/null || warn "OpenClaw build 失败"
        popd >/dev/null || true
    else
        warn "未找到 OpenClaw package.json，跳过"
    fi

    # 安装 WebUI npm 依赖
    local webui_dir="${TRIAD_ROOT}/webui"
    if [[ -f "${webui_dir}/package.json" ]]; then
        info "安装 WebUI npm 依赖 ..."
        pushd "${webui_dir}" >/dev/null || true
        npm install --registry="${NPM_REGISTRY}" || warn "WebUI npm install 部分失败"
        popd >/dev/null || true
    else
        warn "未找到 WebUI package.json，跳过"
    fi

    ok "Node.js 18 环境配置完成"
}

# llama.cpp 源码编译与 systemd 服务安装
check_llama_cpp() {
    step "6" "检测 llama.cpp 状态"

    local llama_server_path=""
    local llama_paths=(
        "${TRIAD_ROOT}/llama.cpp/build/bin/llama-server"
        "${TRIAD_ROOT}/llama.cpp/build/llama-server"
        "${HOME}/llama.cpp/build/bin/llama-server"
        "${HOME}/llama.cpp/build/llama-server"
        "/usr/local/bin/llama-server"
    )

    for p in "${llama_paths[@]}"; do
        if [[ -f "$p" ]] && [[ -x "$p" ]]; then
            llama_server_path="$p"
            break
        fi
    done

    # Also check PATH
    if [[ -z "$llama_server_path" ]]; then
        llama_server_path=$(command -v llama-server 2>/dev/null || true)
    fi

    if [[ -z "$llama_server_path" ]]; then
        line
        err "未检测到 llama-server 可执行文件"
        info "llama.cpp 是 Triad 的核心依赖，请先自行编译安装："
        info "  git clone https://github.com/ggerganov/llama.cpp.git"
        info "  cmake -B build -DGGML_CUDA=ON"
        info "  cmake --build build"
        fatal "llama.cpp 未安装，安装中断"
    fi

    ok "检测到 llama-server: ${llama_server_path}"

    # Check if running on LLAMA_PORT
    info "检测 llama-server 是否已在端口 ${LLAMA_PORT} 运行 ..."
    if curl -s --max-time 3 "http://localhost:${LLAMA_PORT}/health" >/dev/null 2>&1; then
        ok "llama-server 已在端口 ${LLAMA_PORT} 运行，跳过模型下载"
        return 0
    fi

    # Check if process exists but not responding
    local llama_pids
    llama_pids=$(pgrep -f "llama-server" 2>/dev/null || true)
    if [[ -n "$llama_pids" ]]; then
        warn "llama-server 进程存在但未在端口 ${LLAMA_PORT} 响应"
        info "请启动 llama-server: nohup ${llama_server_path} -m <模型路径> --host 0.0.0.0 --port ${LLAMA_PORT} -ngl 999 ..."
        fatal "llama-server 未正常运行，安装中断"
    fi

    warn "llama-server 已编译但未启动"
    info "请启动 llama-server 后再运行安装脚本"
    info "示例: nohup ${llama_server_path} -m /mnt/f/AI_Models/Qwen3.6-27b.gguf --host 0.0.0.0 --port ${LLAMA_PORT} -ngl 999 --ctx-size 4096 ..."
    fatal "llama-server 未运行，安装中断"
}


# Qdrant 向量数据库配置（Docker 容器）
install_qdrant() {
    step "7" "部署 Qdrant 向量数据库（Docker）"
    local qdrant_dir
    qdrant_dir=$(get_data_path "qdrant")
    check_or_mkdir "${qdrant_dir}"
    check_or_mkdir "${qdrant_dir}/storage"

    # 拉取 Qdrant 镜像
    info "拉取 Qdrant Docker 镜像 ..."
    sudo docker pull qdrant/qdrant:latest || warn "Qdrant 镜像拉取失败，将尝试使用已有镜像"

    # 创建 Qdrant 配置
    local qdrant_config="${qdrant_dir}/config.yaml"
    cat > "${qdrant_config}" <<EOF
# Qdrant 配置文件（由 triad_manager.sh 自动生成）
log_level: INFO
storage:
  storage_path: /qdrant/storage
data:
  on_disk_payload: true
service:
  http_port: 6333
  grpc_port: 6334
  max_request_size_mb: 32
  max_workers: 4
EOF

    ok "Qdrant Docker 配置完成，数据目录: ${qdrant_dir}"
}

# .env 环境文件生成
generate_env_file() {
    step "8" "生成 .env 环境配置文件"
    info "写入端口配置到 ${ENV_FILE} ..."
    cat > "${ENV_FILE}" <<EOF
# Triad 环境配置文件（由 triad_manager.sh 自动生成）
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')

# 服务绑定地址
BIND_ADDRESS=${BIND_ADDRESS}

# 服务端口（全部大端口号 +10000）
OPENCLAW_PORT=${OPENCLAW_PORT}
HERMES_PORT=${HERMES_PORT}
LLAMA_PORT=${LLAMA_PORT}
QDRANT_HTTP_PORT=${QDRANT_HTTP_PORT}
QDRANT_GRPC_PORT=${QDRANT_GRPC_PORT}
EMBEDDING_API_PORT=${EMBEDDING_API_PORT}
MCP_SERVER_PORT=${MCP_SERVER_PORT}

# 模型配置（由用户自行管理，启动时通过 -m 参数指定）
MODEL_NAME=${MODEL_NAME}
# MODEL_PATH: 请替换为你的实际模型路径
# 示例: MODEL_PATH=/mnt/f/AI_Models/Qwen3.6-27b.gguf
LLAMA_CPP_ROOT=${TRIAD_ROOT}/llama.cpp

# 数据盘路径
DATA_DISK=${DATA_DISK}
QDRANT_STORAGE=$(get_data_path "qdrant")/storage

# GPU 配置
CUDA_VISIBLE_DEVICES=0
GPU_LAYERS=99
CONTEXT_SIZE=8192

# 日志路径
LOG_DIR=${LOG_DIR}
EOF

    ok ".env 文件生成完成: ${ENV_FILE}"
}

# Web UI 构建
build_web_ui() {
    step "9" "构建 Web UI"
    local web_dir="${TRIAD_ROOT}/webui"
    if [[ ! -d "${web_dir}" ]]; then
        warn "未找到 web 目录 ${web_dir}，跳过 Web UI 构建"
        return 0
    fi

    info "构建 Web UI ..."
    pushd "${web_dir}" >/dev/null || fatal "无法进入 web 目录"
    npm install --registry="${NPM_REGISTRY}" || warn "Web UI npm install 失败"
    npm run build 2>/dev/null || warn "Web UI build 失败（可能需要手动构建）"
    popd >/dev/null || true
    ok "Web UI 构建完成"
}

# 安装后测试验证
post_install_test() {
    step "10" "安装后测试验证"
    # 检查各组件是否存在
    info "验证关键组件 ..."
    local errors=0

    if command -v docker &>/dev/null; then
        ok "Docker: 已安装"
    else
        err "Docker: 未安装"; ((errors++))
    fi

    if command -v python3.10 &>/dev/null; then
        ok "Python 3.10: 已安装"
    else
        err "Python 3.10: 未安装"; ((errors++))
    fi

    if command -v node &>/dev/null; then
        ok "Node.js: 已安装 ($(node -v))"
    else
        err "Node.js: 未安装"; ((errors++))
    fi

    if [[ -f "${TRIAD_ROOT}/llama.cpp/build/bin/llama-server" ]] || [[ -f "${TRIAD_ROOT}/llama.cpp/build/llama-server" ]]; then
        ok "llama.cpp: 已安装"
    else
        warn "llama.cpp: 未检测到运行中的 llama-server"
    fi

    local model_path
    model_path=$(get_data_path "models")/*.gguf
    if compgen -G "${model_path}" >/dev/null 2>&1; then
        ok "模型文件: 检测到 GGUF 文件"
    else
        warn "模型文件: 数据盘未找到 .gguf 文件（用户自行管理模型路径）"
    fi

    if [[ -f "${ENV_FILE}" ]]; then
        ok ".env 配置: 已生成"
    else
        err ".env 配置: 未生成"; ((errors++))
    fi

    # 写入安装完成标记
    if [[ "${errors}" -eq 0 ]]; then
        date '+%Y-%m-%d %H:%M:%S' > "${INSTALL_FLAG}"
        ok "安装验证通过，标记写入 ${INSTALL_FLAG}"
    else
        warn "安装验证发现 ${errors} 个问题，请检查日志"
    fi
}

# install 主流程
cmd_install() {
    highlight "开始 Triad 一键安装流程 (v${SCRIPT_VERSION})"
    init_log_dir

    # 步骤 1: 系统检查
    step "1" "系统环境检查"
    check_wsl2
    check_ubuntu_version
    check_hardware
    check_nvme_disk
    ok "系统检查完成"

    # 步骤 2: 配置国内镜像源
    config_apt_mirror
    config_pip_mirror
    config_docker_mirror

    # 步骤 3: 安装 Docker + nvidia-toolkit
    install_docker

    # 步骤 4: Python 环境
    install_python_env

    # 步骤 5: Node.js 环境
    install_node_env

    # 步骤 6: llama.cpp 检测
    check_llama_cpp

    # 步骤 7: Qdrant 配置
    install_qdrant

    # 步骤 8: .env 生成
    generate_env_file

    # 步骤 9: Web UI 构建
    build_web_ui

    # 步骤 10: 测试验证
    post_install_test

    line
    ok "Triad 一键安装流程全部完成！"
    info "日志文件: ${INSTALL_LOG}"
    info "配置环境变量: source ${ENV_FILE}"
    info "启动服务: ${SCRIPT_NAME} start"
    line
}

# 服务管理子命令
# 获取 .env 中的变量（如果存在）
load_env() {
    if [[ -f "${ENV_FILE}" ]]; then
        # shellcheck source=/dev/null
        set -a
        source "${ENV_FILE}"
        set +a
    fi
}

# 启动所有服务
cmd_start() {
    highlight "启动 Triad 全部服务"
    load_env
    init_log_dir

    # 检查是否已安装
    if [[ ! -f "${INSTALL_FLAG}" ]]; then
        warn "未检测到安装标记 (${INSTALL_FLAG})"
        warn "建议先运行: ${SCRIPT_NAME} install"
        read -rp "是否继续启动? [y/N] " confirm
        [[ "${confirm}" == [yY]* ]] || exit 1
    fi

    line
    info "正在启动服务 ..."
    line

        # 1. 检测 llama-server（用户自行管理，脚本不自动启动）
    info "检测 llama-server (端口: ${LLAMA_PORT}) ..."
    if curl -s --max-time 2 "http://localhost:${LLAMA_PORT}/health" >/dev/null 2>&1; then
        ok "llama-server 已在端口 ${LLAMA_PORT} 运行"
    else
        warn "llama-server 未在端口 ${LLAMA_PORT} 运行"
        info "请手动启动: nohup llama-server -m <你的模型路径> --host 0.0.0.0 --port ${LLAMA_PORT} -ngl 999 ..."
    fi

    # 2. 启动 Qdrant (Docker)
    info "启动 Qdrant (HTTP: ${QDRANT_HTTP_PORT}, gRPC: ${QDRANT_GRPC_PORT}) ..."
    local qdrant_dir
    qdrant_dir=$(get_data_path "qdrant")
    # 如果已有容器在运行，先停止
    sudo docker stop triad-qdrant 2>/dev/null || true
    sudo docker rm triad-qdrant 2>/dev/null || true
    if sudo docker run -d \
        --name triad-qdrant \
        --restart unless-stopped \
        -p "${QDRANT_HTTP_PORT}:6333" \
        -p "${QDRANT_GRPC_PORT}:6334" \
        -v "${qdrant_dir}/storage:/qdrant/storage" \
        -v "${qdrant_dir}/config.yaml:/qdrant/config/production.yaml" \
        qdrant/qdrant:latest; then
        ok "Qdrant 容器已启动"
    else
        err "Qdrant 容器启动失败"
    fi


    # 3. 启动 OpenClaw Gateway
    info "启动 OpenClaw Gateway (端口: ${OPENCLAW_PORT}) ..."
    local openclaw_dir="${TRIAD_ROOT}/host/openclaw"
    if [[ -d "${openclaw_dir}" ]] && [[ -f "${openclaw_dir}/package.json" ]]; then
        pushd "${openclaw_dir}" >/dev/null || true
        if [[ -f .env ]]; then
            export $(grep -v '^#' .env | xargs) 2>/dev/null || true
        fi
        nohup npm start >> "${LOG_DIR}/openclaw.log" 2>&1 &
        popd >/dev/null || true
        ok "OpenClaw 已启动 (PID: $!)"
    else
        warn "未找到 OpenClaw Gateway 目录 (${openclaw_dir})，跳过启动"
    fi

    # 4. 启动 Hermes 编排层
    info "启动 Hermes (端口: ${HERMES_PORT}) ..."
    local hermes_dir="${TRIAD_ROOT}/mind"
    local venv_dir="${TRIAD_ROOT}/venv"
    if [[ -d "${hermes_dir}" ]] && [[ -f "${hermes_dir}/hermes_orchestrator.py" ]]; then
        pushd "${hermes_dir}" >/dev/null || true
        if [[ -f .env ]]; then
            export $(grep -v '^#' .env | xargs) 2>/dev/null || true
        fi
        if [[ -f "${venv_dir}/bin/python" ]]; then
            nohup "${venv_dir}/bin/python" hermes_orchestrator.py >> "${LOG_DIR}/hermes.log" 2>&1 &
        else
            nohup python3 hermes_orchestrator.py >> "${LOG_DIR}/hermes.log" 2>&1 &
        fi
        popd >/dev/null || true
        ok "Hermes 已启动 (PID: $!)"
    else
        warn "未找到 Hermes 目录 (${hermes_dir})，跳过启动"
    fi

    # 5. 启动 MCP Server (如果有)
    info "启动 MCP Server (端口: ${MCP_SERVER_PORT}) ..."
    local mcp_dir="${TRIAD_ROOT}/mcp-server"
    if [[ -d "${mcp_dir}" ]]; then
        if [[ -f "${mcp_dir}/package.json" ]]; then
            pushd "${mcp_dir}" >/dev/null || true
            nohup npm start >> "${LOG_DIR}/mcp-server.log" 2>&1 &
            popd >/dev/null || true
            ok "MCP Server (Node) 已启动"
        elif [[ -f "${mcp_dir}/requirements.txt" ]] || [[ -f "${mcp_dir}/main.py" ]]; then
            if [[ -f "${TRIAD_ROOT}/venv/bin/python" ]]; then
                pushd "${mcp_dir}" >/dev/null || true
                nohup "${TRIAD_ROOT}/venv/bin/python" main.py >> "${LOG_DIR}/mcp-server.log" 2>&1 &
                popd >/dev/null || true
                ok "MCP Server (Python) 已启动"
            fi
        fi
    else
        warn "未找到 MCP Server 目录，跳过启动"
    fi

    # 6. 启动 Embedding API (如果有)
    info "启动 Embedding API (端口: ${EMBEDDING_API_PORT}) ..."
    local emb_dir="${TRIAD_ROOT}/embedding-api"
    if [[ -d "${emb_dir}" ]] && [[ -f "${TRIAD_ROOT}/venv/bin/python" ]]; then
        pushd "${emb_dir}" >/dev/null || true
        nohup "${TRIAD_ROOT}/venv/bin/python" main.py >> "${LOG_DIR}/embedding-api.log" 2>&1 &
        popd >/dev/null || true
        ok "Embedding API 已启动"
    else
        warn "未找到 Embedding API，跳过启动"
    fi

    sleep 2
    line
    ok "全部服务启动指令已发送"
    info "查看状态: ${SCRIPT_NAME} status"
    info "查看日志: ${SCRIPT_NAME} logs"
    line
}

# 停止所有服务
cmd_stop() {
    highlight "停止 Triad 全部服务"
    load_env

    line
    info "正在停止服务 ..."
    line

    # 停止 llama-server 进程（手动启动模式，无 systemd）
    local llama_pids
    llama_pids=$(pgrep -f "llama-server" 2>/dev/null || true)
    if [[ -n "${llama_pids}" ]]; then
        echo "${llama_pids}" | xargs kill -TERM 2>/dev/null || true
        sleep 1
        llama_pids=$(pgrep -f "llama-server" 2>/dev/null || true)
        if [[ -n "${llama_pids}" ]]; then
            echo "${llama_pids}" | xargs kill -KILL 2>/dev/null || true
        fi
        ok "llama-server 已停止"
    else
        warn "llama-server 未运行"
    fi

    # 停止 Docker 容器
    sudo docker stop triad-qdrant 2>/dev/null && ok "Qdrant 容器已停止" || warn "Qdrant 容器未运行"

    # 停止 Node.js / Python 后台进程（通过端口匹配）
    local ports=("${OPENCLAW_PORT}" "${HERMES_PORT}" "${MCP_SERVER_PORT}" "${EMBEDDING_API_PORT}")
    for port in "${ports[@]}"; do
        local pids
        pids=$(lsof -ti :"${port}" 2>/dev/null || true)
        if [[ -n "${pids}" ]]; then
            info "终止占用端口 ${port} 的进程: ${pids}"
            echo "${pids}" | xargs kill -TERM 2>/dev/null || true
            sleep 1
            # 强制清理残留
            pids=$(lsof -ti :"${port}" 2>/dev/null || true)
            if [[ -n "${pids}" ]]; then
                echo "${pids}" | xargs kill -KILL 2>/dev/null || true
            fi
            ok "端口 ${port} 已释放"
        fi
    done

    # 额外清理 llama-server 残留进程
    local llama_pids
    llama_pids=$(pgrep -f "llama-server.*--port.*${LLAMA_PORT}" 2>/dev/null || true)
    if [[ -n "${llama_pids}" ]]; then
        echo "${llama_pids}" | xargs kill -TERM 2>/dev/null || true
        sleep 1
        llama_pids=$(pgrep -f "llama-server.*--port.*${LLAMA_PORT}" 2>/dev/null || true)
        if [[ -n "${llama_pids}" ]]; then
            echo "${llama_pids}" | xargs kill -KILL 2>/dev/null || true
        fi
    fi


    line
    ok "全部服务停止指令已发送"
    line
}

# 查看服务状态
cmd_status() {
    highlight "Triad 服务运行状态"
    load_env

    line
    printf "%-20s %-10s %-25s %-s\n" "服务" "状态" "监听地址" "备注"
    line

    local services=(
        "llama-server:${LLAMA_PORT}"
        "openclaw:${OPENCLAW_PORT}"
        "hermes:${HERMES_PORT}"
        "qdrant-http:${QDRANT_HTTP_PORT}"
        "qdrant-grpc:${QDRANT_GRPC_PORT}"
        "mcp-server:${MCP_SERVER_PORT}"
        "embedding-api:${EMBEDDING_API_PORT}"
    )

    for svc in "${services[@]}"; do
        local name port pid status addr
        name="${svc%%:*}"
        port="${svc##*:}"
        pid=$(lsof -ti :"${port}" 2>/dev/null || true)
        if [[ -n "${pid}" ]]; then
            status="${C_GREEN}运行中${C_RESET}"
            addr="${BIND_ADDRESS}:${port}"
        else
            status="${C_RED}未运行${C_RESET}"
            addr="-"
        fi
        printf "%-20s %-20b %-25s PID:%s\n" "${name}" "${status}" "${addr}" "${pid:-N/A}"
    done

    line
    info "Docker 容器状态:"
    sudo docker ps --filter "name=triad-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true
    line

    info "systemd 服务状态:"
    for svc in llama-server; do
        local sys_status
        sys_status=$(sudo systemctl is-active "${svc}" 2>/dev/null || echo "unknown")
        if [[ "${sys_status}" == "active" ]]; then
            printf "%-20s ${C_GREEN}%-20s${C_RESET}\n" "${svc}" "${sys_status}"
        else
            printf "%-20s ${C_YELLOW}%-20s${C_RESET}\n" "${svc}" "${sys_status}"
        fi
    done
    line

    info "GPU 状态:"
    nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.free --format=csv,noheader 2>/dev/null || warn "无法获取 GPU 状态"
    line
}

# 查看日志
cmd_logs() {
    local service="${1:-all}"
    local lines="${2:-100}"

    highlight "查看日志: ${service} (最近 ${lines} 行)"
    init_log_dir

    if [[ "${service}" == "all" ]]; then
        for logfile in "${LOG_DIR}"/*.log; do
            if [[ -f "${logfile}" ]]; then
                local basename
                basename=$(basename "${logfile}")
                line
                echo -e "${C_CYAN}=== ${basename} ===${C_RESET}"
                tail -n "${lines}" "${logfile}" 2>/dev/null || true
            fi
        done
        line
    else
        local logfile="${LOG_DIR}/${service}.log"
        if [[ -f "${logfile}" ]]; then
            tail -n "${lines}" "${logfile}" 2>/dev/null || true
        else
            # 尝试 systemd journal
            if sudo systemctl is-active --quiet "${service}" 2>/dev/null; then
                sudo journalctl -u "${service}" -n "${lines}" --no-pager 2>/dev/null || true
            else
                err "未找到日志文件: ${logfile}"
            fi
        fi
    fi
}

# 更新（从 GitHub 拉取更新）
cmd_update() {
    highlight "更新 Triad 组件"
    load_env

    line
    info "1. 拉取 Triad 项目更新 ..."
    pushd "${TRIAD_ROOT}" >/dev/null || fatal "无法进入项目目录"
    git fetch origin || warn "git fetch 失败"
    git status || true
    read -rp "是否执行 git pull? [y/N] " confirm
    if [[ "${confirm}" == [yY]* ]]; then
        git pull --ff-only || warn "git pull 失败，可能存在本地冲突"
    fi
    popd >/dev/null || true

    info "2. 更新 llama.cpp ..."
    local llama_dir="${TRIAD_ROOT}/llama.cpp"
    if [[ -d "${llama_dir}/.git" ]]; then
        pushd "${llama_dir}" >/dev/null || true
        git fetch origin
        git pull --ff-only || warn "llama.cpp 更新失败"
        warn "llama.cpp 源码已更新，请自行重新编译（脚本不再自动编译）"
        popd >/dev/null || true
    fi


    info "3. 更新 Docker 镜像 ..."
    sudo docker pull qdrant/qdrant:latest 2>/dev/null || warn "Qdrant 镜像更新失败"

    info "4. 重新构建 Web UI ..."
    build_web_ui

    line
    ok "更新流程执行完毕"
    info "建议运行: ${SCRIPT_NAME} status"
    line
}

# 帮助信息
show_help() {
    cat <<EOF
${C_BOLD}Triad Manager v${SCRIPT_VERSION}${C_RESET}
一键部署脚本：原生 Ubuntu 22.04 (WSL2) + 单卡魔改 RTX 2080Ti 22GB

${C_BOLD}用法:${C_RESET}
    ${SCRIPT_NAME} <命令> [选项]

${C_BOLD}命令:${C_RESET}
    install         一键安装所有依赖与服务
    start           启动所有服务
    stop            停止所有服务
    status          查看所有服务运行状态
    logs [服务] [行数] 查看日志（默认全部，最近100行）
    update          从 GitHub 拉取更新并重新编译
    help            显示此帮助信息

${C_BOLD}install 流程:${C_RESET}
    1. 系统检查（WSL2, Ubuntu 22.04, NVMe, 2080Ti）
    2. 配置国内镜像源（apt/pip/npm/docker）
    3. 安装 docker-ce + nvidia-container-toolkit
    4. 安装 Python 3.10 + pip 依赖
    5. 安装 Node.js 18 + npm 依赖
    6. 检测 llama.cpp 运行状态
    7. 部署 Qdrant 向量数据库（Docker）
    8. 生成 .env 文件（大端口 + 0.0.0.0）
    9. 构建 Web UI
    10. 安装后测试验证

${C_BOLD}端口配置（大端口号 +10000）:${C_RESET}
    llama-server:      ${LLAMA_PORT}
    OpenClaw:           ${OPENCLAW_PORT}
    Hermes:             ${HERMES_PORT}
    Qdrant HTTP:        ${QDRANT_HTTP_PORT}
    Qdrant gRPC:        ${QDRANT_GRPC_PORT}
    Embedding API:      ${EMBEDDING_API_PORT}
    MCP Server:         ${MCP_SERVER_PORT}

${C_BOLD}示例:${C_RESET}
    一键安装并记录日志:
        ./${SCRIPT_NAME} install 2>&1 | tee install.log

    启动服务:
        ./${SCRIPT_NAME} start

    查看 llama-server 最后50行日志:
        ./${SCRIPT_NAME} logs llama-server 50

    停止所有服务:
        ./${SCRIPT_NAME} stop

${C_BOLD}数据盘路径:${C_RESET}
    模型文件:  $(get_data_path "models")
    Qdrant:    $(get_data_path "qdrant")
    日志目录:  ${LOG_DIR}

${C_BOLD}注意:${C_RESET}
    - 本脚本针对原生 Docker（非 Docker Desktop）设计
    - llama.cpp 在宿主机（WSL2 Ubuntu）直接编译，非容器内
    - 安装完成后请重新登录或执行 newgrp docker 以使用 docker 免 sudo
    - 2080Ti 22GB 建议加载 Q4_K_M 量化模型以获得最佳性能
EOF
}

# 主入口
main() {
    # 如果没有参数，显示帮助
    if [[ $# -eq 0 ]]; then
        show_help
        exit 0
    fi

    local cmd="$1"
    shift || true

    case "${cmd}" in
        install)
            cmd_install "$@"
            ;;
        start)
            cmd_start "$@"
            ;;
        stop)
            cmd_stop "$@"
            ;;
        status)
            cmd_status "$@"
            ;;
        logs)
            cmd_logs "$@"
            ;;
        update)
            cmd_update "$@"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            err "未知命令: ${cmd}"
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"