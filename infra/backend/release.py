"""Publish a tested image, deploy it, and restore the previous release if checks fail."""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from infra.backend.verify import VerificationError, verify_service
from infra.foundation.check_applied import check_applied


@dataclass(frozen=True)
class Release:
    template: str
    parameters: dict[str, str]


def release(session, foundation_stack, backend_stack, local_image, image_digest):
    cloudformation = session.client("cloudformation")
    foundation = stack_outputs(cloudformation, foundation_stack)
    applied = cloudformation.get_template(StackName=foundation_stack)
    check_applied(applied["TemplateBody"])

    role       = foundation["BackendDeploymentRole"]
    cluster    = foundation["ECSCluster"]
    api_url    = foundation["ApiUrl"]
    repository = foundation["RepositoryUri"]

    if backend_stack != f"{cluster}-backend":
        raise ValueError("BACKEND_STACK must match the foundation cluster: <cluster>-backend.")

    new_image = image_digest
    if local_image:
        print("Publishing image.", flush=True)
        new_image = publish_image(session.client("ecr"), repository, local_image)

    check_image(repository, new_image)

    new_release = Release(
        template   = Path(__file__).with_name("template.yaml").read_text(),
        parameters = {
            "FoundationStack": foundation_stack,
            "ImageUri":        new_image,
        },
    )
    previous_release = read_previous_release(cloudformation, backend_stack)

    # A failed CloudFormation deployment uses its own rollback, not our restore path.
    print("Deploying release.", flush=True)
    deploy(session, backend_stack, role, new_release)
    deployed = stack_outputs(cloudformation, backend_stack)
    ecs = session.client("ecs")

    print("Verifying ECS, HTTP and WebSockets.", flush=True)
    try:
        verify_release(ecs, cluster, deployed, new_image, api_url)

    except VerificationError as error:
        print(f"Release verification failed: {error}", file=sys.stderr)

        if previous_release is None:
            raise VerificationError(
                "This was the first release; no previous release exists."
            ) from error

        print("Restoring the previous template and parameters through CloudFormation.", flush=True)
        deploy(session, backend_stack, role, previous_release)
        restored = stack_outputs(cloudformation, backend_stack)
        previous_image = previous_release.parameters["ImageUri"]

        verify_release(ecs, cluster, restored, previous_image, api_url)
        print("Previous release restored and verified. The release still failed.", file=sys.stderr)
        raise

    report_success(new_image, deployed["TaskDefinition"], api_url)
    return 0


def verify_release(ecs, cluster, deployed, image, api_url):
    task    = deployed["TaskDefinition"]
    service = deployed["Service"]

    try:
        verify_service(ecs, cluster, service, task, image)
        subprocess.run(["bash", "infra/backend/smoke.sh", api_url], check=True)
    except (ClientError, BotoCoreError, subprocess.CalledProcessError) as error:
        raise VerificationError(str(error)) from error


def report_success(image, task, api_url):
    summary = f"Image: {image}\n\nTask: {task}\n\nAPI: {api_url}\n"
    print(summary)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a") as output:
            output.write(summary)


def stack_outputs(cloudformation, stack_name):
    response = cloudformation.describe_stacks(StackName=stack_name)
    stack = response["Stacks"][0]
    return {output["OutputKey"]: output["OutputValue"] for output in stack["Outputs"]}


def read_previous_release(cloudformation, stack_name):
    try:
        response = cloudformation.describe_stacks(StackName=stack_name)
    except cloudformation.exceptions.ClientError as error:
        # DescribeStacks has no modeled NotFound exception. Permission failures must propagate.
        details = error.response["Error"]
        missing_stack = f"Stack with id {stack_name} does not exist"
        if details["Code"] == "ValidationError" and details["Message"] == missing_stack:
            return None
        raise

    stack = response["Stacks"][0]
    response = cloudformation.get_template(StackName=stack_name)
    template = response["TemplateBody"]
    if not isinstance(template, str):
        template = json.dumps(template)

    parameters = {
        parameter["ParameterKey"]: parameter["ParameterValue"]
        for parameter in stack["Parameters"]
    }
    return Release(template, parameters)


