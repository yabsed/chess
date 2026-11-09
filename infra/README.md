# Deployment

Read the [AWS workflow](../.github/workflows/deploy.yml) for the order,
`deploy.sh` for the commands, and `template.yaml` for the resources.

| Deployment | Template |
| --- | --- |
| Foundation | [Network, EC2, CloudFront, database and roles](foundation/template.yaml) |
| Backend | [Container settings and ECS service](backend/template.yaml) |

Pushes to `main` that change `apps/backend/`, `infra/`, `Makefile`
or the workflow run this sequence when `AWS_DEPLOY_ENABLED=true`:

```text
Check templates
    ↓
Build ARM backend image · test HTTP and WebSocket
    ↓
Deploy foundation
    ↓
Deploy backend · check public API
```

Foundation runs first; a failure stops the release. Pull requests run checks only.
To retry, run **AWS** on `main` in GitHub Actions. Deployments do not overlap.

## Runtime

```text
Browser ──HTTPS── CloudFront ──VPC── EC2 / ECS :8000 ── RDS
                                    t4g.small         private
```

One EC2 host runs `chess-backend-direct`. Port 8000 accepts traffic only from
CloudFront's VPC origins security group. Public IPv4 provides outbound access to
ECR, logs and secrets. There is no ALB or NAT gateway.

| Resource | Configuration |
| --- | --- |
| EC2 | One ARM `t4g.small`; 256 MiB reserved for the host |
| ECS | One task; 1 GiB; fixed host port 8000 |
| Placement | Host attribute `chess.origin=true` |
| RDS | Existing private database, data and protections |

**Deployments briefly interrupt service.** ECS stops the old task before starting
the new one. Health checks and deployment rollback remain enabled; WebSocket
clients must reconnect. Keep `DesiredCount: 1` with this fixed port.

ECS restarts failed tasks. Host failure needs operator recovery: there is no
Auto Scaling group. AMI changes replace the host and update the CloudFront origin;
schedule downtime until both are ready.

## Settings

The workflow reads GitHub variables and Secrets.

| Setting | Purpose |
| --- | --- |
| Secret `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Backend deployment credentials |
| Secret `AWS_INFRA_ACCESS_KEY_ID` / `AWS_INFRA_SECRET_ACCESS_KEY` | Foundation deployment credentials |
| Variable `AWS_DEPLOY_ENABLED` | `true` enables foundation and backend deployment |
| Variable `AWS_REGION` | AWS region |
| Variable `AWS_ACCOUNT_ID` | Account checked before deployment |
| Variable `FOUNDATION_STACK` | Existing foundation stack |
| Variable `FOUNDATION_ROLE` | Foundation CloudFormation execution role |
| Variable `BACKEND_STACK` | Backend stack, named `<ECS cluster>-backend` |

The operator-owned `chess-deployment-access` stack grants deployment permissions,
including the direct EC2 origin and `chess-backend-direct` service. Update that
stack when new permissions are needed; the foundation role cannot edit IAM.
Deployments reuse existing stack parameters.

The database URL stays in Secrets Manager and is injected at task startup.

## Checks

CI lints both templates, builds an ARM image, and checks HTTP and the Engine.IO
WebSocket handshake against a fresh PostgreSQL database. Deployment then checks
ECS rollout completion and the public API. SQL migrations and load tests are separate.

Locally, use Python 3.11+ and curl:

```sh
python3 -m venv /tmp/chess-infra
source /tmp/chess-infra/bin/activate
pip install -r infra/requirements.txt
make infra-check
python3 -m infra.backend.smoke http://127.0.0.1:8000
```

The smoke command needs a running backend. CI uses [local/compose.yaml](local/compose.yaml)
to start it with Docker Compose.

## Local containers

`make backend` starts the backend and PostgreSQL using [local/compose.yaml](local/compose.yaml).

ARM is the deployment default. On an x86 development machine, run
`make backend PLATFORM=linux/amd64`. This changes only the local build and Compose
platform; the deployment script accepts ARM images only.

For local settings, copy [local/.env.example](local/.env.example) to `local/.env`.
Compose builds the database URL from `CHESS_POSTGRES_PASSWORD`; the defaults work
without an environment file.

To serve a single public server with Caddy, set `CHESS_IP` to its public IP
and `CHESS_ORIGIN` to the frontend's HTTPS origin in `infra/local/.env`.
From the repository root, run:

```sh
docker compose -f infra/local/compose.yaml --profile public \
  up -d --build
```

## Resource changes

Preserve resource logical IDs and cross-stack exports. Host settings live in
foundation; task settings live in backend.

RDS retains encryption, backups and deletion protection.
[stack-policy.json](foundation/stack-policy.json) blocks replacement and removal.
[models.py](../apps/backend/app/models.py) creates missing tables at startup;
column changes need a SQL migration before releasing dependent code.

These templates update the existing installation. Account setup is operator-owned.
Foundation requires a pinned ARM64 `ECSImageId` and the VPC's existing
`CloudFront-VPCOrigins-Service-SG` as `CloudFrontOriginSecurityGroup`.

To upgrade the host AMI, use an operator session with `AWS_DEFAULT_REGION`,
`AWS_ACCOUNT_ID`, `FOUNDATION_STACK`, `FOUNDATION_ROLE` and the reviewed
`ECS_IMAGE_ID` set:

```sh
aws cloudformation deploy \
  --stack-name "$FOUNDATION_STACK" \
  --template-file infra/foundation/template.yaml \
  --role-arn "arn:aws:iam::$AWS_ACCOUNT_ID:role/$FOUNDATION_ROLE" \
  --parameter-overrides "ECSImageId=$ECS_IMAGE_ID" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-disable-rollback \
  --no-fail-on-empty-changeset
```

## Roll back an image

Use an operator session. Set `AWS_DEFAULT_REGION`, `FOUNDATION_STACK`,
`BACKEND_STACK` and `PREVIOUS_IMAGE` (an earlier ARM `repository@sha256:...` URI).
The deployed image URI is recorded in each successful AWS run's summary.

```sh
aws sts get-caller-identity

role=$(aws cloudformation describe-stacks \
  --stack-name "$FOUNDATION_STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BackendDeploymentRole'].OutputValue | [0]" \
  --output text)

aws cloudformation deploy \
  --stack-name "$BACKEND_STACK" \
  --template-file infra/backend/template.yaml \
  --role-arn "$role" \
  --parameter-overrides \
    "FoundationStack=$FOUNDATION_STACK" \
    "ImageUri=$PREVIOUS_IMAGE" \
  --no-fail-on-empty-changeset
```

Check the public endpoint afterward with the smoke command. This restores the
image only; the current template and database must support that image.

## Layout

Keep one resource together: identity and retention settings, then properties.
Separate property groups with one blank line and resources with two. Align nearby
values and keep shell command options on separate lines.

## History

On 2026-11-10 (Asia/Seoul), the API moved from ALB to a direct CloudFront VPC
origin. One EC2 host and ECS service remain; RDS and the public API URL are unchanged.
The templates reflect this completed migration, not the intermediate two-host setup.

The ARM transition and NAT removal completed on 2026-11-08 (Asia/Seoul):
[implementation](https://github.com/yabsed/chess/commit/e995b33203dc257c37523f274dcf18c18df1f4b8),
[transition fix](https://github.com/yabsed/chess/commit/b58639411428fbf74b9f1ac712161d45be244c47),
[successful deployment](https://github.com/yabsed/chess/actions/runs/42397948398).
