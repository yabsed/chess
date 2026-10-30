import pytest

from app.chat import ChatLog


def test_history_walks_back_without_repeating_messages_added_between_pages():
    log = ChatLog()
    for index in range(1, 101):
        chat = log.append("white", str(index))
        assert chat.id == index

    page = log.history(20)
    assert [chat["id"] for chat in page["logs"]] == list(range(81, 101))
    assert page["before"] is None
    assert page["has_more"]

    collected = page["logs"]
    while page["has_more"]:
        log.append("black", "new message")
        before = page["logs"][0]["id"]
        page = log.history(20, before = before)

        assert page["before"] == before
        assert len(page["logs"]) == 20
        assert all(chat["id"] < before for chat in page["logs"])
        collected = page["logs"] + collected

    assert [chat["id"] for chat in collected] == list(range(1, 101))
    assert [chat["content"] for chat in collected] == [str(index) for index in range(1, 101)]
    assert log.history(1)["logs"][0]["id"] == 104


@pytest.mark.parametrize("count, limit, before, expected_ids, has_more", [
    (0,  20, None, [],                 False),
    (20, 20, None, list(range(1, 21)),  False),
    (21, 20, None, list(range(2, 22)),  True),
    (25, 20, 21,   list(range(1, 21)),  False),
    (25, 20, 22,   list(range(2, 22)),  True),
    (25, 20, 1,    [],                 False),
    (25, 20, 1000, list(range(6, 26)),  True),
    (25, 0,  None, [],                 True),
    (25, 0,  1,    [],                 False),
])
def test_history_page_boundaries(count, limit, before, expected_ids, has_more):
    log = ChatLog()
    for index in range(count):
        log.append("white", str(index))

    page = log.history(limit, before = before)
    assert [chat["id"] for chat in page["logs"]] == expected_ids
    assert page["before"] == before
    assert page["has_more"] == has_more


def test_message_ids_are_local_to_each_log():
    first  = ChatLog()
    second = ChatLog()

    assert first.append("white", "same text").id == 1
    assert first.append("white", "same text").id == 2
    assert second.append("black", "same text").id == 1
