"""Check the running service against the release that CloudFormation applied."""


class VerificationError(RuntimeError):
    """The deployed service does not match the expected healthy release."""


def verify_service(ecs, cluster: str, service: str, task: str, image: str) -> None:
    response = ecs.describe_services(cluster=cluster, services=[service])

    if response.get("failures"):
        raise VerificationError(f"ECS could not describe service {service}.")

    services = response.get("services", [])
    if len(services) != 1:
        raise VerificationError(f"Expected one ECS service named {service}.")

    deployed_service = services[0]
    expect_field(deployed_service, "taskDefinition", task)

    desired_count = deployed_service.get("desiredCount", 0)
    if desired_count <= 0:
        raise VerificationError("The service must have at least one desired task.")

    expect_field(deployed_service, "runningCount", desired_count)
    expect_field(deployed_service, "pendingCount", 0)

    deployments = deployed_service.get("deployments", [])
    if len(deployments) != 1:
        raise VerificationError("The service must have exactly one deployment.")

    expect_field(deployments[0], "rolloutState", "COMPLETED")

    task_arns = list_running_tasks(ecs, cluster, service)
    if len(task_arns) != desired_count:
        raise VerificationError("The listed tasks do not match the desired task count.")

    running_tasks = describe_tasks(ecs, cluster, task_arns)
    if len(running_tasks) != desired_count:
        raise VerificationError("The described tasks do not match the desired task count.")

    for running_task in running_tasks:
        verify_task(running_task, task, image)


def verify_task(running_task: dict, expected_task: str, expected_image: str) -> None:
    expect_field(running_task, "taskDefinitionArn", expected_task)
    expect_field(running_task, "lastStatus",        "RUNNING")

    backend_containers = [
        container
        for container in running_task.get("containers", [])
        if container.get("name") == "backend"
    ]
    if len(backend_containers) != 1:
        raise VerificationError("A service task must have one backend container.")

    backend = backend_containers[0]
    expected_digest = expected_image.rsplit("@", 1)[-1]

    expect_field(backend, "image",        expected_image)
    expect_field(backend, "imageDigest",  expected_digest)
    expect_field(backend, "healthStatus", "HEALTHY")


def expect_field(record: dict, field: str, expected: object) -> None:
    actual = record.get(field)
    if actual != expected:
        raise VerificationError(f"{field}: expected {expected!r}, got {actual!r}.")


def list_running_tasks(ecs, cluster: str, service: str) -> list[str]:
    paginator = ecs.get_paginator("list_tasks")
    pages = paginator.paginate(
        cluster=cluster,
        serviceName=service,
        desiredStatus="RUNNING",
    )

    task_arns = []
    for page in pages:
        task_arns.extend(page.get("taskArns", []))
    return task_arns


def describe_tasks(ecs, cluster: str, task_arns: list[str]) -> list[dict]:
    running_tasks = []

    # ECS accepts at most 100 task ARNs in one DescribeTasks request.
    for offset in range(0, len(task_arns), 100):
        response = ecs.describe_tasks(
            cluster=cluster,
            tasks=task_arns[offset : offset + 100],
        )
        if response.get("failures"):
            raise VerificationError("ECS could not describe every running task.")

        running_tasks.extend(response.get("tasks", []))

    return running_tasks
