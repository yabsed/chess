"""Read reviewed foundation outputs as inputs for bootstrap/access.yaml."""

import json
import os
import re
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from infra.foundation.check_applied import comparable_template


FOUNDATION = Path(__file__).parents[1] / "foundation/template.yaml"
ACCESS     = Path(__file__).with_name("access.yaml")


def parameters(session, stack_name, expected_account):
    identity = session.client("sts").get_caller_identity()
    account = identity["Account"]
    if account != expected_account:
        raise ValueError("Authenticated AWS account does not match AWS_ACCOUNT_ID.")

    partition = identity["Arn"].split(":")[1]
    region = session.region_name

    # Reviewed foundation
    cloudformation = session.client("cloudformation")
    response = cloudformation.describe_stacks(StackName=stack_name)
    stack = response["Stacks"][0]

    if stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"}:
        raise ValueError("Wait for a successful foundation deployment before preparing permissions.")

    stack_prefix = f"arn:{partition}:cloudformation:{region}:{account}:stack/{stack_name}/"
    if not stack["StackId"].startswith(stack_prefix):
        raise ValueError("Foundation stack does not match the requested account, region and name.")

    applied = cloudformation.get_template(
        StackName     = stack["StackId"],
        TemplateStage = "Original",
    )
    expected = comparable_template(FOUNDATION.read_text())
    if comparable_template(applied["TemplateBody"]) != expected:
        raise ValueError("Apply the reviewed foundation template before preparing bootstrap permissions.")

    # Do not pair outputs from one deployment with a template from another.
    confirmed = cloudformation.describe_stacks(StackName=stack["StackId"])["Stacks"][0]
    for field in (
        "StackId", "StackStatus",
        "CreationTime", "LastUpdatedTime",
        "Parameters", "Outputs",
    ):
        if confirmed.get(field) != stack.get(field):
            raise ValueError("Foundation changed while reading it. Retry after deployment completes.")

    # Bootstrap inputs
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    definitions = comparable_template(ACCESS.read_text())["Parameters"]
    result = []

    for name, definition in definitions.items():
        value = outputs.get(f"Bootstrap{name}")
        if not value:
            raise ValueError(f"Missing Bootstrap{name} output. Apply the foundation template first.")

        check_parameter(name, value, definition, partition, region, account)

        if name == "FoundationStack" and value != stack_name:
            raise ValueError("BootstrapFoundationStack does not match the requested foundation.")

        if name == "NetworkResources":
            value = ",".join(sorted(value.split(",")))

        result.append({"ParameterKey": name, "ParameterValue": value})

    return result


def check_parameter(name, value, definition, partition, region, account):
    # Reuse the destination's schema; native EC2 parameter types need local patterns.
    id_patterns = {
        "AWS::EC2::SecurityGroup::Id": r"sg-[a-f0-9]+",
        "AWS::EC2::Subnet::Id":        r"subnet-[a-f0-9]+",
    }
    pattern = definition.get("AllowedPattern") or id_patterns[definition["Type"]]
    values = value.split(",") if definition["Type"] == "CommaDelimitedList" else [value]

    for item in values:
        if not re.fullmatch(pattern, item):
            raise ValueError(f"Invalid bootstrap parameter: {name}.")

        if not item.startswith("arn:"):
            continue

        _, actual_partition, service, actual_region, actual_account, _ = item.split(":", 5)
        if actual_partition != partition:
            raise ValueError(f"Bootstrap parameter belongs to another AWS partition: {name}.")

        if actual_account != account:
            raise ValueError(f"Bootstrap parameter belongs to another AWS account: {name}.")

        expected_region = "" if service == "cloudfront" else region
        if actual_region != expected_region:
            raise ValueError(f"Bootstrap parameter belongs to another AWS region: {name}.")


def main():
    try:
        required = ("AWS_REGION", "AWS_ACCOUNT_ID", "FOUNDATION_STACK")
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ValueError(f"Missing deployment settings: {', '.join(missing)}")

        session = boto3.Session(region_name=os.environ["AWS_REGION"])
        values = parameters(session, os.environ["FOUNDATION_STACK"], os.environ["AWS_ACCOUNT_ID"])
        print(json.dumps(values, indent=2))
        return 0
    except (BotoCoreError, ClientError, KeyError, OSError, ValueError) as error:
        print(f"Could not prepare bootstrap permissions: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
