import asyncio
import json

from contextlib    import contextmanager
from time          import monotonic
from unittest.mock import AsyncMock

import pytest

from fastapi              import WebSocket
from fastapi.testclient   import TestClient
from starlette.websockets import WebSocketDisconnect

from app.game import Game
from app.main import MAX_ROOMS, ROOM_TTL_SECONDS, app, play, rooms
from app.room import Room


@pytest.fixture
def client():
    rooms.clear()
    with TestClient(app) as client:
        yield client
    rooms.clear()


def create(client):
    response = client.post("/api/games")
    assert response.status_code == 201
    return response.json()


@contextmanager
def connect(client, session):
    with client.websocket_connect(f"/api/games/{session['id']}/ws") as socket:
        socket.send_json({"token": session["token"]})
        yield socket


def state(socket):
    message = socket.receive_json()
    assert message["type"] == "state"
    return message["game"]


def test_http_errors_and_seat_reservation(client):
    assert client.get("/api/health").json() == {"status": "ok"}

    white = create(client)
    black = client.post(f"/api/games/{white['id']}/join").json()

    assert black["color"] == "black"
    assert black["token"] != white["token"]

    assert client.post(f"/api/games/{white['id']}/join").status_code == 409
    assert client.post("/api/games/000000000000/join").status_code == 404
    assert client.post("/api/games/invalid/join").status_code == 422


@pytest.mark.parametrize("token", ["wrong", "한글", "", None])
def test_auth_rejects_invalid_tokens(client, token):
    session = create(client)
    with client.websocket_connect(f"/api/games/{session['id']}/ws") as socket:
        socket.send_json({"token": token})
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4401
    assert not rooms[session["id"]].connected


def test_missing_websocket_room(client):
    with client.websocket_connect("/api/games/000000000000/ws") as socket:
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 4404


def test_waiting_room_cannot_move(client):
    session = create(client)
    with connect(client, session) as socket:
        assert state(socket)["joined"] == ["white"]
        socket.send_json({"type": "move", "revision": 0, "move": "e2e4"})
        assert socket.receive_json()["type"] == "error"
        assert state(socket)["revision"] == 0


def test_two_clients_validate_sync_and_reconnect(client):
    white = create(client)

    with connect(client, white) as w:
        state(w)
        black = client.post(f"/api/games/{white['id']}/join").json()
        assert state(w)["joined"] == ["white", "black"]

        with connect(client, black) as b:
            assert state(w) == state(b)

            b.send_json({"type": "move", "revision": 0, "move": "e7e5"})
            assert b.receive_json()["type"] == "error"
            assert state(w) == state(b)

            for raw in [
                'not json', '[]',
                '{"type":"move"}',
                '{"type":"move","revision":true}',
            ]:
                w.send_text(raw)
                assert w.receive_json()["type"] == "error"
                assert state(w) == state(b)

            w.send_json({"type": "move", "revision": 0, "move": "e2e4"})
            current = state(w)
            assert current == state(b)
            assert current["moves"] == ["e4"]
            assert "token" not in str(current)

            b.send_json({"type": "move", "revision": 0, "move": "e7e5"})
            assert b.receive_json()["type"] == "error"
            assert state(w) == state(b)

            b.send_json({"type": "move", "revision": 1, "move": "e7e5"})
            assert state(w) == state(b)

        assert state(w)["connected"] == ["white"]
        w.send_json({"type": "move", "revision": 2, "move": "g1f3"})
        assert w.receive_json()["type"] == "error"
        assert state(w)["revision"] == 2

        with connect(client, black) as b:
            current = state(b)
            assert current == state(w)
            assert current["moves"] == ["e4", "e5"]
            assert current["revision"] == 2


def test_replaced_connection_does_not_remove_new_socket(client):
    white = create(client)

    with connect(client, white) as first:
        state(first)

        with connect(client, white) as second:
            assert state(second)["connected"] == ["white"]
            with pytest.raises(WebSocketDisconnect) as error:
                first.receive_json()
            assert error.value.code == 4409

            black = client.post(f"/api/games/{white['id']}/join").json()
            assert state(second)["connected"] == ["white"]

            with connect(client, black) as b:
                assert state(second) == state(b)
                second.send_json({"type": "move", "revision": 0, "move": "d2d4"})
                assert state(second)["moves"] == ["d4"]
                assert state(b)["moves"] == ["d4"]


def test_resign_with_disconnected_opponent(client):
    white = create(client)
    client.post(f"/api/games/{white['id']}/join")
    with connect(client, white) as socket:
        state(socket)
        socket.send_json({"type": "resign", "revision": 0})
        assert state(socket)["outcome"] == {"result": "0-1", "reason": "resignation"}


def test_room_cleanup_and_capacity(client):
    expired_at = monotonic() - ROOM_TTL_SECONDS - 1
    rooms["000000000000"] = Room(last_activity = expired_at)
    assert client.post("/api/games/000000000000/join").status_code == 404

    for index in range(MAX_ROOMS):
        rooms[f"{index:012x}"] = Room()

    assert client.post("/api/games").status_code == 503


@pytest.mark.parametrize("authenticated", [False, True])
def test_binary_frames_are_rejected_at_the_receive_boundary(client, authenticated):
    session = create(client)
    with client.websocket_connect(f"/api/games/{session['id']}/ws") as socket:
        if authenticated:
            socket.send_json({"token": session["token"]})
            state(socket)
        socket.send_bytes(b"not a text frame")
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == (1003 if authenticated else 4401)


@pytest.mark.parametrize("error_type", [RuntimeError, KeyError, ValueError])
def test_internal_errors_escape_the_socket_handler_and_clean_up(monkeypatch, error_type):
    def fail(*args):
        raise error_type("internal failure")

    monkeypatch.setattr(Game, "apply", fail)

    async def scenario():
        room = Room()
        await room.join()
        room_id = "ffffffffffff"
        monkeypatch.setitem(rooms, room_id, room)

        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)
        white.receive_text.side_effect = [
            json.dumps({"token": room.white_token}),
            '{"type":"move","revision":0,"move":"e2e4"}',
        ]

        async with room.connect("black", black):
            with pytest.raises(error_type, match = "internal failure"):
                await play(white, room_id)

            assert room.connected == ["black"]
            assert black.send_json.call_args.args[0]["game"]["connected"] == ["black"]

            messages = [call.args[0] for call in white.send_json.call_args_list]
            assert all(message["type"] == "state" for message in messages)
            white.close.assert_not_called()

    asyncio.run(scenario())
