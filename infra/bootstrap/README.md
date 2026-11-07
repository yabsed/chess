# Deployment access

`access.yaml` belongs to the existing `chess-deployment-access` stack. It scopes the
`chess-github-infra` user and the foundation execution role to the project's existing
resources. Normal application and infrastructure workflows do not update this stack.

## Update permissions

Use an authenticated operator session with AWS CLI v2 and jq. Apply the reviewed
foundation first so its `Bootstrap*` outputs describe the intended resource identities.
From the repository root, with `AWS_DEFAULT_REGION` and `FOUNDATION_STACK` set:

```sh
aws sts get-caller-identity
umask 077
aws cloudformation describe-stacks --stack-name "$FOUNDATION_STACK" \
  --query 'Stacks[0].Outputs' --output json |
  jq '[.[] | select(.OutputKey | startswith("Bootstrap")) |
    {ParameterKey: (.OutputKey | ltrimstr("Bootstrap")), ParameterValue: .OutputValue}]' \
  > /tmp/chess-access-parameters.json
```

Review the generated parameters against `access.yaml`: check the account, region and
complete resource list. Keep the exact resource scopes rather than replacing them
with wildcards. Then create a change set:

```sh
aws cloudformation create-change-set \
  --stack-name chess-deployment-access \
  --change-set-name "configure-deployment-access-$(date +%s)" \
  --change-set-type UPDATE \
  --template-body file://infra/bootstrap/access.yaml \
  --parameters file:///tmp/chess-access-parameters.json \
  --capabilities CAPABILITY_NAMED_IAM
```

Review and execute that change set through CloudFormation. If foundation resources
change before execution, regenerate the parameters and create a new plan. There is
no additional program comparing template versions or validating these inputs;
CloudFormation validates the parameter schema and the operator reviews the scope.

## Credentials

An operator manages the existing API-only users and issues their keys outside
CloudFormation. Store matching infrastructure credentials as GitHub Secrets
`AWS_INFRA_ACCESS_KEY_ID` and `AWS_INFRA_SECRET_ACCESS_KEY`; keep the backend pair
separate. Never commit or print keys.

To rotate, register a new pair, verify Infrastructure **plan**, then deactivate and
delete the old pair. Restrict repository write access: trusted workflow code can
use the deployment secrets.
