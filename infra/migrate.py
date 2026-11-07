"""Prepare the one-time ARM migration. Deleting the backend stack stops the app."""

import argparse
import copy
import json
import os
import subprocess

from pathlib import Path

from cfnlint.decode import decode_str


# Only disposable network resources may lose retention. PostgreSQL is untouched.
RETIRE = {
    "NatAddress":            "AWS::EC2::EIP",
    "NatGateway":            "AWS::EC2::NatGateway",
    "PrivateInternetRouteA": "AWS::EC2::Route",
    "PrivateInternetRouteB": "AWS::EC2::Route",
    "TargetGroup":           "AWS::ElasticLoadBalancingV2::TargetGroup",
}


def read_aws(*arguments):
    response = subprocess.check_output(
        ["aws", *arguments, "--output", "json"],
        text=True,
    )
    return json.loads(response)


def run_aws(*arguments):
    subprocess.run(["aws", *arguments], check=True)


def template_for(stack):
    body = read_aws("cloudformation", "get-template", "--stack-name", stack)["TemplateBody"]
    if isinstance(body, dict):
        return body

    template, errors = decode_str(body)
    if errors:
        raise ValueError(f"Cannot decode {stack}: {errors}")
    return template


def disposable(template, resources):
    changed = copy.deepcopy(template)
    for name, resource_type in resources.items():
        resource = changed["Resources"][name]
        if resource["Type"] != resource_type:
            raise ValueError(f"Unexpected resource type for {name}")
        resource["DeletionPolicy"]      = "Delete"
        resource["UpdateReplacePolicy"] = "Delete"
    return changed


def save(directory, name, value):
    path = directory / name
    path.write_text(json.dumps(value, indent=2) + "\n")
    return str(path)


def deploy(stack, path, role):
    run_aws(
        "cloudformation", "deploy",
        "--stack-name", stack,
        "--template-file", path,
        "--role-arn", role,
        "--capabilities", "CAPABILITY_NAMED_IAM",
        "--no-fail-on-empty-changeset",
    )


def prepare(directory):
    account    = os.environ["AWS_ACCOUNT_ID"]
    foundation = os.environ["FOUNDATION_STACK"]
    backend    = os.environ["BACKEND_STACK"]

    identity = read_aws("sts", "get-caller-identity")
    if identity["Account"] != account:
        raise ValueError("AWS account does not match AWS_ACCOUNT_ID")

    foundation_stack = read_aws("cloudformation", "describe-stacks", "--stack-name", foundation)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in foundation_stack["Outputs"]}

    if outputs.get("ComputeArchitecture") == "arm64":
        print("ARM migration is already complete; continuing with the normal deployment.")
        return

    if backend != outputs["ECSCluster"] + "-backend":
        raise ValueError("BACKEND_STACK is not this foundation's backend")

    # Check the export before any mutation, including when another stack imports it.
    imports = read_aws(
        "cloudformation", "list-imports",
        "--export-name", foundation + "-TargetGroup",
    )["Imports"]
    if set(imports) != {backend}:
        raise ValueError(f"Expected only {backend} to import the target group; found {imports}")

    backend_stack = read_aws("cloudformation", "describe-stacks", "--stack-name", backend)["Stacks"][0]
    old_foundation = template_for(foundation)
    old_backend    = template_for(backend)

    # Refuse to delete a backend stack that has acquired any stateful resources.
    expected = {"Service": "AWS::ECS::Service", "TaskDefinition": "AWS::ECS::TaskDefinition"}
    actual = {name: item["Type"] for name, item in old_backend["Resources"].items()}
    if actual != expected:
        raise ValueError(f"Unexpected backend resources: {actual}")

    prepared_foundation = disposable(old_foundation, RETIRE)
    prepared_backend    = disposable(old_backend, {"Service": "AWS::ECS::Service"})
    prepared_foundation.setdefault("Outputs", {})["ArmMigrationPrepared"] = {"Value": "true"}

    # Save both applied templates and their parameters before changing retention.
    directory.mkdir(parents=True, exist_ok=False)
    save(directory, "foundation-before.json", old_foundation)
    save(directory, "backend-before.json", old_backend)
    save(directory, "foundation-stack.json", foundation_stack)
    save(directory, "backend-stack.json", backend_stack)

    foundation_path = save(directory, "foundation-disposable.json", prepared_foundation)
    backend_path    = save(directory, "backend-disposable.json", prepared_backend)

    partition = identity["Arn"].split(":")[1]
    foundation_role = f"arn:{partition}:iam::{account}:role/{os.environ['FOUNDATION_ROLE']}"
    backend_role = outputs["BackendDeploymentRole"]

    run_aws(
        "cloudformation", "set-stack-policy",
        "--stack-name", foundation,
        "--stack-policy-body", "file://infra/foundation/stack-policy.json",
    )

    # These updates change retention only; the old service still runs here.
    deploy(foundation, foundation_path, foundation_role)
    deploy(backend, backend_path, backend_role)

    # CloudFormation removes the service and its imports. The database is in foundation.
    print("Stopping the backend. The next workflow steps rebuild it on ARM.", flush=True)
    run_aws("cloudformation", "delete-stack", "--stack-name", backend, "--role-arn", backend_role)
    run_aws("cloudformation", "wait", "stack-delete-complete", "--stack-name", backend)

    print(f"Migration prepared. Recovery files: {directory}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    prepare(parser.parse_args().backup_dir)
