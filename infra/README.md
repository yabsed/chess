# Deployment

Three workflows have three jobs: Frontend publishes Pages, Backend releases the
application, and Infrastructure plans or applies foundation changes.

| File | Responsibility |
| --- | --- |
| `production.env.example` | Example deployment settings; actual values live in GitHub Variables |
| `foundation.yaml` | Network, traffic, database, compute and backend deployment permissions |
| `backend.yaml` | ECS task definition and service |
| `deployment-access.yaml` | Infrastructure caller policy and CloudFormation execution role |
| `access.py` | Read existing foundation resources and prepare access-stack parameters |
| `foundation.sh` | Create a plan or apply the exact reviewed plan |
| `backend.sh` | Deploy an image digest and restore the previous release if verification fails |
| `verify.sh` / `smoke.sh` | Check ECS state, running image, HTTP and WebSockets |
| `check.py` / `safety.guard` | Check template safety and compare the applied foundation |

These templates manage the existing production resources. Keep their logical IDs.
They are not an empty-account installer: the database, its credentials and the
API-only deployment users were prepared before adoption.

## Settings and keys

Both AWS workflows receive deployment settings from GitHub repository variables.
The scripts use the same environment variables locally. The expected account ID
is checked against the authenticated account before accessing deployment resources.

| GitHub repository setting | Value |
| --- | --- |
| Secret `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Existing `chess-github-deploy` key pair |
| Secret `AWS_INFRA_ACCESS_KEY_ID` / `AWS_INFRA_SECRET_ACCESS_KEY` | Separate `chess-github-infra` key pair |
| Variable `AWS_DEPLOY_ENABLED` | `true` enables backend deployment |
| Variable `AWS_REGION` | AWS region containing the foundation |
| Variable `AWS_ACCOUNT_ID` | Expected deployment account ID |
| Variable `FOUNDATION_STACK` | Existing foundation stack name |
| Variable `BACKEND_STACK` | Backend stack name, matching `<ECS cluster>-backend` |
| Variable `FOUNDATION_ROLE` | Foundation CloudFormation execution role name |
| Variable `VITE_BACKEND_URL` | Public HTTPS API origin used by the frontend |

AWS authentication uses access keys. Each ID and secret must come from the same
issuance. PR checks need no AWS keys or deployment variables. Infrastructure always
needs its own pair; `AWS_DEPLOY_ENABLED` controls only Backend. The reusable Backend workflow receives
only the backend pair. Existing SSH secrets are unused.

The database URL stays in Secrets Manager. ECS injects it at task startup; changing
its value requires a coordinated application redeployment. It never belongs in
GitHub variables, template outputs, logs or artifacts.

For local deployment, copy `infra/production.env.example` to
`infra/production.env`, fill in the target environment, and export it explicitly:

```sh
set -a
source infra/production.env
set +a
```

The real file is ignored by Git. To register those same non-secret settings for CI:

```sh
gh variable set --env-file infra/production.env
```

Variables separate deployment configuration from source code; they are not secret
storage and are not masked in logs. Deployment logs and plan artifacts can contain
account IDs and resource ARNs. Before making this repository public, also review
its Git history and past Actions runs: removing a value from the current files does
not remove earlier copies.

## Backend

Merging to `main` builds and tests the container with PostgreSQL, checks HTTP and
WebSockets, and publishes the checked image to ECR. CloudFormation deploys its
immutable digest. Verification checks the actual ECS task, image digest and public
endpoint. ECS handles failed rollouts; failed post-deployment checks restore the
previous template and all its parameters. The run still fails to flag the bad release.

Backend stops if the checked-in foundation differs from the applied template.
Comments, layout, root documentation and the obsolete revision marker are ignored;
resource settings and resource metadata are compared. Foundation and backend writes
share one concurrency group.

To deploy a known digest from an authenticated operator session at the repository root:

```sh
bash infra/backend.sh '<repository-uri>@sha256:<digest>'
```

Revert an application commit to release an older version through CI. To restore an
existing image without rebuilding, use its digest and matching `backend.yaml` with
`backend.sh`. Keep the current foundation. Container rollback does not undo database
writes; schema migrations must remain compatible with the previous application.

## Foundation

1. Review and merge the template change.
2. Run **Infrastructure → plan** on `main`.
3. Read the run summary and `infrastructure-plan` artifact, including validation events.
4. Run **Infrastructure → apply** with that exact change set ARN.

Apply checks the stack, commit, template hash and change set status. It allows only
updates to existing non-IAM resources without replacement. Additions, removals,
replacements and IAM changes need an operator-reviewed migration and any necessary
execution-role changes. The stack policy also blocks database replacement and removal.
After apply, Infrastructure calls Backend at the same commit.

A failed change set with AWS's no-change reason is a successful no-op plan. Other
errors fail the run. A successful change set may contain no resource changes when
only stack-level settings change; review its description before applying it.

## Deployment access

`chess-deployment-access` owns `deployment-access.yaml`. Account and region come
from CloudFormation pseudo parameters. Generated resource identifiers are explicit
stack parameters, prepared from the existing foundation by `access.py`; named
project resources remain readable in the policy. The foundation workflow cannot
update this bootstrap stack or rewrite IAM policies and role trust. Its caller can plan and
apply the foundation and pass only `chess-foundation-cloudformation` to CloudFormation.
The execution role can update the listed resources and launch the ASG's instances
using a numbered version of its pinned launch template. It can read the database
secret for CloudFormation's resource handler; the caller cannot read it directly.

From an authenticated operator session with the deployment settings exported,
prepare parameters and a change set:

```sh
umask 077
python3 infra/access.py > /tmp/chess-access-parameters.json
aws cloudformation create-change-set \
  --stack-name chess-deployment-access \
  --change-set-name configure-deployment-access \
  --change-set-type UPDATE \
  --template-body file://infra/deployment-access.yaml \
  --parameters file:///tmp/chess-access-parameters.json \
  --capabilities CAPABILITY_NAMED_IAM
