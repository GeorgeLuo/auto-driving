#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND_DIR="${ROOT}/frontend/donkeycar"
PORT="${FRONTEND_PORT:-8088}"
PYTHON="${PYTHON:-$(command -v python3)}"
LABEL="${FRONTEND_LABEL:-com.piracer.frontend}"
RUNTIME_DIR="${FRONTEND_DIR}/.runtime"
PID_FILE="${RUNTIME_DIR}/frontend.pid"
LOG_FILE="${RUNTIME_DIR}/frontend.log"
ERR_FILE="${RUNTIME_DIR}/frontend.err"

mkdir -p "${RUNTIME_DIR}"

if [ -f "${PID_FILE}" ]; then
  old_pid="$(cat "${PID_FILE}")"
  kill "${old_pid}" 2>/dev/null || true
fi

if command -v launchctl >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
  launchctl remove "${LABEL}" >/dev/null 2>&1 || true
  : > "${LOG_FILE}"
  : > "${ERR_FILE}"
  launchctl submit \
    -l "${LABEL}" \
    -o "${LOG_FILE}" \
    -e "${ERR_FILE}" \
    -- "${PYTHON}" -m http.server "${PORT}" --directory "${FRONTEND_DIR}"
else
  cd "${ROOT}"
  nohup "${PYTHON}" -m http.server "${PORT}" --directory "${FRONTEND_DIR}" > "${LOG_FILE}" 2>&1 &
  echo "$!" > "${PID_FILE}"
fi

sleep 1
echo "Static DonkeyCar frontend: http://localhost:${PORT}/"
echo "Pi backend default: http://piracer.local:8887"
