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

화면은 <http://127.0.0.1:5174>, HTTP API 문서는 <http://127.0.0.1:8000/docs>입니다. 브라우저가 백엔드에 직접 연결합니다. 화면 포트를 바꾸면 백엔드의 `CHESS_ORIGIN`도 같은 주소로 맞춥니다.

1. **Create an account**로 가입합니다. 아이디는 영문·숫자·밑줄 3–24자이며 대소문자를 구분하지 않습니다. 비밀번호는 8–128자입니다.
2. 상단 프사를 누르면 JPEG·PNG·WebP 파일을 올릴 수 있습니다. 2 MiB, 1,600만 화소 이하의 이미지를 256×256 WebP로 변환해 저장합니다.
3. **Find opponent**를 누르면 먼저 기다린 사람과 매칭됩니다. 백·흑은 무작위로 배정합니다. 대기 중에는 **Cancel**로 취소합니다.
4. **Invite a friend**는 초대 방을 만듭니다. 다른 계정으로 링크를 열고 **Join as Black**을 누릅니다. 상대가 참가하기 전에는 방을 취소할 수 있습니다.

로그인은 탭마다 구분되므로 두 탭에서 서로 다른 계정으로 시험할 수 있습니다. 계정마다 진행 중인 대국은 하나이며, 새로고침하면 본인 자리로 돌아갑니다. 같은 계정으로 다른 탭에서 접속하면 이전 연결을 종료합니다.

같은 Wi-Fi의 다른 기기에서 시험하려면 백엔드에 `CHESS_ORIGIN=http://내-PC의-IP:5174`, 프런트에 `VITE_BACKEND_URL=http://내-PC의-IP:8000`을 지정하고 두 서버를 `--host 0.0.0.0`으로 실행합니다.

## 구조와 읽는 순서

```text
apps/backend/app/
  account.py    SQLite, 비밀번호, 토큰 세션, 프사
  main.py       HTTP·소켓 입구, 대기 목록, 방 배정
  protocol.py   요청·응답 모델
  room.py       참가자, 연결, 명령 처리와 상태 전송
  game.py       체스 규칙과 대국 상태

apps/frontend/src/
  App.svelte          계정 복구, 로비와 대국 화면
  Account.svelte      로그인·가입·프사 변경
  http.ts             백엔드 주소, 탭의 세션 토큰, HTTP 설정
  game/
    room.svelte.ts    소켓 연결, 입장·명령과 응답 대기
    protocol.ts       통신 타입
    GameView.svelte   참가자, 차례, 대국 버튼
    MoveHistory.svelte
    board/            보드 표시, 승급과 입력
```

로그인은 `Account → HTTP → account.py → { user, token }`으로 이어집니다. HTTP는 `Authorization: Bearer …`, 소켓은 `auth.token`으로 같은 토큰을 전달합니다. 서버는 토큰의 해시로 계정과 자리를 찾습니다. JWT나 별도 소켓 토큰은 없습니다.

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

HTTP는 계정과 프사를 담당합니다. 프런트 주소 하나를 CORS와 소켓의 허용 출처로 지정합니다. 인증은 출처나 쿠키가 아닌 세션 토큰으로 합니다.

| 경로 | 동작 |
| --- | --- |
| `POST /api/register` | 가입하고 `{ user, token }` 반환 |
| `POST /api/session` | 로그인하고 `{ user, token }` 반환 |
| `DELETE /api/session` | 해당 세션 폐기·소켓 종료 |
| `GET /api/me` | 사용자 정보 또는 `null` |
| `PUT /api/me/avatar` | 이미지 파일 자체를 요청 본문으로 업로드 |
| `GET /api/users/{id}/avatar` | 프사 이미지 |
| `GET /api/health` | 서버 상태 |

Socket.IO는 `/api/socket.io`에서 WebSocket을 사용합니다. 연결의 `auth`에 `{ token, id }`를 전달하고 입장·대국 명령을 받을 때도 세션을 다시 확인합니다. 재접속할 때 현재 토큰과 방 ID를 읽습니다. 토큰을 URL에 넣지 않습니다.

| 방향 | 이벤트 | 데이터 |
| --- | --- | --- |
| 클라이언트 → 서버 | `enter` | `{ type: "create" / "join" / "match" / "cancel", id? }` |
| 클라이언트 → 서버 | `command` | `{ type, revision, move? }` |
| 서버 → 클라이언트 | `seat` | `{ id, color }`; 자리가 없으면 빈 데이터 |
| 서버 → 클라이언트 | `waiting` | 대기 여부 |
| 서버 → 클라이언트 | `state` | 판·기보·결과·참가자·접속 상태 |
| 서버 → 클라이언트 | `session_closed` | 로그아웃·만료·다른 탭 접속 안내 |

`enter`와 `command`의 응답은 `{ error: null }` 또는 `{ error: "설명" }`입니다. 대국 명령은 `move`, `resign`, `offer_draw`, `accept_draw`, `decline_draw`, `claim_draw`입니다. 수는 UCI, 기보는 SAN, 판은 FEN을 사용합니다.

## 저장

`apps/backend/data/chess.sqlite`에 계정·세션·프사를 저장합니다. 비밀번호는 Argon2 해시, 세션은 임의 토큰의 SHA-256 해시와 만료 시각으로 보관합니다. 세션은 7일 뒤 만료되고 로그아웃하면 즉시 폐기됩니다. 가입·로그인은 IP당 분당 10회로 제한합니다.

