#!/usr/bin/env bash
set -euo pipefail

# Account

account=$(
  aws sts get-caller-identity \
    --query Account \
    --output text
)

test "$account" = "$AWS_ACCOUNT_ID"


# Foundation outputs

outputs=$(
  aws cloudformation describe-stacks \
    --stack-name "$FOUNDATION_STACK" \
    --query 'Stacks[0].Outputs' \
    --output json |
  jq 'map({(.OutputKey): .OutputValue}) | add'
)

repository=$(jq -er '.RepositoryUri' <<< "$outputs")
role=$(jq -er '.BackendDeploymentRole' <<< "$outputs")
cluster=$(jq -er '.ECSCluster' <<< "$outputs")
api_url=$(jq -er '.ApiUrl' <<< "$outputs")

test "$BACKEND_STACK" = "$cluster-backend"


# Publish the tested image

export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d)
trap 'rm -rf "$DOCKER_CONFIG"' EXIT

aws ecr get-login-password |
  docker login \
    --username AWS \
    --password-stdin "${repository%%/*}"

tag="$GITHUB_SHA-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
docker tag "$IMAGE" "$repository:$tag"
docker push --platform linux/amd64 "$repository:$tag"

digest=$(
  aws ecr batch-get-image \
    --repository-name "${repository#*/}" \
    --image-ids "imageTag=$tag" \
    --query 'images[0].imageId.imageDigest' \
    --output text
)

[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
image_uri="$repository@$digest"


# Deploy and wait for ECS
# ECS rolls back a failed rollout.

aws cloudformation deploy \
  --stack-name "$BACKEND_STACK" \
  --template-file infra/backend/template.yaml \
  --role-arn "$role" \
  --parameter-overrides \
    "FoundationStack=$FOUNDATION_STACK" \
    "ImageUri=$image_uri" \
  --no-fail-on-empty-changeset


# Confirm the service uses the deployed task definition

outputs=$(
  aws cloudformation describe-stacks \
    --stack-name "$BACKEND_STACK" \
    --query 'Stacks[0].Outputs' \
    --output json |
  jq 'map({(.OutputKey): .OutputValue}) | add'
)

expected_task=$(jq -er '.TaskDefinition' <<< "$outputs")
service=$(jq -er '.Service' <<< "$outputs")

service_task=$(
  aws ecs describe-services \
    --cluster "$cluster" \
    --services "$service" \
    --query 'services[0].taskDefinition' \
    --output text
)

test "$service_task" = "$expected_task"


# Check the public API

python3 -m infra.backend.smoke "$api_url"

printf 'Image: %s\n\nAPI: %s\n' "$image_uri" "$api_url" >> "$GITHUB_STEP_SUMMARY"
