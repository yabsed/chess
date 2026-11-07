"""Check HTTP health and open an Engine.IO session without sending application events."""

import argparse
import json
import subprocess
import sys
from urllib.parse import urlsplit

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect


class SmokeError(RuntimeError):
    """The backend endpoint did not pass its HTTP or WebSocket check."""


def check_endpoint(api_url: str) -> None:
    try:
        origin = urlsplit(api_url.rstrip("/"))
    except ValueError as error:
        raise SmokeError("Use a valid HTTP or HTTPS API URL.") from error

    if origin.scheme not in {"http", "https"} or not origin.hostname:
        raise SmokeError("Use a valid HTTP or HTTPS API URL.")
    if origin.username or origin.password or origin.query or origin.fragment:
        raise SmokeError("The API URL must not contain credentials, a query or a fragment.")

    health_url = f"{origin.geturl()}/api/health"
    try:
        response = subprocess.run(
            [
                "curl", "--fail", "--silent", "--show-error", "--max-time", "10",
                "--retry", "8", "--retry-all-errors", "--retry-delay", "2",
                health_url,
            ],
            check=True, stdout=subprocess.PIPE, encoding="utf-8",
        )
    except (OSError, UnicodeError, subprocess.CalledProcessError) as error:
        raise SmokeError("HTTP health request failed.") from error

    try:
        health = json.loads(response.stdout)
    except ValueError as error:
        raise SmokeError("HTTP health response is not valid JSON.") from error

    if not isinstance(health, dict) or health.get("status") != "ok":
        raise SmokeError("HTTP health response must report status 'ok'.")

    websocket_scheme = "wss" if origin.scheme == "https" else "ws"
    websocket_origin = origin._replace(scheme=websocket_scheme).geturl()
    websocket_url = f"{websocket_origin}/api/socket.io/?EIO=4&transport=websocket"

    try:
        with connect(websocket_url, open_timeout=5, close_timeout=5) as websocket:
            packet = websocket.recv(timeout=5)

            # Engine.IO starts the session with packet type 0 (open), followed by JSON.
            if not isinstance(packet, str) or not packet.startswith("0"):
                raise SmokeError("Expected an Engine.IO open packet.")

            try:
                session = json.loads(packet[1:])
            except ValueError as error:
                raise SmokeError("Engine.IO open packet is not valid JSON.") from error

            if not isinstance(session, dict):
                raise SmokeError("Engine.IO open packet must contain a session object.")

            session_id = session.get("sid")
            if not isinstance(session_id, str) or not session_id.strip():
                raise SmokeError("Engine.IO open packet must contain a nonempty session ID.")

    except ImportError as error:
        raise SmokeError("WebSocket proxy support is unavailable.") from error
    except (OSError, ValueError, WebSocketException) as error:
        raise SmokeError(f"WebSocket session check failed ({type(error).__name__}).") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("api_url", help="API origin, such as http://127.0.0.1:8000")
    args = parser.parse_args()

    try:
        check_endpoint(args.api_url)
    except SmokeError as error:
        print(error, file=sys.stderr)
        return 1

    print("HTTP and WebSocket checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
