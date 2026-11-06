# Deployment

Read the workflow for the order, `deploy.sh` for the commands, and `template.yaml`
for the resources.

| Deployment | Workflow | Template |
| --- | --- | --- |
| Foundation | [Infrastructure](../.github/workflows/infra.yml) | [Network, EC2, load balancer, database and roles](foundation/template.yaml) |
| Backend | [Backend](../.github/workflows/backend.yml) | [Container settings and ECS service](backend/template.yaml) |

Backend changes on `main` deploy automatically when `AWS_DEPLOY_ENABLED=true`.
For foundation changes, merge to `main`, then run **Infrastructure** in GitHub Actions.
A push alone does not deploy foundation. Pull requests only run checks.

Deploy required foundation changes before releasing the backend. The workflows
share a concurrency group, but neither starts the other.

## Settings

The workflows read GitHub variables and Secrets.

| Setting | Purpose |
| --- | --- |
| Secret `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Backend deployment credentials |
| Secret `AWS_INFRA_ACCESS_KEY_ID` / `AWS_INFRA_SECRET_ACCESS_KEY` | Foundation deployment credentials |
| Variable `AWS_DEPLOY_ENABLED` | `true` enables backend releases |
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

Both workflows run `cfn-lint` on the two templates. Backend also builds the image,
starts it with a fresh PostgreSQL database, and checks HTTP and the Engine.IO
WebSocket handshake. After deployment it checks the service's task definition
and the public endpoint.

To run the checks locally, use Python 3.11+ and curl:

```sh
python3 -m venv /tmp/chess-infra
source /tmp/chess-infra/bin/activate
pip install -r infra/requirements.txt
make infra-check
python3 -m infra.backend.smoke http://127.0.0.1:8000
```

The smoke command needs a running backend. CI uses Docker Compose to start it.
These checks do not test SQL migrations or enforce custom infrastructure policies.

## Resource changes

Keep existing resource logical IDs and cross-stack exports. EC2 capacity limits
live in foundation; the container count is `DesiredCount` in backend.

The database keeps encryption, backups, deletion protection and retention.
[stack-policy.json](foundation/stack-policy.json) blocks its replacement and removal
during foundation updates. SQL schema changes need their own migration before
deploying code that requires them.

CloudFormation handles failed stack updates; ECS rolls back failed container
rollouts. A failed public endpoint check fails the workflow for investigation.

## Roll back an image

Use an operator session. Set `AWS_DEFAULT_REGION`, `FOUNDATION_STACK`,
`BACKEND_STACK` and `PREVIOUS_IMAGE` (the earlier `repository@sha256:...` URI).
The deployed image URI is recorded in each successful Backend run's summary.

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
