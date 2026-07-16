#!/usr/bin/env bash
set -euo pipefail

PI_HOME="${1:?Pi home is required}"
ACTION="${2:?Service action is required}"
DRIVE_ARGS_B64="${3:--}"
SERVICE_NAME="automa-donkey.service"
ENV_FILE="${PI_HOME}/.config/automa/donkey.env"

if [[ "${DRIVE_ARGS_B64}" != "-" && "${DRIVE_ARGS_B64}" != b64:* ]]; then
  echo "Invalid encoded drive arguments." >&2
  exit 2
fi

if [[ "${ACTION}" != "restart" ]]; then
  echo "Unsupported service action: ${ACTION}" >&2
  exit 2
fi

if [[ "${DRIVE_ARGS_B64}" != "-" ]]; then
  mkdir -p "$(dirname "${ENV_FILE}")"
  printf 'AUTOMA_DRIVE_ARGS_B64=%s\n' "${DRIVE_ARGS_B64#b64:}" > "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
fi

if ! sudo systemctl cat "${SERVICE_NAME}" >/dev/null 2>&1; then
  echo "${SERVICE_NAME} is not installed; run 'automa vehicles update core' first." >&2
  exit 3
fi

sudo systemctl restart "${SERVICE_NAME}"
printf 'service=%s\n' "${SERVICE_NAME}"
printf 'enabled=%s\n' "$(sudo systemctl is-enabled "${SERVICE_NAME}")"
printf 'active=%s\n' "$(sudo systemctl is-active "${SERVICE_NAME}")"
