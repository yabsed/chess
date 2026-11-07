# Deployment

Read the [AWS workflow](../.github/workflows/deploy.yml) for the order,
`deploy.sh` for the commands, and `template.yaml` for the resources.

| Deployment | Template |
| --- | --- |
| Foundation | [Network, EC2, load balancer, database and roles](foundation/template.yaml) |
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

Every deployment runs foundation first. An empty CloudFormation change set
succeeds without updating resources; a failed foundation deployment stops the
backend release. Foundation-only changes also build and release the backend.

Pull requests only run checks. `AWS_DEPLOY_ENABLED` controls both deployments;
checks still run when it is disabled. To retry manually, run **AWS** on `main`
in GitHub Actions. Deployments share a concurrency group and do not overlap.

## Runtime

```text
Browser → CloudFront → private ALB → ECS container → private PostgreSQL
                                       │
                                t4g.small · 2 GiB
                                public subnet / IPv4
                                       │
                              ECR, logs and secrets
```

The host reaches public AWS endpoints through the internet gateway, without NAT.
Its security group accepts app connections only from the ALB. The ALB uses the
host's private address and the dynamic port registered by ECS; the container still
listens on port 8000. RDS keeps its private subnets and existing data.

The task keeps its 1 GiB memory limit. ECS reserves 256 MiB for the host and agent,
so two tasks do not fit on one host. During a release, managed scaling can add a
second host, start the new task, then drain the old one. Empty capacity scales in
after a delay. Normal capacity is one host; the maximum is two.

The `ECSImageId` stack parameter pins the ECS-optimized AL2023 ARM64 AMI.
Deployments reuse its existing value. Instance refresh and managed draining replace
the hosts; occupied hosts are protected from ordinary scale-in.

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

An operator manages deployment credentials and the existing
`chess-deployment-access` permission stack outside this repository. New resources
or IAM changes may require an operator to update those permissions first.
Existing stack parameters are reused during deployment.

The database URL stays in Secrets Manager and is injected when a task starts.
Keep credentials and secret values out of Git.

## Checks

The workflow runs `cfn-lint` on both templates. An `ubuntu-24.04-arm` runner builds
and tests the ARM image with a fresh PostgreSQL database. The backend has the same 1 GiB limit
and read-only filesystem as its ECS task. Checks cover HTTP and the Engine.IO
WebSocket handshake; they are not a production load benchmark. After deployment
the workflow checks the service's task definition and public endpoint.

To run the checks locally, use Python 3.11+ and curl:

```sh
python3 -m venv /tmp/chess-infra
source /tmp/chess-infra/bin/activate
pip install -r infra/requirements.txt
make infra-check
python3 -m infra.backend.smoke http://127.0.0.1:8000
```

The smoke command needs a running backend. CI uses [local/compose.yaml](local/compose.yaml)
to start it with Docker Compose.
These checks do not test SQL migrations or run account-aware CloudFormation validation.

## Local containers

`make backend` starts the backend and PostgreSQL using [local/compose.yaml](local/compose.yaml).
The Compose project is named `chess` to keep existing local volume names.
CI overrides that name for each run.

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

Keep existing resource logical IDs and cross-stack export names. EC2
capacity limits live in foundation; the container count is `DesiredCount` in backend.

The database keeps encryption, backups, deletion protection and retention.
[stack-policy.json](foundation/stack-policy.json) blocks its replacement and removal
during foundation updates.

Backend tables are declared in [models.py](../apps/backend/app/models.py).
Startup creates missing tables; it does not alter existing columns.
SQL schema changes need their own migration before deploying code that requires them.

CloudFormation handles failed stack updates; ECS rolls back failed container
rollouts. A failed public endpoint check fails the workflow for investigation.

For initial foundation provisioning, supply `ECSImageId` explicitly using an
ECS-optimized AL2023 ARM64 AMI from the deployment region. The workflow updates
the existing stacks; account setup and execution roles are managed by an operator.

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

The ARM transition and NAT removal completed on 2026-11-08 (Asia/Seoul):
[implementation](https://github.com/yabsed/chess/commit/e995b33203dc257c37523f274dcf18c18df1f4b8),
[transition fix](https://github.com/yabsed/chess/commit/b58639411428fbf74b9f1ac712161d45be244c47),
[successful deployment](https://github.com/yabsed/chess/actions/runs/42397948398).
