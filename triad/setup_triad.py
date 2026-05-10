#!/usr/bin/env python3
# Triad v3.1.0 setup: 创建 install.sh + docker-compose.v3.yml (OpenClaw Native)

import os, yaml, re

triad_dir = os.path.expanduser("~/Triad/triad")
os.chdir(triad_dir)

# ===== 1. 写入 install.sh =====
install_sh = '#!/usr/bin/env bash\n# =============================================================================\n# Triad v3.1.0 HPC 安装脚本\n# 流程: 检测环境 → 报告缺失 → 自动修复 → 启动服务\n# 用法: chmod +x install.sh && ./install.sh\n# =============================================================================\n\nset -e\n\nRED=\'\\033[0;31m\'; GREEN=\'\\033[0;32m\'; YELLOW=\'\\033[1;33m\'; BLUE=\'\\033[0;34m\'; CYAN=\'\\033[0;36m\'; BOLD=\'\\033[1m\'; NC=\'\\033[0m\'\n\nOK=0; WARN=0; FAIL=0\nFIXED=0\n\nok()   { printf "  ${GREEN}OK${NC}  %s\\n" "$1"; OK=$((OK+1)); }\nwarn() { printf "  ${YELLOW}WARN${NC} %s\\n" "$1"; WARN=$((WARN+1)); }\nfail() { printf "  ${RED}FAIL${NC} %s\\n" "$1"; FAIL=$((FAIL+1)); }\ninfo() { printf "  ${BLUE}INFO${NC} %s\\n" "$1"; }\nfix()  { printf "  ${GREEN}FIX${NC}  %s\\n" "$1"; FIXED=$((FIXED+1)); }\nsec()  { printf "\\n${BOLD}${CYAN}▶ %s${NC}\\n" "$1"; printf "${CYAN}%s${NC}\\n" "--------------------------------------------------"; }\n\n# ═════════════════════════════════════════════════════════════════════════════\n# 阶段一: 环境检测\n# ═════════════════════════════════════════════════════════════════════════════\n\ndetect() {\n    clear\n    printf "\\n${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}\\n"\n    printf "${BOLD}${CYAN}║     Triad v2.3.1 HPC 安装脚本 — 环境检测                    ║${NC}\\n"\n    printf "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}\\n"\n    printf "  用户: %s | 时间: %s\\n\\n" "$(whoami)" "$(date +\'%Y-%m-%d %H:%M:%S\')"\n\n    # ── 系统 ──\n    sec "系统环境"\n    grep -qi microsoft /proc/version 2>/dev/null && ok "WSL2" || info "原生Linux"\n    UBU=$(grep -oP \'VERSION_ID="\\K[0-9.]+\' /etc/os-release 2>/dev/null || echo "?")\n    [ "$UBU" = "22.04" ] || [ "$UBU" = "24.04" ] && ok "Ubuntu $UBU" || warn "Ubuntu $UBU"\n\n    # ── 硬件 ──\n    sec "硬件信息"\n    MEM_GB=$(free -g 2>/dev/null | awk \'/^Mem:/{print $2}\' 2>/dev/null || echo "?")\n    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo "未检测")\n    printf "  ${BLUE}INFO${NC} CPU: ${BOLD}%s${NC} (%s核) 内存:%sGB GPU:%s\\n" \\\n        "$(lscpu 2>/dev/null | grep \'Model name\' | head -1 | cut -d: -f2 | xargs 2>/dev/null || echo \'?\')" \\\n        "$(nproc 2>/dev/null || echo \'?\')" "$MEM_GB" "$GPU_INFO"\n\n    # ── Docker ──\n    sec "Docker"\n    if command -v docker >/dev/null 2>&1; then\n        ok "Docker $(docker --version|awk \'{print $3}\'|tr -d \',\')"\n        docker info >/dev/null 2>&1 && ok "Daemon 运行" || { fail "Daemon 未运行"; return 1; }\n        docker compose version >/dev/null 2>&1 && ok "Compose v2" || { command -v docker-compose >/dev/null 2>&1 && warn "Compose v1" || fail "Compose 未装"; }\n        docker info 2>/dev/null|grep -q nvidia && ok "NVIDIA Runtime" || warn "NVIDIA Runtime 未配置 (nvidia-ctk runtime configure --runtime=docker)"\n    else\n        fail "Docker 未安装"; return 1\n    fi\n\n    # ── Python ──\n    sec "Python"\n    command -v python3 >/dev/null 2>&1 || { fail "python3 未装"; return 1; }\n    PY_MINOR=$(python3 --version 2>&1|awk \'{print $2}\'|cut -d. -f2)\n    [ "$PY_MINOR" -ge 10 ] 2>/dev/null && ok "Python 3.$PY_MINOR" || warn "Python 3.$PY_MINOR"\n    for p in httpx aiohttp tiktoken pynvml python-dotenv websockets aiofiles; do\n        python3 -c "import $p" 2>/dev/null && ok "py:$p" || fail "py缺失:$p"\n    done\n\n    # ── Node.js ──\n    sec "Node.js"\n    command -v node >/dev/null 2>&1 && ok "Node $(node --version)" || fail "Node.js 未装"\n    command -v npm >/dev/null 2>&1 && ok "npm" || warn "npm 缺失"\n\n    # ── 目录 ──\n    sec "目录"\n    for d in "$HOME/.triad" "$HOME/.triad/models" "$HOME/.triad/memory" "$HOME/.triad/memory/config" "$HOME/.triad/memory/skills" "$HOME/.triad/memory/skills/self-evolved" "$HOME/.triad/apps" "$HOME/.triad/apps/comfyui"; do\n        [ -d "$d" ] && ok "dir:$(basename "$d")" || fail "缺dir:$(basename "$d")"\n    done\n    [ -d "$HOME/Triad" ] && ok "源码~/Triad" || fail "源码未克隆"\n\n    # ── 配置 ──\n    sec "配置"\n    [ -f "$HOME/.triad/.env" ] && ok ".env" || fail ".env 缺失"\n\n    # ── 模型 ──\n    sec "模型"\n    CNT=$(find "$HOME/.triad/models" -name "*.gguf" -type f 2>/dev/null|wc -l)\n    [ "$CNT" -gt 0 ] && ok "GGUF模型:$CNT个" || info "无GGUF模型 (使用外部llama-server)"\n\n    # ── Docker 镜像 ──\n    sec "Docker镜像"\n    for i in "qdrant/qdrant:v1.8.0" "triad/openclaw:hpc-latest" "triad/hermes:hpc-latest" "triad/registry:hpc-latest" "triad/clawpod:base" "triad/clawpod:gpu"; do\n        docker images --format "{{.Repository}}:{{.Tag}}" 2>/dev/null|grep -q "^$i$" && ok "img:$i" || { echo "$i"|grep -q "^triad/" && info "需build:$i" || warn "缺img:$i"; }\n    done\n\n    # ── 容器 ──\n    sec "容器"\n    for c in triad-openclaw triad-hermes triad-qdrant triad-registry; do\n        S=$(docker ps -a --filter "name=$c" --format "{{.Status}}" 2>/dev/null|head -1)\n        [ -n "$S" ] && { echo "$S"|grep -q "^Up" && ok "运行:$c" || warn "停止:$c"; } || info "未创建:$c"\n    done\n\n    # ── 端口 ──\n    sec "端口"\n    for e in "18080:OpenClaw" "17001:ACP" "17002:WS" "18000:Hermes" "19000:Embed" "16333:Qdrant" "18500:Registry" "18188:ComfyUI"; do\n        P=$(echo "$e"|cut -d: -f1); ss -tlnp 2>/dev/null|grep -q ":$P " && ok "端口$P" || info "未监听:$P"\n    done\n\n    # ── llama-server ──\n    sec "llama-server"\n    curl -s http://localhost:40080/health >/dev/null 2>&1 && ok "llama-server:40080" || info "llama-server未检测 (外部运行)"\n\n    # ── WebUI ──\n    sec "WebUI"\n    for d in "$HOME/Triad/triad/webui" "$HOME/triad/webui"; do\n        [ -d "$d" ] && [ -f "$d/package.json" ] && { ok "源码:$d"; [ -d "$d/node_modules" ] && ok "node_modules" || warn "未npm install"; [ -d "$d/dist" ] && ok "已构建" || warn "未npm run build"; break; }\n    done\n\n    # ── 总结 ──\n    printf "\\n${BOLD}${CYAN}══════════════ 检测结果 ══════════════${NC}\\n"\n    printf "${GREEN}OK${NC}:%d  ${YELLOW}WARN${NC}:%d  ${RED}FAIL${NC}:%d\\n\\n" "$OK" "$WARN" "$FAIL"\n\n    if [ "$FAIL" -gt 0 ]; then\n        printf "  ${YELLOW}有 %d 项需要修复，开始自动修复...${NC}\\n\\n" "$FAIL"\n        return 1\n    else\n        printf "  ${GREEN}环境检测全部通过！${NC}\\n\\n"\n        return 0\n    fi\n}\n\n# ═════════════════════════════════════════════════════════════════════════════\n# 阶段二: 自动修复\n# ═════════════════════════════════════════════════════════════════════════════\n\nfix_all() {\n    sec "开始自动修复"\n\n    # ── 1. 安装缺失 Python 包 ──\n    for p in httpx aiohttp tiktoken pynvml python-dotenv websockets aiofiles; do\n        python3 -c "import $p" 2>/dev/null || {\n            fix "安装 Python 包: $p"\n            pip3 install "$p" 2>/dev/null || pip install "$p"\n        }\n    done\n\n    # ── 2. 创建缺失目录 ──\n    for d in "$HOME/.triad" "$HOME/.triad/models" "$HOME/.triad/memory" "$HOME/.triad/memory/config" "$HOME/.triad/memory/skills" "$HOME/.triad/memory/skills/self-evolved" "$HOME/.triad/apps" "$HOME/.triad/apps/comfyui"; do\n        [ -d "$d" ] || { fix "创建目录: $d"; mkdir -p "$d"; }\n    done\n\n    # ── 3. 写入 .env ──\n    if [ ! -f "$HOME/.triad/.env" ]; then\n        fix "写入 ~/.triad/.env"\n        cat > "$HOME/.triad/.env" << \'EOF\'\nTRIAD_ROOT=/home/iuranus/.triad\nUID=1000\nGID=1000\nGPU_MEMORY=22000\nCPU_CORES=48\nGATEWAY_PORT=18080\nGATEWAY_HOST=0.0.0.0\nLLAMA_HOST=192.168.0.128\nLLAMA_PORT=40080\nLLAMA_CTX_SIZE=8192\nQDRANT_HOST=qdrant\nQDRANT_PORT=16333\nCOMFYUI_HOST=host.docker.internal\nCOMFYUI_PORT=18188\nHF_ENDPOINT=https://hf-mirror.com\nLOG_LEVEL=info\nEOF\n    fi\n\n    # ── 4. 修复 docker-compose.hpc.yml ──\n    sec "修复 docker-compose.hpc.yml"\n    cd "$HOME/Triad/triad"\n    \n    # 先恢复原始文件\n    git checkout -- docker-compose.hpc.yml 2>/dev/null || true\n    \n    fix "清理 YAML 注释和无效配置"\n    python3 << \'PYEOF\'\nimport yaml, re\n\nwith open(\'docker-compose.hpc.yml\', \'r\') as f:\n    lines = f.readlines()\n\ndef is_chinese_comment(s):\n    if not s.startswith(\'#\'): return False\n    rest = s[1:].strip()\n    if re.search(r\'[\\u4e00-\\u9fff]\', rest): return True\n    if not rest.endswith(\':\') and not rest.startswith(\'-\') and len(rest) > 3: return True\n    return False\n\ndef is_restore_key(s):\n    if not s.startswith(\'#\'): return False\n    rest = s[1:].strip()\n    if re.search(r\'[\\u4e00-\\u9fff]\', rest): return False\n    if not rest.endswith(\':\'): return False\n    key = rest[:-1].strip()\n    if \' \' in key: return False\n    return bool(re.match(r\'^[a-zA-Z_][a-zA-Z0-9_\\-.]*$\', key))\n\nINVALID = {\'swap\', \'cpu_percent\', \'memswap_limit\', \'isolation\', \'init\',\n           \'oom_kill_disable\', \'oom_score_adj\', \'autoremove\'}\n\nstage1 = []\nfor line in lines:\n    s = line.strip()\n    if s == \'\':\n        stage1.append(\'\\n\')\n        continue\n    if s.startswith(\'#\'):\n        if is_restore_key(s):\n            rest = s[1:].strip()\n            indent = len(line) - len(line.lstrip())\n            stage1.append(\' \' * indent + rest + \'\\n\')\n        continue\n    if \' #\' in line:\n        idx = line.index(\' #\')\n        before = line[:idx].rstrip()\n        if before:\n            stage1.append(before + \'\\n\')\n            continue\n    m = re.match(r\'^(\\s*)(\\w+):\', s)\n    if m and m.group(2) in INVALID:\n        continue\n    stage1.append(line)\n\nresult = []\nprev_empty = False\nfor line in stage1:\n    empty = line.strip() == \'\'\n    if empty and prev_empty: continue\n    result.append(line)\n    prev_empty = empty\n\nif result and not result[-1].endswith(\'\\n\'): result[-1] += \'\\n\'\noutput = \'\'.join(result)\n\n# 深度清理：递归删除所有 None、空 dict、空 list\ndef deep_clean(obj):\n    if isinstance(obj, dict):\n        cleaned = {k: deep_clean(v) for k, v in obj.items() if v is not None}\n        cleaned = {k: v for k, v in cleaned.items() if v is not None and v != {} and v != []}\n        return cleaned if cleaned else None\n    elif isinstance(obj, list):\n        cleaned = [deep_clean(i) for i in obj]\n        cleaned = [i for i in cleaned if i is not None and i != {} and i != []]\n        return cleaned if cleaned else None\n    return obj\n\nparsed = yaml.safe_load(output)\nparsed = deep_clean(parsed)\n\noutput = yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=True, width=200)\n\n# 最终验证\nreparsed = yaml.safe_load(output)\nservices = reparsed[\'services\']\n# docker-compose 格式检查\nerrors = []\nfor svc_name, svc in services.items():\n    devices = svc.get(\'deploy\', {}).get(\'resources\', {}).get(\'reservations\', {}).get(\'devices\')\n    if devices is not None and not isinstance(devices, list):\n        errors.append(f"{svc_name}: reservations.devices invalid type")\n    for key in [\'ports\', \'volumes\', \'profiles\']:\n        val = svc.get(key)\n        if val is not None and not isinstance(val, list):\n            errors.append(f"{svc_name}: {key} should be array")\nif errors:\n    print(f"❌ YAML 验证失败: {errors}")\n    exit(1)\n\nprint(f"✅ YAML 验证通过 | 服务: {len(services)}个 | 0 错误")\nwith open(\'docker-compose.hpc.yml\', \'w\') as f:\n    f.write(output)\nPYEOF\n\n    # ── 5. docker-compose 验证 ──\n    fix "验证 docker-compose 配置"\n    docker-compose -f docker-compose.hpc.yml config > /dev/null || {\n        fail "docker-compose 配置验证失败"\n        return 1\n    }\n\n    printf "\\n${GREEN}所有修复完成！${NC}\\n"\n    return 0\n}\n\n# ═════════════════════════════════════════════════════════════════════════════\n# 阶段三: 构建与启动\n# ═════════════════════════════════════════════════════════════════════════════\n\nstart_all() {\n    sec "拉取外部镜像"\n    docker pull qdrant/qdrant:v1.8.0 || warn "Docker Hub 超时，后续重试"\n\n    sec "构建 Triad 镜像"\n    docker-compose -f docker-compose.hpc.yml build || warn "部分镜像构建失败"\n\n    sec "启动核心服务"\n    docker-compose -f docker-compose.hpc.yml up -d\n\n    sec "服务状态"\n    docker-compose -f docker-compose.hpc.yml ps\n\n    printf "\\n${BOLD}${CYAN}══════════════════════════════════════════════════════════════${NC}\\n"\n    printf "${GREEN}${BOLD}  Triad HPC 启动完成！${NC}\\n\\n"\n    printf "  访问地址:\\n"\n    printf "    ${CYAN}WebUI${NC}:     http://localhost:15173\\n"\n    printf "    ${CYAN}OpenClaw${NC}:  http://localhost:18080\\n"\n    printf "    ${CYAN}WebSocket${NC}: ws://localhost:17002\\n"\n    printf "    ${CYAN}Qdrant${NC}:    http://localhost:16333\\n"\n    printf "    ${CYAN}llama${NC}:     http://localhost:40080\\n"\n    printf "\\n  查看日志: ${YELLOW}docker-compose -f docker-compose.hpc.yml logs -f${NC}\\n"\n    printf "\\n"\n}\n\n# ═════════════════════════════════════════════════════════════════════════════\n# 主入口\n# ═════════════════════════════════════════════════════════════════════════════\n\nmain() {\n    # 阶段一: 检测\n    if detect; then\n        # 检测通过，直接启动\n        read -p "环境检测全部通过，是否直接启动? [Y/n] " ans\n        [ "$ans" = "n" ] || [ "$ans" = "N" ] && exit 0\n    else\n        # 有失败项，自动修复\n        read -p "发现 $FAIL 项需要修复，是否自动修复? [Y/n] " ans\n        [ "$ans" = "n" ] || [ "$ans" = "N" ] && exit 1\n        fix_all || exit 1\n\n        # 修复后再检测一次\n        sec "重新检测"\n        detect || { fail "修复后仍有错误，请手动检查"; exit 1; }\n    fi\n\n    # 阶段三: 启动\n    start_all\n}\n\nmain "$@"\n'

