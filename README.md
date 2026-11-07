# Small Chess

Play chess in your browser. Find an opponent or invite a friend.

**[Play chess → https://yabsed.github.io/chess/](https://yabsed.github.io/chess/)**

![Small Chess game](image.png)

## Get started

1. Click **Create an account** and choose a username and password.
2. Click **Find opponent** to match with another waiting player.
3. Select a piece, then its destination. You can also use the arrow keys and Enter.

To play with a friend, click **Invite a friend** and share the link. Your friend signs in and clicks **Join as Black** to start.

## Good to know

- Click your profile picture to upload a JPEG, PNG or WebP image up to 2 MiB.
- Use **Flip board** to change your view or **Offer draw** to propose a draw.
- Reload to return to your game. Sign in again after closing the tab.
- Games have no time limit. If your opponent disconnects, wait for them to reconnect.
- Games survive server restarts. Disconnected games expire after 24 hours without activity.

[Report an issue](https://github.com/yabsed/chess/issues) · [Credits and licenses](apps/frontend/public/third-party-notices.txt)

## Run locally

```sh
make install
make backend
make frontend
```

`make backend` builds the backend image and starts it with PostgreSQL through
Compose. The API runs on `127.0.0.1:8000`, PostgreSQL on `127.0.0.1:5432`.
Local development and ECS use the same Dockerfile and startup command.
After backend changes, run `make backend` again. To serve a single public server
through Caddy, set `CHESS_IP` and `CHESS_ORIGIN` in `.env`, then run
`docker compose --profile public up -d --build`.

```sh
make check
make build
```

## Shared state

Each backend keeps its own sockets. PostgreSQL holds accounts, sessions, seats,
the waiting list and games. Games store the initial position and UCI moves so
restoring them also restores repetition history.

`game.py` owns chess rules. `room.py` seats players and commits their actions.
`account.py` handles HTTP accounts; `main.py` connects sockets to those operations.

Lobby transactions serialize seating and room capacity across servers. Moves
lock only their room, then recheck the player's session, seat and game revision.
The 500-room limit counts unfinished games; completed games follow the separate
24-hour expiry policy.

An action replies when its transaction commits. In that same transaction,
`NOTIFY chess` names the users whose screens may have changed. One background
loop per server reads those users' state and sends updates. It compares the DB
rows before reconstructing games, so unchanged games need no chess calculation.
Seat, waiting status and board arrive together as one `state` message.

Every ten seconds, a backend renews its players' presence and reconciles snapshots
even if an alert was missed. Presence expires after 35 seconds without renewal.
An old socket cannot clear its replacement. Use a direct PostgreSQL connection:
the notification listener needs a dedicated session, not transaction pooling.

The schema is initialized under a database lock at startup. This establishes the
initial schema; future column changes need explicit, backward-compatible SQL
migrations before rolling out the code that uses them.

## AWS deployment

The frontend runs on GitHub Pages. The backend runs on ECS on EC2, reached through
CloudFront and a private ALB. RDS PostgreSQL holds the shared state.

[`infra/foundation/template.yaml`](infra/foundation/template.yaml) manages the infrastructure.
[`infra/backend/template.yaml`](infra/backend/template.yaml) manages the ECS task and service.
GitHub Actions deploys backend images by digest through CloudFormation; it verifies
the running release and the public HTTP and WebSocket endpoints.

Backend changes merged into `main` deploy automatically when `AWS_DEPLOY_ENABLED`
is `true`. Foundation changes use the **Infrastructure** workflow: plan, review the
change set, then apply that exact plan. Frontend changes use the existing Pages workflow.

See [infrastructure operations](infra/README.md) for credentials, deployment,
rollback, capacity, data protection, and the existing-resource adoption boundary.

## Move the existing SQLite accounts

For the first transition, schedule a short maintenance window: the previous
backend uses local state and a different socket message format. Stop it, retain
the `chess-data` volume, and copy its `chess.sqlite` file to a backup. Import that
backup into an empty destination database from a machine that can reach it:

```sh
export CHESS_DATABASE_URL='postgresql://...'
uv run --directory apps/backend python -m app.import_sqlite /absolute/path/chess.sqlite
```

The importer preserves user IDs, password hashes, avatars and unexpired login
tokens. It opens the backup read-only and refuses to overwrite existing accounts.
The old version never saved ongoing games, so those cannot be imported. Deploy the
new backend and frontend together, then reopen traffic. Keep the old volume until
the imported accounts and new deployment have been checked.
