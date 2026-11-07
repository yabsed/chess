# Deployment access

Use this directory when preparing or changing deployment permissions. Normal
application releases and foundation updates do not deploy this stack.

[`access.yaml`](access.yaml) belongs to the existing `chess-deployment-access`
stack. It gives the API-only `chess-github-infra` user permission to plan and apply
the foundation, passing only its dedicated CloudFormation role. The role can update
the listed resources and launch instances through a numbered version of the pinned
launch template. It cannot rewrite IAM policies or role trust. The foundation
workflow cannot change this bootstrap stack.

Foundation's `Bootstrap*` outputs provide the exact inputs for `access.yaml`.
[`parameters.py`](parameters.py) checks the deployed template against this checkout,
then validates those values against the destination parameter schema and AWS target.
The allowlist lives in the output definitions beside the resources that supply it.
The helper uses only STS and CloudFormation reads; it never changes permissions or
issues keys. The role can read the database secret for CloudFormation's resource
handler; the infrastructure user cannot read it directly.

## Update permissions

First apply the reviewed foundation template through Infrastructure **plan → apply**.
The new `Bootstrap*` outputs must exist before preparing access parameters; older
foundations are rejected. Adding these outputs does not change resources or IAM
permissions. Backend releases can continue before this output-only update.

From the repository root, use an authenticated operator session, install the
[deployment tools and settings](../README.md#run-locally), and prepare parameters
outside Git:

```sh
umask 077
python3 -m infra.bootstrap.parameters > /tmp/chess-access-parameters.json

aws cloudformation create-change-set \
  --stack-name chess-deployment-access \
  --change-set-name "configure-deployment-access-$(date +%s)" \
  --change-set-type UPDATE \
  --template-body file://infra/bootstrap/access.yaml \
  --parameters file:///tmp/chess-access-parameters.json \
  --capabilities CAPABILITY_NAMED_IAM
```

Review the resolved policy and change set before executing it. A no-change result
needs no execution. Preserve exact resource scopes, including the launch template,
security group and private subnets; do not replace them with wildcards.

The helper rejects incomplete deployments and detects stack changes during its
reads. The resulting JSON is a snapshot, not a lock. If foundation changes before
execution, regenerate the JSON and recreate and review the access change set;
an existing change set keeps the parameter values it was created with.

## Issue or rotate keys

An operator creates the API-only infrastructure user once, deploys the bootstrap,
and issues its access key outside CloudFormation. Register the matching pair as:

- `AWS_INFRA_ACCESS_KEY_ID`
- `AWS_INFRA_SECRET_ACCESS_KEY`

Send each value directly to `gh secret set` through standard input, or use GitHub's
secret editor. Never print keys or put them in arguments or files. Keep the backend
user's `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` pair separate.

To rotate, register a second pair, verify Infrastructure **plan**, then deactivate
and delete the old pair. These credentials can change production infrastructure.
Explicit secret forwarding prevents accidental inheritance; it does not isolate
repository secrets from other trusted workflow code. Restrict repository write access.
