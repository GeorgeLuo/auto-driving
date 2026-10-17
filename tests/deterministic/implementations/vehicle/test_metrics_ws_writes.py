from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from implementations.vehicle.chase_sim.metrics_ws import MetricsUiConnection, MetricsUiWebSocketError


class WebSocketWriteTests(unittest.TestCase):
    def connection(self):
        connection = MetricsUiConnection('ws://example.invalid', timeout_s=0.1)
        connection.sock = mock.Mock()
        self.addCleanup(connection.abort)
        return connection

    def test_concurrent_writes_serialize_whole_frames(self):
        connection = self.connection()
        entered, release = threading.Event(), threading.Event()
        def send(_data):
            entered.set()
            self.assertTrue(release.wait(1))
        connection.sock.sendall.side_effect = send
        connection.timeout_s = 2
        first = threading.Thread(target=connection.send_json, args=({'type': 'request'},))
        second = threading.Thread(target=connection._send_frame, args=(b'pong',), kwargs={'opcode': 10})
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        self.assertEqual(connection.sock.sendall.call_count, 1)
        release.set()
        first.join(1)
        second.join(1)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(connection.sock.sendall.call_count, 2)

    def test_blocked_write_is_aborted_at_deadline(self):
        connection = self.connection()
        sock = connection.sock
        release = threading.Event()
        sock.sendall.side_effect = lambda _data: release.wait(1)
        sock.shutdown.side_effect = lambda _how: release.set()
        started = time.monotonic()
        with self.assertRaises(MetricsUiWebSocketError):
            connection.send_json({'type': 'request'})
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertIsNone(connection.sock)
        self.assertTrue(sock.shutdown.called)

    def test_lock_wait_uses_same_deadline_and_aborts(self):
        connection = self.connection()
        connection._send_lock.acquire()
        try:
            with self.assertRaisesRegex(MetricsUiWebSocketError, 'writer'):
                connection.send_json({'type': 'request'})
            self.assertIsNone(connection.sock)
        finally:
            connection._send_lock.release()

    def test_close_does_not_wait_indefinitely_for_writer(self):
        connection = self.connection()
        connection._send_lock.acquire()
        try:
            started = time.monotonic()
            connection.close(timeout_s=0.02)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertIsNone(connection.sock)
        finally:
            connection._send_lock.release()
