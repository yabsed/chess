# Infrastructure

`foundation.yaml` owns the network, private ALB, CloudFront, ECS compute, ECR,
RDS, secrets, logs, and deployment policies. `backend.yaml` owns the ECS service
and task definition. Application deployments update only the backend stack.

The production account, region, and stack names live in `production.env`.
The foundation keeps the existing console-created stack name and its five
original logical IDs. Renaming those resources is a migration, not formatting.

## Deploy the backend

Merging backend changes into `main` builds an image, runs it with PostgreSQL,
checks HTTP and WebSockets, pushes the checked image, and deploys its ECR digest
through CloudFormation. The workflow verifies the running task definition and
image digest, then checks the public endpoint. A failed ECS rollout uses the
deployment circuit breaker. Failed post-deployment checks redeploy the previous
template and parameters; the workflow remains failed so the bad release is visible.

GitHub Secrets `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` belong to the
existing `chess-github-deploy` IAM user. Its policy is defined in the foundation.
It can push this image and deploy the backend stack, but cannot change the
foundation, read the database secret, or update ECS directly.

Keep the repository variable `AWS_DEPLOY_ENABLED=true` to enable deployment.
Setting it to `false` keeps validation and image checks running without deploying.
The frontend's Pages workflow still uses `VITE_BACKEND_URL`.

To deploy an existing digest from a trusted local session:

```sh
set -a
. infra/production.env
set +a
bash infra/deploy.sh '<repository-uri>@sha256:<digest>'
```

Re-running Backend builds a new candidate. To roll back code, revert the commit
and let CD deploy it. To restore a previous release without rebuilding, check out
that release's `backend.yaml` and use its recorded digest with `deploy.sh`.
Keep the current foundation file: an app rollback must not undo the infrastructure.

## Change the foundation

1. Review and merge the template change.
2. Run **Infrastructure → plan** on `main`.
3. Read the change list and validation artifact. Check replacements in particular.
4. Run **Infrastructure → apply**, supplying the exact change set ARN.

Apply verifies the stack, commit, template hash, and change set status. Database
removal or replacement is refused; those require an explicit data migration.
`stack-policy.json` also prevents CloudFormation from replacing or removing the
database during execution, including updates started outside this workflow.
After the foundation completes, the same workflow calls Backend at that commit.
Foundation and backend writes share a concurrency group. A backend run whose
foundation hash differs from the applied template stops before changing ECS.
This handles a commit containing both infrastructure and application changes.

The infrastructure workflow needs separate operator credentials in
`AWS_INFRA_ACCESS_KEY_ID` and `AWS_INFRA_SECRET_ACCESS_KEY`. Do not put an
administrator key in the backend secrets or reuse its restricted IAM user here.
An operator can also create, review, and execute the same CloudFormation change
set with a local AWS session. No console-only setting is required for stack changes.

## Capacity and data

The initial adoption preserves one backend task, a Single-AZ database, and one
NAT gateway. It is not an end-to-end multi-AZ service. Two EC2 instances alone do
not give the application two replicas. Before increasing task count or requiring
one task per host, increase the ASG's maximum enough to fit a rolling deployment.

ECS managed scaling owns the ASG's desired capacity. The template owns its bounds,
launch template, and AMI. AMI changes are explicit; `AutoScalingInstanceRefresh`
replaces hosts and ECS managed draining moves their tasks. The warmup period must
cover ECS registration. A successful EC2 health check alone does not prove the
application is ready; verify service health after a host update.

RDS is encrypted and private, with one day of automated backups and deletion
protection. AWS rejected seven-day retention under this account's free plan;
increase it after that account restriction is removed. The database, secret,
image repository, and logs are retained on stack
deletion/replacement. Retention is not a migration: a new empty database must never
silently replace the current endpoint. Restoring a backup creates another database;
check its contents before changing application credentials or traffic.

The current secret contains the complete PostgreSQL URL. Only its identity is
managed by the template; its value is never an output or a GitHub secret. The ECS
execution role injects it at task startup. Updating a secret does not update running
containers; rotate credentials with a coordinated application redeployment.

Application startup establishes the initial schema. Later schema migrations must
remain compatible with the previous app release. Container rollback does not roll
back database writes or schema changes.

## Adoption and new environments

The first import adds existing resources without changing their settings. It is
followed by a separate update that connects references, adds exports and the
backend deployment role, and applies the reviewed protection settings. Task
definition and service are imported into the backend stack before its first release.
Run drift detection after import; inspect unsupported resource types directly.
Default VPC resources, AWS service-linked roles, and the CloudFront-managed security
group remain service-managed. The API-only IAM user's policy is managed here; its
identity and access keys are the authentication bootstrap boundary.

This first version adopts the existing database and URL secret. It does not yet
bootstrap database credentials in an empty account: RDS master credentials, an
application database user, and the URL secret must be prepared before importing
that database. Do not run a blind CREATE against these production templates.
A clean-account bootstrap needs the planned separate credential inputs and a
one-off database setup task; the normal backend release path is already shared.

No CloudTrail trail was configured in the inspected account during adoption.
CloudFormation events and CloudTrail event history are available, but this change
does not add long-term audit-log storage.

## Validate locally

```sh
python3 -m venv /tmp/chess-infra
/tmp/chess-infra/bin/pip install cfn-lint==1.60.0 guardpycfn==0.1.0
PATH="/tmp/chess-infra/bin:$PATH" make infra-check
```

The guard rules check private encrypted databases, retention, secret handling,
read-only containers, and rolling-deployment protection. They complement the
CloudFormation schema check and AWS change-set validation.