```

Use a fresh change-set name for each update. Review the resolved policy and change
set before executing it. A no-change result needs no execution. `access.py` only reads the allowlisted logical resources;
it never changes permissions or issues keys. Keep the generated file outside Git.
The resulting policy must still name exact resources, including the pinned launch
template, security group and private subnets. Do not replace them with wildcards.

An operator creates the API-only infrastructure user once, deploys this bootstrap,
and issues its key outside CloudFormation. Send both values directly to `gh secret
set` through standard input or use GitHub's secret editor. Never print keys or put
them in arguments or files. To rotate, register a second pair, verify Infrastructure
`plan`, then deactivate and delete the old pair. Keep the backend key separate.

These credentials can change production infrastructure. Explicit secret forwarding
prevents accidental inheritance; it does not isolate repository secrets from other
trusted workflow code. Restrict repository write access accordingly.

## Data and capacity

Production currently has one application task, a Single-AZ database and one NAT
gateway. More replicas need enough ASG capacity for a rolling deployment. ECS owns
desired host capacity; the template owns its bounds, launch template and AMI. Host
updates use instance refresh and managed draining. Verify application health afterward.

RDS is private, encrypted and deletion-protected. The database, URL secret, ECR
repository and logs are retained on stack deletion or replacement. Retention is not
a migration: restoring a backup creates another database whose contents must be
checked before traffic moves. Backup retention is one day because this account's
free plan rejected seven days. No long-term CloudTrail trail was added during adoption.

## Local validation

```sh
python3 -m venv /tmp/chess-infra
/tmp/chess-infra/bin/pip install -r infra/requirements.txt
PATH="/tmp/chess-infra/bin:$PATH" make infra-check
```

Schema and Guard checks cover database protection, secret handling, container
restrictions and deployment permissions. AWS plan validation checks the live account.
