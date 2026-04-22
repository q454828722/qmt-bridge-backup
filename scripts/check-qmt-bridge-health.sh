#!/usr/bin/env bash
set -u

PORT="${QMT_BRIDGE_PORT:-18888}"
HOST="${QMT_BRIDGE_CLIENT_HOST:-127.0.0.1}"
TASK_NAME="${QMT_BRIDGE_WINDOWS_TASK_NAME:-QMT Bridge Server}"
FORWARD_SERVICE="${QMT_BRIDGE_FORWARD_SERVICE:-qmt-bridge-forward.service}"
TIMEOUT_SECONDS="${QMT_BRIDGE_HEALTH_TIMEOUT:-5}"

overall=0

set_result() {
    local code="$1"
    if (( code > overall )); then
        overall="$code"
    fi
}

section() {
    printf '\n[%s]\n' "$1"
}

status_line() {
    local label="$1"
    local message="$2"
    printf '%-5s %s\n' "$label" "$message"
}

check_windows_server() {
    section "1/3 Windows server"

    if ! command -v powershell.exe >/dev/null 2>&1; then
        status_line "FAIL" "powershell.exe not found; cannot inspect Windows task/process state"
        set_result 2
        return
    fi

    local project_root
    local windows_root
    local windows_check_script
    local output code

    project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if command -v wslpath >/dev/null 2>&1; then
        windows_root="$(wslpath -w "$project_root")"
    else
        windows_root="D:\\qmt-bridge"
    fi
    windows_check_script="${windows_root}\\scripts\\check-qmt-bridge-windows.ps1"

    output="$(powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$windows_check_script" -ProjectRoot "$windows_root" -Port "$PORT" -TaskName "$TASK_NAME" -TimeoutSeconds "$TIMEOUT_SECONDS" -SkipApiHealth 2>&1)"
    code=$?

    printf '%s\n' "$output" | sed 's/\r$//'
    case "$code" in
        0)
            status_line "PASS" "Windows qmt-server listener and scheduled task look healthy"
            ;;
        1)
            status_line "WARN" "Windows qmt-server is listening, but scheduled task metadata needs attention"
            ;;
        *)
            status_line "FAIL" "Windows qmt-server listener was not found"
            ;;
    esac
    set_result "$code"
}

check_wsl_forward() {
    section "2/3 WSL forward"

    local active="unknown"
    local enabled="unknown"
    local listener

    if command -v systemctl >/dev/null 2>&1; then
        active="$(systemctl --user is-active "$FORWARD_SERVICE" 2>/dev/null || true)"
        enabled="$(systemctl --user is-enabled "$FORWARD_SERVICE" 2>/dev/null || true)"
        printf 'service: %s enabled=%s active=%s\n' "$FORWARD_SERVICE" "$enabled" "$active"
    else
        printf 'service: systemctl not found\n'
    fi

    listener="$(ss -ltnp 2>/dev/null | awk -v target="${HOST}:${PORT}" '$4 == target {print}')"
    if [[ -n "$listener" ]]; then
        printf 'listener: %s\n' "$listener"
    else
        printf 'listener: none on %s:%s\n' "$HOST" "$PORT"
    fi

    if [[ "$active" == "active" && -n "$listener" ]]; then
        status_line "PASS" "WSL localhost forward is active"
        set_result 0
    elif [[ -n "$listener" ]]; then
        status_line "WARN" "WSL localhost port is listening, but systemd service is not active"
        set_result 1
    else
        status_line "FAIL" "WSL localhost forward is not listening"
        set_result 2
    fi
}

check_api_health() {
    section "3/3 API health"

    local url="http://${HOST}:${PORT}/api/meta/health"
    local output code

    output="$(curl -fsS --max-time "$TIMEOUT_SECONDS" "$url" 2>&1)"
    code=$?
    printf 'url: %s\n' "$url"
    printf 'response: %s\n' "$output"

    if [[ "$code" -eq 0 && "$output" == *'"status":"ok"'* ]]; then
        status_line "PASS" "QMT Bridge API health is ok"
        set_result 0
    else
        status_line "FAIL" "QMT Bridge API health check failed"
        set_result 2
    fi
}

printf 'QMT Bridge health check\n'
printf 'time: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
printf 'target: %s:%s\n' "$HOST" "$PORT"

check_windows_server
check_wsl_forward
check_api_health

section "Summary"
case "$overall" in
    0)
        status_line "PASS" "all checks passed"
        ;;
    1)
        status_line "WARN" "service is usable, but at least one check reported a warning"
        ;;
    *)
        status_line "FAIL" "one or more required checks failed"
        ;;
esac

exit "$overall"
