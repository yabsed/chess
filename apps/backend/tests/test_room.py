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


@pytest.mark.parametrize("message", [
    '{"type":"move","revision":0,"move":"e2e4"}',
    '{"type":"chat","content":"hello"}',
    '{"type":"chat_history"}',
])
def test_replaced_socket_cannot_issue_commands(message):
    async def scenario():
        room = Room()
        await room.join()
        first       = AsyncMock(spec = WebSocket)
        replacement = AsyncMock(spec = WebSocket)
        black       = AsyncMock(spec = WebSocket)

        async with room.connect("black", black), room.connect("white", first):
            async with room.connect("white", replacement):
                replacement.send_json.reset_mock()
                black.send_json.reset_mock()

                accepted = await room.handle("white", first, message)
                assert not accepted
                replacement.send_json.assert_not_called()
                black.send_json.assert_not_called()
                assert room._game.revision == 0
                assert room._chat.recent(20) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("message", [
    "not json",
    "[]",
    '{"isChat":true,"content":"hello"}',
    '{"type":"unknown"}',
    '{"type":"move","revision":0,"isChat":true,"content":"hello"}',
    '{"type":"chat"}',
    '{"type":"chat","content":""}',
    '{"type":"chat","content":null}',
    '{"type":"chat","content":123}',
    '{"type":"chat","content":"hello","sender":"black"}',
    '{"type":"chat","content":"hello","revision":0}',
    json.dumps({"type": "chat", "content": "x" * 141}),
    '{"type":"chat_history","limit":-1}',
    '{"type":"chat_history","limit":true}',
    '{"type":"chat_history","limit":"20"}',
    '{"type":"chat_history","content":"hello"}',
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
            assert responses[1]["game"]["revision"] == 0
            assert room._chat.recent(20) == []

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
        expected = {"type": "chat", "sender": color, "content": content}

        async with room.connect("white", white), room.connect("black", black):
            before = room._game.snapshot()
            white.send_json.reset_mock()
            black.send_json.reset_mock()

            assert await room.handle(color, sockets[color], json.dumps({
                "type": "chat", "content": content,
            }))

            white.send_json.assert_awaited_once_with(expected)
            black.send_json.assert_awaited_once_with(expected)
            assert room._game.snapshot() == before
            assert room._chat.recent(20) == [{"sender": color, "content": content}]

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, 1, 100, None])
def test_chat_history_replies_only_to_requester_without_appending_messages(limit):
    async def scenario():
        room = Room()
        await room.join()
        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)
        logs = [{"sender": "white", "content": str(index)} for index in range(25)]
        request = {"type": "chat_history"}
        if limit is not None:
            request["limit"] = limit

        async with room.connect("white", white), room.connect("black", black):
            for chat in logs:
                await room.handle("white", white, json.dumps({"type": "chat", "content": chat["content"]}))
            white.send_json.reset_mock()
            black.send_json.reset_mock()

            assert await room.handle("black", black, json.dumps(request))

            expected_limit = 20 if limit is None else limit
            black.send_json.assert_awaited_once_with({
                "type": "chat_history", "logs": logs[::-1][:expected_limit],
            })
            white.send_json.assert_not_called()
            assert room._chat.recent(100) == logs[::-1]
            assert room._game.revision == 0

    asyncio.run(scenario())


def test_simultaneous_chats_reach_both_players_in_the_same_order():
    async def scenario():
        room = Room()
        await room.join()
        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)

        async with room.connect("white", white), room.connect("black", black):
            white.send_json.reset_mock()
            black.send_json.reset_mock()

            await asyncio.gather(
                room.handle("white", white, '{"type":"chat","content":"hello"}'),
                room.handle("black", black, '{"type":"chat","content":"hi"}'),
            )

            received = [call.args[0] for call in white.send_json.call_args_list]
            assert received == [call.args[0] for call in black.send_json.call_args_list]
            assert len(received) == 2
            assert {message["sender"] for message in received} == {"white", "black"}
            assert all(message["type"] == "chat" for message in received)
            assert room._chat.recent(20) == [
                {"sender": message["sender"], "content": message["content"]}
                for message in reversed(received)
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("command, disconnected_color", [
    ({"type": "chat", "content": "hello"}, "white"),
    ({"type": "chat", "content": "hello"}, "black"),
    ({"type": "chat_history"}, "white"),
])
def test_chat_send_failure_updates_the_remaining_players_connection_state(command, disconnected_color):
    async def scenario():
        room = Room()
        await room.join()
        white = AsyncMock(spec = WebSocket)
        black = AsyncMock(spec = WebSocket)
        sockets = {"white": white, "black": black}

        async with room.connect("white", white), room.connect("black", black):
            white.send_json.reset_mock()
            black.send_json.reset_mock()
            sockets[disconnected_color].send_json.side_effect = OSError("connection lost")

            still_connected = await room.handle("white", white, json.dumps(command))

            remaining_color = "black" if disconnected_color == "white" else "white"
            remaining = sockets[remaining_color]
            latest_state = remaining.send_json.call_args.args[0]["game"]
            assert latest_state["connected"] == [remaining_color]
            assert latest_state["revision"] == 0
            assert still_connected == (disconnected_color != "white")

    asyncio.run(scenario())
