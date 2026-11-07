import json
import os
import subprocess
import sys
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from websockets.exceptions import InvalidProxy
from websockets.sync.server import serve

from infra.backend.smoke import SmokeError, check_endpoint


class HttpHealthTests(unittest.TestCase):
    def setUp(self):
        self.http = self.enterContext(patch("infra.backend.smoke.subprocess.run"))
        self.http.return_value.stdout = '{"status":"ok"}'
        self.connect = self.enterContext(patch("infra.backend.smoke.connect"))
        self.websocket = self.connect.return_value.__enter__.return_value
        self.websocket.recv.return_value = '0{"sid":"session-1"}'

    def test_http_failure_stops_before_websocket(self):
        errors = (
            subprocess.CalledProcessError(22, "curl"), FileNotFoundError("curl"),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
        )
        for error in errors:
            with self.subTest(error=error):
                self.http.side_effect = error
                with self.assertRaisesRegex(SmokeError, "HTTP health request failed"):
                    check_endpoint("http://localhost:8000")

        self.connect.assert_not_called()

    def test_invalid_health_response_stops_before_websocket(self):
        for body in ("", "not json", "[]", "null", "{}", '{"status":"failed"}'):
            with self.subTest(body=body):
                self.http.return_value.stdout = body
                with self.assertRaisesRegex(SmokeError, "HTTP health response"):
                    check_endpoint("http://localhost:8000")

        self.connect.assert_not_called()

    def test_https_uses_secure_websocket_and_bounded_timeouts(self):
        check_endpoint("https://backend.example.test/")

        self.connect.assert_called_once_with(
            "wss://backend.example.test/api/socket.io/?EIO=4&transport=websocket",
            open_timeout=5, close_timeout=5,
        )
        self.websocket.recv.assert_called_once_with(timeout=5)
        command = self.http.call_args.args[0]
        self.assertEqual(command[-1], "https://backend.example.test/api/health")
        self.assertIn("--retry-all-errors", command)
        for option, expected in (("--max-time", "10"), ("--retry", "8"), ("--retry-delay", "2")):
            self.assertEqual(command[command.index(option) + 1], expected)

    def test_invalid_url_fails_before_network_access(self):
        urls = (
            "ftp://backend.example.test", "http://", "http://[broken",
            "http://user:secret@backend.example.test", "https://backend.example.test?token=secret",
        )
        for url in urls:
            with self.subTest(url=url), self.assertRaises(SmokeError):
                check_endpoint(url)

        self.http.assert_not_called()
        self.connect.assert_not_called()

    def test_connection_errors_use_the_smoke_failure_boundary(self):
        for error in (ConnectionRefusedError("refused"), TimeoutError("opening timed out")):
            with self.subTest(error=error):
                self.connect.side_effect = error
                with self.assertRaisesRegex(SmokeError, "WebSocket session check failed"):
                    check_endpoint("http://localhost:8000")

    def test_proxy_failure_does_not_expose_proxy_credentials(self):
        self.connect.side_effect = InvalidProxy("http://user:secret@proxy", "invalid proxy")

        with self.assertRaisesRegex(SmokeError, "InvalidProxy") as failure:
            check_endpoint("http://localhost:8000")
        self.assertNotIn("secret", str(failure.exception))

    def test_missing_optional_proxy_support_uses_the_smoke_failure_boundary(self):
        self.connect.side_effect = ImportError("connecting through a SOCKS proxy requires python-socks")

        with self.assertRaisesRegex(SmokeError, "WebSocket proxy support is unavailable"):
            check_endpoint("http://localhost:8000")


class ProxyConfigurationTests(unittest.TestCase):
    def test_malformed_proxy_urls_fail_without_exposing_credentials(self):
        proxies = (
            "http://[broken", "http://localhost:bad", "http://localhost:999999",
            "http://user:secret@／host:8080",
        )
        with patch("infra.backend.smoke.subprocess.run", return_value=Mock(stdout='{"status":"ok"}')):
            for proxy in proxies:
                with self.subTest(proxy=proxy), patch.dict(os.environ, {"wss_proxy": proxy}, clear=True):
                    with self.assertRaisesRegex(SmokeError, "ValueError") as failure:
                        check_endpoint("https://backend.example.test")
                    self.assertNotIn("secret", str(failure.exception))


@contextmanager
def backend_endpoint(packet='0{"sid":"session-1"}', handshake_status=None):
    paths = []
    application_messages = []
    close_codes = []
    closed = threading.Event()

    def health_or_upgrade(connection, request):
        paths.append(request.path)
        if request.path == "/api/health":
            return connection.respond(200, json.dumps({"status": "ok"}))
        if handshake_status is not None:
            return connection.respond(handshake_status, "WebSocket unavailable")
        return None

    def session(connection):
        try:
            if packet is not None:
                connection.send(packet)
            # The check must only receive the open packet and close, without app events.
            for message in connection:
                application_messages.append(message)
        finally:
            close_codes.append(connection.close_code)
            closed.set()

    with serve(
        session, "127.0.0.1", 0,
        origins=[None], process_request=health_or_upgrade,
        close_timeout=1, logger=Mock(),
    ) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.socket.getsockname()[1]

        try:
            yield f"http://127.0.0.1:{port}", paths, application_messages, close_codes, closed
        finally:
            server.shutdown()
            thread.join(timeout=2)


class WebSocketIntegrationTests(unittest.TestCase):
    def test_http_then_engine_io_session_and_clean_close(self):
        with backend_endpoint() as (url, paths, messages, close_codes, closed):
            check_endpoint(url)

            self.assertTrue(closed.wait(2))
            self.assertEqual(paths, ["/api/health", "/api/socket.io/?EIO=4&transport=websocket"])
            self.assertEqual(messages, [])
            self.assertEqual(close_codes, [1000])

    def test_malformed_or_missing_session_cannot_pass(self):
        packets = (
            '4{"sid":"wrong-packet-type"}', b'0{"sid":"binary"}', "0not json",
            "0[]", "0{}", '0{"sid":""}', '0{"sid":" "}', '0{"sid":123}',
        )
        for packet in packets:
            with self.subTest(packet=packet), backend_endpoint(packet) as (url, _, messages, _, closed):
                with self.assertRaises(SmokeError):
                    check_endpoint(url)
                self.assertTrue(closed.wait(2))
                self.assertEqual(messages, [])

    def test_failed_upgrade_cannot_pass(self):
        with backend_endpoint(handshake_status=403) as (url, paths, _, _, _):
            with self.assertRaisesRegex(SmokeError, "WebSocket session check failed"):
                check_endpoint(url)
            self.assertEqual(len(paths), 2)

    def test_session_packet_timeout_fails_and_closes_connection(self):
        with backend_endpoint(packet=None) as (url, _, messages, _, closed):
            with self.assertRaisesRegex(SmokeError, "WebSocket session check failed"):
                check_endpoint(url)
            self.assertTrue(closed.wait(2))
            self.assertEqual(messages, [])

    def test_cli_returns_failure_for_invalid_session(self):
        with backend_endpoint("0{}") as (url, _, _, _, _):
            result = subprocess.run(
                [sys.executable, "-m", "infra.backend.smoke", url],
                capture_output=True, text=True, timeout=15,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("nonempty session ID", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
