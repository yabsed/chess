#!/usr/bin/env bash
set -euo pipefail

cluster=${1:?Pass the ECS cluster}
service=${2:?Pass the ECS service}
task=${3:?Pass the expected task definition ARN}
image=${4:?Pass the expected image digest URI}

state=$(aws ecs describe-services --cluster "$cluster" --services "$service")
jq -e --arg task "$task" '
  (.failures | length) == 0 and
  (.services | length) == 1 and
  (.services[0] |
    .taskDefinition == $task and
    .desiredCount > 0 and
    .runningCount == .desiredCount and
    .pendingCount == 0 and
    (.deployments | length) == 1 and
    .deployments[0].rolloutState == "COMPLETED")
' <<< "$state" > /dev/null

tasks=$(aws ecs list-tasks --cluster "$cluster" --service-name "$service" \
  --desired-status RUNNING --output json)
mapfile -t task_arns < <(jq -r '.taskArns[]' <<< "$tasks")
test "${#task_arns[@]}" -gt 0

running=$(aws ecs describe-tasks --cluster "$cluster" --tasks "${task_arns[@]}")
count=$(jq '.services[0].desiredCount' <<< "$state")
digest=${image##*@}
jq -e --arg task "$task" --arg image "$image" --arg digest "$digest" --argjson count "$count" '
  (.failures | length) == 0 and
  (.tasks | length) == $count and
  all(.tasks[];
    .taskDefinitionArn == $task and
    .lastStatus == "RUNNING" and
    any(.containers[];
      .name == "backend" and
      .image == $image and
      .imageDigest == $digest and
      .healthStatus == "HEALTHY"))
' <<< "$running" > /dev/null
