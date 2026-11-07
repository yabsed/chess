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

`deploy.sh` resolves the regional ECS-optimized AL2023 ARM64 AMI once and stores
its ID in the `ECSImageId` stack parameter. Later deployments reuse that ID.
To upgrade the AMI, explicitly update that parameter. Instance refresh and managed
draining replace the hosts; occupied hosts are protected from ordinary scale-in.

## First ARM deployment

The first transition accepts downtime. Use **AWS → Run workflow → main**, with
`migrate` checked and `AWS_DEPLOY_ENABLED=true`. Normal pushes intentionally stop
before infrastructure changes until this migration has been prepared.

After the ARM image passes checks, [migrate.py](migrate.py) performs these steps:

1. Check the account, backend resources and target-group export consumers.
2. Save the applied templates and stack parameters in the `before-arm` artifact.
3. Make only the old NAT, EIP, default private routes, target group and backend
   service disposable. Database retention and its stack policy stay intact.
4. Delete the backend stack, stopping the service and releasing its imports.

The workflow then deploys foundation and backend as usual. Foundation removes the
NAT and EIP, replaces the IP target group with an instance target group, and moves
the hosts to ARM/public subnets. Backend is recreated with the tested ARM image.
The removed resources have `Delete` policies before removal, so they do not remain
as retained, billable resources. Old retained task definitions contain no running
compute.

The migration runs with the infrastructure credentials. The external permission
stack must allow reading both templates, `cloudformation:ListImports`, updating
and deleting the backend stack, and passing its existing execution role. Normal
foundation deployment also needs `ssm:GetParameter` on the public ECS ARM AMI path.
Review these operator-managed permissions before the first run.

If migration preparation fails before stack deletion, the old service remains.
If the backend stack was deleted, fix the failed step and rerun with **migrate
unchecked**. The preparation marker and saved templates allow recovery without
repeating the deletion. Do not run overlapping manual stack operations.

To restore the former architecture after a failed transition, first remove any
recreated ARM backend service and stack to release its imports. Use an operator
session to deploy `foundation-before.json` from the artifact with its saved
parameters, then `backend-before.json` with its saved image URI and parameters.
Restore foundation first: an x86 image cannot run on the new ARM hosts. This
recreates the retired NAT infrastructure and resumes its charges. RDS is preserved
in either direction. Once the ARM release succeeds, use ordinary image rollback
below and leave `migrate` unchecked.

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

The workflow runs `cfn-lint` on both templates and tests the migration's destructive
boundaries without AWS access. An `ubuntu-24.04-arm` runner builds and tests the
ARM image with a fresh PostgreSQL database. The backend has the same 1 GiB limit
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

Keep existing resource logical IDs and cross-stack export names. The first ARM
migration releases backend imports before replacing the target group. EC2
capacity limits live in foundation; the container count is `DesiredCount` in backend.

The database keeps encryption, backups, deletion protection and retention.
[stack-policy.json](foundation/stack-policy.json) blocks its replacement and removal
during foundation updates.

Backend tables are declared in [models.py](../apps/backend/app/models.py).
Startup creates missing tables; it does not alter existing columns.
SQL schema changes need their own migration before deploying code that requires them.

CloudFormation handles failed stack updates; ECS rolls back failed container
rollouts. A failed public endpoint check fails the workflow for investigation.

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
