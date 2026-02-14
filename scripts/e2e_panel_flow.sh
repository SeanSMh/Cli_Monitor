#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$(mktemp -d /tmp/cli_monitor_e2e_logs.XXXXXX)"
PORT="${CLI_MONITOR_E2E_PORT:-18787}"
BASE_URL="http://127.0.0.1:${PORT}"
APP_PID=""

cleanup() {
  if [[ -n "${APP_PID}" ]] && kill -0 "${APP_PID}" >/dev/null 2>&1; then
    kill "${APP_PID}" >/dev/null 2>&1 || true
  fi
  rm -rf "${LOG_DIR}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_http() {
  local url="$1"
  local retry="${2:-60}"
  local i=0
  while (( i < retry )); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
    i=$((i + 1))
  done
  return 1
}

json_field() {
  local json="$1"
  local key="$2"
  python3 - "$json" "$key" <<'PY'
import json
import sys
obj = json.loads(sys.argv[1])
print(obj.get(sys.argv[2], ""))
PY
}

echo "[e2e] log dir: ${LOG_DIR}"
echo "[e2e] start panel app in E2E mode on ${BASE_URL}"
(
  cd "${ROOT_DIR}"
  AI_MONITOR_DIR="${LOG_DIR}" \
  CLI_MONITOR_E2E=1 \
  CLI_MONITOR_E2E_PORT="${PORT}" \
  python3 panel_app.py
) >/tmp/cli_monitor_e2e_panel.log 2>&1 &
APP_PID=$!

if ! wait_http "${BASE_URL}/state"; then
  echo "[e2e] error: E2E endpoint not ready: ${BASE_URL}/state"
  echo "[e2e] panel log: /tmp/cli_monitor_e2e_panel.log"
  tail -n 80 /tmp/cli_monitor_e2e_panel.log || true
  exit 1
fi
state="$(curl -fsS "${BASE_URL}/state")"
enabled="$(json_field "${state}" "enabled")"
if [[ "${enabled}" != "True" && "${enabled}" != "true" ]]; then
  echo "[e2e] error: e2e endpoint not enabled"
  echo "${state}"
  exit 1
fi

echo "[e2e] create running task log"
LOG_FILE="${LOG_DIR}/codex_$(date +%s)_$$"_e2e.log
cat >"${LOG_FILE}" <<EOF
--- MONITOR_START: codex | $(date '+%Y-%m-%d %H:%M:%S') ---
--- MONITOR_META term_program: ${TERM_PROGRAM:-} ---
--- MONITOR_META tty: $(tty 2>/dev/null || true) ---
--- MONITOR_META cwd: ${ROOT_DIR} ---
--- MONITOR_META shell_pid: $$ ---
running...
EOF

sleep 3
echo "[e2e] append waiting prompt"
echo "Apply changes? (y/n)" >>"${LOG_FILE}"

echo "[e2e] wait unread_notification_count > 0"
ok=0
for _ in $(seq 1 20); do
  state="$(curl -fsS "${BASE_URL}/state")"
  unread="$(python3 - "${state}" <<'PY'
import json,sys
print(int(json.loads(sys.argv[1]).get("unread_notification_count", 0)))
PY
)"
  if (( unread > 0 )); then
    ok=1
    break
  fi
  sleep 1
done
if (( ok == 0 )); then
  echo "[e2e] error: unread_notification_count not increased"
  echo "[e2e] panel log: /tmp/cli_monitor_e2e_panel.log"
  exit 1
fi

echo "[e2e] trigger statusbar toggle path and verify unread cleared"
curl -fsS -X POST "${BASE_URL}/toggle_panel" >/dev/null
sleep 0.8
curl -fsS -X POST "${BASE_URL}/toggle_panel" >/dev/null
sleep 0.8
state="$(curl -fsS "${BASE_URL}/state")"
unread="$(python3 - "${state}" <<'PY'
import json,sys
print(int(json.loads(sys.argv[1]).get("unread_notification_count", 0)))
PY
)"
if (( unread != 0 )); then
  echo "[e2e] error: unread_notification_count expected 0, got ${unread}"
  echo "${state}"
  exit 1
fi

echo "[e2e] trigger card-click equivalent focus_task path"
focus_result="$(curl -fsS -X POST "${BASE_URL}/focus_task?log_file=${LOG_FILE}")"
echo "[e2e] focus result: ${focus_result}"
echo "[e2e] PASS"
