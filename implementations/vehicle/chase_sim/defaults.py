from __future__ import annotations

import os


CHASE_UI_WS_URL_ENV = "CHASE_UI_WS_URL"

DEFAULT_CHASE_UI_WS_URL = "ws://localhost:5050/ws/control"


def get_default_chase_ui_ws_url() -> str:
    return os.environ.get(CHASE_UI_WS_URL_ENV, DEFAULT_CHASE_UI_WS_URL)