with open("install.sh", "w") as f:
    f.write(install_sh)
os.chmod("install.sh", 0o755)
print("✅ install.sh 已创建")

# ===== 2. 修复 docker-compose.hpc.yml =====
with open("docker-compose.hpc.yml", "r") as f:
    lines = f.readlines()

def is_chinese_comment(s):
    if not s.startswith("#"): return False
    rest = s[1:].strip()
    if re.search(r"[\u4e00-\u9fff]", rest): return True
    if not rest.endswith(":") and not rest.startswith("-") and len(rest) > 3: return True
    return False

def is_restore_key(s):
    if not s.startswith("#"): return False
    rest = s[1:].strip()
    if re.search(r"[\u4e00-\u9fff]", rest): return False
    if not rest.endswith(":"): return False
    key = rest[:-1].strip()
    if " " in key: return False
    return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_\-.]*$", key))

INVALID = {"swap", "cpu_percent", "memswap_limit", "isolation", "init",
            "oom_kill_disable", "oom_score_adj", "autoremove"}

stage1 = []
for line in lines:
    s = line.strip()
    if s == "":
        stage1.append("\n")
        continue
    if s.startswith("#"):
        if is_restore_key(s):
            rest = s[1:].strip()
            indent = len(line) - len(line.lstrip())
            stage1.append(" " * indent + rest + "\n")
        continue
    if " #" in line:
        idx = line.index(" #")
        before = line[:idx].rstrip()
        if before:
            stage1.append(before + "\n")
            continue
    m = re.match(r"^(\s*)(\w+):", s)
    if m and m.group(2) in INVALID:
        continue
    stage1.append(line)

result = []
prev_empty = False
for line in stage1:
    empty = line.strip() == ""
    if empty and prev_empty: continue
    result.append(line)
    prev_empty = empty

if result and not result[-1].endswith("\n"): result[-1] += "\n"
output = "".join(result)

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

# 验证
reparsed = yaml.safe_load(output)
services = reparsed["services"]
errors = []
for svc_name, svc in services.items():
    devices = svc.get("deploy", {}).get("resources", {}).get("reservations", {}).get("devices")
    if devices is not None and not isinstance(devices, list):
        errors.append(f"{svc_name}: reservations.devices invalid type")
    for key in ["ports", "volumes", "profiles"]:
        val = svc.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"{svc_name}: {key} should be array")

if errors:
    print(f"❌ YAML 错误: {errors}")
    exit(1)

with open("docker-compose.hpc.yml", "w") as f:
    f.write(output)
print(f"✅ docker-compose.hpc.yml 已修复 | 服务: {len(services)}个 | 0 错误")
print("\n接下来执行: cd ~/Triad/triad && chmod +x install.sh && ./install.sh")
