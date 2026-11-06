"""The v2 page, served beside the v1 pages until it replaces them.

Its files are served as they are laid out under ``pages/``: ``pages/v2`` at
``/v2/`` and ``pages/shared`` at ``/shared/``. The page's relative links then
resolve over HTTP as they do when it is opened from disk, where its mock
answers every route.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from .loopback_http import PAGES_DIR, STATIC_CONTENT_TYPES
from .workbench_view import WORKBENCH_CONTENT_SECURITY_POLICY

V2_PAGE_PATH = "/v2/"
# The page's own files (capture/ is the recorded run its mock plays), and the shared files it loads.
V2_FILE_PATTERN = re.compile(
    r"^/(v2/(?:v2\.css|js/[a-z]+\.js|capture/[a-z0-9_]+\.(?:js|png))|shared/[a-z]+\.(?:css|js))$"
)
CONTENT_TYPES = {**STATIC_CONTENT_TYPES, ".html": "text/html; charset=utf-8", ".png": "image/png"}


def serve_v2_file(handler: Any, route: str, *, include_body: bool) -> bool:
    """Serve the v2 page or a file it loads; False leaves the route to the caller.

    Like the workbench, the page runs no inline script, so it is served under the same policy.
    """

    if route == V2_PAGE_PATH:
        relative = "v2/index.html"
    else:
        match = V2_FILE_PATTERN.match(route)
        if match is None:
            return False
        relative = match.group(1)
    try:
        body = (PAGES_DIR / relative).read_bytes()
    except FileNotFoundError:
        return False
    handler.content_security_policy = WORKBENCH_CONTENT_SECURITY_POLICY
    handler._send(200, body, CONTENT_TYPES[PurePosixPath(relative).suffix], include_body=include_body)
    return True
