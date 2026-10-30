# Small Chess

FastAPI와 Svelte 5로 만든 실시간 1:1 체스입니다. **한 수가 클릭에서 서버 검증을 거쳐 두 화면에 나타나기까지** 쉽게 따라 읽을 수 있도록 구성했습니다.

## 실행

Python 3.11 이상, Node.js 22.12 이상, [uv](https://docs.astral.sh/uv/getting-started/installation/)가 필요합니다. 프로젝트 루트에서:

```sh
make install
```

터미널 두 개를 열어 각각 실행하세요.

```sh
# 터미널 1
make backend
```

```sh
# 터미널 2
make frontend
```

- 화면: <http://127.0.0.1:5174>
- HTTP API 문서: <http://127.0.0.1:8000/docs>
- 상태 확인: <http://127.0.0.1:8000/api/health>

프런트 포트는 `make frontend PORT=5175`처럼 바꿀 수 있습니다. Vite가 `/api`의 HTTP와 WebSocket을 백엔드로 전달합니다.

첫 화면에서 **New game**을 누르면 백이 됩니다. 초대 링크를 다른 브라우저나 새 탭의 주소창에 붙여 넣고 **Join as Black**을 누르세요. 같은 컴퓨터에서는 일반 창과 시크릿 창으로 접속하면 편합니다. 두 플레이어가 접속하면 시작합니다.

같은 Wi-Fi의 다른 기기로 접속하려면 `apps/frontend`에서 `npm run dev -- --host 0.0.0.0`으로 실행하고 `http://내-PC의-IP:5174`로 여세요. 그 주소에서 만든 초대 링크를 공유하면 됩니다.

## Docker로 백엔드 실행

로컬 Python과 uv 대신 Docker를 사용할 수 있습니다. 프로젝트 루트에서:

```sh
make backend-docker
```

이미지는 백엔드만 포함합니다. 프런트는 `npm ci --prefix apps/frontend`로 설치하고 `make frontend`로 실행하세요. 기존 백엔드가 8000번 포트를 사용 중이면 먼저 종료하세요. `Ctrl+C`로 컨테이너를 종료하면 컨테이너는 제거되고 이미지는 남습니다.

소스를 수정하면 다시 빌드해 실행하세요. 컨테이너는 `uv.lock`의 실행 의존성을 설치하고 일반 사용자 권한으로 Uvicorn 한 프로세스를 실행합니다.

## 구조

```text
chess/
├── apps/
│   ├── backend/
│   │   ├── app/
│   │   │   ├── main.py          # 서버 구성, HTTP·Socket.IO 요청 수신
│   │   │   ├── protocol.py      # HTTP 응답과 Socket.IO 입력 모델
│   │   │   ├── room.py          # 참가·연결·명령 처리와 잠금, 상태 전송
│   │   │   └── game.py          # 체스 상태, 수 검증, 종료 처리
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── uv.lock
│   └── frontend/
│       ├── src/
│       │   ├── main.ts                     # Svelte를 DOM에 연결
│       │   ├── App.svelte                  # Room의 수명과 입장 화면
│       │   ├── app.css                     # Simple.css 위의 색상과 공통 배치
│       │   ├── http.ts                     # Ky 공통 설정과 HTTP 오류 메시지
│       │   └── game/
│       │       ├── GameView.svelte         # 차례, 접속 상태, 대국 버튼
│       │       ├── MoveHistory.svelte      # 백·흑의 수를 짝지어 표시
│       │       ├── room.svelte.ts          # 입장, 연결, 행동 조건과 명령
│       │       ├── api.ts                  # 방 생성·참가 HTTP 요청
│       │       ├── protocol.ts             # 자리·대국 상태·명령·이벤트의 타입
│       │       └── board/
│       │           ├── Board.svelte        # 판 방향, 승급 선택, 반응형 갱신 시점
│       │           ├── chessboard.ts       # cm-chessboard 생성, 수 해석, 입력과 표시
│       │           └── cm-chessboard.d.ts  # 사용하는 cm-chessboard API의 타입 선언
│       ├── public/third-party-notices.txt
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

프런트의 대국 화면·상태·API는 `src/game/`에 함께 둡니다. 보드 컴포넌트와 라이브러리 연결 코드·타입 선언은 `game/board/`에 모으고, 대국 화면에서는 `Board.svelte`를 사용합니다. 앱 전체의 HTTP 설정은 `src/http.ts`에 둡니다.

백엔드는 **요청·방·체스 규칙**, 프런트는 **접속·대국 화면·체스판·기보**를 경계로 나눴습니다. 각 파일에서 한 가지 주제를 따라 읽을 수 있습니다. 체스 규칙은 `python-chess`, 판 표시와 입력은 `cm-chessboard`, 기본 HTML 스타일은 `simpledotcss`, 실시간 연결과 메시지 전달은 `socket.io-client`와 `python-socketio`에 맡깁니다. 프런트는 서버가 보내는 FEN으로 판을 갱신하고 합법적인 수를 표시합니다. 승급 선택은 브라우저의 `<dialog>`를 사용합니다.

## 코드 읽는 순서

한 수는 다음 순서로 처리됩니다.

```text
Board.selectMove() → 프런트 Room.send("move", "e2e4")
  → command { type: "move", revision: 0, move: "e2e4" }
  → 서버 Room.handle(): 잠금 → 형식·revision·접속 조건 확인 → Game.apply()
  → state: 두 화면의 판과 기보 갱신
  → acknowledgement: 요청한 화면의 명령 대기 해제와 오류 표시
```

`state`는 상대의 접속 변화에도 도착합니다. 명령 완료는 해당 요청의 응답으로 확인하며, 전송·응답·대기 해제는 프런트 `send()`에서 함께 읽습니다. 서버는 명령을 거절해도 최신 상태를 먼저 보내고 거절 이유를 응답합니다. 예상하지 못한 내부 예외는 연결을 정리한 뒤 서버 로그로 올립니다.

방의 잠금은 명령 처리와 상태 전송 순서를 지킵니다. 같은 클라이언트의 이벤트는 순서대로 처리하고, 다른 클라이언트의 요청은 방의 잠금으로 직렬화합니다. 이전 탭을 종료할 때는 잠금을 먼저 풀어 종료 핸들러가 같은 잠금을 기다리며 멈추지 않게 합니다.

`main.py`에서 FastAPI와 Socket.IO 서버를 구성하고 `Room`에 전송할 서버를 전달합니다. `Room`은 게임·잠금·백의 자리를 직접 초기화합니다. 방 조회는 `Room` 또는 `None`을 반환하고, HTTP와 Socket.IO의 입구에서 각각 오류 응답과 연결 거절로 처리합니다.

코드를 바꿀 때도 이 기준을 유지합니다.

- 데이터와 그 데이터를 바꾸는 동작을 가까이 둡니다. 검증·변경·전송을 실행 순서대로 읽을 수 있게 합니다.
- 프런트의 통신 타입은 `protocol.ts`, 연결 상태는 `room.svelte.ts`, UCI 해석과 `Move`는 `board/chessboard.ts`에 둡니다.
- App은 확인된 자리를 대국 화면에 전달합니다. 새 대국은 화면 상태를 초기화하고, 연결 종료는 마지막 보드와 결과를 남깁니다.
- `Room.allowed`는 화면과 전송 코드가 함께 읽는 행동 조건입니다. 도착한 명령의 차례·합법성은 서버가 다시 검증합니다.
- 판은 서버의 FEN으로 갱신합니다. 새 상태·방향·입력 가능 여부가 바뀌면 진행 중인 선택을 취소합니다.
- 함수와 파일은 독립적으로 이해할 주제로 나눕니다. 짧은 표현식은 사용하는 곳에서 바로 읽게 둡니다.

소스의 배치에도 의미를 둡니다. 함께 비교할 필드·대입문은 작은 묶음 안에서 열을 맞추고, 준비·검증·변경·전송 사이에 빈 줄을 둡니다. 짧은 호출과 속성은 붙여 읽고, 긴 표현은 의미가 나뉘는 곳에서 줄을 나눕니다. 주석에는 코드만으로 드러나지 않는 이유와 순서 제약을 적습니다.

들여쓰기는 Python 4칸, 프런트 2칸이며 열 정렬에는 공백을 씁니다. `.editorconfig`가 기본 공백 규칙을 지정합니다. 자동 포매팅 후에도 문단과 열 정렬이 유지되는지 diff를 확인하세요.

## 통신 규약

HTTP는 방 생성과 자리 배정을 맡습니다. 요청 경로·응답 필드·오류 코드는 실행 중인 서버의 [Swagger UI](http://127.0.0.1:8000/docs)에서 확인하고 호출할 수 있습니다. 같은 정의를 [OpenAPI JSON](http://127.0.0.1:8000/openapi.json)으로도 제공합니다.

HTTP 함수는 서버 `protocol.py`의 모델을 반환형으로 선언하고 해당 객체를 반환합니다. FastAPI가 이 반환형으로 응답을 검증하고 명세를 만듭니다. 오류 응답도 명세에 포함합니다. 프런트는 `protocol.ts`에 필요한 타입을 직접 선언합니다. HTTP 응답이나 Socket.IO 규약을 바꾸면 서버 코드와 프런트 타입을 함께 수정합니다.

Socket.IO는 `/api/socket.io`에서 WebSocket 전송을 사용합니다. 연결 인증과 대국 이벤트는 OpenAPI에 포함되지 않으므로 아래에 따로 설명합니다.

Socket.IO 연결의 `auth`에 `{ id, token }`을 전달합니다. 토큰은 초대 링크와 서버의 URL 로그에 들어가지 않습니다. 서버는 인증을 마친 뒤 최초 `state`를 보내고 연결을 승인합니다. 프런트는 이 상태를 받아야 대국 입력을 활성화합니다. 인증 실패나 만료된 방은 `connect_error`로 알려주며 자동 재접속을 중단합니다.

명령은 `command` 이벤트로 보내고 Socket.IO acknowledgement로 해당 요청의 응답을 받습니다.

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

명령 응답은 성공 시 `{ "error": null }`, 거절 시 `{ "error": "It is not your turn." }` 형태입니다. 서버의 `command()` 반환값이 요청한 클라이언트의 `emitWithAck()`를 완료합니다. 두 플레이어에게 최신 `state`를 전송한 뒤 응답하며, 명령의 형식이 잘못된 경우에도 같은 흐름으로 처리합니다.

응답 대기는 10초입니다. 응답이 없으면 명령의 처리 여부를 알 수 없으므로 재전송하지 않고, 재접속해 최신 상태를 받습니다. 이때 마지막 판은 계속 표시하지만 새 상태를 받기 전에는 입력할 수 없습니다. 이전 대국의 응답이나 연결 종료는 새 대국에 반영하지 않습니다.

서버가 보내는 이벤트는 다음과 같습니다.

| 이벤트 | 데이터 |
| --- | --- |
| `state` | `GameSnapshot` 전체 상태 |
| `session_closed` | 다른 탭의 접속으로 좌석이 교체되었다는 안내; 이어서 연결 종료 |

`GameSnapshot`은 프런트가 그대로 보관하는 서버 상태입니다. 판 배치는 FEN, 합법 수와 마지막 수는 UCI, 기보는 SAN으로 보냅니다. `chessboard.ts`에서만 UCI를 `Move` 객체로 해석합니다. 무승부 청구는 `python-chess`의 `can_claim_draw()`를 사용하므로 다음 합법적인 수로 조건을 만족하는 경우도 포함합니다.

## 검사와 빌드

프로젝트 루트에서:

```sh
make check
make build
make serve
```

`check`는 Svelte·TypeScript를 검사하고, `build`는 `apps/frontend/dist`를 만듭니다. 두 작업에는 Python이 필요 없습니다. `serve`는 빌드된 화면과 API를 <http://127.0.0.1:8000>에서 함께 제공합니다. `dist`는 백엔드 시작 시 확인하므로 이미 실행 중인 서버는 빌드 후 재시작하세요.

## 프런트 의존성

[Simple.css](https://simplecss.org/)가 버튼·입력·표·article·dialog의 기본 스타일을 제공합니다. `app.css`에는 색상과 공통 배치만 두고, 컴포넌트에는 그 화면에 필요한 배치를 둡니다. [cm-chessboard](https://github.com/shaack/cm-chessboard)가 좌표·기물·클릭·드래그·키보드 입력을 맡습니다. 이 패키지들과 SVG는 npm 의존성에서 Vite 빌드에 포함하므로 실행 중 외부 CDN에 접근하지 않습니다. 직접 관리할 코드는 줄지만 브라우저에는 해당 라이브러리 코드도 전달됩니다.

[Ky](https://github.com/sindresorhus/ky)는 HTTP 요청의 JSON 처리와 오류 검사를 맡습니다. 공통 인스턴스는 `/api/`를 기준으로 요청하며, 타임아웃은 10초, 자동 재시도는 사용하지 않습니다. 각 API 함수에는 경로와 응답 타입을 적습니다. Ky는 MIT 라이선스이며 고지문에 포함했습니다.

`socket.io-client`는 기존 서버와 같은 출처의 `/api/socket.io`에 연결합니다. 외부 실시간 통신 서비스나 계정은 필요 없습니다. Python 서버의 `python-socketio`와 함께 사용하는 두 패키지는 MIT 라이선스입니다. 클라이언트와 그 의존성의 라이선스도 `third-party-notices.txt`에 포함합니다.

cm-chessboard는 JavaScript만 배포하므로 `src/game/board/cm-chessboard.d.ts`에 실제로 사용하는 API의 타입을 적었습니다. 패키지를 올릴 때 이 선언과 실제 API가 맞는지 확인하세요. 코드 라이선스는 MIT이며, 사용하는 Wikimedia 기물 SVG는 Cburnett·Rfc1394의 그림을 shaack이 묶은 CC BY-SA 3.0 자료입니다. 배포되는 `third-party-notices.txt`와 화면의 Credits 링크에 출처를 남깁니다.

## 저장과 실행 범위

대국 상태는 서버 메모리에 저장됩니다. **서버가 재시작되면 대국이 초기화됩니다.** 개발 중 `--reload`가 작동해도 마찬가지입니다. 한 프로세스로 실행해야 하며 `--workers`를 늘리면 프로세스별 방 목록이 달라집니다. 현재 구조에서 영구 저장이나 여러 서버가 필요해지면 이 방 저장 부분을 데이터베이스와 공유 메시지 처리로 확장하면 됩니다.

브라우저의 `sessionStorage`에는 방별 자리 토큰을 보관합니다. 같은 탭을 새로고침하면 복구되지만, 탭을 닫거나 저장소를 지우면 복구가 보장되지 않습니다. 같은 토큰으로 다른 탭에서 연결하면 이전 연결을 종료합니다. 초대 링크만으로 기존 플레이어의 자리를 가져갈 수는 없습니다.

두 플레이어가 연결되어 있어야 수를 둘 수 있고, 상대가 끊기면 재접속을 기다립니다. 시계나 연결 끊김에 따른 자동 패배는 없습니다. 빈 방은 마지막 활동으로부터 24시간이 지나면 다음 요청 때 정리하며, 전체 방은 최대 500개입니다.

학습할 때 함께 볼 공식 문서: [Socket.IO 클라이언트](https://socket.io/docs/v4/client-api/), [Python Socket.IO 서버](https://python-socketio.readthedocs.io/en/stable/server.html), [Svelte의 상태](https://svelte.dev/docs/svelte/$state), [파생 상태](https://svelte.dev/docs/svelte/$derived), [python-chess 규칙 API](https://python-chess.readthedocs.io/en/latest/core.html).
