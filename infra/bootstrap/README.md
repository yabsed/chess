# Deployment access

`access.yaml` belongs to the existing `chess-deployment-access` stack. It scopes the
`chess-github-infra` user and the foundation execution role to the project's existing
resources. Normal application and infrastructure workflows do not update this stack.

## Update permissions

Apply this stack before using the Infrastructure workflow: it grants the
`cloudformation:GetTemplateSummary` permission needed by `aws cloudformation deploy`.

Existing parameter values are reused only for parameters already present in the
deployed stack. The original access template had only `FoundationStack`; upgrading
it requires the other ten values from its existing resource allowlist once.

Use an authenticated operator session with AWS CLI v2 and jq. The foundation's
`Bootstrap*` outputs supply the existing resource identities after a foundation
template containing those outputs has been deployed. Older foundations do not
have them; use the resource values in their deployed access template for the first
upgrade. From the repository root, with `AWS_DEFAULT_REGION` and `FOUNDATION_STACK`
set:

```sh
aws sts get-caller-identity
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
complete resource list. Keep the exact resource scopes rather than replacing them
with wildcards. Then deploy:

```sh
aws cloudformation deploy \
  --stack-name chess-deployment-access \
  --template-file infra/bootstrap/access.yaml \
  --parameter-overrides file:///tmp/chess-access-parameters.json \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-disable-rollback \
  --no-fail-on-empty-changeset
```

The command waits for completion. If foundation resource identities change later,
regenerate these parameters from its outputs and deploy the access stack again.

## Credentials

An operator manages the existing API-only users and issues their keys outside
CloudFormation. Store matching infrastructure credentials as GitHub Secrets
`AWS_INFRA_ACCESS_KEY_ID` and `AWS_INFRA_SECRET_ACCESS_KEY`; keep the backend pair
separate. Never commit or print keys.

To rotate, register a new pair, verify an Infrastructure deployment, then deactivate
and delete the old pair. Restrict repository write access: trusted workflow code
can use the deployment secrets.
