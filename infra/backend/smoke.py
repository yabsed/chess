"""Check HTTP health and the Engine.IO WebSocket handshake."""

import argparse
import json
import subprocess

from websockets.sync.client import connect


def check_endpoint(origin: str) -> None:
    origin = origin.rstrip("/")

    # HTTP health

    response = subprocess.check_output(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time", "10",
            "--retry", "8",
            "--retry-all-errors",
            "--retry-delay", "2",
            f"{origin}/api/health",
        ],
        text=True,
    )

    if json.loads(response).get("status") != "ok":
        raise ValueError("HTTP health check failed")


    # Engine.IO WebSocket handshake

    websocket = origin.replace("https://", "wss://", 1)
    websocket = websocket.replace("http://", "ws://", 1)

    with connect(
        f"{websocket}/api/socket.io/?EIO=4&transport=websocket",
        open_timeout=5,
        close_timeout=5,
        proxy=None,
    ) as connection:
        packet = connection.recv(timeout=5)

    if not isinstance(packet, str) or not packet.startswith("0"):
        raise ValueError("Expected an Engine.IO open packet")

    session = json.loads(packet[1:])
    sid = session.get("sid")
    if not isinstance(sid, str) or not sid.strip():
        raise ValueError("Expected a nonempty Engine.IO session ID")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="API origin, such as http://127.0.0.1:8000")
    check_endpoint(parser.parse_args().origin)
    print("HTTP and WebSocket checks passed.")
