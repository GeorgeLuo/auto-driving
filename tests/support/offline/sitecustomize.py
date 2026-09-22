"""Keep deterministic CLI subprocesses on local, ephemeral fixture endpoints."""

import socket


_getaddrinfo = socket.getaddrinfo


def _fixture_addresses(host, port, *args, **kwargs):
    # Default simulator/Pi ports are real services, not disposable test servers.
    if host not in ("127.0.0.1", "localhost", "::1", None) or str(port) in (
        "5050",
        "8887",
    ):
        raise socket.gaierror(
            socket.EAI_NONAME, "External discovery disabled in deterministic tests"
        )
    return _getaddrinfo(host, port, *args, **kwargs)


socket.getaddrinfo = _fixture_addresses
