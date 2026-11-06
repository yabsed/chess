"""Two real backends share an isolated PostgreSQL schema for every test."""

import os
import hashlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from concurrent.futures import ThreadPoolExecutor
from io      import BytesIO
from pathlib import Path

import requests
import socketio
import psycopg

from PIL import Image
from pwdlib import PasswordHash
from psycopg import sql
from psycopg.conninfo import make_conninfo


class ChessFlow(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = os.environ.get("CHESS_TEST_DATABASE_URL", "postgresql://chess:chess@127.0.0.1:5432/chess")
        self.schema = "test_" + uuid.uuid4().hex
        with psycopg.connect(self.base) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.database = make_conninfo(self.base, options = f"-c search_path={self.schema}")
        self.log = open(Path(self.directory.name) / "server.log", "w+")
        self.addCleanup(self.log.close)

        self.ports   = []
        self.servers = [None, None]
        for _ in range(2):
            with socket.socket() as port:
                port.bind(("127.0.0.1", 0))
                self.ports.append(port.getsockname()[1])
        self.urls = [f"http://127.0.0.1:{port}" for port in self.ports]
        self.url  = self.urls[0]
        self.next_server = 1
        self.origin = "https://chess.example"
        for index in range(2):
            self.addCleanup(self.stop_server, index)
            self.start_server(index)

    def drop_schema(self):
        with psycopg.connect(self.base) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def start_server(self, index = 0):
        self.servers[index] = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.ports[index])],
            cwd = Path(__file__).resolve().parents[1],
            env = {**os.environ, "CHESS_DATABASE_URL": self.database, "CHESS_ORIGIN": self.origin},
            stdout = self.log, stderr = self.log,
        )
        for _ in range(100):
            try:
                if requests.get(self.urls[index] + "/api/health", timeout = 0.2).ok:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.05)
        self.log.seek(0)
        self.fail(self.log.read())

    def stop_server(self, index = 0):
        server = self.servers[index]
        if server is not None and server.poll() is None:
            server.terminate()
            server.wait(timeout = 10)

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

    def connect(self, http, room_id = None, server = None):
        client = socketio.Client(reconnection = False, websocket_extra_options = {"origin": self.origin})
        events = {"seat": None, "waiting": False, "state": None, "closed": None}
        client.on("state", lambda value: events.update(seat = value["seat"], waiting = value["waiting"], state = value["game"]))
        client.on("session_closed", lambda value: events.update(closed = value))
        if server is None:
            server = self.next_server
            self.next_server = 1 - server
        client.connect(self.urls[server], socketio_path = "api/socket.io", transports = ["websocket"],
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
        with psycopg.connect(self.database) as db:
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

        with psycopg.connect(self.database) as db:
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
        with psycopg.connect(self.database) as db:
            db.execute("UPDATE sessions SET expires_at = now() - interval '1 day' WHERE user_id = %s", (user["id"],))
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
        sid = a.get_sid()
        a.disconnect()
        with psycopg.connect(self.database) as db:
            self.eventually(lambda: db.execute("SELECT 1 FROM players WHERE sid = %s", (sid,)).fetchone() is None)
        self.enter(b, "match")
        self.assertIsNone(be["seat"])
        self.assertTrue(be["waiting"])
        replacement, restored = self.connect(alice)
        self.enter(replacement, "match")
        self.eventually(lambda: be["seat"] is not None)
        self.assertEqual(restored["seat"]["id"], be["seat"]["id"])

    def invited_game(self):
        alice, _ = self.account("alice")
        bob, _   = self.account("bob")
        a, ae = self.connect(alice, server = 0)
        b, be = self.connect(bob, server = 1)
        self.enter(a, "create")
        self.enter(b, "join", ae["seat"]["id"])
        self.eventually(lambda: len(ae["state"]["connected"]) == 2)
        return alice, bob, a, ae, b, be

    def test_moves_and_repetition_survive_a_killed_server(self):
        alice, bob, a, ae, b, be = self.invited_game()
        moves = ["g1f3", "g8f6", "f3g1", "f6g8"] * 2
        for revision, move in enumerate(moves):
            client = a if revision % 2 == 0 else b
            result = client.call("command", {"type": "move", "move": move, "revision": revision})
            self.assertIsNone(result["error"])
        self.eventually(lambda: ae["state"]["revision"] == 8)
        self.assertTrue(ae["state"]["can_claim_draw"])
        before = ae["state"]
        room_id = ae["seat"]["id"]

        self.servers[0].kill()
        self.servers[0].wait(timeout = 5)
        replacement, restored = self.connect(alice, server = 1)
        self.assertEqual(restored["seat"]["id"], room_id)
        for key in ("fen", "moves", "revision", "can_claim_draw"):
            self.assertEqual(restored["state"][key], before[key])
        self.assertEqual(len(restored["state"]["connected"]), 2)

        result = replacement.call("command", {"type": "claim_draw", "revision": 8})
        self.assertIsNone(result["error"])
        self.eventually(lambda: be["state"]["outcome"] is not None)
        self.assertEqual(be["state"]["outcome"]["reason"], "draw_claim")

        self.start_server(0)
        resumed, final = self.connect(alice, room_id, server = 0)
        self.assertEqual(final["state"]["revision"], 9)
        self.assertEqual(final["state"]["outcome"]["result"], "1/2-1/2")

    def test_simultaneous_commands_commit_once(self):
        alice, bob, a, ae, b, be = self.invited_game()
        command = {"type": "offer_draw", "revision": 0}
        with ThreadPoolExecutor() as pool:
            replies = list(pool.map(lambda client: client.call("command", command), (a, b)))

        self.assertEqual(sum(reply["error"] is None for reply in replies), 1)
        self.eventually(lambda: ae["state"]["revision"] == be["state"]["revision"] == 1)
        self.assertEqual(ae["state"]["draw_offer"], be["state"]["draw_offer"])
        self.assertIsNotNone(a.call("command", command)["error"])
        self.assertEqual(ae["state"]["revision"], 1)

    def test_avatar_and_session_changes_reach_the_other_server(self):
        alice, bob, a, ae, b, be = self.invited_game()
        picture = BytesIO()
        Image.new("RGB", (32, 32), "blue").save(picture, format = "PNG")
        uploaded = alice.put(self.urls[1] + "/api/me/avatar", data = picture.getvalue())
        self.assertEqual(uploaded.status_code, 200)
        avatar = uploaded.json()["avatar"]
        self.eventually(lambda: be["state"]["players"]["white"]["avatar"] == avatar)

        replacement, restored = self.connect(alice, server = 1)
        self.eventually(lambda: ae["closed"] is not None)
        self.assertEqual(len(restored["state"]["connected"]), 2)
        result = replacement.call("command", {"type": "move", "move": "e2e4", "revision": 0})
        self.assertIsNone(result["error"])

        alice.delete(self.urls[0] + "/api/session")
        self.eventually(lambda: restored["closed"] is not None)
        self.eventually(lambda: be["state"]["connected"] == ["black"])

    def test_login_limit_is_shared(self):
        alice, _ = self.account("alice")
        for index in range(10):
            result = requests.post(self.urls[index % 2] + "/api/session", json = {
                "username": "alice", "password": "wrong-password",
            })
            self.assertEqual(result.status_code, 429 if index == 9 else 401)

    def test_expired_presence_cannot_be_matched(self):
        alice, _ = self.account("alice")
        bob, _   = self.account("bob")
        a, ae = self.connect(alice, server = 0)
        b, be = self.connect(bob, server = 1)
        self.enter(a, "match")
        self.servers[0].kill()
        self.servers[0].wait(timeout = 5)

        with psycopg.connect(self.database) as db:
            db.execute("UPDATE players SET seen_at = now() - interval '1 minute' WHERE waiting_at IS NOT NULL")
        self.enter(b, "match")
        self.assertIsNone(be["seat"])
        self.assertTrue(be["waiting"])

    def test_periodic_refresh_recovers_a_missed_notification(self):
        alice, _ = self.account("alice")
        client, events = self.connect(alice)
        # Delete directly, without NOTIFY, as if the listener missed the change.
        with psycopg.connect(self.database) as db:
            db.execute("DELETE FROM sessions")
        deadline = time.monotonic() + 12
        while events["closed"] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIsNotNone(events["closed"])

    def test_health_checks_the_database(self):
        with psycopg.connect(self.database) as db:
            db.execute("ALTER TABLE rooms RENAME TO unavailable")
        for url in self.urls:
            self.assertEqual(requests.get(url + "/api/health").status_code, 503)

    def test_only_inactive_rooms_expire(self):
        alice, bob, a, ae, b, be = self.invited_game()
        room_id = ae["seat"]["id"]
        with psycopg.connect(self.database) as db:
            db.execute("UPDATE rooms SET updated_at = now() - interval '2 days'")

        replacement, restored = self.connect(alice, server = 1)
        self.assertEqual(restored["seat"]["id"], room_id)
        self.eventually(lambda: ae["closed"] is not None)
        replacement.disconnect()
        b.disconnect()

        with psycopg.connect(self.database) as db:
            self.eventually(lambda: db.execute("SELECT 1 FROM players WHERE sid IS NOT NULL").fetchone() is None)
            db.execute("UPDATE rooms SET updated_at = now() - interval '2 days'")
        _, expired = self.connect(alice, room_id, server = 0)
        self.assertIsNone(expired["seat"])
        with psycopg.connect(self.database) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM rooms WHERE id = %s", (room_id,)).fetchone())

    def test_sqlite_import_preserves_accounts_and_refuses_overwrite(self):
        backup = Path(self.directory.name) / "old.sqlite"
        token  = "a" * 43
        key    = hashlib.sha256(token.encode()).hexdigest()
        hashed = PasswordHash.recommended().hash("correct-password")

        source = sqlite3.connect(backup)
        with source:
            source.execute("CREATE TABLE users (id, username, password_hash, avatar, avatar_version)")
            source.execute("CREATE TABLE sessions (token_hash, user_id, expires_at)")
            source.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", (27, "imported", hashed, b"avatar", "v1"))
            source.execute("INSERT INTO sessions VALUES (?, ?, ?)", (key, 27, int(time.time()) + 3600))
        source.close()
        original = backup.read_bytes()

        def run_import():
            return subprocess.run(
                [sys.executable, "-m", "app.import_sqlite", str(backup)],
                cwd = Path(__file__).resolve().parents[1],
                env = {**os.environ, "CHESS_DATABASE_URL": self.database},
                capture_output = True, text = True, timeout = 15,
            )

        imported = run_import()
        self.assertEqual(imported.returncode, 0, imported.stderr)
        headers = {"Authorization": "Bearer " + token}
        for url in self.urls:
            user = requests.get(url + "/api/me", headers = headers).json()
            self.assertEqual(user["id"], 27)
            self.assertEqual(requests.get(url + user["avatar"]).content, b"avatar")

        login = requests.post(self.url + "/api/session", json = {
            "username": "imported", "password": "correct-password",
        })
        self.assertEqual(login.status_code, 200)
        _, new_user = self.account("newuser")
        self.assertGreater(new_user["id"], 27)

        refused = run_import()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("already has accounts", refused.stderr)
        self.assertEqual(backup.read_bytes(), original)
        with psycopg.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 2)
