"""Exercise the smoke command against a local HTTP and WebSocket server."""

import json
import subprocess
import sys
import threading
import unittest
from contextlib import contextmanager

from websockets.sync.server import serve


@contextmanager
def endpoint(health="ok", packet='0{"sid":"session-1"}'):
    paths, messages = [], []

    def route(connection, request):
        paths.append(request.path)
        if request.path == "/api/health":
            return connection.respond(200, json.dumps({"status": health}))
        return None

    def websocket(connection):
        connection.send(packet)
        messages.extend(connection)

    with serve(websocket, "127.0.0.1", 0, process_request=route, close_timeout=1) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.socket.getsockname()[1]}", paths, messages
        finally:
            server.shutdown()
            thread.join()


class SmokeTests(unittest.TestCase):
    def run_check(self, url):
        return subprocess.run(
            [sys.executable, "-m", "infra.backend.smoke", url],
            capture_output=True, text=True, timeout=15,
        )

    def test_healthy_http_and_websocket(self):
        with endpoint() as (url, paths, messages):
            result = self.run_check(url)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(paths, ["/api/health", "/api/socket.io/?EIO=4&transport=websocket"])
        self.assertEqual(messages, [])

    def test_unhealthy_http_stops_before_websocket(self):
        with endpoint(health="failed") as (url, paths, _):
            result = self.run_check(url)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(paths, ["/api/health"])

    def test_invalid_websocket_session_fails(self):
        for packet in ('4{"sid":"wrong-type"}', '0{}', '0{"sid":" "}'):
            with self.subTest(packet=packet), endpoint(packet=packet) as (url, _, _):
                result = self.run_check(url)
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
