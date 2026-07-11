from __future__ import annotations

import os


LOCAL_CAR_BASE_URL_ENV = "PIRACER_BASE_URL"
LOCAL_CAR_ID_ENV = "PIRACER_ID"

DEFAULT_LOCAL_CAR_BASE_URL = "http://piracer.local:8887"
DEFAULT_LOCAL_CAR_ID = "piracer"


def get_default_local_car_base_url() -> str:
    return os.environ.get(LOCAL_CAR_BASE_URL_ENV, DEFAULT_LOCAL_CAR_BASE_URL).rstrip("/")


def get_default_local_car_id() -> str:
    return os.environ.get(LOCAL_CAR_ID_ENV, DEFAULT_LOCAL_CAR_ID)
