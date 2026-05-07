#!/usr/bin/env bash
# =============================================================================
# Triad v2.3.1 HPC 安装脚本
# 流程: 检测环境 → 报告缺失 → 自动修复 → 启动服务
# 用法: chmod +x install.sh && ./install.sh
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

OK=0; WARN=0; FAIL=0
FIXED=0

ok()   { printf "  ${GREEN}OK${NC}  %s\n" "$1"; OK=$((OK+1)); }
warn() { printf "  ${YELLOW}WARN${NC} %s\n" "$1"; WARN=$((WARN+1)); }
fail() { printf "  ${RED}FAIL${NC} %s\n" "$1"; FAIL=$((FAIL+1)); }
info() { printf "  ${BLUE}INFO${NC} %s\n" "$1"; }
fix()  { printf "  ${GREEN}FIX${NC}  %s\n" "$1"; FIXED=$((FIXED+1)); }
sec()  { printf "\n${BOLD}${CYAN}▶ %s${NC}\n" "$1"; printf "${CYAN}%s${NC}\n" "--------------------------------------------------"; }

# ═════════════════════════════════════════════════════════════════════════════
# 阶段一: 环境检测
# ═════════════════════════════════════════════════════════════════════════════

detect() {
    clear
    printf "\n${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}\n"
    printf "${BOLD}${CYAN}║     Triad v2.3.1 HPC 安装脚本 — 环境检测                    ║${NC}\n"
    printf "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}\n"
    printf "  用户: %s | 时间: %s\n\n" "$(whoami)" "$(date +'%Y-%m-%d %H:%M:%S')"

    # ── 系统 ──
    sec "系统环境"
    grep -qi microsoft /proc/version 2>/dev/null && ok "WSL2" || info "原生Linux"
    UBU=$(grep -oP 'VERSION_ID="\K[0-9.]+' /etc/os-release 2>/dev/null || echo "?")
    [ "$UBU" = "22.04" ] || [ "$UBU" = "24.04" ] && ok "Ubuntu $UBU" || warn "Ubuntu $UBU"

    # ── 硬件 ──
    sec "硬件信息"
    MEM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}' 2>/dev/null || echo "?")
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "未检测")
    printf "  ${BLUE}INFO${NC} CPU: ${BOLD}%s${NC} (%s核) 内存:%sGB GPU:%s\n" \
        "$(lscpu 2>/dev/null | grep 'Model name' | head -1 | cut -d: -f2 | xargs 2>/dev/null || echo '?')" \
        "$(nproc 2>/dev/null || echo '?')" "$MEM_GB" "$GPU_INFO"

    # ── Docker ──
    sec "Docker"
    if command -v docker >/dev/null 2>&1; then
        ok "Docker $(docker --version|awk '{print $3}'|tr -d ',')"
        docker info >/dev/null 2>&1 && ok "Daemon 运行" || { fail "Daemon 未运行"; return 1; }
        docker compose version >/dev/null 2>&1 && ok "Compose v2" || { command -v docker-compose >/dev/null 2>&1 && warn "Compose v1" || fail "Compose 未装"; }
        docker info 2>/dev/null|grep -q nvidia && ok "NVIDIA Runtime" || warn "NVIDIA Runtime 未配置 (nvidia-ctk runtime configure --runtime=docker)"
    else
        fail "Docker 未安装"; return 1
    fi

    # ── Python ──
    sec "Python"
    command -v python3 >/dev/null 2>&1 || { fail "python3 未装"; return 1; }
    PY_MINOR=$(python3 --version 2>&1|awk '{print $2}'|cut -d. -f2)
    [ "$PY_MINOR" -ge 10 ] 2>/dev/null && ok "Python 3.$PY_MINOR" || warn "Python 3.$PY_MINOR"
    for p in httpx aiohttp tiktoken pynvml python-dotenv websockets aiofiles; do
        python3 -c "import $p" 2>/dev/null && ok "py:$p" || fail "py缺失:$p"
    done

    # ── Node.js ──
    sec "Node.js"
    command -v node >/dev/null 2>&1 && ok "Node $(node --version)" || fail "Node.js 未装"
    command -v npm >/dev/null 2>&1 && ok "npm" || warn "npm 缺失"

    # ── 目录 ──
    sec "目录"
    for d in "$HOME/.triad" "$HOME/.triad/models" "$HOME/.triad/memory" "$HOME/.triad/memory/config" "$HOME/.triad/memory/skills" "$HOME/.triad/memory/skills/self-evolved" "$HOME/.triad/apps" "$HOME/.triad/apps/comfyui"; do
        [ -d "$d" ] && ok "dir:$(basename "$d")" || fail "缺dir:$(basename "$d")"
    done
    [ -d "$HOME/Triad" ] && ok "源码~/Triad" || fail "源码未克隆"

    # ── 配置 ──
    sec "配置"
    [ -f "$HOME/.triad/.env" ] && ok ".env" || fail ".env 缺失"

    # ── 模型 ──
    sec "模型"
    CNT=$(find "$HOME/.triad/models" -name "*.gguf" -type f 2>/dev/null|wc -l)
    [ "$CNT" -gt 0 ] && ok "GGUF模型:$CNT个" || info "无GGUF模型 (使用外部llama-server)"

    # ── Docker 镜像 ──
    sec "Docker镜像"
    for i in "qdrant/qdrant:v1.8.0" "triad/openclaw:hpc-latest" "triad/hermes:hpc-latest" "triad/registry:hpc-latest" "triad/clawpod:base" "triad/clawpod:gpu"; do
        docker images --format "{{.Repository}}:{{.Tag}}" 2>/dev/null|grep -q "^$i$" && ok "img:$i" || { echo "$i"|grep -q "^triad/" && info "需build:$i" || warn "缺img:$i"; }
    done

    # ── 容器 ──
    sec "容器"
    for c in triad-openclaw triad-hermes triad-qdrant triad-registry; do
        S=$(docker ps -a --filter "name=$c" --format "{{.Status}}" 2>/dev/null|head -1)
        [ -n "$S" ] && { echo "$S"|grep -q "^Up" && ok "运行:$c" || warn "停止:$c"; } || info "未创建:$c"
    done

    # ── 端口 ──
    sec "端口"
    for e in "18080:OpenClaw" "17001:ACP" "17002:WS" "18000:Hermes" "19000:Embed" "16333:Qdrant" "18500:Registry" "18188:ComfyUI"; do
        P=$(echo "$e"|cut -d: -f1); ss -tlnp 2>/dev/null|grep -q ":$P " && ok "端口$P" || info "未监听:$P"
    done

    # ── llama-server ──
    sec "llama-server"
    curl -s http://localhost:40080/health >/dev/null 2>&1 && ok "llama-server:40080" || info "llama-server未检测 (外部运行)"

    # ── WebUI ──
    sec "WebUI"
    for d in "$HOME/Triad/triad/webui" "$HOME/triad/webui"; do
        [ -d "$d" ] && [ -f "$d/package.json" ] && { ok "源码:$d"; [ -d "$d/node_modules" ] && ok "node_modules" || warn "未npm install"; [ -d "$d/dist" ] && ok "已构建" || warn "未npm run build"; break; }
    done

    # ── 总结 ──
    printf "\n${BOLD}${CYAN}══════════════ 检测结果 ══════════════${NC}\n"
    printf "${GREEN}OK${NC}:%d  ${YELLOW}WARN${NC}:%d  ${RED}FAIL${NC}:%d\n\n" "$OK" "$WARN" "$FAIL"

    if [ "$FAIL" -gt 0 ]; then
        printf "  ${YELLOW}有 %d 项需要修复，开始自动修复...${NC}\n\n" "$FAIL"
        return 1
    else
        printf "  ${GREEN}环境检测全部通过！${NC}\n\n"
        return 0
    fi
}

