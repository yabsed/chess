#!/usr/bin/env bash
set -euo pipefail

# Check the account

account=$(
  aws sts get-caller-identity \
    --query Account \
    --output text
)

test "$account" = "$AWS_ACCOUNT_ID"


# A target-group export cannot change while the old backend imports it.

stack=$(
  aws cloudformation describe-stacks \
    --stack-name "$FOUNDATION_STACK" \
    --query 'Stacks[0]' \
    --output json
)

architecture=$(jq -r '.Outputs[] | select(.OutputKey == "ComputeArchitecture") | .OutputValue' <<< "$stack")

if [[ "$architecture" != arm64 ]]; then
  prepared=$(jq -r '.Outputs[] | select(.OutputKey == "ArmMigrationPrepared") | .OutputValue' <<< "$stack")
  if [[ "$prepared" != true ]]; then
    echo 'First ARM deployment requires the manual AWS workflow with migrate=true.' >&2
    exit 1
  fi

  imports=$(
    aws cloudformation list-imports \
      --export-name "$FOUNDATION_STACK-TargetGroup" \
      --query Imports \
      --output json
  )

  if [[ "$(jq 'length' <<< "$imports")" != 0 ]]; then
    echo 'First ARM deployment requires the manual AWS workflow with migrate=true.' >&2
    exit 1
  fi
fi


# Resolve the ARM AMI only on migration. Later releases reuse the pinned parameter.

image_id=$(jq -r '.Parameters[] | select(.ParameterKey == "ECSImageId") | .ParameterValue' <<< "$stack")

if [[ -z "$image_id" ]]; then
  image_id=$(
    aws ssm get-parameter \
      --name /aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id \
      --query Parameter.Value \
      --output text
  )
fi

[[ "$image_id" =~ ^ami-[0-9a-f]+$ ]]


# Protect the database

aws cloudformation set-stack-policy \
  --stack-name "$FOUNDATION_STACK" \
  --stack-policy-body file://infra/foundation/stack-policy.json


# Deploy and wait; keep the stack's current parameters

aws cloudformation deploy \
  --stack-name "$FOUNDATION_STACK" \
  --template-file infra/foundation/template.yaml \
  --role-arn "arn:aws:iam::$AWS_ACCOUNT_ID:role/$FOUNDATION_ROLE" \
  --parameter-overrides "ECSImageId=$image_id" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-disable-rollback \
  --no-fail-on-empty-changeset
