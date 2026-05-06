#!/bin/bash
set -euo pipefail

# =============================================================================
# WSL2 网关路由脚本 — 解决 Windows 宿主机访问 WSL2 内 Docker 服务
# =============================================================================
# 问题: WSL2 内的 Docker 容器运行在虚拟网卡后面，Windows 浏览器无法直接
#       访问 localhost:18080（即使 Docker 做了 -p 18080:18080 映射）
#
# 方案: 1. 在 WSL2 侧将服务绑定到 0.0.0.0（Docker -p 已默认如此）
#       2. 获取 WSL2 实例的 eth0 IP
#       3. 在 Windows 侧使用 netsh portproxy 将 localhost:PORT → WSL2_IP:PORT
#       4. 配置 Windows 防火墙规则允许入站
#       5. 可选: 启用 WSL2 localhost 互通（仅适用于较新 Windows 11）
#
# 用法:
#   ./wsl2_gateway.sh setup    # 配置端口转发和防火墙
#   ./wsl2_gateway.sh status   # 查看当前状态
#   ./wsl2_gateway.sh clean    # 清理所有转发规则
#   ./wsl2_gateway.sh auto     # 自动检测并配置所有 Triad 服务端口
# =============================================================================

# --- 颜色定义 ---
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m'

# --- 日志函数 ---
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${CYAN}${BOLD}[STEP]${NC}  $*"; }
log_fatal() { echo -e "${RED}${BOLD}[FATAL]${NC} $*"; }
success()   { echo -e "${GREEN}✓${NC} $*"; }

# --- 致命退出 ---
fatal_exit() {
    log_fatal "$1"
    echo -e "${YELLOW}修复指引:${NC} $2"
    exit 1
}

# --- 帮助信息 ---
show_help() {
    cat << 'EOF'
用法: ./wsl2_gateway.sh [命令] [选项]

命令:
  setup [端口...]  配置端口转发（默认: 18080 18000 16333 16379）
  status          查看当前 WSL2 IP、端口转发状态和防火墙规则
  clean [端口...]  清理端口转发规则（默认清理所有 Triad 端口）
  auto            自动检测 Triad Docker 容器并配置对应端口
  test            测试所有已配置端口是否可连通
  help            显示此帮助信息

环境变量:
  WSL2_GATEWAY_PORTS    默认端口列表（空格分隔）
  WSL2_AUTO_CLEAN       是否在 setup 前自动清理旧规则（true/false）

示例:
  ./wsl2_gateway.sh setup
  ./wsl2_gateway.sh setup 18080 3000 9090
  ./wsl2_gateway.sh auto
  ./wsl2_gateway.sh clean
  WSL2_AUTO_CLEAN=true ./wsl2_gateway.sh setup
EOF
}

# --- 检测是否在 WSL2 环境 ---
check_wsl2() {
    local is_wsl2=false
    if uname -a | grep -qi "WSL2" 2>/dev/null; then
        is_wsl2=true
    elif [[ -d "/mnt/wslg" ]] && [[ -e "/proc/sys/fs/binfmt_misc/WSLInterop" ]]; then
        is_wsl2=true
    elif grep -qi "microsoft" /proc/version 2>/dev/null; then
        is_wsl2=true
    fi

    if [[ "$is_wsl2" != true ]]; then
        fatal_exit "未检测到 WSL2 环境" \
            "此脚本专为 WSL2 设计。请确认你在 WSL2 Ubuntu 中运行。\n   检查: uname -a | grep WSL2"
    fi
    success "WSL2 环境确认"
}