def check_image(repository, image):
    image_repository, separator, digest = image.partition("@")

    if image_repository != repository:
        raise ValueError("Image repository does not match the foundation ECR repository.")

    if not separator or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Use a valid sha256 image digest.")


def publish_image(ecr, repository, local_image):
    registry, repository_name = repository.split("/", 1)
    commit  = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    run     = os.environ.get("GITHUB_RUN_ID") or str(int(time.time()))
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT") or "1"

    tag          = f"{commit}-{run}-{attempt}"
    tagged_image = f"{repository}:{tag}"

    response = ecr.get_authorization_token()
    token = response["authorizationData"][0]["authorizationToken"]

    decoded = base64.b64decode(token).decode()
    username, password = decoded.split(":", 1)

    # Docker credentials exist only for this publish, including when login or push fails.
    with TemporaryDirectory(prefix="chess-docker-") as docker_config:
        docker_env = dict(os.environ, DOCKER_CONFIG=docker_config)
        subprocess.run(
            ["docker", "login", "--username", username, "--password-stdin", registry],
            input = password + "\n",
            text  = True,
            env   = docker_env,
            check = True,
        )
        subprocess.run(["docker", "tag", local_image, tagged_image], env=docker_env, check=True)
        subprocess.run(
            ["docker", "push", "--platform", "linux/amd64", tagged_image],
            env   = docker_env,
            check = True,
        )

    response = ecr.batch_get_image(
        repositoryName = repository_name,
        imageIds       = [{"imageTag": tag}],
    )
    if response.get("failures") or not response.get("images"):
        raise ValueError("ECR did not return the published image digest.")

    digest = response["images"][0]["imageId"]["imageDigest"]
    return f"{repository}@{digest}"


def deploy(session, stack_name, role, release: Release):
    # Use the same credentials and region as the SDK's verified deployment target.
    credentials = session.get_credentials().get_frozen_credentials()
    environment = dict(os.environ)
    environment["AWS_ACCESS_KEY_ID"]     = credentials.access_key
    environment["AWS_SECRET_ACCESS_KEY"] = credentials.secret_key

    environment.pop("AWS_SECURITY_TOKEN", None)
    environment.pop("AWS_SESSION_TOKEN", None)
    if credentials.token:
        environment["AWS_SESSION_TOKEN"] = credentials.token

    overrides = [f"{key}={value}" for key, value in release.parameters.items()]

    # Both new and previous releases become CLI input here, only for this deployment.
    with TemporaryDirectory(prefix="chess-release-") as work:
        template = Path(work) / "template.yaml"
        template.write_text(release.template)

        subprocess.run(
            [
                "aws", "cloudformation", "deploy",

                "--region",              session.region_name,
                "--stack-name",          stack_name,
                "--template-file",       str(template),
                "--role-arn",            role,
                "--parameter-overrides", *overrides,
                "--no-fail-on-empty-changeset",
            ],
            env   = environment,
            check = True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    image = parser.add_mutually_exclusive_group(required=True)
    image.add_argument("local_image", nargs="?", help="Already built and tested local Docker image")
    image.add_argument("--digest", help="Existing ECR image URI with a sha256 digest")
    args = parser.parse_args()

    try:
        required = [
            "AWS_REGION",
            "AWS_ACCOUNT_ID",
            "FOUNDATION_STACK",
            "BACKEND_STACK",
        ]
        if os.environ.get("GITHUB_ACTIONS") == "true":
            required += ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
        for name in required:
            if not os.environ.get(name):
                raise ValueError(f"Set {name} before releasing.")

        session = boto3.Session(region_name=os.environ["AWS_REGION"])
        account = session.client("sts").get_caller_identity()["Account"]
        if account != os.environ["AWS_ACCOUNT_ID"]:
            raise ValueError("AWS account does not match AWS_ACCOUNT_ID.")

        return release(
            session,
            foundation_stack = os.environ["FOUNDATION_STACK"],
            backend_stack    = os.environ["BACKEND_STACK"],

            local_image  = args.local_image,
            image_digest = args.digest,
        )
    except (
        ValueError,
        VerificationError,
        ClientError,
        BotoCoreError,
        subprocess.CalledProcessError,
        OSError,
    ) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
