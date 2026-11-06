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
make database
make backend
# In another terminal:
make frontend
```

PostgreSQL runs on `127.0.0.1:5432`; the backend uses `CHESS_DATABASE_URL`.
`make backend-docker` runs both through Compose instead. To serve a single public
server through Caddy, set `CHESS_IP` and `CHESS_ORIGIN` in `.env`, then run
`docker compose --profile public up -d --build`.

```sh
make test
make check
make build
```

Tests start two backends against a temporary schema in `CHESS_TEST_DATABASE_URL`
(defaults to the local database). They exercise cross-server play, concurrent
commands, logout, a killed server, and repetition draws after recovery.

## Shared state

Each backend keeps its own sockets. PostgreSQL holds accounts, sessions, seats,
the waiting list and games. Games store the initial position and UCI moves so
restoring them also restores repetition history.

Lobby transactions serialize seating across servers. Moves lock their room and
check its revision before committing. The transaction also sends `NOTIFY chess`;
every backend reads a fresh snapshot for its local players and sends only changed
screens. Seat, waiting status and board arrive as one `state` message.

Every ten seconds, a backend renews its players' presence and reconciles snapshots
even if an alert was missed. Presence expires after 35 seconds without renewal.
An old socket cannot clear its replacement. Use a direct PostgreSQL connection:
the notification listener needs a dedicated session, not transaction pooling.

The schema is initialized under a database lock at startup. This establishes the
initial schema; future column changes need explicit, backward-compatible SQL
migrations before rolling out the code that uses them.

## ECS on EC2

The frontend can stay on GitHub Pages. Set its `VITE_BACKEND_URL` repository variable
to a stable HTTPS API domain pointing at an ALB, then rebuild the frontend.
`CHESS_ORIGIN` is the frontend origin, for example `https://yabsed.github.io`.

Provision these AWS resources before deploying:

- An ECR repository and an ECS cluster with an EC2 Auto Scaling Group capacity
  provider using an ECS-optimized AMI. The image targets Linux x86-64. For host failure recovery, run at least
  two tasks on distinct instances, spread across availability zones.
- An HTTPS ALB with an ACM certificate, an `ip` target group on port 8000, and
  `/api/health` checks. Configure its idle timeout above the Socket.IO heartbeat
  interval. Give ECS the capacity to run old and new tasks during a deployment.
- A private RDS PostgreSQL database with backups. Allow port 5432 from the task
  security group; allow task port 8000 only from the ALB security group. Private
  instances also need access to ECS, ECR, S3, Secrets Manager and CloudWatch through
  NAT or the corresponding VPC endpoints.
- A Secrets Manager secret containing the complete PostgreSQL connection string,
  and a CloudWatch log group named `/ecs/chess`. The image includes the RDS CA bundle:
  use `?sslmode=verify-full&sslrootcert=/etc/ssl/certs/rds.pem` in the RDS URL.
- An ECS task execution role allowed to pull the image, write logs and read that
  secret. The application itself uses database credentials and needs no AWS task
  role. Set `ECS_ENABLE_AWSLOGS_EXECUTIONROLE_OVERRIDE=true` on the EC2 ECS agent
  to use that role for logs. Give its database user ownership of the application's schema.
- A GitHub OIDC role restricted to this repository's deployment branch, with ECR
  push, ECS register/update/describe and `iam:PassRole` for the execution role.

The backend workflow tests first, builds a commit-tagged image, fills
[`deploy/task-definition.json`](deploy/task-definition.json), and updates an
existing ECS service. It enables deployment rollback and verifies that the new
task revision actually became active. The service should use `awsvpc` networking,
the capacity provider, and the ALB target group above. Create its initial task
revision from the same template with the image, role, origin, proxy CIDRs, secret and region
filled in; these are the fields the workflow fills on subsequent deployments.

Set these GitHub repository variables to enable deployment:

| Variable | Value |
| --- | --- |
| `AWS_ROLE_ARN` | GitHub OIDC deployment role; leaving it unset runs tests only |
| `AWS_REGION` | Region containing the service |
| `ECR_REPOSITORY` | Image repository name |
| `ECS_CLUSTER`, `ECS_SERVICE` | Existing cluster and service names |
| `ECS_EXECUTION_ROLE_ARN` | Task execution role |
| `DATABASE_SECRET_ARN` | Secret whose plaintext value is the database URL |
| `CHESS_ORIGIN` | Allowed frontend origin |
| `TRUSTED_PROXY_CIDRS` | ALB subnet CIDRs, comma-separated, for trusted forwarded client IPs |

No backend task needs a local data volume. Rolling deployment can briefly
disconnect sockets; clients reconnect through the same API domain and recover
their committed game. Database availability is still required for play.

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
