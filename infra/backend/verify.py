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
    if deployed_service.get("taskDefinition") != task:
        raise VerificationError("The service uses a different task definition.")

    desired_count = deployed_service.get("desiredCount", 0)
    if desired_count <= 0:
        raise VerificationError("The service must have at least one desired task.")

    if deployed_service.get("runningCount") != desired_count:
        raise VerificationError("The service has not reached its desired task count.")

    if deployed_service.get("pendingCount") != 0:
        raise VerificationError("The service still has pending tasks.")

    deployments = deployed_service.get("deployments", [])
    if len(deployments) != 1:
        raise VerificationError("The service must have exactly one deployment.")

    if deployments[0].get("rolloutState") != "COMPLETED":
        raise VerificationError("The service deployment has not completed.")

    task_arns = []
    pages = ecs.get_paginator("list_tasks").paginate(
        cluster=cluster,
        serviceName=service,
        desiredStatus="RUNNING",
    )
    for page in pages:
        task_arns.extend(page.get("taskArns", []))

    if len(task_arns) != desired_count:
        raise VerificationError("The listed tasks do not match the desired task count.")

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

    if len(running_tasks) != desired_count:
        raise VerificationError("The described tasks do not match the desired task count.")

    for running_task in running_tasks:
        verify_task(running_task, task, image)


def verify_task(running_task: dict, expected_task: str, expected_image: str) -> None:
    if running_task.get("taskDefinitionArn") != expected_task:
        raise VerificationError("A running task uses a different task definition.")

    if running_task.get("lastStatus") != "RUNNING":
        raise VerificationError("A service task is not running.")

    backend_containers = [
        container
        for container in running_task.get("containers", [])
        if container.get("name") == "backend"
    ]
    if len(backend_containers) != 1:
        raise VerificationError("A service task must have one backend container.")

    backend = backend_containers[0]
    if backend.get("image") != expected_image:
        raise VerificationError("A backend container uses a different image URI.")

    expected_digest = expected_image.rsplit("@", 1)[-1]
    if backend.get("imageDigest") != expected_digest:
        raise VerificationError("A backend container runs a different image digest.")

    if backend.get("healthStatus") != "HEALTHY":
        raise VerificationError("A backend container is not healthy.")
