"""Exercise the real HTTP and Socket.IO boundaries against a temporary database."""

import os
import hashlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io      import BytesIO
from pathlib import Path

import requests
import socketio
from PIL import Image


class ChessFlow(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "chess.sqlite"
        self.log = open(Path(self.directory.name) / "server.log", "w+")
        self.addCleanup(self.log.close)

        with socket.socket() as port:
            port.bind(("127.0.0.1", 0))
            self.port = port.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.origin = "https://chess.example"
        self.start_server()
        self.addCleanup(self.stop_server)

    def start_server(self):
        self.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port)],
            cwd = Path(__file__).resolve().parents[1],
            env = {**os.environ, "CHESS_DB": str(self.database), "CHESS_ORIGIN": self.origin},
            stdout = self.log, stderr = self.log,
        )
        for _ in range(100):
            try:
                if requests.get(self.url + "/api/health", timeout = 0.2).ok:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.05)
        self.log.seek(0)
        self.fail(self.log.read())

    def stop_server(self):
        self.server.terminate()
        self.server.wait(timeout = 10)

    def account(self, username):
        http = requests.Session()
        http.headers["Origin"] = self.origin
        self.addCleanup(http.close)
        result = http.post(self.url + "/api/register", json = {"username": username, "password": "correct-password"})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertNotIn("Set-Cookie", result.headers)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        session = result.json()
        http.headers["Authorization"] = "Bearer " + session["token"]
        return http, session["user"]

    def connect(self, http, room_id = None):
        client = socketio.Client(reconnection = False, websocket_extra_options = {"origin": self.origin})
        events = {"seat": None, "waiting": False, "state": None, "closed": None}
        client.on("seat", lambda value = None: events.update(seat = value))
        client.on("waiting", lambda value: events.update(waiting = value))
        client.on("state", lambda value: events.update(state = value))
        client.on("session_closed", lambda value: events.update(closed = value))
        client.connect(self.url, socketio_path = "api/socket.io", transports = ["websocket"],
                       auth = {"id": room_id, "token": http.headers["Authorization"].removeprefix("Bearer ")})
        self.addCleanup(client.disconnect)
        return client, events

    def enter(self, client, kind, room_id = None):
        result = client.call("enter", {"type": kind, "id": room_id}, timeout = 3)
        self.assertIsNone(result["error"], result)
        return result

    def eventually(self, predicate):
        for _ in range(60):
            if predicate():
                return
            time.sleep(0.05)
        self.fail("Expected socket state did not arrive")

    def test_accounts_avatars_and_restart(self):
        alice, user = self.account("Alice")
        self.assertEqual(user["username"], "alice")
        token = alice.headers["Authorization"].removeprefix("Bearer ")
        with closing(sqlite3.connect(self.database)) as db:
            stored = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
            self.assertEqual(stored, hashlib.sha256(token.encode()).hexdigest())
        duplicate = alice.post(self.url + "/api/register", json = {"username": "ALICE", "password": "correct-password"})
        self.assertEqual(duplicate.status_code, 409)
        wrong = alice.post(self.url + "/api/session", json = {"username": "alice", "password": "wrong-password"})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(requests.put(self.url + "/api/me/avatar", headers = {"Origin": self.origin}, data = b"x").status_code, 401)
        self.assertIsNone(requests.get(self.url + "/api/me", cookies = {"chess_session": token}).json())

        self.assertEqual(alice.put(self.url + "/api/me/avatar", data = b"not an image").status_code, 400)
        self.assertEqual(alice.put(self.url + "/api/me/avatar", data = b"x" * (2 * 1024 * 1024 + 1)).status_code, 413)
        picture = BytesIO()
        Image.new("RGB", (400, 300), "green").save(picture, format = "PNG")
        uploaded = alice.put(self.url + "/api/me/avatar", data = picture.getvalue())
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        avatar = uploaded.json()["avatar"]
        image = alice.get(self.url + avatar)
        self.assertEqual(image.headers["Content-Type"], "image/webp")
        self.assertEqual(Image.open(BytesIO(image.content)).size, (256, 256))

        self.stop_server()
        self.start_server()
        self.assertEqual(alice.get(self.url + "/api/me").json()["avatar"], avatar)
        client, events = self.connect(alice)
        self.assertEqual(alice.delete(self.url + "/api/session").status_code, 204)
        self.eventually(lambda: events["closed"] is not None)
        self.assertIsNone(alice.get(self.url + "/api/me").json())
        with self.assertRaises(socketio.exceptions.ConnectionError):
            self.connect(alice)

        with closing(sqlite3.connect(self.database)) as db:
            hashed = db.execute("SELECT password_hash FROM users").fetchone()[0]
            self.assertTrue(hashed.startswith("$argon2id$"))
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)

    def test_logout_only_closes_its_own_session(self):
        alice, _ = self.account("alice")
        another = requests.Session()
        another.headers["Origin"] = self.origin
        self.addCleanup(another.close)

        login = another.post(self.url + "/api/session", json = {
            "username": "ALICE", "password": "correct-password",
        })
        self.assertEqual(login.status_code, 200, login.text)
        another.headers["Authorization"] = "Bearer " + login.json()["token"]
        client, events = self.connect(another)

        self.assertEqual(alice.delete(self.url + "/api/session").status_code, 204)
        self.enter(client, "create")
        self.assertIsNone(events["closed"])

        self.assertEqual(another.delete(self.url + "/api/session").status_code, 204)
        self.eventually(lambda: events["closed"] is not None)
        self.assertEqual(another.delete(self.url + "/api/session").status_code, 204)

    def test_cross_origin_requests_and_socket_auth(self):
        alice, user = self.account("alice")
        headers = {
            "Origin": self.origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type",
        }
        preflight = requests.options(self.url + "/api/me/avatar", headers = headers)
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(preflight.headers["Access-Control-Allow-Origin"], self.origin)
        self.assertNotIn("Access-Control-Allow-Credentials", preflight.headers)

        result = alice.get(self.url + "/api/me")
        self.assertEqual(result.json(), user)
        self.assertEqual(result.headers["Access-Control-Allow-Origin"], self.origin)
        self.assertEqual(result.headers["Cache-Control"], "no-store")

        headers["Origin"] = "https://other.example"
        denied = requests.options(self.url + "/api/me/avatar", headers = headers)
        self.assertEqual(denied.status_code, 400)
        self.assertNotIn("Access-Control-Allow-Origin", denied.headers)

        token = alice.headers["Authorization"].removeprefix("Bearer ")
        for origin, auth in (
            (self.origin, {}),
            (self.origin, {"token": "x" * 43}),
            (self.origin, {"token": token, "id": "invalid"}),
            ("https://other.example", {"token": token}),
        ):
            with self.subTest(origin = origin, auth = bool(auth)):
                client = socketio.Client(websocket_extra_options = {"origin": origin})
                self.addCleanup(client.disconnect)
                with self.assertRaises(socketio.exceptions.ConnectionError):
                    client.connect(self.url, socketio_path = "api/socket.io",
                                   transports = ["websocket"], auth = auth)

        forged = alice.put(self.url + "/api/me/avatar", data = b"x",
                           headers = {"Authorization": "Basic " + token})
        self.assertEqual(forged.status_code, 401)

    def test_matching_invites_and_reconnection(self):
        alice, _ = self.account("alice")
        bob, _   = self.account("bob")
        carol, _ = self.account("carol")
        a, ae = self.connect(alice)
        b, be = self.connect(bob)
        c, ce = self.connect(carol)

        self.enter(a, "match")
        self.enter(a, "match")
        self.assertTrue(ae["waiting"])
        self.enter(a, "cancel")
        self.assertFalse(ae["waiting"])

        with ThreadPoolExecutor() as pool:
            list(pool.map(lambda client: self.enter(client, "match"), (a, b, c)))
        self.eventually(lambda: sum(e["seat"] is not None for e in (ae, be, ce)) == 2)
        paired  = [(client, event, http) for client, event, http in ((a, ae, alice), (b, be, bob), (c, ce, carol)) if event["seat"]]
        waiting = next((client, event) for client, event in ((a, ae), (b, be), (c, ce)) if event["seat"] is None)
        self.assertEqual(paired[0][1]["seat"]["id"], paired[1][1]["seat"]["id"])
        self.assertNotEqual(paired[0][1]["seat"]["color"], paired[1][1]["seat"]["color"])
        self.enter(waiting[0], "cancel")

        white = next(p for p in paired if p[1]["seat"]["color"] == "white")
        black = next(p for p in paired if p[1]["seat"]["color"] == "black")
        self.eventually(lambda: len(white[1]["state"]["connected"]) == 2)
        denied = black[0].call("command", {"type": "move", "move": "e7e5", "revision": 0})
        self.assertIsNotNone(denied["error"])
        move = white[0].call("command", {"type": "move", "move": "e2e4", "revision": 0})
        self.assertIsNone(move["error"])
        self.eventually(lambda: black[1]["state"]["moves"] == ["e4"])

        replacement, restored = self.connect(white[2])
        self.eventually(lambda: white[1]["closed"] is not None)
        self.assertEqual(restored["seat"], white[1]["seat"])
        self.assertEqual(restored["state"]["moves"], ["e4"])
        self.assertIsNone(replacement.call("command", {"type": "resign", "revision": 1})["error"])

        self.enter(replacement, "create")
        invite = restored["seat"]["id"]
        self.enter(black[0], "join", invite)
        self.assertEqual(black[1]["seat"]["id"], invite)
        self.assertIsNotNone(waiting[0].call("enter", {"type": "join", "id": invite})["error"])

        self.enter(waiting[0], "create")
        self.enter(waiting[0], "cancel")
        self.assertIsNone(waiting[1]["seat"])
        self.enter(waiting[0], "match")
        waiting[0].disconnect()

    def test_expired_session_cannot_move(self):
        alice, user = self.account("alice")
        client, events = self.connect(alice)
        self.enter(client, "create")
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE sessions SET expires_at = 0 WHERE user_id = ?", (user["id"],))
        client.emit("command", {"type": "resign", "revision": 0})
        self.eventually(lambda: events["closed"] is not None)
        self.assertIsNone(alice.get(self.url + "/api/me").json())

    def test_cancel_races_with_match_and_disconnect(self):
        alice, _ = self.account("alice")
        bob, _   = self.account("bob")
        a, ae = self.connect(alice)
        b, be = self.connect(bob)

        for _ in range(4):
            self.enter(a, "match")
            with ThreadPoolExecutor() as pool:
                cancel = pool.submit(self.enter, a, "cancel")
                match  = pool.submit(self.enter, b, "match")
                cancel.result()
                match.result()

            if ae["seat"] is not None:
                self.assertEqual(ae["seat"]["id"], be["seat"]["id"])
                self.assertIsNone(a.call("command", {"type": "resign", "revision": 0})["error"])
            else:
                self.assertTrue(be["waiting"])
            self.enter(a, "cancel")
            self.enter(b, "cancel")

        self.enter(a, "match")
        a.disconnect()
        self.enter(b, "match")
        self.assertIsNone(be["seat"])
        self.assertTrue(be["waiting"])
        replacement, restored = self.connect(alice)
        self.enter(replacement, "match")
        self.assertEqual(restored["seat"]["id"], be["seat"]["id"])
