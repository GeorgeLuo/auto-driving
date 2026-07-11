#!/usr/bin/env bash
set -euo pipefail

PI_HOST="${PI_HOST:-piracer@piracer.local}"
PI_HOME="${PI_HOME:-/home/piracer}"
DRIVE_ARGS="${DRIVE_ARGS:-}"

ssh "${PI_HOST}" "PI_HOME='${PI_HOME}' DRIVE_ARGS='${DRIVE_ARGS}' bash -s" <<'REMOTE'
set -euo pipefail

if [ -f "${PI_HOME}/mycar/donkey_web.pid" ]; then
  old_pid="$(cat "${PI_HOME}/mycar/donkey_web.pid")"
  kill "${old_pid}" 2>/dev/null || true
fi

cd "${PI_HOME}/mycar"
mkdir -p logs
source "${PI_HOME}/env/bin/activate"
nohup python manage.py drive ${DRIVE_ARGS} > "${PI_HOME}/mycar/logs/donkey_web.log" 2>&1 &
echo "$!" > "${PI_HOME}/mycar/donkey_web.pid"

sleep 8
echo "pid=$(cat "${PI_HOME}/mycar/donkey_web.pid")"
ps -p "$(cat "${PI_HOME}/mycar/donkey_web.pid")" -o pid,stat,cmd || true
ss -ltnp | grep 8887 || true
tail -50 "${PI_HOME}/mycar/logs/donkey_web.log"
REMOTE

echo "Drive server URL: http://piracer.local:8887/drive"
