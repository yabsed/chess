# Deployment

Workflows show when and in what order to run each step. Shell scripts contain the
AWS commands. CloudFormation manages resources; ECS starts and replaces containers.

| File | Responsibility |
| --- | --- |
| [backend/deploy.sh](backend/deploy.sh) | Publish the tested image, deploy it, and check the API |
| [foundation/deploy.sh](foundation/deploy.sh) | Check the account, protect the database, and deploy foundation |
| [foundation/template.yaml](foundation/template.yaml) | Network, EC2 capacity, ALB, CloudFront, RDS, ECR and roles |
| [backend/template.yaml](backend/template.yaml) | ECS task definition and service |
| [bootstrap/access.yaml](bootstrap/access.yaml) | Infrastructure deployment permissions |
| [backend/smoke.py](backend/smoke.py) | HTTP health and Engine.IO WebSocket handshake |

The templates manage existing production resources. Preserve their logical IDs.
The database, its credentials and deployment users predate CloudFormation; this
is not an installer for an empty account.

Read a workflow first, then its script, then the template it deploys. Each script
runs from top to bottom; there is no shared deployment framework. Commands run from
the repository root. Keep one command option per line and blank lines between tasks.
In YAML, use two blank lines between resources and one between related groups of
properties. Align neighboring values within a group; keep long values on their own
lines. Preserve that layout when editing instead of compressing it into fewer lines.

## Settings

| GitHub repository setting | Value |
| --- | --- |
| Secret `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Backend deployment key pair |
| Secret `AWS_INFRA_ACCESS_KEY_ID` / `AWS_INFRA_SECRET_ACCESS_KEY` | Infrastructure deployment key pair |
| Variable `AWS_DEPLOY_ENABLED` | `true` enables backend deployment |
| Variable `AWS_REGION` | Deployment region |
| Variable `AWS_ACCOUNT_ID` | Expected AWS account |
| Variable `FOUNDATION_STACK` | Existing foundation stack |
| Variable `BACKEND_STACK` | Backend stack, named `<ECS cluster>-backend` |
| Variable `FOUNDATION_ROLE` | Foundation CloudFormation execution role name |
| Variable `VITE_BACKEND_URL` | Public HTTPS API origin for the frontend |

Each key ID and secret must belong to the same issuance. Workflows check the AWS
account before making changes. The database URL stays in Secrets Manager and is
injected at task startup. Repository variables and workflow logs are not secret
storage. See [bootstrap](bootstrap/README.md) for deployment permissions.

## Backend

Merge backend changes to `main`. The [Backend workflow](../.github/workflows/backend.yml):

1. Checks infrastructure templates and tests the smoke command.
2. Builds the image, starts it with a fresh PostgreSQL database, and checks HTTP and WebSockets.
3. Pushes that image to ECR and passes its immutable digest to `aws cloudformation deploy`.
4. Confirms the ECS service uses the resulting task definition and checks the public endpoint.

Pull requests stop after the local checks. Releases require `AWS_DEPLOY_ENABLED=true`.
ECS's deployment circuit breaker and CloudFormation handle failed rollouts.
A failed public endpoint check fails the workflow for investigation; it does not
trigger a second automatic deployment. The successful run summary records the image digest.

## Infrastructure

Review template changes in Git and merge them to `main`. In Actions, select the
[Infrastructure workflow](../.github/workflows/infra.yml) and **Run workflow** on
`main`. There are no inputs. The run:

1. Checks infrastructure templates and tests the smoke command.
2. Checks the AWS account and sets the database protection policy.
3. Runs `aws cloudformation deploy` and waits for completion.

Pull requests and manual runs on other branches stop after the checks. A push to
`main` does not deploy infrastructure. Each manual run deploys its checked-out
template with the stack's existing parameters. An unchanged template succeeds
without an update; other AWS errors fail the run. CloudFormation handles rollback
when an update fails. Deployment includes no separate change-set review.

The deployment user needs `cloudformation:GetTemplateSummary` for this command.
An operator must apply the [bootstrap permissions](bootstrap/README.md) before
using this workflow for the first time after switching to `deploy`.

The existing infrastructure role is limited to updates of allowlisted resources.
Resource additions and IAM changes may require an operator with the appropriate
permissions. The database stack policy blocks replacement and removal; deletion
protection and retention settings remain in the template.

Infrastructure and backend releases share a deployment concurrency group, but run
independently. Deploying infrastructure does not start Backend. When an app change
needs new infrastructure, merge and deploy the infrastructure first, then merge the
app change or manually run Backend on `main`. The workflow does not compare the
entire foundation template before releasing an app.

## Roll back an application

Use the previous successful run's image digest with the current compatible backend
template. From the repository root, with AWS CLI v2 and deployment credentials:

```sh
set -a
source infra/production.env
set +a
export AWS_DEFAULT_REGION="$AWS_REGION"

aws sts get-caller-identity  # Check the account before deploying.
role=$(aws cloudformation describe-stacks --stack-name "$FOUNDATION_STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BackendDeploymentRole'].OutputValue | [0]" --output text)

aws cloudformation deploy \
  --stack-name "$BACKEND_STACK" \
  --template-file infra/backend/template.yaml \
  --role-arn "$role" \
  --parameter-overrides "FoundationStack=$FOUNDATION_STACK" "ImageUri=$PREVIOUS_IMAGE" \
  --no-fail-on-empty-changeset
```

Set `PREVIOUS_IMAGE` to the full `repository@sha256:...` URI first. Check the public
endpoint afterward with the smoke command below. This changes the application image;
it does not restore older infrastructure or undo SQL changes. Database changes must
remain compatible with both app versions during a rolling deployment and rollback.

## Local checks

Use Python 3.11+, curl and a virtual environment. Deployment workflows also use AWS
CLI v2, jq and Docker, provided by the GitHub runner.

```sh
python3 -m venv /tmp/chess-infra
source /tmp/chess-infra/bin/activate
pip install -r infra/requirements.txt
make infra-check
python3 -m infra.backend.smoke http://127.0.0.1:8000
```

`infra-check` runs cfn-lint, the safety rules in `safety.guard`, and three smoke-command
tests against a local HTTP/WebSocket server. It uses no AWS credentials. The Backend
workflow additionally checks the real application with a fresh PostgreSQL database;
this does not test migrations of an existing production database.

For local AWS commands, copy `production.env.example` to the ignored `production.env`
and fill in the target settings. The workflows use GitHub variables, not this file.

Production has one application task, a Single-AZ database and one NAT gateway.
The ASG's capacity must accommodate old and new tasks during rolling deployment.
ECS owns desired host capacity; the template owns the limits and launch template.
