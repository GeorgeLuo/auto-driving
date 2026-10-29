"""A host holds a presence socket the simulator lists to other agents, and learns why it was closed."""
from __future__ import annotations

import struct
import threading
import unittest
from unittest import mock

from implementations.vehicle.chase_sim.metrics_ws import (
    MetricsUiConnection, MetricsUiWebSocketError, MetricsUiWsClient,
)
from implementations.vehicle.chase_sim.presence import MetricsUiPresence
from tests.shared.implementations.runtime.wait import wait_until
from tests.support.fake_metrics_ui import fake_metrics_ui

HOST = {"kind": "host", "host_name": "chase-sim-chaser", "url": "http://127.0.0.1:8123", "pid": 4321}


class CloseReasonTests(unittest.TestCase):
    def close_with(self, payload: bytes) -> MetricsUiWebSocketError:
        connection = MetricsUiConnection("ws://example.invalid", timeout_s=0.1)
        connection.sock = mock.Mock()
        self.addCleanup(connection.abort)
        with mock.patch.object(connection, "_recv_frame", return_value=(0x8, payload)):
            with self.assertRaises(MetricsUiWebSocketError) as raised:
                connection.recv_json()
        return raised.exception

    def test_a_server_close_names_its_code_and_reason(self) -> None:
        error = self.close_with(struct.pack("!H", 1001) + b"server shutdown (api)")
        self.assertEqual(str(error), "WebSocket closed by server: 1001 server shutdown (api)")
        self.assertEqual(error.details, {"close_code": 1001, "close_reason": "server shutdown (api)"})

    def test_a_close_without_a_payload_still_says_the_server_closed(self) -> None:
        self.assertEqual(str(self.close_with(b"")), "WebSocket closed by server")


class PresenceTests(unittest.TestCase):
    def test_a_held_presence_is_listed_to_other_agents_until_closed(self) -> None:
        with fake_metrics_ui() as server:
            client = MetricsUiWsClient(server.url, timeout_s=2.0)
            with MetricsUiPresence(server.url, HOST, retry_s=0.05):
                [agent] = wait_until(client.list_agents, message="the presence was never listed")
                self.assertEqual(agent["client"], HOST)
                self.assertTrue(agent["connectedAt"])
            wait_until(lambda: client.list_agents() == [], message="the closed presence stayed listed")

    def test_a_closed_presence_logs_the_reason_and_attaches_again(self) -> None:
        with (
            fake_metrics_ui() as server,
            self.assertLogs("implementations.vehicle.chase_sim.presence", "INFO") as logs,
            MetricsUiPresence(server.url, HOST, retry_s=0.05),
        ):
            client = MetricsUiWsClient(server.url, timeout_s=2.0)
            wait_until(client.list_agents, message="the presence was never listed")
            server.close_agents("server shutdown (api)")
            wait_until(lambda: sum("Attached" in line for line in logs.output) == 2,
                       message="the presence never attached again")
            wait_until(lambda: [agent["client"] for agent in client.list_agents()] == [HOST],
                       message="the simulator does not list exactly the reattached presence")
        self.assertTrue(any("WebSocket closed by server: 1001 server shutdown (api)" in line
                            for line in logs.output), logs.output)

    def test_an_unexpected_failure_is_reported_and_the_presence_still_attaches(self) -> None:
        failures = iter([RuntimeError("unexpected registration reply")])

        class FailingOnce(MetricsUiConnection):
            def connect(self) -> None:
                for failure in failures:
                    raise failure
                super().connect()

        with (
            fake_metrics_ui() as server,
            mock.patch("implementations.vehicle.chase_sim.presence.MetricsUiConnection", FailingOnce),
            self.assertLogs("implementations.vehicle.chase_sim.presence", "INFO") as logs,
            MetricsUiPresence(server.url, HOST, retry_s=0.05),
        ):
            client = MetricsUiWsClient(server.url, timeout_s=2.0)
            wait_until(lambda: [agent["client"] for agent in client.list_agents()] == [HOST],
                       message="the presence never attached after the failure")
        self.assertEqual(
            [line for line in logs.output if "unexpected registration reply" in line],
            [f"WARNING:implementations.vehicle.chase_sim.presence:Not attached to Metrics UI at "
             f"{server.url}: unexpected registration reply; retrying every 0.05s"],
        )


class CloseAgentsTests(unittest.TestCase):
    def test_a_dropped_client_does_not_stop_close_of_the_agents_still_up(self) -> None:
        class Socket:
            def __init__(self, error: OSError | None = None) -> None:
                self.error = error
                self.sent: list[bytes] = []

            def sendall(self, data: bytes) -> None:
                if self.error is not None:
                    raise self.error
                self.sent.append(data)

        class Handler:
            def __init__(self, error: OSError | None = None) -> None:
                self.send_lock = threading.Lock()
                self.request = Socket(error)

        dropped = Handler(BrokenPipeError())
        closed = Handler(OSError(9, "Bad file descriptor"))
        live = Handler()
        with fake_metrics_ui() as server:
            with server._agents_lock:
                server._agents[dropped] = {}
                server._agents[closed] = {}
                server._agents[live] = {}
            server.close_agents("server shutdown (api)")
        self.assertEqual(len(live.request.sent), 1)
        frame = live.request.sent[0]
        self.assertEqual(frame[0], 0x88)
        self.assertIn(b"server shutdown (api)", frame)


if __name__ == "__main__":
    unittest.main()
