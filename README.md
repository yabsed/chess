# Small Chess

FastAPI와 Svelte 5로 만든 실시간 1:1 체스입니다. **한 수가 클릭에서 서버 검증을 거쳐 두 화면에 나타나기까지** 쉽게 따라 읽을 수 있도록 구성했습니다.

## 실행

Python 3.11 이상, Node.js 22.12 이상, [uv](https://docs.astral.sh/uv/getting-started/installation/)가 필요합니다. 프로젝트 루트에서:

```sh
make install
```

터미널 두 개를 열어 각각 실행하세요.

```sh
make backend
```

```sh
make frontend
```

- 화면: <http://127.0.0.1:5174>
- HTTP API 문서: <http://127.0.0.1:8000/docs>
- 상태 확인: <http://127.0.0.1:8000/api/health>

프런트 포트는 `make frontend PORT=5175`처럼 바꿀 수 있습니다.

`make` 없이도 실행할 수 있습니다.

```sh
# 터미널 1
cd apps/backend
uv sync --locked
uv run uvicorn app.main:app --reload --ws-max-size 4096
```

```sh
# 터미널 2, 프로젝트 루트에서 시작
cd apps/frontend
npm ci
npm run dev -- --port 5174
```

첫 화면에서 **New game**을 누르면 백이 됩니다. 초대 링크를 다른 브라우저나 새 탭의 주소창에 붙여 넣고 **Join as Black**을 누르세요. 같은 컴퓨터에서는 일반 창과 시크릿 창으로 테스트하면 편합니다. 두 플레이어가 접속하면 시작합니다.

같은 Wi-Fi의 다른 기기로 접속하려면 프런트를 `npm run dev -- --host 0.0.0.0 --port 5174`로 실행하고 `http://내-PC의-IP:5174`로 여세요. 그 주소에서 만든 초대 링크를 공유하면 됩니다. 백엔드는 Vite가 대신 연결하므로 로컬 주소로 실행해도 됩니다.

## Docker로 백엔드 실행

Docker가 실행 중이면 프로젝트 루트에서 다음 명령으로 백엔드를 빌드하고 실행합니다.

```sh
make backend-docker
```

로컬 Python과 uv 설치 없이 백엔드를 실행할 수 있습니다. 프런트는 다른 터미널에서 `npm ci --prefix apps/frontend` 후 `make frontend`로 실행하세요. 프런트가 기존과 같은 `http://127.0.0.1:8000`으로 API와 WebSocket을 연결합니다. 이 이미지는 백엔드만 포함합니다.

`make backend`가 실행 중이라면 먼저 `Ctrl+C`로 종료하세요. 두 실행 방법 모두 호스트의 8000번 포트를 사용합니다. Docker로 실행한 백엔드도 `Ctrl+C`로 종료하면 컨테이너가 제거되고, 이미지는 다음 실행에 재사용됩니다.

Makefile 안의 명령은 다음 두 줄입니다.

```sh
docker build -t chess-backend apps/backend
docker run --rm --name chess-backend -p 127.0.0.1:8000:8000 chess-backend
```

`Dockerfile`은 Python 3.13 환경에 `uv.lock`의 실행 의존성을 설치한 뒤 `app/`을 복사합니다. 의존성 설치를 먼저 두어 소스만 바뀌면 설치 단계를 캐시에서 재사용합니다. `.dockerignore`는 로컬 가상환경·캐시·테스트·환경 파일을 빌드 컨텍스트에서 제외합니다. [uv의 Docker 안내](https://docs.astral.sh/uv/guides/integration/docker/)를 바탕으로 구성했습니다.

컨테이너는 일반 사용자 권한으로 Uvicorn 한 프로세스를 실행합니다. `--host 0.0.0.0`은 컨테이너 안에서 요청을 받고, `-p 127.0.0.1:8000:8000`은 호스트의 로컬 포트와 연결합니다. 소스를 수정하면 종료 후 `make backend-docker`를 다시 실행해 이미지를 갱신하세요. 대국은 메모리에 저장되므로 컨테이너를 재시작하면 초기화됩니다.

## 구조

```text
chess/
├── apps/
│   ├── backend/
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py          # HTTP 입장, WebSocket 인증과 명령 수신
│   │   │   ├── room.py          # 참가·연결·명령 처리와 잠금, 상태 전송
│   │   │   └── game.py          # 체스 상태, 수 검증, 종료 처리
│   │   ├── tests/
│   │   │   ├── test_api.py      # 실제 TestClient 두 개의 통신
│   │   │   ├── test_room.py     # 동시 요청, 전송 실패, 연결 정리
│   │   │   └── test_game.py     # 특수 이동과 승패·무승부 규칙
│   │   ├── .dockerignore
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── uv.lock
│   └── frontend/
│       ├── src/
│       │   ├── lib/
│       │   │   ├── GameView.svelte    # 차례, 접속 상태, 대국 버튼
│       │   │   ├── Board.svelte       # 기물 선택, 이동 표시, 승급
│       │   │   ├── MoveHistory.svelte # 백·흑의 수를 짝지어 표시
│       │   │   ├── api.ts             # 입장, 자동 재접속, 수신 데이터 해석
│       │   │   └── types.ts           # 통신 데이터와 화면 데이터 타입
│       │   ├── App.svelte      # 입장, 자리 복구, 서버 상태와 명령 연결
│       │   ├── app.css         # 공통 스타일
│       │   └── main.ts         # Svelte를 DOM에 연결
│       ├── index.html
│       ├── package.json
│       ├── package-lock.json
│       ├── tsconfig.json
│       └── vite.config.ts      # Svelte와 /api 프록시
├── .editorconfig
├── .gitignore
├── Makefile
└── README.md
```

백엔드는 **요청·방·체스 규칙**, 프런트는 **접속·대국 화면·체스판·기보**를 경계로 나눴습니다. 각 파일에서 한 가지 주제를 따라 읽을 수 있습니다. 체스 자체의 이동 규칙은 `python-chess` 패키지에 맡기고, 프런트는 서버가 내려준 합법적인 수를 표시합니다.

## 구현한 기능

- 방 생성, 링크 초대, 백·흑 자리 배정, 세 번째 플레이어 입장 차단
- 두 화면의 실시간 동기화, 차례와 합법적인 이동 검증
- 캐슬링, 앙파상, 퀸·룩·비숍·나이트 승급
- 체크, 체크메이트, 스테일메이트, 기물 부족에 따른 무승부
- 3회 반복·50수 규칙의 무승부 청구, 5회 반복·75수 규칙의 자동 종료
- 무승부 제안·수락·거절, 기권, 종료 후 새 대국
- SAN 기보, 마지막 수와 이동 가능한 칸 표시, 흑의 시점, 판 뒤집기
- 모바일 화면, 키보드로 기물 선택, 영어 화면·접근성 라벨·오류 안내
- 같은 탭의 새로고침 복구, 끊긴 연결의 자동 재접속, 상대 접속 상태

## 코드 읽는 순서

1. **`game.py`**: `Game.apply()`가 명령을 검증하고 상태를 바꿉니다. `snapshot()`은 브라우저에 보낼 일반 Python 딕셔너리를 만듭니다. 규칙상 거절할 명령은 `InvalidAction`으로 알립니다. FastAPI와 독립적이라 체스 규칙만 테스트할 수 있습니다.
2. **`room.py`**: `Room.join()`은 흑의 자리를 배정하고, `handle()`은 메시지 형식·revision·접속 조건을 검증한 뒤 수를 적용하고 전송합니다. 잠금은 이 메서드들 안에서 관리합니다. `connect()`는 `async with`에 들어갈 때 연결을 등록하고, 벗어날 때 정리합니다. `Player`는 토큰·소켓·전송을 맡고, `Room._broadcast()`는 전송 실패까지 반영한 상태를 두 참가자에게 알립니다.
3. **`main.py`**: `@app.post`는 방 생성과 참가를, `@app.websocket`은 인증과 메시지 수신을 담당합니다. 인증 후 받은 메시지는 `room.handle()`에 전달합니다. 여기서 방의 잠금을 잡거나 참가자 목록·대국 상태를 직접 바꾸지 않습니다.
4. **`api.ts` → `App.svelte`**: `createGame()`과 `joinGame(id)`로 입장 목적을 구분합니다. WebSocket으로 받은 `GameSnapshot`의 UCI 문자열은 `readGame()`에서 한 번 해석해 화면용 `Game`으로 바꿉니다. `onState`, `onStatus`, `onError` 콜백은 App의 `$state`를 갱신합니다. `onMount`는 저장한 자리를 복구하고, 반환한 정리 함수는 연결을 닫습니다. 재접속 간격은 0.5초부터 최대 5초이며 이전 명령은 재전송하지 않습니다.
5. **`GameView.svelte`**: `$props`로 받은 상태에서 `$derived`로 화면을 계산합니다. `isMyTurn`은 내 차례인지, `canSendCommand`는 명령을 보낼 수 있는지, `canMove`는 지금 수를 둘 수 있는지를 뜻합니다. 사용자 동작은 `onsend`로 App에 전달합니다. 통신과 저장소는 App에서 처리합니다.
6. **`Board.svelte` → `MoveHistory.svelte`**: 체스판은 `move.from`, `move.to`, `move.promotion`으로 이동을 읽습니다. 좌표·색·라벨과 기보의 백·흑 수를 먼저 계산하고 템플릿에서 표시합니다. 체스판의 선택은 `$state`, 이동 가능한 칸은 `$derived`입니다. `$effect`는 서버 상태가 바뀌면 이전 선택과 승급 창을 지웁니다. 각 화면의 CSS는 해당 컴포넌트의 `<style>`에, 공통 버튼·패널·글꼴은 `app.css`에 있습니다.

예를 들어 `e2`의 폰을 `e4`로 옮기는 흐름은 다음과 같습니다.

```text
Board.select("e4")
  → Board.onmove → GameView.onsend → App.send("move", "e2e4")
  → WebSocket으로 { type: "move", revision: 0, move: "e2e4" }
  → Room.handle() → 방의 lock 획득 → Pydantic·revision·접속 조건 검증
  → Game.apply() → 차례·python-chess 합법성 확인 → 상태 변경
  → Room._broadcast() → Player.send() → 두 플레이어에게 전체 상태 전송
  → api.readGame() → "e2e4"를 { uci, from, to, promotion }으로 해석
  → App의 game 갱신 → Svelte가 화면을 다시 그림
```

`asyncio.Lock`은 한 방의 명령 처리와 전송 순서를 보장합니다. `revision`은 이미 바뀐 판을 보고 보낸 요청을 거절합니다. 서버는 브라우저가 보낸 말의 위치나 승패를 신뢰하지 않습니다.

`InvalidAction`은 사용자에게 오류 메시지와 최신 상태를 보내는 경로입니다. 예상하지 못한 내부 예외는 이 경로에서 잡지 않습니다. 소켓 예외도 실제 송수신·종료 호출 주변에서만 처리하므로, 대국 로직의 버그가 연결 끊김으로 숨겨지지 않습니다.

코드를 바꿀 때도 이 기준을 유지합니다.

- 관련 정보와 그 정보를 바꾸는 동작을 함께 둡니다. `Room`을 사용하는 코드는 잠금·상태 변경·전송 순서를 직접 조립할 필요가 없습니다.
- 문자열의 의미는 경계에서 해석합니다. `Move`는 UCI 원문과 출발 칸·도착 칸·승급 기물을 함께 보관하므로 화면에서 다시 자르지 않습니다.
- 이름으로 조건을 구분합니다. `awaitingReply`는 서버 응답 대기, `isMyTurn`은 차례, `canMove`는 접속·응답 대기까지 반영한 이동 가능 여부입니다.
- 값이 없으면 먼저 처리합니다. 선택하지 않은 칸을 가짜 문자열로 표현하지 않습니다.
- 상태 변경은 순서대로 적습니다. 메시지 수신 → 검증 → 수 적용 → 전송을 그대로 따라갈 수 있습니다.
- 함수와 컴포넌트는 하나의 동작이나 화면 단위로 나눕니다. 짧은 표현식까지 함수로 감싸지는 않습니다.

소스의 배치에도 의미를 둡니다. import·대입문·필드는 같은 묶음 안에서 열을 맞추고, 주제가 바뀌는 곳에 빈 줄을 둡니다. 짧은 호출과 속성은 함께 읽히도록 붙이고, 비교할 상태 조건은 줄을 나눠 정렬합니다. Svelte 태그는 식별·상태·접근성·동작을 구분하며, CSS의 짧은 상태별 차이는 한 행씩 놓습니다. 파일 전체를 가장 긴 이름에 맞추지는 않습니다.

들여쓰기는 Python 4칸, 프런트 2칸이며 열 정렬에는 공백을 씁니다. Makefile 명령은 탭입니다. `.editorconfig`가 기본 공백 규칙을 지정합니다. 자동 포매팅을 사용할 때도 이 문단과 열 정렬이 유지되는지 diff를 확인하세요.

개발 중에는 Vite가 `/api`의 HTTP와 WebSocket을 FastAPI로 전달합니다. 브라우저 입장에서는 같은 출처이므로 별도의 CORS 설정과 API 주소 환경변수가 필요 없습니다. HTTP API 문서에는 WebSocket 메시지 규약이 나타나지 않으므로 아래를 참고하세요.

## 통신 규약

| 요청 | 결과 |
| --- | --- |
| `GET /api/health` | 서버 상태 |
| `POST /api/games` | `{ id, token, color: "white" }`, HTTP 201 |
| `POST /api/games/{id}/join` | `{ id, token, color: "black" }` |
| `WS /api/games/{id}/ws` | 인증 후 명령 수신·상태 전송 |

WebSocket 연결 후 5초 안에 첫 메시지로 `{"token":"입장할 때 받은 토큰"}`을 보냅니다. 토큰은 초대 링크와 서버의 URL 로그에 들어가지 않습니다. 인증에 성공하면 곧바로 전체 상태를 받습니다.

```json
{ "type": "move", "revision": 0, "move": "e2e4" }
```

`move`는 UCI 표기입니다. 승급은 `a7a8q`처럼 마지막 글자로 기물을 지정합니다. 나머지 명령은 `move` 필드 없이 `type`과 `revision`만 보냅니다.

| type | 동작 |
| --- | --- |
| `move` | 내 차례에 합법적인 수 두기 |
| `resign` | 기권; 상대가 연결을 끊어도 가능 |
| `offer_draw` | 무승부 제안 |
| `accept_draw` / `decline_draw` | 상대 제안 수락 / 거절 |
| `claim_draw` | 내 차례에 반복·50수 규칙에 따른 무승부 청구 |

서버 응답은 두 가지입니다.

```text
{ type: "state", you: "white" | "black", game: { ... } }
{ type: "error", message: "It is not your turn." }
```

`game`의 통신 타입은 `src/lib/types.ts`의 `GameSnapshot`입니다. 서버는 UCI 문자열을 보내고, 프런트의 `Game`은 이를 `Move` 객체로 해석한 타입입니다. 명령 형식이나 대국 조건 때문에 거절한 요청에는 오류와 최신 상태를 보내며 연결을 유지합니다. 바이너리 메시지는 연결을 종료합니다. 무승부 청구 가능 여부는 `python-chess`의 `can_claim_draw()`를 사용하므로 다음 합법적인 수로 청구 조건을 만족하는 경우도 포함합니다.

## 테스트와 빌드

```sh
make check  # 백엔드 테스트 + Svelte/TypeScript 검사
make build # frontend/dist 생성
make serve # 빌드된 화면과 API를 FastAPI 하나로 제공
```

빌드 후에는 <http://127.0.0.1:8000>에서 접속합니다. `dist`는 백엔드 시작 시 확인하므로 이미 실행 중인 서버는 빌드 후 재시작하세요. `npm run preview`는 정적 빌드 미리보기만 제공하므로 실제 대국 확인에는 `make serve`를 사용합니다.

테스트는 특수 규칙, 잘못된 수, 승패, 인증, 동기화, 잘못된 메시지, 오래된 revision, 연결 교체, 재접속, 방 만료와 용량 제한을 검증합니다. 동시에 들어온 참가 요청·명령의 처리 순서, 전송 실패 시 상대에게 알리는 접속 상태, 내부 예외의 전파와 연결 정리도 확인합니다.

## 저장과 실행 범위

대국 상태는 서버 메모리에 저장됩니다. **서버가 재시작되면 대국이 초기화됩니다.** 개발 중 `--reload`가 작동해도 마찬가지입니다. 한 프로세스로 실행해야 하며 `--workers`를 늘리면 프로세스별 방 목록이 달라집니다. 현재 구조에서 영구 저장이나 여러 서버가 필요해지면 이 방 저장 부분을 데이터베이스와 공유 메시지 처리로 확장하면 됩니다.

브라우저의 `sessionStorage`에는 방별 자리 토큰을 보관합니다. 같은 탭을 새로고침하면 복구되지만, 탭을 닫거나 저장소를 지우면 복구가 보장되지 않습니다. 같은 토큰으로 다른 탭에서 연결하면 이전 연결을 종료합니다. 초대 링크만으로 기존 플레이어의 자리를 가져갈 수는 없습니다.

두 플레이어가 연결되어 있어야 수를 둘 수 있고, 상대가 끊기면 재접속을 기다립니다. 시계나 연결 끊김에 따른 자동 패배는 없습니다. 빈 방은 마지막 활동으로부터 24시간이 지나면 다음 요청 때 정리하며, 전체 방은 최대 500개입니다.

학습할 때 함께 볼 공식 문서: [FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/), [Svelte의 상태](https://svelte.dev/docs/svelte/$state), [파생 상태](https://svelte.dev/docs/svelte/$derived), [python-chess 규칙 API](https://python-chess.readthedocs.io/en/latest/core.html).
