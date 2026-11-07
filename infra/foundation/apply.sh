#!/usr/bin/env bash
set -euo pipefail

change_set=${1:?Pass the ARN of the reviewed change set}


# Account

account=$(
  aws sts get-caller-identity \
    --query Account \
    --output text
)

test "$account" = "$AWS_ACCOUNT_ID"


# Check the plan's target stack

stack=$(
  aws cloudformation describe-stacks \
    --stack-name "$FOUNDATION_STACK" \
    --query 'Stacks[0].StackId' \
    --output text
)

planned_stack=$(
  aws cloudformation describe-change-set \
    --change-set-name "$change_set" \
    --query StackId \
    --output text
)

test "$planned_stack" = "$stack"


# Protect the database

aws cloudformation set-stack-policy \
  --stack-name "$FOUNDATION_STACK" \
  --stack-policy-body file://infra/foundation/stack-policy.json


# Apply the reviewed plan

aws cloudformation execute-change-set \
  --change-set-name "$change_set" \
  --no-disable-rollback

aws cloudformation wait stack-update-complete --stack-name "$FOUNDATION_STACK"