# --- 检测 PowerShell 可用性 ---
check_powershell() {
    log_info "检测 Windows PowerShell 可用性..."
    if ! command -v powershell.exe &>/dev/null; then
        fatal_exit "powershell.exe 不可用" \
            "WSL2 应该自带 powershell.exe 调用能力。请检查:\n   1) /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe 是否存在\n   2) 或尝试: powershell.exe -Command 'Write-Host OK'\n   3) 如果路径不同，设置 PATH: export PATH=\$PATH:/mnt/c/Windows/System32/WindowsPowerShell/v1.0"
    fi

    # 测试 PowerShell 实际可执行
    local ps_test
    ps_test=$(powershell.exe -NoProfile -Command "Write-Output 'WSL2_PING_OK'" 2>/dev/null | tr -d '\r')
    if [[ "$ps_test" != "WSL2_PING_OK" ]]; then
        log_warn "PowerShell 响应异常，尝试使用 pwsh..."
        if command -v pwsh.exe &>/dev/null; then
            readonly PS_CMD="pwsh.exe"
        else
            readonly PS_CMD="powershell.exe"
            log_warn "PowerShell 可能无响应，某些操作可能需要管理员权限"
        fi
    else
        readonly PS_CMD="powershell.exe"
    fi
    success "PowerShell 可用: $PS_CMD"
}

