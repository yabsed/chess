"""서버: 클라이언트를 방에 넣고, 같은 방에 메시지를 보낸다.

프로젝트 루트에서 설치하고 실행한다.
    uv sync --directory apps/backend --locked
    source apps/backend/.venv/bin/activate
    cd document/261101_01_backend/sockio.py
    python -m uvicorn server:app --port 8765

서버를 켜 둔 채 다른 터미널에서 client.py를 실행한다.
"""

import socketio


sio = socketio.AsyncServer(async_mode = "asgi")
app = socketio.ASGIApp(sio, on_shutdown = sio.shutdown)


# @sio.event는 함수 이름을 이벤트 이름으로 등록한다.
@sio.event
async def join(sid, room):
    await sio.enter_room(sid, room)
    print(f"[입장] {sid} → {room}")
    return f"{room} 입장 완료"  # 클라이언트의 call()이 받는 ACK


@sio.event
async def chat(sid, data):
    room = data["room"]

    # to = room : 같은 방
    # to = sid  : 보낸 사람
    # to = None : 전체 (to를 생략해도 같다)
    await sio.emit("notice", data, to = room)
