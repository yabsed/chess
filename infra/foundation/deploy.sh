#!/usr/bin/env bash
set -euo pipefail

# Check the account

account=$(
  aws sts get-caller-identity \
    --query Account \
    --output text
)

test "$account" = "$AWS_ACCOUNT_ID"


# Protect the database

aws cloudformation set-stack-policy \
  --stack-name "$FOUNDATION_STACK" \
  --stack-policy-body file://infra/foundation/stack-policy.json


# Deploy and wait; keep the stack's current parameters

aws cloudformation deploy \
  --stack-name "$FOUNDATION_STACK" \
  --template-file infra/foundation/template.yaml \
  --role-arn "arn:aws:iam::$AWS_ACCOUNT_ID:role/$FOUNDATION_ROLE" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-disable-rollback \
  --no-fail-on-empty-changeset
