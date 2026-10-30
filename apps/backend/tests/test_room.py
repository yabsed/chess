import asyncio
import json

from unittest.mock import AsyncMock

import pytest

from fastapi import WebSocket

from app.room import Room, RoomFull


@pytest.mark.parametrize("disconnected_color", ["white", "black"])
def test_broadcast_tells_remaining_player_about_send_failure(disconnected_color):
    async def scenario():
        room = Room()
        await room.join()
        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)
        sockets = {"white": white, "black": black}

        async with room.connect("white", white), room.connect("black", black):
            sockets[disconnected_color].send_json.side_effect = OSError("connection lost")
            await room.handle(
                "white", white,
                json.dumps({"type": "move", "revision": 0, "move": "e2e4"}),
            )

            remaining_color = "black" if disconnected_color == "white" else "white"
            remaining    = sockets[remaining_color]
            latest_state = remaining.send_json.call_args.args[0]["game"]
            assert latest_state["connected"] == [remaining_color]
            assert latest_state["joined"]    == ["white", "black"]
            assert latest_state["moves"]     == ["e4"]
            assert room.connected           == [remaining_color]

    asyncio.run(scenario())


def test_connection_scope_cleans_up_after_processing_error():
    async def scenario():
        room = Room()
        await room.join()
        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)

        async with room.connect("black", black):
            with pytest.raises(ValueError, match = "processing failed"):
                async with room.connect("white", white):
                    raise ValueError("processing failed")

            assert room.connected == ["black"]
            latest_state = black.send_json.call_args.args[0]["game"]
            assert latest_state["connected"] == ["black"]

            # After cleanup, the same seat can reconnect and process the next command.
            async with room.connect("white", white):
                await room.handle(
                    "white", white,
                    json.dumps({"type": "move", "revision": 0, "move": "e2e4"}),
                )
                latest_state = black.send_json.call_args.args[0]["game"]
                assert latest_state["moves"] == ["e4"]

    asyncio.run(scenario())


def test_concurrent_joins_reserve_only_one_black_seat():
    async def scenario():
        room = Room()
        results = await asyncio.gather(room.join(), room.join(), return_exceptions = True)
        tokens     = [result for result in results if isinstance(result, str)]
        rejections = [result for result in results if isinstance(result, RoomFull)]

        assert len(tokens) == 1
        assert len(rejections) == 1
        assert room.authenticate(tokens[0]) == "black"

    asyncio.run(scenario())


def test_simultaneous_commands_apply_one_move_and_resync_the_other():
    async def scenario():
        room = Room()
        await room.join()
        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)

        async with room.connect("white", white), room.connect("black", black):
            white.send_json.reset_mock()
            black.send_json.reset_mock()
            await asyncio.gather(
                room.handle("white", white, '{"type":"move","revision":0,"move":"e2e4"}'),
                room.handle("white", white, '{"type":"move","revision":0,"move":"d2d4"}'),
            )

            messages = [call.args[0] for call in white.send_json.call_args_list]
            assert [message["type"] for message in messages] == ["state", "error", "state"]
            assert messages[1]["message"] == "The game has changed. Please try again."
            assert messages[0]["game"] == messages[2]["game"]
            assert messages[2]["game"]["revision"] == 1
            assert len(messages[2]["game"]["moves"]) == 1
            assert black.send_json.call_args.args[0]["game"] == messages[2]["game"]

    asyncio.run(scenario())


def test_replaced_socket_cannot_issue_commands():
    async def scenario():
        room = Room()
        await room.join()
        first       = AsyncMock(spec = WebSocket)
        replacement = AsyncMock(spec = WebSocket)
        black       = AsyncMock(spec = WebSocket)

        async with room.connect("black", black), room.connect("white", first):
            async with room.connect("white", replacement):
                accepted = await room.handle(
                    "white", first, '{"type":"move","revision":0,"move":"e2e4"}'
                )
                assert not accepted
                assert black.send_json.call_args.args[0]["game"]["revision"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("message", [
    "not json",
    "[]",
    '{"isChat":true,"content":"hello"}',
    *[
        json.dumps({"type": "move", "revision": 0, "isChat": True, **fields})
        for fields in [
            {"content": "hello", "amount": -1},
            {"content": ""},
            {"content": "x" * 141},
            {"content": 123},
        ]
    ],
])
def test_rejected_messages_report_errors_without_mutating_chat_or_closing_connection(message):
    async def scenario():
        room = Room()
        await room.join()
        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)

        async with room.connect("white", white), room.connect("black", black):
            white.send_json.reset_mock()
            black.send_json.reset_mock()

            assert await room.handle("white", white, message)

            responses = [call.args[0] for call in white.send_json.call_args_list]
            assert [response["type"] for response in responses] == ["error", "state"]
            assert responses[0]["message"]
            assert responses[1]["logs"] == []
            assert responses[1]["game"]["revision"] == 0
            assert black.send_json.call_args.args[0]["logs"] == []
            assert room._chatRoom.logs == []

            # A rejected message must leave the same connection usable for chess.
            assert await room.handle(
                "white", white, '{"type":"move","revision":0,"move":"e2e4"}'
            )
            assert black.send_json.call_args.args[0]["game"]["moves"] == ["e4"]

    asyncio.run(scenario())


@pytest.mark.parametrize("color", ["white", "black"])
def test_chat_reaches_both_players_without_changing_game_state(color):
    async def scenario():
        room = Room()
        await room.join()
        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)
        sockets = {"white": white, "black": black}
        content = "x" * 140
        expected_logs = [{"sender": color, "content": content}]

        async with room.connect("white", white), room.connect("black", black):
            before = white.send_json.call_args.args[0]["game"]
            white.send_json.reset_mock()
            black.send_json.reset_mock()

            assert await room.handle(color, sockets[color], json.dumps({
                "type": "move", "revision": 0, "isChat": True,
                "content": content, "amount": 5,
            }))

            for recipient, socket in sockets.items():
                responses = [call.args[0] for call in socket.send_json.call_args_list]
                expected_types = ["chat_history", "state"] if recipient == color else ["state"]
                assert [response["type"] for response in responses] == expected_types
                assert all(response["logs"] == expected_logs for response in responses)
                assert responses[-1]["game"] == before
            assert len(room._chatRoom.logs) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("content_fields", [{}, {"content": None}])
@pytest.mark.parametrize("amount", [0, 1, 20])
def test_chat_history_requests_do_not_append_messages(content_fields, amount):
    async def scenario():
        room = Room()
        socket = AsyncMock(spec = WebSocket)
        command = {"type": "move", "revision": 0, "isChat": True}

        async with room.connect("white", socket):
            for content in ["first", "second"]:
                await room.handle("white", socket, json.dumps({**command, "content": content}))
            socket.send_json.reset_mock()

            assert await room.handle("white", socket, json.dumps({
                **command, "amount": amount, **content_fields,
            }))

            responses = [call.args[0] for call in socket.send_json.call_args_list]
            assert [response["type"] for response in responses] == ["chat_history", "state"]
            assert responses[0]["logs"] == [
                {"sender": "white", "content": "second"},
                {"sender": "white", "content": "first"},
            ][:amount]
            assert len(room._chatRoom.logs) == 2
            assert responses[1]["game"]["revision"] == 0

    asyncio.run(scenario())
