"""The CLI's JSON requests to a host or catalog route."""

from __future__ import annotations

import re
import unittest

from cli.automa_cli.shared.io.http_json import request_json
from tests.support.fake_hosts import answering, closed_url, hung_url


class HttpJsonTests(unittest.TestCase):
    def test_an_error_status_with_a_json_object_is_the_answer(self) -> None:
        with answering(b'{"ok": false, "error": "no decision yet"}', 503) as url:
            self.assertEqual(request_json(url, timeout_s=3.0), (503, {"ok": False, "error": "no decision yet"}))

    def test_a_reply_without_a_json_object_names_why(self) -> None:
        for body, status, reason in (
            (b"[]", 200, "returned a non-object JSON payload"),
            (b"<html></html>", 200, "returned non-JSON body"),
            (b"", 503, "returned HTTP 503"),
        ):
            with self.subTest(body=body, status=status), answering(body, status) as url:
                with self.assertRaisesRegex(ConnectionError, rf"^GET {re.escape(url)} {reason}$"):
                    request_json(url, timeout_s=3.0)

    def test_a_request_without_a_reply_names_the_request_and_keeps_the_cause(self) -> None:
        with hung_url() as hung, answering(None) as dropped:
            for url, timeout_s, reason in (
                (closed_url(), 3.0, "failed: .*Connection refused"),
                (hung, 0.2, "did not answer within 0.2 s"),
                (dropped, 3.0, "failed: "),
            ):
                with self.subTest(reason=reason):
                    with self.assertRaisesRegex(ConnectionError, rf"^GET {re.escape(url)} {reason}") as raised:
                        request_json(url, timeout_s=timeout_s)
                    self.assertIsNotNone(raised.exception.__cause__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