# ═════════════════════════════════════════════════════════════════════════════
# 阶段二: 自动修复
# ═════════════════════════════════════════════════════════════════════════════

fix_all() {
    sec "开始自动修复"

    # ── 1. 安装缺失 Python 包 ──
    for p in httpx aiohttp tiktoken pynvml python-dotenv websockets aiofiles; do
        python3 -c "import $p" 2>/dev/null || {
            fix "安装 Python 包: $p"
            pip3 install "$p" || pip install "$p"
        }
    done

    # ── 2. 创建缺失目录 ──
    for d in "$HOME/.triad" "$HOME/.triad/models" "$HOME/.triad/memory" "$HOME/.triad/memory/config" "$HOME/.triad/memory/skills" "$HOME/.triad/memory/skills/self-evolved" "$HOME/.triad/apps" "$HOME/.triad/apps/comfyui"; do
        [ -d "$d" ] || { fix "创建目录: $d"; mkdir -p "$d"; }
    done

    # ── 3. 写入 .env ──
    if [ ! -f "$HOME/.triad/.env" ]; then
        fix "写入 ~/.triad/.env"
        cat > "$HOME/.triad/.env" << 'EOF'
TRIAD_ROOT=/home/iuranus/.triad
UID=1000
GID=1000
GPU_MEMORY=22000
CPU_CORES=48
GATEWAY_PORT=18080
GATEWAY_HOST=0.0.0.0
LLAMA_HOST=192.168.0.128
LLAMA_PORT=40080
LLAMA_CTX_SIZE=8192
QDRANT_HOST=qdrant
QDRANT_PORT=16333
COMFYUI_HOST=host.docker.internal
COMFYUI_PORT=18188
HF_ENDPOINT=https://hf-mirror.com
LOG_LEVEL=info
EOF
    fi

    # ── 4. 修复 docker-compose.hpc.yml ──
    sec "修复 docker-compose.hpc.yml"
    cd "$HOME/Triad/triad"
    
    # 先恢复原始文件
    git checkout -- docker-compose.hpc.yml 2>/dev/null || true
    
    fix "清理 YAML 注释和无效配置"
    python3 << 'PYEOF'
import yaml, re

with open('docker-compose.hpc.yml', 'r') as f:
    lines = f.readlines()

def is_chinese_comment(s):
    if not s.startswith('#'): return False
    rest = s[1:].strip()
    if re.search(r'[\u4e00-\u9fff]', rest): return True
    if not rest.endswith(':') and not rest.startswith('-') and len(rest) > 3: return True
    return False

def is_restore_key(s):
    if not s.startswith('#'): return False
    rest = s[1:].strip()
    if re.search(r'[\u4e00-\u9fff]', rest): return False
    if not rest.endswith(':'): return False
    key = rest[:-1].strip()
    if ' ' in key: return False
    return bool(re.match(r'^[a-zA-Z_][a-zA-Z0-9_\-.]*$', key))

INVALID = {'swap', 'cpu_percent', 'memswap_limit', 'isolation', 'init',
           'oom_kill_disable', 'oom_score_adj', 'autoremove'}

stage1 = []
for line in lines:
    s = line.strip()
    if s == '':
        stage1.append('\n')
        continue
    if s.startswith('#'):
        if is_restore_key(s):
            rest = s[1:].strip()
            indent = len(line) - len(line.lstrip())
            stage1.append(' ' * indent + rest + '\n')
        continue
    if ' #' in line:
        idx = line.index(' #')
        before = line[:idx].rstrip()
        if before:
            stage1.append(before + '\n')
            continue
    m = re.match(r'^(\s*)(\w+):', s)
    if m and m.group(2) in INVALID:
        continue
    stage1.append(line)

result = []
prev_empty = False
for line in stage1:
    empty = line.strip() == ''
    if empty and prev_empty: continue
    result.append(line)
    prev_empty = empty

if result and not result[-1].endswith('\n'): result[-1] += '\n'
output = ''.join(result)

# 深度清理：递归删除所有 None、空 dict、空 list
def deep_clean(obj):
    if isinstance(obj, dict):
        cleaned = {k: deep_clean(v) for k, v in obj.items() if v is not None}
        cleaned = {k: v for k, v in cleaned.items() if v is not None and v != {} and v != []}
        return cleaned if cleaned else None
    elif isinstance(obj, list):
        cleaned = [deep_clean(i) for i in obj]
        cleaned = [i for i in cleaned if i is not None and i != {} and i != []]
        return cleaned if cleaned else None
    return obj

parsed = yaml.safe_load(output)
parsed = deep_clean(parsed)

output = yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=True, width=200)

