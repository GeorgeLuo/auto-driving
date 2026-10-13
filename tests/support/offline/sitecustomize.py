"""Keep deterministic CLI subprocesses on local, ephemeral fixture endpoints."""

import socket


def offline_getaddrinfo(getaddrinfo):
    """Return ``getaddrinfo`` limited to local fixture endpoints."""

    def fixture_addresses(host, port, *args, **kwargs):
        # Default simulator/Pi ports are real services, not disposable test servers.
        if host not in ("127.0.0.1", "localhost", "::1", None) or str(port) in (
            "5050",
            "8887",
        ):
            raise socket.gaierror(
                socket.EAI_NONAME, "External discovery disabled in deterministic tests"
            )
        return getaddrinfo(host, port, *args, **kwargs)

    return fixture_addresses


if __name__ == "sitecustomize":
    socket.getaddrinfo = offline_getaddrinfo(socket.getaddrinfo)
