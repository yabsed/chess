"""Plan foundation changes, then apply the reviewed plan from the same checkout."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError, WaiterError


TEMPLATE     = Path(__file__).with_name("template.yaml")
STACK_POLICY = Path(__file__).with_name("stack-policy.json")


def plan(cloudformation, stack_name, role_arn, template, description, work):
    stack_response = cloudformation.describe_stacks(StackName=stack_name)
    stack = stack_response["Stacks"][0]
    parameters = [
        {"ParameterKey": parameter["ParameterKey"], "UsePreviousValue": True}
        for parameter in stack.get("Parameters", [])
        if parameter["ParameterKey"] != "TemplateRevision"
    ]

    run     = os.environ.get("GITHUB_RUN_ID") or str(int(time.time()))
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT") or "1"

    created = cloudformation.create_change_set(
        StackName     = stack_name,
        ChangeSetName = f"{stack_name}-{run}-{attempt}",
        ChangeSetType = "UPDATE",

        Description  = description,
        TemplateBody = template,
        Parameters   = parameters,

        RoleARN      = role_arn,
        Capabilities = ["CAPABILITY_NAMED_IAM"],
    )
    change_set = created["Id"]

    # A no-change plan also fails this waiter. Save its evidence before deciding.
    wait_error = None
    try:
        waiter = cloudformation.get_waiter("change_set_create_complete")
        waiter.wait(ChangeSetName=change_set)
    except WaiterError as error:
        wait_error = error

    changes = read_plan(cloudformation, change_set)
    save_json(work / "changes.json", changes)

    paginator = cloudformation.get_paginator("describe_events")
    events = paginator.paginate(ChangeSetName=change_set)
    validation = events.build_full_result()
    save_json(work / "validation.json", validation)

    if wait_error is not None:
        if has_no_changes(changes):
            summarize("No infrastructure changes. Nothing to apply.")
            return

        reason = changes.get("StatusReason") or str(wait_error)
        raise RuntimeError(f"Could not create the plan: {reason}") from wait_error

    report_plan(change_set, description, changes, work)


def apply(cloudformation, stack_name, change_set, description, work):
    stack_response = cloudformation.describe_stacks(StackName=stack_name)
    stack = stack_response["Stacks"][0]
    changes = read_plan(cloudformation, change_set)
    save_json(work / "changes.json", changes)

    # Plan identity and readiness
    if changes.get("StackId") != stack["StackId"]:
        raise ValueError("The plan belongs to another stack. Run plan again.")

    if changes.get("Description") != description:
        raise ValueError("The plan does not match this commit and template. Run plan again.")

    if changes.get("Status") != "CREATE_COMPLETE":
        raise ValueError("The plan has not completed successfully. Run plan again.")

    if changes.get("ExecutionStatus") != "AVAILABLE":
        raise ValueError("The plan is no longer available to apply. Run plan again.")

    if not isinstance(changes.get("Changes"), list):
        raise ValueError("The plan has no resource change list. Run plan again.")

    # Allowed resource changes
    for change in changes["Changes"]:
        resource = change.get("ResourceChange", {})

        if resource.get("Action") != "Modify":
            raise ValueError("Adding or removing resources requires a trusted operator.")

        if resource.get("Replacement") != "False":
            raise ValueError("Replacing resources requires a trusted operator.")

        resource_type = resource.get("ResourceType")
        if not resource_type:
            raise ValueError("The plan is missing a resource type. Run plan again.")

        if resource_type.startswith("AWS::IAM::"):
            raise ValueError("Changing IAM resources requires a trusted operator.")

    # Finish every check before changing the policy or executing the plan.
    cloudformation.set_stack_policy(
        StackName       = stack_name,
        StackPolicyBody = STACK_POLICY.read_text(),
    )
    cloudformation.execute_change_set(
        ChangeSetName   = change_set,
        DisableRollback = False,
    )
    waiter = cloudformation.get_waiter("stack_update_complete")
    waiter.wait(StackName=stack_name)


def read_plan(cloudformation, change_set):
    paginator = cloudformation.get_paginator("describe_change_set")
    pages = paginator.paginate(ChangeSetName=change_set)
    return pages.build_full_result()


def has_no_changes(plan):
    if plan.get("Status") != "FAILED":
        return False

    if plan.get("Changes"):
        return False

    reason = plan.get("StatusReason") or ""
    return reason.startswith("The submitted information didn't contain changes.")


def report_plan(change_set, description, changes, work):
    lines = [f"Change set: {change_set}", "", description, ""]

    for change in changes.get("Changes", []):
        resource = change["ResourceChange"]
        lines.append(
            f"- {resource['Action']} {resource['LogicalResourceId']} "
            f"(replacement: {resource.get('Replacement', 'N/A')})"
        )

    lines.extend(["", f"Plan files: {work}"])
    summarize("\n".join(lines))


def summarize(message):
    print(message)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as file:
            file.write(message + "\n")


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "apply"])
    parser.add_argument("change_set", nargs="?", default="")
    args = parser.parse_args(argv)

    if args.action == "apply" and not args.change_set:
        parser.error("Pass the change set ARN from a successful plan.")

    try:
        for name in [
            "AWS_REGION",
            "AWS_ACCOUNT_ID",
            "FOUNDATION_STACK",
            "FOUNDATION_ROLE",
        ]:
            if not os.environ.get(name):
                raise ValueError(f"Set {name}.")

        if os.environ.get("GITHUB_ACTIONS") == "true":
            if not os.environ.get("AWS_ACCESS_KEY_ID"):
                raise ValueError("Set the AWS_INFRA_ACCESS_KEY_ID repository secret.")
            if not os.environ.get("AWS_SECRET_ACCESS_KEY"):
                raise ValueError("Set the AWS_INFRA_SECRET_ACCESS_KEY repository secret.")

        region           = os.environ["AWS_REGION"]
        expected_account = os.environ["AWS_ACCOUNT_ID"]
        stack_name       = os.environ["FOUNDATION_STACK"]
        role_name        = os.environ["FOUNDATION_ROLE"]

        session = boto3.Session(region_name=region)
        identity = session.client("sts").get_caller_identity()
        account = identity["Account"]

        if account != expected_account:
            raise ValueError("AWS account does not match AWS_ACCOUNT_ID.")

        template = TEMPLATE.read_bytes()
        commit   = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        digest   = hashlib.sha256(template).hexdigest()

        description = f"commit={commit} template={digest}"

        # Keep local plan evidence available after the command exits.
        work = Path(os.environ.get("RUNNER_TEMP") or tempfile.mkdtemp(prefix="chess-plan-"))
        cloudformation = session.client("cloudformation")

        if args.action == "plan":
            role_arn = f"arn:aws:iam::{account}:role/{role_name}"
            plan(
                cloudformation,
                stack_name  = stack_name,
                role_arn    = role_arn,

                template    = template.decode(),
                description = description,
                work        = work,
            )
        else:
            apply(
                cloudformation,
                stack_name  = stack_name,
                change_set  = args.change_set,

                description = description,
                work        = work,
            )

        return 0
    except (
        BotoCoreError,
        ClientError,
        OSError,
        subprocess.CalledProcessError,
        ValueError,
        RuntimeError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
