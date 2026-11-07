#!/usr/bin/env bash
set -euo pipefail

image=${1:?Pass an ECR image digest URI}

set -a
source infra/production.env
set +a

account=$(aws sts get-caller-identity --query Account --output text)
[[ $account == "$AWS_ACCOUNT_ID" ]]

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

aws cloudformation describe-stacks --stack-name "$FOUNDATION_STACK" > "$work/foundation.json"
output() {
  jq -er --arg key "$1" '.Stacks[0].Outputs[] | select(.OutputKey == $key) | .OutputValue' \
    "$work/foundation.json"
}

# Compare CloudFormation values, so comments and spacing never block a release.
aws cloudformation get-template --stack-name "$FOUNDATION_STACK" > "$work/applied.json"
python3 infra/check.py "$work/applied.json"

role=$(output BackendDeploymentRole)
cluster=$(output ECSCluster)
url=$(output ApiUrl)
repository=$(output RepositoryUri)
[[ $image == "$repository"@sha256:* && ${image##*@} =~ ^sha256:[a-f0-9]{64}$ ]]
[[ $BACKEND_STACK == "$cluster-backend" ]]

# Save the complete previous release, including configuration, before changing it.
previous=false
if aws cloudformation describe-stacks --stack-name "$BACKEND_STACK" \
    > "$work/previous-stack.json" 2> "$work/previous-error"; then
  previous=true
  aws cloudformation get-template --stack-name "$BACKEND_STACK" --output json |
    jq -r '.TemplateBody | if type == "string" then . else tojson end' > "$work/previous-template.yaml"
  jq -r '.Stacks[0].Parameters[] | "\(.ParameterKey)=\(.ParameterValue)"' \
    "$work/previous-stack.json" > "$work/previous-parameters"
elif ! grep -q 'does not exist' "$work/previous-error"; then
  cat "$work/previous-error" >&2
  exit 1
fi

deploy() {
  local template=$1
  shift

  aws cloudformation deploy \
    --stack-name "$BACKEND_STACK" \
    --template-file "$template" \
    --role-arn "$role" \
    --parameter-overrides "$@" \
    --no-fail-on-empty-changeset
}

deploy infra/backend.yaml "FoundationStack=$FOUNDATION_STACK" "ImageUri=$image"

aws cloudformation describe-stacks --stack-name "$BACKEND_STACK" > "$work/deployed.json"
task=$(jq -er '.Stacks[0].Outputs[] | select(.OutputKey == "TaskDefinition") | .OutputValue' "$work/deployed.json")
service=$(jq -er '.Stacks[0].Outputs[] | select(.OutputKey == "Service") | .OutputValue' "$work/deployed.json")

if bash infra/verify.sh "$cluster" "$service" "$task" "$image" && bash infra/smoke.sh "$url"; then
  printf 'Deployed %s\nTask: %s\nAPI: %s\n' "$image" "$task" "$url"
  if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
    printf 'Image: %s\n\nTask: %s\n\nAPI: %s\n' "$image" "$task" "$url" >> "$GITHUB_STEP_SUMMARY"
  fi
  exit 0
fi

echo 'Release verification failed.' >&2
if [[ $previous == true ]]; then
  mapfile -t parameters < "$work/previous-parameters"
  echo 'Restoring the previous template and parameters through CloudFormation.' >&2
  deploy "$work/previous-template.yaml" "${parameters[@]}"

  aws cloudformation describe-stacks --stack-name "$BACKEND_STACK" > "$work/restored.json"
  restored_task=$(jq -er '.Stacks[0].Outputs[] | select(.OutputKey == "TaskDefinition") | .OutputValue' "$work/restored.json")
  restored_image=$(jq -er '.Stacks[0].Parameters[] | select(.ParameterKey == "ImageUri") | .ParameterValue' "$work/previous-stack.json")
  bash infra/verify.sh "$cluster" "$service" "$restored_task" "$restored_image"
  bash infra/smoke.sh "$url"
  echo 'Previous release restored and verified.' >&2
else
  echo 'This was the first release; no previous release exists.' >&2
fi
exit 1
