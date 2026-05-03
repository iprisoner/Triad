#!/bin/bash
# =============================================================================
# ComfyUI 宿主机原生安装脚本 (WSL2 Ubuntu 22.04+)
# =============================================================================
# 用途: 在 WSL2 宿主机创建 Python venv，安装 ComfyUI 本体 + 自定义节点
# 替代方案: 替代 Docker 内运行，解决 PyTorch CUDA 依赖冲突和驱动兼容问题
#
# 安装路径:
#   venv:    ~/.triad/venvs/comfyui/
#   app:     ~/.triad/apps/comfyui/
#   models:  ~/.triad/models/comfyui/
#
# 启动命令:
#   cd ~/.triad/apps/comfyui && source ~/.triad/venvs/comfyui/bin/activate \
#       && python main.py --listen 0.0.0.0 --port 8188
#
# Docker 容器通过 host.docker.internal:8188 访问
# =============================================================================

set -euo pipefail

VENV_DIR="${HOME}/.triad/venvs/comfyui"
COMFYUI_DIR="${HOME}/.triad/apps/comfyui"
MODELS_DIR="${HOME}/.triad/models/comfyui"
CUSTOM_NODES_DIR="${COMFYUI_DIR}/custom_nodes"

echo "=== Triad ComfyUI Native Installer ==="
echo "VENV_DIR:    ${VENV_DIR}"
echo "COMFYUI_DIR: ${COMFYUI_DIR}"
echo "MODELS_DIR:  ${MODELS_DIR}"
echo ""

# ---------------------------------------------------------------------------
# 0. 前置检查
# ---------------------------------------------------------------------------

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }
command -v git >/dev/null 2>&1 || { echo "ERROR: git not found"; exit 1; }
command -v nvidia-smi >/dev/null 2>&1 || { echo "WARNING: nvidia-smi not found — GPU 驱动可能未安装"; }

# 检查 CUDA 可用性 (宿主机侧)
if command -v nvcc >/dev/null 2>&1; then
    echo "CUDA Toolkit: $(nvcc --version | grep release)"
else
    echo "WARNING: nvcc not found — PyTorch 将安装预编译 CUDA 二进制"
fi

# Python 版本检查
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python version: ${PY_VER}"
if python3 -c 'import sys; exit(0 if sys.version_info >= (3,10) else 1)'; then
    echo "Python version OK (>= 3.10)"
else
    echo "ERROR: Python >= 3.10 required"
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. 创建目录结构
# ---------------------------------------------------------------------------

mkdir -p "${VENV_DIR}"
mkdir -p "${COMFYUI_DIR}"
mkdir -p "${MODELS_DIR}"
mkdir -p "${CUSTOM_NODES_DIR}"

# 模型子目录
for subdir in checkpoints loras controlnet vae upscale_models insightface clip unet diffusion_models; do
    mkdir -p "${MODELS_DIR}/${subdir}"
done

echo ""
echo "=== Step 1/7: Creating Python venv ==="
if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    python3 -m venv "${VENV_DIR}"
    echo "venv created at ${VENV_DIR}"
else
    echo "venv already exists, skipping creation"
fi

source "${VENV_DIR}/bin/activate"

# 升级 pip
pip install --upgrade pip wheel setuptools

# ---------------------------------------------------------------------------
# 2. 安装 PyTorch CUDA 版 (cu121)
# ---------------------------------------------------------------------------

echo ""
echo "=== Step 2/7: Installing PyTorch (CUDA 12.1) ==="
# 魔改 2080Ti 22GB 使用 Turing SM_75，cu121 完全兼容
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# 验证 PyTorch CUDA
python3 -c "import torch; print(f'PyTorch {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"

# ---------------------------------------------------------------------------
# 3. 克隆 ComfyUI 本体
# ---------------------------------------------------------------------------

echo ""
echo "=== Step 3/7: Cloning ComfyUI ==="
if [ ! -d "${COMFYUI_DIR}/.git" ]; then
    git clone https://github.com/comfyanonymous/ComfyUI.git "${COMFYUI_DIR}"
    echo "ComfyUI cloned to ${COMFYUI_DIR}"
else
    echo "ComfyUI already exists, pulling latest..."
    cd "${COMFYUI_DIR}"
    git pull --ff-only
fi

# ---------------------------------------------------------------------------
# 4. 安装 ComfyUI 核心依赖
# ---------------------------------------------------------------------------

echo ""
echo "=== Step 4/7: Installing ComfyUI dependencies ==="
cd "${COMFYUI_DIR}"
pip install -r requirements.txt

# 额外依赖: insightface (InstantID 需要)
pip install insightface onnxruntime-gpu