# --- 获取 WSL2 IP ---
get_wsl2_ip() {
    local wsl_ip=""

    # 方法 1: ip addr show eth0
    wsl_ip=$(ip addr show eth0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1)

    # 方法 2: hostname -I
    if [[ -z "$wsl_ip" ]]; then
        wsl_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi

    # 方法 3: ip route
    if [[ -z "$wsl_ip" ]]; then
        wsl_ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[^ ]+')
    fi

    # 方法 4: /proc/net/fib_trie（备用）
    if [[ -z "$wsl_ip" ]]; then
        wsl_ip=$(grep -oP '172\.2[0-9]\.\d+\.\d+' /proc/net/fib_trie 2>/dev/null | head -1)
    fi

    if [[ -z "$wsl_ip" ]]; then
        fatal_exit "无法获取 WSL2 IP 地址" \
            "请手动检查:\n   ip addr show eth0\n   hostname -I\n   ip route get 1.1.1.1\n如果 eth0 不存在，可能是 WSL1 或网络配置异常"
    fi

    # 验证 IP 格式
    if [[ ! "$wsl_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        fatal_exit "获取的 WSL2 IP 格式无效: '$wsl_ip'" \
            "请检查网络配置: ip addr show"
    fi

    echo "$wsl_ip"
}

# --- 获取 Docker triad-bridge 网关 ---
get_triad_gateway() {
    local gateway=""
    if command -v docker &>/dev/null; then
        gateway=$(docker network inspect triad-bridge 2>/dev/null \
            | grep -oP '"Gateway":\s*"\K[^"]+' | head -1 || true)
    fi
    echo "${gateway:-未找到（triad-bridge 可能未创建）}"
}

# --- 执行 PowerShell 命令（封装错误处理） ---
ps_exec() {
    local cmd="$1"
    local desc="${2:-PowerShell 命令}"
    local output

    log_info "执行: $desc"
    local rc=0
    output=$($PS_CMD -NoProfile -NonInteractive -Command "$cmd" 2>&1) || rc=$?
    if [[ $rc -ne 0 ]]; then
        log_error "[$label] PowerShell command failed with exit code $rc"
        echo "$output" | head -5 | while read -r line; do log_warn "  > $line"; done
        return $rc
    fi

    # 检查常见错误
    if echo "$output" | grep -qi "Access is denied"; then
        log_error "$desc 失败: 访问被拒绝（需要管理员权限）"
        echo -e "${YELLOW}修复:${NC} 以管理员身份启动 PowerShell 并运行:\n   $cmd"
        return 1
    fi
    if echo "$output" | grep -qi "requested operation requires elevation"; then
        log_error "$desc 失败: 需要管理员权限（UAC）"
        return 1
    fi
    if echo "$output" | grep -qi "The object already exists"; then
        log_warn "$desc: 对象已存在（重复执行）"
        return 0
    fi
    if echo "$output" | grep -qi "The process cannot access the file"; then
        log_error "$desc 失败: 端口被占用"
        return 1
    fi

    # 输出不为空时打印
    if [[ -n "$output" ]] && [[ "$output" != "*" ]]; then
        # 过滤掉空行和噪音
        local filtered
        filtered=$(echo "$output" | grep -v '^\s*$' | grep -v 'Copyright' | grep -v 'Welcome' || true)
        if [[ -n "$filtered" ]]; then
            echo "  → $filtered"
        fi
    fi

    return 0
}

# --- 配置单个端口转发 ---
setup_port_forward() {
    local listen_port="$1"
    local connect_port="${2:-$1}"
    local wsl_ip="$WSL2_IP"

    log_info "配置端口转发: Windows localhost:${listen_port} → ${wsl_ip}:${connect_port}"

    # 先删除可能存在的旧规则（避免冲突）
    $PS_CMD -NoProfile -Command "
        netsh interface portproxy delete v4tov4 listenport=${listen_port} listenaddress=127.0.0.1 2>&1 | Out-Null
        netsh interface portproxy delete v4tov4 listenport=${listen_port} listenaddress=127.0.0.1 2>&1 | Out-Null
    " 2>/dev/null || true

    # 添加新规则（同时绑定 0.0.0.0）
    ps_exec "
        \$err1 = netsh interface portproxy add v4tov4 listenport=${listen_port} listenaddress=127.0.0.1 connectport=${connect_port} connectaddress=${wsl_ip} 2>&1
        if (\$err1 -match 'Access is denied|elevation') { exit 1 }
        Write-Output 'PortProxy-OK'
    " "netsh portproxy add ${listen_port}"

    # 添加防火墙规则（入站）
    local fw_rule_name="Triad-WSL2-Port-${listen_port}"
    ps_exec "
        # 先删除旧规则
        Remove-NetFirewallRule -DisplayName '${fw_rule_name}' -ErrorAction SilentlyContinue 2>&1 | Out-Null
        # 添加入站规则
        New-NetFirewallRule -DisplayName '${fw_rule_name}' -Direction Inbound -Action Allow -Protocol TCP -LocalPort ${listen_port} -RemoteAddress 127.0.0.1 -ErrorAction Stop | Out-Null
        Write-Output 'Firewall-OK'
    " "防火墙规则 ${listen_port}"

    success "端口 ${listen_port} 转发配置完成"
}

# --- 清理单个端口转发 ---
clean_port_forward() {
    local listen_port="$1"
    local fw_rule_name="Triad-WSL2-Port-${listen_port}"

    log_info "清理端口 ${listen_port}..."

    # 删除 portproxy
    $PS_CMD -NoProfile -Command "
        netsh interface portproxy delete v4tov4 listenport=${listen_port} listenaddress=127.0.0.1 2>&1 | Out-Null
        netsh interface portproxy delete v4tov4 listenport=${listen_port} listenaddress=127.0.0.1 2>&1 | Out-Null
        Write-Output 'Deleted'
    " 2>/dev/null || true

    # 删除防火墙规则
    $PS_CMD -NoProfile -Command "
        Remove-NetFirewallRule -DisplayName '${fw_rule_name}' -ErrorAction SilentlyContinue 2>&1 | Out-Null
        Write-Output 'Firewall-Deleted'
    " 2>/dev/null || true

    success "端口 ${listen_port} 清理完成"
}

# --- 显示当前 portproxy 状态 ---
show_portproxy_status() {
    log_info "Windows 端口转发状态:"
    local pp_status
    pp_status=$($PS_CMD -NoProfile -Command "netsh interface portproxy show v4tov4" 2>/dev/null | tr -d '\r')
    if [[ -n "$pp_status" ]]; then
        echo "$pp_status"
    else
        log_warn "无法获取端口转发状态（可能需要管理员权限）"
    fi
}

# --- 显示防火墙规则 ---
show_firewall_status() {
    log_info "Windows 防火墙 Triad 规则:"
    local fw_status
    fw_status=$($PS_CMD -NoProfile -Command "Get-NetFirewallRule -DisplayName 'Triad-WSL2-*' | Select-Object DisplayName, Direction, Action, Enabled | Format-Table -AutoSize" 2>/dev/null | tr -d '\r')
    if [[ -n "$fw_status" ]]; then
        echo "$fw_status"
    else
        log_warn "未找到 Triad 防火墙规则（可能未配置或需要管理员权限）"
    fi
}

# --- 测试端口连通性 ---
test_ports() {
    log_step "测试端口连通性"

    local ports=("${TRIAD_PORTS[@]}")
    for port in "${ports[@]}"; do
        log_info "测试端口 ${port}..."

        # 从 WSL2 内部测试
        if curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://${WSL2_IP}:${port}" &>/dev/null || \
           nc -z "$WSL2_IP" "$port" 2>/dev/null; then
            success "WSL2 内部 → ${WSL2_IP}:${port} 可连通"
        else
            log_warn "WSL2 内部 → ${WSL2_IP}:${port} 无响应（服务可能未启动）"
        fi

        # 从 Windows 侧测试（通过 PowerShell）
        local win_test
        win_test=$($PS_CMD -NoProfile -Command "
            try {
                \$conn = Test-NetConnection -ComputerName 127.0.0.1 -Port ${port} -WarningAction SilentlyContinue
                if (\$conn.TcpTestSucceeded) { Write-Output 'OPEN' } else { Write-Output 'CLOSED' }
            } catch { Write-Output 'ERROR' }
        " 2>/dev/null | tr -d '\r')

        case "$win_test" in
            OPEN)
                success "Windows localhost:${port} → 可连通 ✓"
                ;;
            CLOSED)
                log_warn "Windows localhost:${port} → 不可连通（端口转发可能未生效或服务未启动）"
                ;;
            ERROR|*)
                log_warn "Windows localhost:${port} → 测试失败（可能需要管理员权限）"
                ;;
        esac
    done
}

