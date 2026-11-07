import copy
import unittest
from unittest.mock import Mock, call

from infra.backend.verify import VerificationError, verify_service


TASK = "arn:aws:ecs:us-east-1:123456789012:task-definition/backend:7"
IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/backend@sha256:" + "a" * 64


class ServiceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.service = {
            "taskDefinition": TASK,
            "desiredCount": 1,
            "runningCount": 1,
            "pendingCount": 0,
            "deployments": [{"rolloutState": "COMPLETED"}],
        }
        self.task = {
            "taskDefinitionArn": TASK,
            "lastStatus": "RUNNING",
            "containers": [{
                "name": "backend",
                "image": IMAGE,
                "imageDigest": "sha256:" + "a" * 64,
                "healthStatus": "HEALTHY",
            }],
        }
        self.ecs = Mock()
        self.ecs.describe_services.return_value = {"services": [self.service]}
        self.ecs.get_paginator.return_value.paginate.return_value = [
            {"taskArns": ["task-1"]},
        ]
        self.ecs.describe_tasks.return_value = {"tasks": [self.task]}

    def verify(self):
        verify_service(self.ecs, "cluster", "backend", TASK, IMAGE)

    def test_healthy_release_passes(self):
        self.verify()

        self.ecs.describe_services.assert_called_once_with(
            cluster="cluster", services=["backend"],
        )
        self.ecs.get_paginator.assert_called_once_with("list_tasks")
        self.ecs.get_paginator.return_value.paginate.assert_called_once_with(
            cluster="cluster", serviceName="backend", desiredStatus="RUNNING",
        )
        self.ecs.describe_tasks.assert_called_once_with(
            cluster="cluster", tasks=["task-1"],
        )
        self.ecs.get_waiter.assert_not_called()

    def test_missing_or_failed_service_cannot_pass(self):
        responses = [
            {},
            {"services": []},
            {"services": [self.service, self.service]},
            {"services": [self.service], "failures": [{"reason": "MISSING"}]},
        ]
        for response in responses:
            with self.subTest(response=response):
                self.ecs.describe_services.return_value = response
                with self.assertRaises(VerificationError):
                    self.verify()

        self.ecs.describe_tasks.assert_not_called()

    def test_incomplete_or_wrong_deployment_cannot_pass(self):
        invalid_states = [
            ("taskDefinition", "previous-task-definition"),
            ("desiredCount", 0),
            ("runningCount", 0),
            ("pendingCount", 1),
            ("deployments", []),
            ("deployments", [{"rolloutState": "IN_PROGRESS"}]),
            ("deployments", [{"rolloutState": "FAILED"}]),
            ("deployments", [{"rolloutState": "COMPLETED"}] * 2),
        ]
        for key, value in invalid_states:
            with self.subTest(key=key, value=value):
                service = dict(self.service, **{key: value})
                self.ecs.describe_services.return_value = {"services": [service]}
                with self.assertRaises(VerificationError):
                    self.verify()

        self.ecs.describe_tasks.assert_not_called()

    def test_listed_task_count_must_match_service(self):
        for task_arns in ([], ["task-1", "task-2"]):
            with self.subTest(task_arns=task_arns):
                self.ecs.get_paginator.return_value.paginate.return_value = [
                    {"taskArns": task_arns},
                ]
                with self.assertRaisesRegex(VerificationError, "listed tasks"):
                    self.verify()

        self.ecs.describe_tasks.assert_not_called()

    def test_task_description_failures_or_missing_tasks_cannot_pass(self):
        responses = [
            {"tasks": []},
            {"tasks": [self.task], "failures": [{"reason": "MISSING"}]},
        ]
        for response in responses:
            with self.subTest(response=response):
                self.ecs.describe_tasks.return_value = response
                with self.assertRaises(VerificationError):
                    self.verify()

    def test_wrong_task_or_container_cannot_pass(self):
        invalid_tasks = [
            {"taskDefinitionArn": "previous-task-definition"},
            {"lastStatus": "STOPPED"},
            {"containers": []},
        ]
        for changes in invalid_tasks:
            with self.subTest(changes=changes):
                task = dict(self.task, **changes)
                self.ecs.describe_tasks.return_value = {"tasks": [task]}
                with self.assertRaises(VerificationError):
                    self.verify()

    def test_backend_must_have_the_expected_image_and_health(self):
        invalid_containers = [
            {"name": "other"},
            {"image": "repository:latest"},
            {"imageDigest": "sha256:" + "b" * 64},
            {"healthStatus": "UNHEALTHY"},
            {"healthStatus": "UNKNOWN"},
        ]
        for changes in invalid_containers:
            with self.subTest(changes=changes):
                task = copy.deepcopy(self.task)
                task["containers"][0].update(changes)
                self.ecs.describe_tasks.return_value = {"tasks": [task]}
                with self.assertRaises(VerificationError):
                    self.verify()

    def test_other_containers_do_not_replace_backend_verification(self):
        self.task["containers"].insert(0, {"name": "sidecar"})
        self.verify()

    def test_every_task_is_verified_across_pages_and_batches(self):
        self.service.update(desiredCount=101, runningCount=101)
        task_arns = [f"task-{index}" for index in range(101)]
        self.ecs.get_paginator.return_value.paginate.return_value = [
            {"taskArns": task_arns[:60]},
            {"taskArns": task_arns[60:]},
        ]
        self.ecs.describe_tasks.side_effect = [
            {"tasks": [self.task] * 100},
            {"tasks": [self.task]},
        ]

        self.verify()

        self.assertEqual(self.ecs.describe_tasks.call_args_list, [
            call(cluster="cluster", tasks=task_arns[:100]),
            call(cluster="cluster", tasks=task_arns[100:]),
        ])

        last_task = dict(self.task, lastStatus="STOPPED")
        self.ecs.describe_tasks.side_effect = [
            {"tasks": [self.task] * 100},
            {"tasks": [last_task]},
        ]
        with self.assertRaisesRegex(VerificationError, "not running"):
            self.verify()

    def test_sdk_error_is_not_hidden_as_a_missing_service(self):
        error = RuntimeError("SDK request failed")
        self.ecs.describe_services.side_effect = error

        with self.assertRaises(RuntimeError) as failure:
            self.verify()

        self.assertIs(failure.exception, error)


if __name__ == "__main__":
    unittest.main()