# ---------------------------------------------------------------------------
# 5. 创建模型目录软链接 (将模型集中存储在 ~/.triad/models/)
# ---------------------------------------------------------------------------

echo ""
echo "=== Step 5/7: Linking model directories ==="
cd "${COMFYUI_DIR}"

# 确保 models 目录存在
mkdir -p models

# 创建软链接 (如果尚未存在)
link_model_dir() {
    local name="$1"
    if [ -L "models/${name}" ]; then
        echo "  models/${name} -> already linked"
    elif [ -d "models/${name}" ] && [ ! -L "models/${name}" ]; then
        echo "  WARNING: models/${name} is a real directory; preserving existing files"
    else
        ln -sf "${MODELS_DIR}/${name}" "models/${name}"
        echo "  models/${name} -> ${MODELS_DIR}/${name}"
    fi
}

link_model_dir "checkpoints"
link_model_dir "loras"
link_model_dir "controlnet"
link_model_dir "vae"
link_model_dir "upscale_models"
link_model_dir "clip"
link_model_dir "unet"
link_model_dir "diffusion_models"

# insightface 模型目录 (InstantID 需要)
if [ -d "${MODELS_DIR}/insightface" ]; then
    mkdir -p models/insightface
    # insightface 通常自动下载到 ~/.insightface，这里创建备用链接
    echo "  insightface models will be auto-downloaded to ~/.insightface on first use"
fi

# ---------------------------------------------------------------------------
# 6. 安装自定义节点 (Custom Nodes)
# ---------------------------------------------------------------------------

echo ""
echo "=== Step 6/7: Installing custom nodes ==="
cd "${CUSTOM_NODES_DIR}"

install_custom_node() {
    local repo_url="$1"
    local dir_name="$2"
    if [ ! -d "${dir_name}" ]; then
        echo "  Cloning ${dir_name}..."
        git clone "${repo_url}" "${dir_name}"
    else
        echo "  ${dir_name} already exists, pulling..."
        cd "${dir_name}" && git pull --ff-only && cd "${CUSTOM_NODES_DIR}"
    fi
    # 安装节点依赖 (如果有 requirements.txt)
    if [ -f "${dir_name}/requirements.txt" ]; then
        echo "  Installing ${dir_name} requirements..."
        pip install -r "${dir_name}/requirements.txt"
    fi
}

# ComfyUI-Manager (节点管理器，强烈推荐)
install_custom_node "https://github.com/ltdrdata/ComfyUI-Manager.git" "ComfyUI-Manager"

# ComfyUI-InstantID (面部一致性)
install_custom_node "https://github.com/cubiq/ComfyUI-InstantID.git" "ComfyUI-InstantID"

# ComfyUI-ControlNet-Aux (ControlNet 预处理)
install_custom_node "https://github.com/Fannovel16/comfyui_controlnet_aux.git" "comfyui_controlnet_aux"

# ComfyUI-VideoHelperSuite (视频处理)
install_custom_node "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git" "ComfyUI-VideoHelperSuite"

# ComfyUI-IPAdapter-Plus (IP-Adapter 增强)
install_custom_node "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git" "ComfyUI_IPAdapter_plus"

# WAS Node Suite (实用工具集合)
install_custom_node "https://github.com/WASasquatch/was-node-suite-comfyui.git" "was-node-suite-comfyui"

# ---------------------------------------------------------------------------
# 7. 完成报告
# ---------------------------------------------------------------------------

echo ""
echo "============================================"
echo "  ComfyUI Native Installation Complete!"
echo "============================================"
echo ""
echo "Install locations:"
echo "  venv:    ${VENV_DIR}"
echo "  app:     ${COMFYUI_DIR}"
echo "  models:  ${MODELS_DIR}"
echo ""
echo "Next steps:"
echo "  1. Place model files into ~/.triad/models/comfyui/"
echo "     - checkpoints/    : SDXL Base, Refiner, etc. (*.safetensors, *.ckpt)"
echo "     - loras/          : LoRA weights (*.safetensors)"
echo "     - controlnet/     : ControlNet models (*.pth, *.safetensors)"
echo "     - vae/            : VAE models (*.safetensors, *.pt)"
echo ""
echo "  2. Start ComfyUI:"
echo "     source ${VENV_DIR}/bin/activate"
echo "     cd ${COMFYUI_DIR}"
echo "     python main.py --listen 0.0.0.0 --port 8188 --preview-method auto --highvram"
echo ""
echo "  3. Docker containers access via:"
echo "     host.docker.internal:8188"
echo ""
echo "  4. Open browser at:"
echo "     http://localhost:8188"
echo ""
echo "VRAM mode for 2080Ti 22GB: --highvram (模型常驻 VRAM)"
echo "If OOM occurs, switch to --normalvram or --lowvram"
echo ""