# --- 自动检测 Docker 端口 ---
auto_detect_ports() {
    log_step "自动检测 Triad Docker 容器端口"

    if ! command -v docker &>/dev/null; then
        log_warn "Docker 不可用，跳过自动检测"
        return 1
    fi

    local detected_ports=()
    local container_list
    container_list=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i "triad\|claw\|agent\|api\|redis\|vector" || true)

    if [[ -z "$container_list" ]]; then
        log_warn "未检测到 Triad 相关容器（可能尚未启动）"
        log_info "将使用默认端口: ${DEFAULT_PORTS[*]}"
        return 1
    fi

    while IFS= read -r container; do
        [[ -z "$container" ]] && continue
        log_info "检测容器: $container"

        local port_bindings
        port_bindings=$(docker inspect -f '{{range \$p, \$conf := .HostConfig.PortBindings}}{{\$p}} -> {{range \$conf}}{{if .HostPort}}{{.HostPort}}{{end}}{{end}} {{end}}' "$container" 2>/dev/null || true)

        if [[ -n "$port_bindings" ]]; then
            # 提取宿主机端口
            local host_ports
            host_ports=$(echo "$port_bindings" | grep -oP '\d+(?=/)' | sort -u | tr '\n' ' ')
            for port in $host_ports; do
                if [[ "$port" =~ ^[0-9]+$ ]]; then
                    detected_ports+=("$port")
                    success "检测到端口映射: ${container} → ${port}"
                fi
            done
        fi
    done <<< "$container_list"

    if [[ ${#detected_ports[@]} -gt 0 ]]; then
        # 去重
        mapfile -t TRIAD_PORTS < <(printf '%s\n' "${detected_ports[@]}" | sort -u)
        log_info "自动配置端口: ${TRIAD_PORTS[*]}"
        return 0
    else
        log_warn "未检测到端口绑定"
        return 1
    fi
}

# --- 启用 WSL2 localhost 互通（Windows 11 22H2+） ---
enable_wsl_localhost_binding() {
    log_step "尝试启用 WSL2 localhost 互通"

    log_info "检查当前 WSL 配置..."
    local wsl_conf
    wsl_conf=$(cat /mnt/c/Users/"$USER"/.wslconfig 2>/dev/null || true)

    # 通过 PowerShell 配置 .wslconfig
    ps_exec "
        \$wslConfigPath = '\$env:USERPROFILE\.wslconfig'
        if (Test-Path \$wslConfigPath) {
            \$content = Get-Content \$wslConfigPath -Raw
            if (\$content -match 'localhostForwarding\s*=\s*true') {
                Write-Output 'Already-Enabled'
            } else {
                Add-Content \$wslConfigPath '\`n[wsl2]\`nlocalhostForwarding=true'
                Write-Output 'Enabled-NeedRestart'
            }
        } else {
            Set-Content \$wslConfigPath '[wsl2]\`nlocalhostForwarding=true'
            Write-Output 'Created-NeedRestart'
        }
    " "配置 .wslconfig localhostForwarding"

    log_warn "WSL2 localhost 互通配置可能需要重启 WSL2: wsl --shutdown"
}

# --- 显示网络诊断信息 ---
show_diagnostics() {
    log_step "网络诊断信息"

    echo ""
    echo -e "${BOLD}WSL2 网络接口:${NC}"
    ip addr show 2>/dev/null | grep 'inet ' | sed 's/^/  /'

    echo ""
    echo -e "${BOLD}WSL2 默认路由:${NC}"
    ip route | grep default | sed 's/^/  /'

    echo ""
    echo -e "${BOLD}Windows 宿主机 IP（通过 PowerShell）:${NC}"
    local win_ip
    win_ip=$($PS_CMD -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { \$_.IPAddress -match '^172\\.2[0-9]\\.' -or \$_.IPAddress -match '^192\\.168\\.' -or \$_.IPAddress -match '^10\\.' } | Select-Object -First 1).IPAddress" 2>/dev/null | tr -d '\r')
    if [[ -n "$win_ip" ]]; then
        echo "  Windows IP: $win_ip"
    else
        log_warn "无法获取 Windows IP"
    fi

    echo ""
    echo -e "${BOLD}Docker 网络（triad-bridge）:${NC}"
    if command -v docker &>/dev/null; then
        docker network inspect triad-bridge 2>/dev/null | grep -E '"Subnet"|"Gateway"|"IPv4Address"' | sed 's/^/  /' || echo "  (未创建)"
    else
        echo "  Docker 不可用"
    fi

    echo ""
    echo -e "${BOLD}端口监听状态（WSL2 侧）:${NC}"
    ss -tlnp 2>/dev/null | grep -E 'State|18080|18000|16333|16379' | sed 's/^/  /' || \
    netstat -tlnp 2>/dev/null | grep -E 'Proto|18080|18000|16333|16379' | sed 's/^/  /' || \
    log_warn "ss/netstat 不可用"
}

# =============================================================================
# 主逻辑
# =============================================================================

# --- 默认配置 ---
readonly DEFAULT_PORTS=(18080 18000 16333 16379)
TRIAD_PORTS=()

# 从环境变量读取端口
if [[ -n "${WSL2_GATEWAY_PORTS:-}" ]]; then
    IFS=' ' read -r -a TRIAD_PORTS <<< "$WSL2_GATEWAY_PORTS"
else
    TRIAD_PORTS=("${DEFAULT_PORTS[@]}")
fi

# --- 解析参数 ---
CMD="${1:-setup}"
shift || true

# 如果提供了额外端口参数，覆盖默认值
if [[ $# -gt 0 ]]; then
    TRIAD_PORTS=("$@")
fi

# --- 执行前检查 ---
check_wsl2
check_powershell

# --- 获取 WSL2 IP ---
log_step "获取 WSL2 IP 地址"
WSL2_IP=$(get_wsl2_ip)
success "WSL2 IP: $WSL2_IP"

# --- 获取 triad-bridge 网关 ---
TRIAD_GATEWAY=$(get_triad_gateway)

# --- 命令分发 ---
case "$CMD" in
    setup)
        log_step "执行: setup — 配置端口转发"

        if [[ "${WSL2_AUTO_CLEAN:-false}" == "true" ]]; then
            log_info "WSL2_AUTO_CLEAN=true，先清理旧规则..."
            for port in "${TRIAD_PORTS[@]}"; do
                clean_port_forward "$port" 2>/dev/null || true
            done
        fi

        for port in "${TRIAD_PORTS[@]}"; do
            setup_port_forward "$port"
        done

        echo ""
        echo -e "${GREEN}${BOLD}==============================================${NC}"
        echo -e "${GREEN}${BOLD}     WSL2 网关配置完成${NC}"
        echo -e "${GREEN}${BOLD}==============================================${NC}"
        echo ""
        echo -e "${BOLD}访问地址（Windows 浏览器）:${NC}"
        for port in "${TRIAD_PORTS[@]}"; do
            case "$port" in
                18080) echo -e "  ClawPanel 前端:   ${CYAN}http://localhost:18080${NC}" ;;
                18000) echo -e "  Triad API:        ${CYAN}http://localhost:18000${NC}" ;;
                16333) echo -e "  Qdrant VectorDB:  ${CYAN}http://localhost:16333${NC}" ;;
                16379) echo -e "  Redis:            ${CYAN}redis://localhost:16379${NC}" ;;
                *)    echo -e "  服务端口:         ${CYAN}http://localhost:${port}${NC}" ;;
            esac
        done
        echo ""
        echo -e "${BOLD}内部网络:${NC}"
        echo -e "  WSL2 IP:          ${CYAN}${WSL2_IP}${NC}"
        echo -e "  triad-bridge 网关: ${CYAN}${TRIAD_GATEWAY}${NC}"
        echo -e "  Docker 子网:      ${CYAN}172.20.0.0/16 (或调整后的子网)${NC}"
        echo ""
        echo -e "${YELLOW}提示:${NC}"
        echo -e "  • 如果 Windows 侧无法访问，请确认已以管理员权限运行 PowerShell"
        echo -e "  • 首次配置后，Windows 防火墙可能会弹出确认对话框"
        echo -e "  • 每次 WSL2 重启后 IP 可能变化，建议重新运行本脚本"
        echo -e "  • Windows 11 用户可考虑在 .wslconfig 中启用 localhostForwarding=true"
        echo ""
        ;;

    status)
        log_step "执行: status — 查看当前状态"

        echo ""
        echo -e "${BOLD}WSL2 IP:${NC} ${CYAN}${WSL2_IP}${NC}"
        echo -e "${BOLD}triad-bridge 网关:${NC} ${CYAN}${TRIAD_GATEWAY}${NC}"
        echo ""

        show_portproxy_status
        echo ""
        show_firewall_status
        echo ""
        show_diagnostics
        ;;

    clean)
        log_step "执行: clean — 清理端口转发"

        for port in "${TRIAD_PORTS[@]}"; do
            clean_port_forward "$port"
        done

        echo ""
        echo -e "${GREEN}所有 Triad 端口转发已清理${NC}"
        echo -e "${YELLOW}如需完全清理，请在管理员 PowerShell 中运行:${NC}"
        echo -e "   netsh interface portproxy reset"
        ;;

    auto)
        log_step "执行: auto — 自动检测并配置"

        if auto_detect_ports; then
            log_info "使用自动检测到的端口: ${TRIAD_PORTS[*]}"
        else
            log_info "使用默认端口: ${TRIAD_PORTS[*]}"
        fi

        # 清理旧规则
        for port in "${TRIAD_PORTS[@]}"; do
            clean_port_forward "$port" 2>/dev/null || true
        done

        for port in "${TRIAD_PORTS[@]}"; do
            setup_port_forward "$port"
        done

        echo ""
        echo -e "${GREEN}${BOLD}自动配置完成${NC}"
        echo -e "配置端口: ${TRIAD_PORTS[*]}"
        ;;

    test)
        test_ports
        ;;

    enable-localhost)
        enable_wsl_localhost_binding
        ;;

    diag|diagnostics)
        show_diagnostics
        ;;

    help|--help|-h)
        show_help
        ;;

    *)
        log_error "未知命令: $CMD"
        show_help
        exit 1
        ;;
esac

success "wsl2_gateway.sh 执行完毕"
exit 0
