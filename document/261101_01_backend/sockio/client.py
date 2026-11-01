"""클라이언트: 터미널 하나가 참가자 한 명이다.

이 폴더에서 터미널 세 개를 열고 각각 실행한다.
    ../../../apps/backend/.venv/bin/python client.py

이름 / 방을 alice / room-a, bob / room-a, charlie / room-b로 입력한다.
같은 방끼리 메시지를 받는다. /quit 또는 Ctrl+C로 종료한다.
"""

import socketio


url = "http://127.0.0.1:8765"

name = input("이름: ")
room = input("방:   ")

sio = socketio.Client(reconnection = False)


@sio.event
def notice(data):
    print(f"\n[{data['room']}] {data['name']}: {data['text']}")


try:
    sio.connect(url, transports = ["websocket"])

    reply = sio.call("join", room)  # 입장이 끝나면 서버의 반환값을 받는다.
    print(reply)

    while True:
        text = input("> ")
        if text == "/quit":
            break

        data = {
            "name" : name,
            "room" : room,
            "text" : text,
        }
        sio.emit("chat", data)

except (EOFError, KeyboardInterrupt):
    print()

finally:
    sio.disconnect()
