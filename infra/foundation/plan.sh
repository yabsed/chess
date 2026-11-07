#!/usr/bin/env bash
set -euo pipefail

# Account

account=$(
  aws sts get-caller-identity \
    --query Account \
    --output text
)

test "$account" = "$AWS_ACCOUNT_ID"


# Create a plan
# Keep the stack's current parameters; use the template from this commit.

change_set=$(
  aws cloudformation create-change-set \
    --stack-name "$FOUNDATION_STACK" \
    --change-set-name "chess-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" \
    --change-set-type UPDATE \
    --description "Commit $GITHUB_SHA" \
    --template-body file://infra/foundation/template.yaml \
    --parameters \
      ParameterKey=ECSClusterName,UsePreviousValue=true \
      ParameterKey=DeploymentUserName,UsePreviousValue=true \
    --role-arn "arn:aws:iam::$AWS_ACCOUNT_ID:role/$FOUNDATION_ROLE" \
    --capabilities CAPABILITY_NAMED_IAM \
    --query Id \
    --output text
)

printf 'Change set: %s\n' "$change_set" | tee -a "$GITHUB_STEP_SUMMARY"


# Wait for the plan, then print its changes

if aws cloudformation wait change-set-create-complete \
  --change-set-name "$change_set"; then

  aws cloudformation describe-change-set \
    --change-set-name "$change_set" \
    --query '{ChangeSet:ChangeSetId,Changes:Changes[*].ResourceChange}'
  exit 0
fi


# No changes, or a planning error
# CloudFormation reports an unchanged template as a failed change set.

reason=$(
  aws cloudformation describe-change-set \
    --change-set-name "$change_set" \
    --query StatusReason \
    --output text
)

echo "$reason"
if [[ "$reason" == "The submitted information didn't contain changes."* ]]; then
  echo 'No changes; nothing to apply.' >> "$GITHUB_STEP_SUMMARY"
  exit 0
fi

exit 1