프런트의 토큰은 백엔드 주소별 `sessionStorage`에 저장합니다. 새로고침에는 유지되고 탭을 닫으면 사라집니다. HttpOnly 쿠키와 달리 해당 페이지의 JavaScript에서 읽을 수 있습니다. 브라우저가 인증 쿠키를 보내지 않으므로 GitHub Pages와 AWS의 주소가 달라도 서드파티 쿠키에 의존하지 않습니다.

| 환경 변수 | 기본값·용도 |
| --- | --- |
| `CHESS_DB` | `apps/backend/data/chess.sqlite`; DB 파일 경로 |
| `CHESS_ORIGIN` | `http://127.0.0.1:5174`; 허용할 프런트 출처 |
| `VITE_BACKEND_URL` | `http://127.0.0.1:8000`; 프런트 빌드 시 지정할 백엔드 출처 |

대국과 매칭 대기는 메모리에 있어 **서버를 재시작하면 초기화됩니다.** 한 프로세스로 실행합니다. 시계와 접속 종료에 따른 자동 패배는 없으며, 상대가 끊기면 재접속을 기다립니다. 접속자가 없는 방은 마지막 활동으로부터 24시간이 지나면 다음 요청 때 정리합니다. 방은 최대 500개입니다.

## 배포: GitHub Pages + Lightsail

프런트는 `https://yabsed.github.io/chess/`, 백엔드는 `https://고정IP`로 제공합니다. 도메인 구매는 필요하지 않습니다. 프런트의 파일·링크는 상대 경로를 사용하므로 `/chess/` 아래에서도 동작합니다.

### 백엔드

Lightsail에 고정 IP를 연결하고 TCP 80·443을 허용합니다. 서버에 [Docker Engine과 Compose](https://docs.docker.com/engine/install/debian/)를 설치한 뒤, 저장소에서 다음을 실행합니다.

```sh
cp .env.example .env
```

`.env`의 `CHESS_IP`를 실제 고정 IP로 바꿉니다. `CHESS_ORIGIN=https://yabsed.github.io`에는 `/chess/`를 붙이지 않습니다. 아래 예시의 `203.0.113.10`은 실제 서버 주소로 바꿔야 합니다.

```sh
sudo docker compose up -d --build
curl https://203.0.113.10/api/health
```

`compose.yaml`은 백엔드와 Caddy만 실행합니다. Caddy가 공개 IP의 HTTPS 인증서를 발급·갱신하고 HTTP·WebSocket을 백엔드로 전달합니다. `Caddyfile`은 [Let's Encrypt의 IP 인증서](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability/)를 위한 `shortlived` 프로필을 명시합니다. 인증서의 유효기간은 약 6일이므로 Caddy와 인증서 볼륨을 유지합니다.

앞서 OS에 Caddy를 설치해 실행했다면 `sudo systemctl disable --now caddy`로 중지한 뒤 Compose를 시작합니다. 두 Caddy가 같은 80·443 포트를 사용할 수는 없습니다.

백엔드의 8000번 포트는 외부에 공개하지 않습니다. Caddy가 전달한 사용자 IP로 로그인 횟수를 제한하므로, 백엔드는 내부 프록시의 전달 헤더를 신뢰하도록 설정합니다. `chess-data` 볼륨에는 계정·세션·프사, `caddy-data` 볼륨에는 인증서를 보관합니다. **`docker compose down -v`는 데이터를 삭제하므로 사용하지 않습니다.**

### 프런트

1. GitHub 저장소의 **Settings → Pages → Source**를 **GitHub Actions**로 설정합니다.
2. **Settings → Secrets and variables → Actions → Variables**에 `VITE_BACKEND_URL=https://실제고정IP`를 추가합니다. `/api`는 붙이지 않습니다.
3. `main`에 프런트 변경을 푸시하거나 **Actions → Frontend → Run workflow**를 실행합니다.

워크플로가 검사·빌드 후 Pages에 배포합니다. 백엔드 주소는 공개 설정이며, 비밀번호나 토큰을 넣지 않습니다. 백엔드의 인증서가 정상 발급되어 `/api/health`에 접속되는 상태여야 합니다.

## 검증과 빌드

```sh
make check
make test
make build
make serve
```

`check`는 Svelte·TypeScript를 검사합니다. `test`는 임시 DB와 실제 HTTP·WebSocket 연결로 다른 출처의 요청·토큰 인증·프사·세션 만료·동시 매칭·취소·재접속을 검증합니다. `build` 후 `serve`는 빌드한 화면을 <http://127.0.0.1:5174>에서 미리 봅니다. 백엔드는 `make backend`로 별도 실행합니다.

프런트는 Simple.css·cm-chessboard·Ky·Socket.IO를 사용합니다. 브라우저에 포함하는 라이브러리와 기물 SVG의 출처·라이선스는 `apps/frontend/public/third-party-notices.txt`와 화면의 Credits 링크에 있습니다. 백엔드는 FastAPI·python-socketio·python-chess에 `pwdlib[argon2]`와 Pillow를 추가해 비밀번호와 이미지 처리를 맡깁니다.
