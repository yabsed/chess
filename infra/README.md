# Infrastructure

`foundation.yaml` owns the network, private ALB, CloudFront, ECS compute, ECR,
RDS, secrets, logs, and deployment policies. `backend.yaml` owns the ECS service
and task definition. Application deployments update only the backend stack.
`deployment-access.yaml` separately owns the infrastructure caller policy and
CloudFormation execution role; the infrastructure workflow cannot update that stack.

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

IAM requires the configured execution role when planning. Apply verifies the stack,
commit, template hash, and change set status.
The workflow accepts only modifications without replacement to existing non-IAM
resources. New resources, removals, replacements, and IAM changes require a reviewed
operator change and, where needed, an updated execution-role allowlist. Database
removal or replacement requires an explicit data migration.
`stack-policy.json` also prevents CloudFormation from replacing or removing the
database during execution, including updates started outside this workflow.
After the foundation completes, the same workflow calls Backend at that commit.
Foundation and backend writes share a concurrency group. A backend run whose
foundation hash differs from the applied template stops before changing ECS.
This handles a commit containing both infrastructure and application changes.

An unchanged template produces a successful plan with "Nothing to apply" in the
run summary. Its empty change set cannot be applied. Other planning errors remain
failures, with change-set and validation details uploaded when available.

## GitHub credentials and bootstrap

The four repository secrets are two different key pairs:

| Secret | IAM user | Purpose |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | `chess-github-deploy` | Backend ECR push and deployment |
| `AWS_SECRET_ACCESS_KEY` | `chess-github-deploy` | Secret half of that same key pair |
| `AWS_INFRA_ACCESS_KEY_ID` | `chess-github-infra` | Foundation change sets |
| `AWS_INFRA_SECRET_ACCESS_KEY` | `chess-github-infra` | Secret half of that same key pair |

OIDC is not used. The infrastructure user is API-only and has no console password.
It can create and execute change sets for the production foundation and pass only
`chess-foundation-cloudformation` to CloudFormation. It has no direct EC2, RDS, IAM
administration, or Secrets Manager access. The execution role has read and update
permissions scoped to existing foundation resources, plus the EC2 launch permissions
needed to update its ASG. IAM policy and trust changes are deliberately excluded:
rewriting a passable role would otherwise let the workflow escalate its privileges.
The ASG must use a numbered launch-template version.

These are still production credentials: foundation updates can change networking,
compute, and database settings. Keep write access to this repository restricted.
Explicit reusable-workflow secret arguments prevent accidentally inheriting extra
credentials; they do not isolate repository secrets from other trusted workflow code.
The execution role can read the database URL when CloudFormation reads the secret
resource. Its value must never appear in workflow output, parameters, or artifacts.

The bootstrap is `chess-deployment-access` in the production account and region.
`deployment-access.parameters.json` records the reviewed existing-resource ARN
allowlist; these are resource identifiers, not credential values. Adding a resource
or changing IAM requires a trusted operator to review both the foundation change
and the bootstrap permissions. Never broaden these to administrator access merely
to make a deployment pass.

The API-only user is created directly with IAM before deploying the bootstrap:
this account blocks the login-profile probes used by CloudFormation's IAM user
resource. Its policy is managed by the bootstrap template. Access keys stay outside
CloudFormation entirely. Create a key for `chess-github-infra` and send its two values
directly to the matching GitHub Secrets using `gh secret set` through standard input
or GitHub's secret editor. Do not commit them, print them, or put them in shell arguments.
Both values must come from the same key; the backend user's existing key is unrelated.

For rotation, create a second key on the same user, replace both infrastructure
secrets, and run **Infrastructure → plan** on `main`. After it succeeds, deactivate
and delete the previous key. GitHub cannot reveal a saved secret's original value.
`SSH_PRIVATE_KEY` and `SSH_KNOWN_HOSTS` are not used by these AWS workflows.

No GUI-only step is required. An operator can also create, review, and execute a
CloudFormation change set using an authenticated local AWS session.

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
read-only containers, rolling-deployment protection, and key/IAM boundaries. They complement the
CloudFormation schema check and AWS change-set validation.
