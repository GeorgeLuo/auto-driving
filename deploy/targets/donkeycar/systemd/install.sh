#!/usr/bin/env bash
set -euo pipefail

PI_HOME="${1:?Pi home is required}"
PI_USER="${2:?Pi user is required}"
DRIVE_ARGS_B64="${3:--}"
SERVICE_NAME="automa-donkey.service"
ASSET_DIR="${PI_HOME}/.config/automa/systemd"
CONFIG_DIR="${PI_HOME}/.config/automa"
ENV_FILE="${CONFIG_DIR}/donkey.env"
TEMPLATE="${ASSET_DIR}/${SERVICE_NAME}.in"
RENDERED="${ASSET_DIR}/${SERVICE_NAME}"

if [[ "${DRIVE_ARGS_B64}" != "-" && "${DRIVE_ARGS_B64}" != b64:* ]]; then
  echo "Invalid encoded drive arguments." >&2
  exit 2
fi

mkdir -p "${CONFIG_DIR}"
rm -f "${PI_HOME}/mycar/donkey_web.pid" "${PI_HOME}/mycar/logs/donkey_web.log"
if [[ "${DRIVE_ARGS_B64}" != "-" || ! -f "${ENV_FILE}" ]]; then
  if [[ "${DRIVE_ARGS_B64}" == "-" ]]; then
    DRIVE_ARGS_B64="b64:"
  fi
  printf 'AUTOMA_DRIVE_ARGS_B64=%s\n' "${DRIVE_ARGS_B64#b64:}" > "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
fi

python3 - "${TEMPLATE}" "${RENDERED}" "${PI_USER}" "${PI_HOME}" <<'PY'
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
rendered_path = Path(sys.argv[2])
pi_user = sys.argv[3]
pi_home = sys.argv[4]

unit = template_path.read_text(encoding="utf-8")
unit = unit.replace("@AUTOMA_USER@", pi_user)
unit = unit.replace("@AUTOMA_PI_HOME@", pi_home)
if "@AUTOMA_" in unit:
    raise RuntimeError("unresolved Automa service template token")
rendered_path.write_text(unit, encoding="utf-8")
PY

sudo install -m 0644 "${RENDERED}" "/etc/systemd/system/${SERVICE_NAME}"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

printf 'service=%s\n' "${SERVICE_NAME}"
printf 'enabled=%s\n' "$(sudo systemctl is-enabled "${SERVICE_NAME}")"
printf 'active=%s\n' "$(sudo systemctl is-active "${SERVICE_NAME}")"
