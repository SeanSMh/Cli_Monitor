#!/usr/bin/env bash
# Codex launcher with app-server proxy + monitord integration.

set -u

function _resolve_script_path() {
    local source_path="${BASH_SOURCE[0]}"
    while [[ -L "${source_path}" ]]; do
        local source_dir
        source_dir="$(cd "$(dirname "${source_path}")" && pwd)"
        source_path="$(readlink "${source_path}")"
        [[ "${source_path}" != /* ]] && source_path="${source_dir}/${source_path}"
    done
    cd "$(dirname "${source_path}")" && pwd
}

SCRIPT_DIR="$(_resolve_script_path)"
RESOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_APP_ROOT=""
if [[ -d "${RESOURCE_DIR}/lib" ]]; then
    for candidate in "${RESOURCE_DIR}"/lib/python3.*; do
        if [[ -d "${candidate}/registry" ]]; then
            PYTHON_APP_ROOT="${candidate}"
            break
        fi
    done
fi
if [[ -n "${PYTHON_APP_ROOT}" ]]; then
    ROOT_DIR="${PYTHON_APP_ROOT}"
fi
PYTHON_BIN="${PYTHON_BIN:-python3}"
AI_MONITOR_DIR="${AI_MONITOR_DIR:-/tmp/ai_monitor_logs}"
CLI_MONITOR_DAEMON_HOST="${CLI_MONITOR_DAEMON_HOST:-127.0.0.1}"
CLI_MONITOR_DAEMON_PORT="${CLI_MONITOR_DAEMON_PORT:-8766}"
DAEMON_SCRIPT="${ROOT_DIR}/daemon/monitord.py"
PROXY_SCRIPT="${ROOT_DIR}/proxy/codex_app_server_proxy.py"

mkdir -p "${AI_MONITOR_DIR}"

function _ai_meta_sanitize() {
    local raw="${1:-}"
    raw="${raw//$'\n'/ }"
    raw="${raw//$'\r'/ }"
    raw="${raw//---/}"
    echo "$raw"
}

function _pick_two_ports() {
    "${PYTHON_BIN}" - <<'PY'
import socket

ports = []
while len(ports) < 2:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    if port not in ports:
        ports.append(port)
print(f"{ports[0]} {ports[1]}")
PY
}

function _merge_session_registry_pairs() {
    "${PYTHON_BIN}" - "${ROOT_DIR}" "${SESSION_ID}" "$@" <<'PY'
import sys

root_dir, session_id = sys.argv[1], sys.argv[2]
args = sys.argv[3:]
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from registry.session_registry import merge_session_registry

if len(args) % 2 != 0:
    raise SystemExit("expected key/value pairs")

payload = {}
for index in range(0, len(args), 2):
    key = str(args[index] or "").strip()
    raw_value = args[index + 1]
    if not key:
        continue
    if raw_value in {"true", "false"}:
        value = raw_value == "true"
    else:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = raw_value
    payload[key] = value

merge_session_registry(session_id, payload)
PY
}

function _delete_session_registry() {
    "${PYTHON_BIN}" - "${ROOT_DIR}" "${SESSION_ID}" <<'PY'
import sys

root_dir, session_id = sys.argv[1], sys.argv[2]
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from registry.session_registry import delete_session_registry

delete_session_registry(session_id)
PY
}

function _wait_http_ready() {
    local url="$1"
    local timeout_seconds="${2:-5}"
    "${PYTHON_BIN}" - "${url}" "${timeout_seconds}" <<'PY'
import sys
import time
import urllib.request

url = sys.argv[1]
deadline = time.time() + float(sys.argv[2])
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as resp:
            if 200 <= int(resp.status) < 500:
                raise SystemExit(0)
    except Exception:
        time.sleep(0.1)
raise SystemExit(1)
PY
}

function _wait_tcp_ready() {
    local host="$1"
    local port="$2"
    local timeout_seconds="${3:-5}"
    "${PYTHON_BIN}" - "${host}" "${port}" "${timeout_seconds}" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
deadline = time.time() + float(sys.argv[3])
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            raise SystemExit(0)
    except Exception:
        time.sleep(0.1)
raise SystemExit(1)
PY
}

function _write_meta_lines() {
    local log_file="$1"
    local _term_program="$(_ai_meta_sanitize "${TERM_PROGRAM:-}")"
    local _term_program_version="$(_ai_meta_sanitize "${TERM_PROGRAM_VERSION:-}")"
    local _tty="$(_ai_meta_sanitize "$(tty 2>/dev/null || true)")"
    local _cwd="$(_ai_meta_sanitize "${PWD:-}")"
    local _shell_pid="$(_ai_meta_sanitize "$$")"
    local _shell_ppid="$(_ai_meta_sanitize "${PPID:-}")"
    echo "--- MONITOR_META term_program: ${_term_program} ---" >> "$log_file"
    echo "--- MONITOR_META term_program_version: ${_term_program_version} ---" >> "$log_file"
    echo "--- MONITOR_META tty: ${_tty} ---" >> "$log_file"
    echo "--- MONITOR_META cwd: ${_cwd} ---" >> "$log_file"
    echo "--- MONITOR_META shell_pid: ${_shell_pid} ---" >> "$log_file"
    echo "--- MONITOR_META shell_ppid: ${_shell_ppid} ---" >> "$log_file"
    echo "--- MONITOR_META terminal_emulator: $(_ai_meta_sanitize "${TERMINAL_EMULATOR:-}") ---" >> "$log_file"
    echo "--- MONITOR_META state_source: codex_proxy ---" >> "$log_file"
}

function _append_monitor_end_if_missing() {
    local log_file="$1"
    local exit_code="$2"
    if [[ ! -f "$log_file" ]]; then
        return 0
    fi
    if tail -n 10 "$log_file" 2>/dev/null | grep -q "MONITOR_END:"; then
        return 0
    fi
    echo "--- MONITOR_END: ${exit_code} | $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$log_file"
}

function _codex_supports_tui_app_server() {
    "${CODEX_BIN}" features list 2>/dev/null | grep -q '^tui_app_server[[:space:]]'
}

function _resolve_codex_bin() {
    local configured_bin="${CLI_MONITOR_CODEX_BIN:-}"
    if [[ -n "${configured_bin}" && -x "${configured_bin}" ]]; then
        echo "${configured_bin}"
        return 0
    fi

    local original_bin="${CLI_MONITOR_ORIGINAL_CODEX_BIN:-}"
    if [[ -n "${original_bin}" && -x "${original_bin}" ]]; then
        echo "${original_bin}"
        return 0
    fi

    local discovered_bin
    discovered_bin="$(type -P codex 2>/dev/null || true)"
    if [[ -z "${discovered_bin}" && -n "${ZSH_VERSION:-}" ]]; then
        discovered_bin="$(whence -p codex 2>/dev/null || true)"
    fi
    if [[ -n "${discovered_bin}" && -x "${discovered_bin}" ]]; then
        echo "${discovered_bin}"
        return 0
    fi

    local app_bundle_bin="/Applications/Codex.app/Contents/Resources/codex"
    if [[ -x "${app_bundle_bin}" ]]; then
        echo "${app_bundle_bin}"
        return 0
    fi

    return 1
}

function _fallback_logged_codex() {
    local codex_bin="$1"
    shift
    local session_id="$1"
    shift
    local log_file="$1"
    shift
    local cmd=( "$codex_bin" "$@" )
    if [[ "$(uname)" == "Darwin" ]]; then
        CLI_MONITOR_SESSION_ID="${session_id}" CLI_MONITOR_LOG_FILE="${log_file}" \
            script -a -F -q "${log_file}" "${cmd[@]}"
    else
        local cmd_str
        printf -v cmd_str '%q ' "${cmd[@]}"
        CLI_MONITOR_SESSION_ID="${session_id}" CLI_MONITOR_LOG_FILE="${log_file}" \
            script -a -f -q -c "${cmd_str% }" "${log_file}"
    fi
    return $?
}

CODEX_BIN="$(_resolve_codex_bin || true)"
if [[ -z "${CODEX_BIN}" ]]; then
    echo "codex binary not found" >&2
    exit 127
fi

SESSION_ID="codex_$(date +%s)_$$_${RANDOM}"
LOG_FILE="${AI_MONITOR_DIR}/${SESSION_ID}.log"
LAUNCH_TOKEN="$(_ai_meta_sanitize "${CLI_MONITOR_LAUNCH_TOKEN:-}")"
read -r REAL_PORT PROXY_PORT <<< "$(_pick_two_ports)"
REAL_URL="ws://127.0.0.1:${REAL_PORT}"
PROXY_URL="ws://127.0.0.1:${PROXY_PORT}"
STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

echo "--- MONITOR_START: codex | $(date '+%Y-%m-%d %H:%M:%S') ---" > "${LOG_FILE}"
_write_meta_lines "${LOG_FILE}"
echo "--- MONITOR_META session_id: ${SESSION_ID} ---" >> "${LOG_FILE}"
echo "--- MONITOR_META real_app_server_url: ${REAL_URL} ---" >> "${LOG_FILE}"
echo "--- MONITOR_META proxy_url: ${PROXY_URL} ---" >> "${LOG_FILE}"
if [[ -n "${LAUNCH_TOKEN}" ]]; then
    echo "--- MONITOR_META launch_token: ${LAUNCH_TOKEN} ---" >> "${LOG_FILE}"
fi

_merge_session_registry_pairs \
    tool codex \
    log_file "${LOG_FILE}" \
    real_app_server_url "${REAL_URL}" \
    proxy_url "${PROXY_URL}" \
    started_at "${STARTED_AT}" \
    state_source codex_proxy

APP_SERVER_PID=""
PROXY_PID=""
EXIT_CODE=0

function _cleanup() {
    local exit_code="${1:-0}"
    if [[ -n "${PROXY_PID}" ]]; then
        kill "${PROXY_PID}" >/dev/null 2>&1 || true
    fi
    if [[ -n "${APP_SERVER_PID}" ]]; then
        kill "${APP_SERVER_PID}" >/dev/null 2>&1 || true
    fi
    _delete_session_registry
    _append_monitor_end_if_missing "${LOG_FILE}" "${exit_code}"
}

trap '_cleanup "$EXIT_CODE"' EXIT

PYTHONPATH_PREFIX="${ROOT_DIR}"
if [[ -n "${PYTHONPATH:-}" ]]; then
    PYTHONPATH_PREFIX="${ROOT_DIR}:${PYTHONPATH}"
fi

PYTHONPATH="${PYTHONPATH_PREFIX}" "${PYTHON_BIN}" "${DAEMON_SCRIPT}" --host "${CLI_MONITOR_DAEMON_HOST}" --port "${CLI_MONITOR_DAEMON_PORT}" --ensure-running >/dev/null 2>&1 || true

if ! "${CODEX_BIN}" app-server --help >/dev/null 2>&1; then
    _fallback_logged_codex "${CODEX_BIN}" "${SESSION_ID}" "${LOG_FILE}" "$@"
    EXIT_CODE=$?
    exit "${EXIT_CODE}"
fi

CLI_MONITOR_SESSION_ID="${SESSION_ID}" \
CLI_MONITOR_LOG_FILE="${LOG_FILE}" \
CLI_MONITOR_DAEMON_HOST="${CLI_MONITOR_DAEMON_HOST}" \
CLI_MONITOR_DAEMON_PORT="${CLI_MONITOR_DAEMON_PORT}" \
    "${CODEX_BIN}" app-server --listen "${REAL_URL}" >> "${LOG_FILE}" 2>&1 &
APP_SERVER_PID=$!

_merge_session_registry_pairs app_server_pid "${APP_SERVER_PID}"

if ! _wait_http_ready "http://127.0.0.1:${REAL_PORT}/readyz" 5; then
    echo "[cli-monitor] app-server failed to become ready; falling back to direct codex." >> "${LOG_FILE}"
    kill "${APP_SERVER_PID}" >/dev/null 2>&1 || true
    APP_SERVER_PID=""
    _fallback_logged_codex "${CODEX_BIN}" "${SESSION_ID}" "${LOG_FILE}" "$@"
    EXIT_CODE=$?
    exit "${EXIT_CODE}"
fi

CLI_MONITOR_SESSION_ID="${SESSION_ID}" \
CLI_MONITOR_LOG_FILE="${LOG_FILE}" \
CLI_MONITOR_DAEMON_HOST="${CLI_MONITOR_DAEMON_HOST}" \
CLI_MONITOR_DAEMON_PORT="${CLI_MONITOR_DAEMON_PORT}" \
    PYTHONPATH="${PYTHONPATH_PREFIX}" "${PYTHON_BIN}" "${PROXY_SCRIPT}" \
    --listen-host 127.0.0.1 \
    --listen-port "${PROXY_PORT}" \
    --upstream-url "${REAL_URL}" \
    --session-id "${SESSION_ID}" \
    --log-file "${LOG_FILE}" >> "${LOG_FILE}" 2>&1 &
PROXY_PID=$!

if ! _wait_tcp_ready "127.0.0.1" "${PROXY_PORT}" 5; then
    echo "[cli-monitor] proxy failed to become ready; falling back to direct codex." >> "${LOG_FILE}"
    kill "${PROXY_PID}" >/dev/null 2>&1 || true
    kill "${APP_SERVER_PID}" >/dev/null 2>&1 || true
    PROXY_PID=""
    APP_SERVER_PID=""
    _fallback_logged_codex "${CODEX_BIN}" "${SESSION_ID}" "${LOG_FILE}" "$@"
    EXIT_CODE=$?
    exit "${EXIT_CODE}"
fi

REMOTE_ARGS=( --remote "${PROXY_URL}" )
if _codex_supports_tui_app_server; then
    REMOTE_ARGS=( --enable tui_app_server --remote "${PROXY_URL}" )
fi

_fallback_logged_codex "${CODEX_BIN}" "${SESSION_ID}" "${LOG_FILE}" "${REMOTE_ARGS[@]}" "$@"
EXIT_CODE=$?
exit "${EXIT_CODE}"
