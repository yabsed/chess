#!/usr/bin/env bash
set -euo pipefail

action=${1:?Usage: foundation.sh plan | apply CHANGE_SET_ARN}
change_set=${2:-}

: "${AWS_REGION:?Set AWS_REGION}"
: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${FOUNDATION_STACK:?Set FOUNDATION_STACK}"
: "${FOUNDATION_ROLE:?Set FOUNDATION_ROLE}"

account=$(aws sts get-caller-identity --query Account --output text)
if [[ $account != "$AWS_ACCOUNT_ID" ]]; then
  echo 'AWS account does not match AWS_ACCOUNT_ID.' >&2
  exit 1
fi

work=${RUNNER_TEMP:-$(mktemp -d)}
commit=$(git rev-parse HEAD)
hash=$(sha256sum infra/foundation.yaml | cut -d ' ' -f 1)
description="commit=$commit template=$hash"

case "$action" in
  plan)
    aws cloudformation describe-stacks --stack-name "$FOUNDATION_STACK" > "$work/stack.json"
    parameters=$(jq '[
      .Stacks[0].Parameters[]
      | select(.ParameterKey != "TemplateRevision")
      | {ParameterKey, UsePreviousValue: true}
    ]' "$work/stack.json")

    change_set=$(aws cloudformation create-change-set \
      --stack-name "$FOUNDATION_STACK" \
      --change-set-name "$FOUNDATION_STACK-${GITHUB_RUN_ID:-$(date +%s)}-${GITHUB_RUN_ATTEMPT:-1}" \
      --change-set-type UPDATE \
      --description "$description" \
      --template-body file://infra/foundation.yaml \
      --parameters "$parameters" \
      --role-arn "arn:aws:iam::$AWS_ACCOUNT_ID:role/$FOUNDATION_ROLE" \
      --capabilities CAPABILITY_NAMED_IAM \
      --query Id --output text)

    status=0
    aws cloudformation wait change-set-create-complete --change-set-name "$change_set" || status=$?
    aws cloudformation describe-change-set --change-set-name "$change_set" > "$work/changes.json"
    aws cloudformation describe-events --change-set-name "$change_set" > "$work/validation.json"

    if (( status != 0 )); then
      if jq -e '.Status == "FAILED" and
        ((.Changes // []) | length == 0) and
        (.StatusReason | startswith("The submitted information didn\u0027t contain changes."))' \
        "$work/changes.json" > /dev/null; then
        echo 'No infrastructure changes. Nothing to apply.' | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
        exit 0
      fi
      jq -r '.StatusReason' "$work/changes.json" >&2
      exit "$status"
    fi

    {
      printf 'Change set: %s\n\nCommit: %s\n\n' "$change_set" "$commit"
      jq -r '.Changes[].ResourceChange |
        "- \(.Action) \(.LogicalResourceId) (replacement: \(.Replacement // "N/A"))"' "$work/changes.json"
      printf '\nPlan files: %s\n' "$work"
    } | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    ;;

  apply)
    : "${change_set:?Pass the change set ARN from a successful plan}"
    stack=$(aws cloudformation describe-stacks --stack-name "$FOUNDATION_STACK" \
      --query 'Stacks[0].StackId' --output text)
    aws cloudformation describe-change-set --change-set-name "$change_set" > "$work/changes.json"

    # Apply precisely the reviewed plan from this checkout.
    jq -e --arg stack "$stack" --arg description "$description" '
      .StackId == $stack and .Description == $description and
      .Status == "CREATE_COMPLETE" and .ExecutionStatus == "AVAILABLE"
    ' "$work/changes.json" > /dev/null

    # IAM, additions, removals and replacements require a trusted operator.
    jq -e 'all(.Changes[].ResourceChange;
      .Action == "Modify" and .Replacement == "False" and
      (.ResourceType | startswith("AWS::IAM::") | not))' "$work/changes.json" > /dev/null

    aws cloudformation set-stack-policy --stack-name "$FOUNDATION_STACK" \
      --stack-policy-body file://infra/stack-policy.json
    aws cloudformation execute-change-set --change-set-name "$change_set" --no-disable-rollback
    aws cloudformation wait stack-update-complete --stack-name "$FOUNDATION_STACK"
    ;;

  *)
    echo 'Usage: foundation.sh plan | apply CHANGE_SET_ARN' >&2
    exit 1
    ;;
esac
