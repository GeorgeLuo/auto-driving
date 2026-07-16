#!/usr/bin/env bash
set -euo pipefail

PI_HOME="${AUTOMA_PI_HOME:?AUTOMA_PI_HOME is required}"
DRIVE_ARGS_RAW=""
if [[ -n "${AUTOMA_DRIVE_ARGS_B64:-}" ]]; then
  DRIVE_ARGS_RAW="$(printf '%s' "${AUTOMA_DRIVE_ARGS_B64}" | base64 --decode)"
fi

DRIVE_ARGS=()
if [[ -n "${DRIVE_ARGS_RAW}" ]]; then
  read -r -a DRIVE_ARGS <<< "${DRIVE_ARGS_RAW}"
fi

export VIRTUAL_ENV="${PI_HOME}/env"
export PATH="${VIRTUAL_ENV}/bin:${PATH}"
cd "${PI_HOME}/mycar"
exec "${VIRTUAL_ENV}/bin/python" manage.py drive "${DRIVE_ARGS[@]}"