# 最终验证
reparsed = yaml.safe_load(output)
services = reparsed['services']
# docker-compose 格式检查
errors = []
for svc_name, svc in services.items():
    devices = svc.get('deploy', {}).get('resources', {}).get('reservations', {}).get('devices')
    if devices is not None and not isinstance(devices, list):
        errors.append(f"{svc_name}: reservations.devices invalid type")
    for key in ['ports', 'volumes', 'profiles']:
        val = svc.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"{svc_name}: {key} should be array")
if errors:
    print(f"❌ YAML 验证失败: {errors}")
    exit(1)

print(f"✅ YAML 验证通过 | 服务: {len(services)}个 | 0 错误")
with open('docker-compose.hpc.yml', 'w') as f:
    f.write(output)
PYEOF

    # ── 5. docker-compose 验证 ──
    fix "验证 docker-compose 配置"
    docker-compose -f docker-compose.hpc.yml config > /dev/null || {
        fail "docker-compose 配置验证失败"
        return 1
    }

    printf "\n${GREEN}所有修复完成！${NC}\n"
    return 0
}

# ═════════════════════════════════════════════════════════════════════════════
# 阶段三: 构建与启动
# ═════════════════════════════════════════════════════════════════════════════

start_all() {
    sec "拉取外部镜像"
    docker pull qdrant/qdrant:v1.8.0 || warn "Docker Hub 超时，后续重试"

    sec "构建 Triad 镜像"
    docker-compose -f docker-compose.hpc.yml build || warn "部分镜像构建失败"

    sec "启动核心服务"
    docker-compose -f docker-compose.hpc.yml up -d

    sec "服务状态"
    docker-compose -f docker-compose.hpc.yml ps

    printf "\n${BOLD}${CYAN}══════════════════════════════════════════════════════════════${NC}\n"
    printf "${GREEN}${BOLD}  Triad HPC 启动完成！${NC}\n\n"
    printf "  访问地址:\n"
    printf "    ${CYAN}WebUI${NC}:     http://localhost:15173\n"
    printf "    ${CYAN}OpenClaw${NC}:  http://localhost:18080\n"
    printf "    ${CYAN}WebSocket${NC}: ws://localhost:17002\n"
    printf "    ${CYAN}Qdrant${NC}:    http://localhost:16333\n"
    printf "    ${CYAN}llama${NC}:     http://localhost:40080\n"
    printf "\n  查看日志: ${YELLOW}docker-compose -f docker-compose.hpc.yml logs -f${NC}\n"
    printf "\n"
}

# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════

main() {
    # 阶段一: 检测
    if detect; then
        # 检测通过，直接启动
        read -p "环境检测全部通过，是否直接启动? [Y/n] " ans
        [ "$ans" = "n" ] || [ "$ans" = "N" ] && exit 0
    else
        # 有失败项，自动修复
        read -p "发现 $FAIL 项需要修复，是否自动修复? [Y/n] " ans
        [ "$ans" = "n" ] || [ "$ans" = "N" ] && exit 1
        fix_all || exit 1

        # 修复后再检测一次
        sec "重新检测"
        detect || { fail "修复后仍有错误，请手动检查"; exit 1; }
    fi

    # 阶段三: 启动
    start_all
}

main "$@"
