#!/usr/bin/env bash
set -euo pipefail

local_image=
image=
if [[ $# == 1 && $1 != --* ]]; then
  local_image=$1
elif [[ $# == 2 && $1 == --digest ]]; then
  image=$2
else
  echo 'Usage: release.sh LOCAL_IMAGE | --digest ECR_DIGEST_URI' >&2
  exit 1
fi

# Check the target before publishing an image or changing a stack.
: "${AWS_REGION:?Set AWS_REGION}"
: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${FOUNDATION_STACK:?Set FOUNDATION_STACK}"
: "${BACKEND_STACK:?Set BACKEND_STACK}"

if [[ ${GITHUB_ACTIONS:-} == true ]]; then
  : "${AWS_ACCESS_KEY_ID:?Set AWS_ACCESS_KEY_ID repository secret}"
  : "${AWS_SECRET_ACCESS_KEY:?Set AWS_SECRET_ACCESS_KEY repository secret}"
fi

account=$(aws sts get-caller-identity --query Account --output text)
if [[ $account != "$AWS_ACCOUNT_ID" ]]; then
  echo 'AWS account does not match AWS_ACCOUNT_ID.' >&2
  exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

output() {
  jq -er --arg key "$2" \
    '.Stacks[0].Outputs[] | select(.OutputKey == $key) | .OutputValue' "$1"
}

aws cloudformation describe-stacks --stack-name "$FOUNDATION_STACK" > "$work/foundation.json"
aws cloudformation get-template --stack-name "$FOUNDATION_STACK" > "$work/applied.json"
python3 infra/foundation/check-applied.py "$work/applied.json"

role=$(output "$work/foundation.json" BackendDeploymentRole)
cluster=$(output "$work/foundation.json" ECSCluster)
url=$(output "$work/foundation.json" ApiUrl)
repository=$(output "$work/foundation.json" RepositoryUri)

if [[ $BACKEND_STACK != "$cluster-backend" ]]; then
  echo 'BACKEND_STACK must match the foundation cluster: <cluster>-backend.' >&2
  exit 1
fi

# Publish the tested local image. Operators may instead provide an existing digest.
if [[ -n $local_image ]]; then
  echo 'Publishing image.'
  export DOCKER_CONFIG="$work/docker"
  registry=${repository%%/*}
  commit=$(git rev-parse HEAD)
  tag="$commit-${GITHUB_RUN_ID:-$(date +%s)}-${GITHUB_RUN_ATTEMPT:-1}"

  aws ecr get-login-password |
    docker login --username AWS --password-stdin "$registry"
  docker tag "$local_image" "$repository:$tag"
  docker push --platform linux/amd64 "$repository:$tag"

  digest=$(aws ecr batch-get-image \
    --repository-name "${repository##*/}" \
    --image-ids "imageTag=$tag" \
    --query 'images[0].imageId.imageDigest' --output text)
  image="$repository@$digest"
fi

if [[ $image != "$repository"@sha256:* || ! ${image##*@} =~ ^sha256:[a-f0-9]{64}$ ]]; then
  echo 'Use a sha256 image digest from the foundation ECR repository.' >&2
  exit 1
fi

# Preserve the complete previous release, including all configuration parameters.
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

echo 'Deploying release.'
deploy infra/backend/template.yaml "FoundationStack=$FOUNDATION_STACK" "ImageUri=$image"

aws cloudformation describe-stacks --stack-name "$BACKEND_STACK" > "$work/deployed.json"
task=$(output "$work/deployed.json" TaskDefinition)
service=$(output "$work/deployed.json" Service)

echo 'Verifying ECS, HTTP and WebSockets.'
if bash infra/backend/verify.sh "$cluster" "$service" "$task" "$image" &&
   bash infra/backend/smoke.sh "$url"; then
  printf 'Deployed %s\nTask: %s\nAPI: %s\n' "$image" "$task" "$url"
  if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
    printf 'Image: %s\n\nTask: %s\n\nAPI: %s\n' "$image" "$task" "$url" >> "$GITHUB_STEP_SUMMARY"
  fi
  exit 0
fi

# Failed post-deployment checks restore the previous release and still fail CI.
echo 'Release verification failed.' >&2
if [[ $previous == false ]]; then
  echo 'This was the first release; no previous release exists.' >&2
  exit 1
fi

echo 'Restoring the previous template and parameters through CloudFormation.' >&2
mapfile -t parameters < "$work/previous-parameters"
deploy "$work/previous-template.yaml" "${parameters[@]}"

aws cloudformation describe-stacks --stack-name "$BACKEND_STACK" > "$work/restored.json"
task=$(output "$work/restored.json" TaskDefinition)
service=$(output "$work/restored.json" Service)
image=$(jq -er '.Stacks[0].Parameters[] | select(.ParameterKey == "ImageUri") | .ParameterValue' \
  "$work/previous-stack.json")

bash infra/backend/verify.sh "$cluster" "$service" "$task" "$image"
bash infra/backend/smoke.sh "$url"
echo 'Previous release restored and verified.' >&2
exit 1
