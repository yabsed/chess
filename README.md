# Small Chess

FastAPI와 Svelte 5로 만든 실시간 1:1 체스입니다. 아이디로 로그인하고, 프사를 올리고, 상대를 찾거나 친구를 초대합니다. **요청이 검증·상태 변경·전송으로 이어지는 순서**를 따라 읽을 수 있게 구성했습니다.

## 실행

Python 3.11 이상, Node.js 22.12 이상, [uv](https://docs.astral.sh/uv/getting-started/installation/)가 필요합니다.

```sh
make install
```

터미널 두 개에서 각각 실행합니다.

```sh
make backend
```

```sh
make frontend
```

화면은 <http://127.0.0.1:5174>, HTTP API 문서는 <http://127.0.0.1:8000/docs>입니다. Vite가 `/api`의 HTTP와 WebSocket을 백엔드로 전달합니다. 포트는 `make frontend PORT=5175`로 바꿀 수 있습니다.

1. **Create an account**로 가입합니다. 아이디는 영문·숫자·밑줄 3–24자이며 대소문자를 구분하지 않습니다. 비밀번호는 8–128자입니다.
2. 상단 프사를 누르면 JPEG·PNG·WebP 파일을 올릴 수 있습니다. 2 MiB, 1,600만 화소 이하의 이미지를 256×256 WebP로 변환해 저장합니다.
3. **Find opponent**를 누르면 먼저 기다린 사람과 매칭됩니다. 백·흑은 무작위로 배정합니다. 대기 중에는 **Cancel**로 취소합니다.
4. **Invite a friend**는 초대 방을 만듭니다. 다른 계정으로 링크를 열고 **Join as Black**을 누릅니다. 상대가 참가하기 전에는 방을 취소할 수 있습니다.

같은 컴퓨터에서 두 계정을 시험하려면 일반 창과 시크릿 창을 사용하세요. 계정마다 진행 중인 대국은 하나이며, 새로고침하면 본인 자리로 돌아갑니다. 같은 계정으로 다른 탭에서 접속하면 이전 연결을 종료합니다.

같은 Wi-Fi의 다른 기기에서는 `apps/frontend`에서 `npm run dev -- --host 0.0.0.0`으로 실행하고 `http://내-PC의-IP:5174`로 접속합니다.

## 구조와 읽는 순서

```text
apps/backend/app/
  account.py    SQLite, 비밀번호, 쿠키 세션, 프사
  main.py       HTTP·소켓 입구, 대기 목록, 방 배정
  protocol.py   요청·응답 모델
  room.py       참가자, 연결, 명령 처리와 상태 전송
  game.py       체스 규칙과 대국 상태

apps/frontend/src/
  App.svelte          계정 복구, 로비와 대국 화면
  Account.svelte      로그인·가입·프사 변경
  http.ts             HTTP 공통 설정
  game/
    room.svelte.ts    소켓 연결, 입장·명령과 응답 대기
    protocol.ts       통신 타입
    GameView.svelte   참가자, 차례, 대국 버튼
    MoveHistory.svelte
    board/            보드 표시, 승급과 입력
```

로그인은 `Account → HTTP → account.py → 쿠키`로 이어집니다. 이후 소켓도 같은 쿠키를 확인합니다. 별도 자리 토큰이나 브라우저 저장소 없이 서버가 사용자 ID로 방과 색을 찾습니다.

입장은 다음 흐름입니다.

```text
Room.enter() → enter { type, id? }
  → 로그인 확인 → 로비 잠금 → 생성·참가·매칭·취소
  → seat { id, color } → state → acknowledgement
```

대기 목록은 `사용자 ID → 소켓 ID` 사전 하나입니다. 중복 대기는 같은 항목을 사용하고, 연결이 끊기면 제거합니다. 매칭은 두 자리를 먼저 확정한 뒤 결과를 전송하므로 응답을 놓쳐도 재접속으로 복구됩니다. 취소와 매칭이 겹치면 로비 잠금을 먼저 얻은 작업이 적용됩니다. 이미 매칭된 경우에는 배정된 자리로 들어갑니다.

한 수는 다음 흐름입니다.

```text
Board → Room.send() → command { type, revision, move? }
  → 로그인 확인 → 방 잠금 → 검증 → Game.apply()
  → state → acknowledgement
```

서버가 차례·합법성·revision을 검증합니다. 프런트의 `Room.allowed`는 버튼과 입력 조건을 함께 표현합니다. 응답이 10초 안에 오지 않으면 요청을 다시 보내지 않고 재접속해 서버 상태를 확인합니다.

함수와 파일은 독립적으로 이해할 주제로 나눕니다. 작은 표현식은 사용하는 곳에 두고, 관련 필드·대입문은 작은 묶음 안에서 열을 맞춥니다. 검증·변경·전송 사이에는 빈 줄을 둡니다. Python은 4칸, 프런트는 2칸을 들여쓰며 `.editorconfig`에 기본 규칙이 있습니다.

## 통신

HTTP는 계정과 프사를 담당합니다. 변경 요청에는 화면과 같은 출처의 `Origin` 헤더가 필요합니다.

| 경로 | 동작 |
| --- | --- |
| `POST /api/register` | 가입하고 로그인 |
| `POST /api/session` | 로그인 |
| `DELETE /api/session` | 세션 폐기·소켓 종료·쿠키 삭제 |
| `GET /api/me` | 사용자 정보 또는 `null` |
| `PUT /api/me/avatar` | 이미지 파일 자체를 요청 본문으로 업로드 |
| `GET /api/users/{id}/avatar` | 프사 이미지 |
| `GET /api/health` | 서버 상태 |

Socket.IO는 `/api/socket.io`에서 WebSocket을 사용합니다. 연결의 `auth`에는 복귀할 방 ID `{ id }`만 전달하며, 인증은 쿠키로 합니다. 로그인 세션은 입장·대국 명령을 받을 때도 다시 확인합니다.

| 방향 | 이벤트 | 데이터 |
| --- | --- | --- |
| 클라이언트 → 서버 | `enter` | `{ type: "create" / "join" / "match" / "cancel", id? }` |
| 클라이언트 → 서버 | `command` | `{ type, revision, move? }` |
| 서버 → 클라이언트 | `seat` | `{ id, color }`; 자리가 없으면 빈 데이터 |
| 서버 → 클라이언트 | `waiting` | 대기 여부 |
| 서버 → 클라이언트 | `state` | 판·기보·결과·참가자·접속 상태 |
| 서버 → 클라이언트 | `session_closed` | 로그아웃·만료·다른 탭 접속 안내 |

`enter`와 `command`의 응답은 `{ error: null }` 또는 `{ error: "설명" }`입니다. 대국 명령은 `move`, `resign`, `offer_draw`, `accept_draw`, `decline_draw`, `claim_draw`입니다. 수는 UCI, 기보는 SAN, 판은 FEN을 사용합니다.

## 저장과 배포

`apps/backend/data/chess.sqlite`에 계정·세션·프사를 저장합니다. 비밀번호는 Argon2 해시, 세션은 임의 토큰의 SHA-256 해시와 만료 시각으로 보관합니다. 세션 쿠키는 HttpOnly·SameSite=Strict이며 7일 뒤 만료됩니다. 가입·로그인은 IP당 분당 10회로 제한합니다.

| 환경 변수 | 기본값·용도 |
| --- | --- |
| `CHESS_DB` | `apps/backend/data/chess.sqlite`; DB 파일 경로 |
| `CHESS_ORIGIN` | 생략하면 요청의 scheme·Host 사용; 배포 시 실제 화면 주소 지정 |

HTTPS 배포에서는 `CHESS_ORIGIN=https://실제도메인`을 설정합니다. 이 경우 쿠키에도 Secure가 적용됩니다. 프런트와 API는 같은 출처에서 제공하고 프록시는 Host·Origin·WebSocket을 전달해야 합니다. DB 디렉터리는 서버 사용자에게 쓰기 권한이 있어야 합니다.

대국과 매칭 대기는 메모리에 있어 **서버를 재시작하면 초기화됩니다.** 한 프로세스로 실행합니다. 시계와 접속 종료에 따른 자동 패배는 없으며, 상대가 끊기면 재접속을 기다립니다. 접속자가 없는 방은 마지막 활동으로부터 24시간이 지나면 다음 요청 때 정리합니다. 방은 최대 500개입니다.

Docker로 백엔드를 실행하려면:

```sh
make backend-docker
```

`chess-data` 볼륨이 `/app/data`에 연결되어 컨테이너를 다시 만들어도 계정·프사가 남습니다. 컨테이너는 일반 사용자로 실행하며, 프런트는 별도로 실행합니다.

## 검증과 빌드

```sh
make check
make test
make build
make serve
```

`check`는 Svelte·TypeScript를 검사합니다. `test`는 임시 DB와 실제 HTTP·WebSocket 연결로 계정·프사·세션 만료·동시 매칭·취소·재접속을 검증합니다. `build` 후 `serve`는 화면과 API를 <http://127.0.0.1:8000>에서 함께 제공합니다.

프런트는 Simple.css·cm-chessboard·Ky·Socket.IO를 사용합니다. 브라우저에 포함하는 라이브러리와 기물 SVG의 출처·라이선스는 `apps/frontend/public/third-party-notices.txt`와 화면의 Credits 링크에 있습니다. 백엔드는 FastAPI·python-socketio·python-chess에 `pwdlib[argon2]`와 Pillow를 추가해 비밀번호와 이미지 처리를 맡깁니다.
