# Deployment access

`access.yaml` belongs to the existing `chess-deployment-access` stack. It scopes the
`chess-github-infra` user and the foundation execution role to the project's existing
resources. Normal application and infrastructure workflows do not update this stack.

## Update permissions

From the repository root, use an operator session with AWS CLI v2. Set
`AWS_DEFAULT_REGION` to the stack's region (`ap-southeast-2` for production).
The deployed stack already stores its resource parameters; this command reuses them.

```sh
aws sts get-caller-identity

aws cloudformation deploy \
  --stack-name chess-deployment-access \
  --template-file infra/bootstrap/access.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-disable-rollback \
  --no-fail-on-empty-changeset
```

Check the account before deploying. The command waits for completion.

## Change resource scopes

When resource identities change, update the stack's parameters too. The deployed
foundation template must expose the `Bootstrap*` outputs declared in this repo.
With `FOUNDATION_STACK` set, use jq to collect them:

```sh
umask 077

aws cloudformation describe-stacks \
  --stack-name "$FOUNDATION_STACK" \
  --query 'Stacks[0].Outputs' \
  --output json |
  jq '[
    .[]
    | select(.OutputKey | startswith("Bootstrap"))
    | {
        ParameterKey:   (.OutputKey | ltrimstr("Bootstrap")),
        ParameterValue: .OutputValue
      }
  ]' > /tmp/chess-access-parameters.json
```

Review the generated parameters against `access.yaml`: check the account, region and
complete resource list. Add
`--parameter-overrides file:///tmp/chess-access-parameters.json` to the deployment
command above. Keep exact resource scopes instead of wildcards. Adding a resource
may also require an operator to grant access before the foundation deployment.

## Credentials

An operator manages the existing API-only users and issues their keys outside
CloudFormation. Store matching infrastructure credentials as GitHub Secrets
`AWS_INFRA_ACCESS_KEY_ID` and `AWS_INFRA_SECRET_ACCESS_KEY`; keep the backend pair
separate. Never commit or print keys.

To rotate, register a new pair, verify an Infrastructure deployment, then deactivate
and delete the old pair. Restrict repository write access: trusted workflow code
can use the deployment secrets.
