import base64
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from botocore.exceptions import ClientError

from infra.backend import release
from infra.backend.verify import VerificationError


ACCOUNT = "123456789012"
REPOSITORY = f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/backend"
NEW_IMAGE = REPOSITORY + "@sha256:" + "a" * 64
PREVIOUS_IMAGE = REPOSITORY + "@sha256:" + "b" * 64
FOUNDATION_TEMPLATE = Path(__file__).resolve().parents[1] / "foundation/template.yaml"


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        environment = {
            "AWS_REGION": "us-east-1",
            "AWS_ACCOUNT_ID": ACCOUNT,
            "FOUNDATION_STACK": "chess-foundation",
            "BACKEND_STACK": "chess-backend",
            "GITHUB_ACTIONS": "true",
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "2",
            "AWS_ACCESS_KEY_ID": "environment-access-key",
            "AWS_SECRET_ACCESS_KEY": "environment-secret-key",
        }
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, environment, clear=True).start()
        self.stdout = patch("sys.stdout", new_callable=io.StringIO).start()
        self.stderr = patch("sys.stderr", new_callable=io.StringIO).start()

        self.previous_template = {"Resources": {"PreviousResource": {"Type": "AWS::ECS::Service"}}}
        self.previous_parameters = {
            "FoundationStack": "chess-foundation",
            "ImageUri": PREVIOUS_IMAGE,
            "FrontendOrigin": "https://previous.example.test",
            "Extra": "keep spaces and $literal text",
        }
        self.first_release = False
        self.previous_read_error = None
        self.deployed_read_error = None
        self.command_failure = None
        self.deployments = []
        self.docker_configs = []

        self.cloudformation = Mock()
        self.cloudformation.exceptions.ClientError = ClientError
        self.cloudformation.describe_stacks.side_effect = self.describe_stack
        self.cloudformation.get_template.side_effect = self.get_template

        self.ecr = Mock()
        authorization = base64.b64encode(b"AWS:password:with:colons").decode()
        self.ecr.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": authorization}],
        }
        self.ecr.batch_get_image.return_value = {
            "images": [{"imageId": {"imageDigest": "sha256:" + "a" * 64}}],
        }

        self.ecs = Mock()
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": ACCOUNT}
        self.session = Mock(region_name="us-east-1")
        self.session.client.side_effect = {
            "cloudformation": self.cloudformation,
            "ecr": self.ecr,
            "ecs": self.ecs,
            "sts": self.sts,
        }.__getitem__
        self.credentials = SimpleNamespace(
            access_key="session-access-key",
            secret_key="session-secret-key",
            token=None,
        )
        self.session.get_credentials.return_value.get_frozen_credentials.return_value = self.credentials

        self.create_session = patch.object(release.boto3, "Session", return_value=self.session).start()
        self.verify = patch.object(release, "verify_service").start()
        self.run = patch.object(release.subprocess, "run", side_effect=self.run_command).start()
        patch.object(release.subprocess, "check_output", return_value="commit\n").start()

    def describe_stack(self, *, StackName):
        if StackName == "chess-foundation":
            values = {
                "BackendDeploymentRole": "deployment-role",
                "ECSCluster": "chess",
                "ApiUrl": "https://api.example.test",
                "RepositoryUri": REPOSITORY,
            }
        elif not self.deployments:
            if self.previous_read_error:
                raise self.previous_read_error
            if self.first_release:
                raise ClientError({"Error": {
                    "Code": "ValidationError",
                    "Message": "Stack with id chess-backend does not exist",
                }}, "DescribeStacks")
            return {"Stacks": [{"Parameters": [
                {"ParameterKey": key, "ParameterValue": value}
                for key, value in self.previous_parameters.items()
            ]}]}
        else:
            if self.deployed_read_error:
                raise self.deployed_read_error
            values = {
                "TaskDefinition": "new-task" if len(self.deployments) == 1 else "previous-task",
                "Service": "backend-service",
            }

        return {"Stacks": [{"Outputs": [
            {"OutputKey": key, "OutputValue": value}
            for key, value in values.items()
        ]}]}

    def get_template(self, *, StackName):
        if StackName == "chess-foundation":
            return {"TemplateBody": FOUNDATION_TEMPLATE.read_text()}
        return {"TemplateBody": self.previous_template}

    def run_command(self, command, **options):
        if command[:3] == ["aws", "cloudformation", "deploy"]:
            template_path = Path(command[command.index("--template-file") + 1])
            start = command.index("--parameter-overrides") + 1
            end = command.index("--no-fail-on-empty-changeset")
            self.deployments.append({
                "template": template_path.read_text(),
                "parameters": command[start:end],
                "command": command,
                "environment": options["env"],
            })

        if command[:2] == ["docker", "login"]:
            docker_config = Path(options["env"]["DOCKER_CONFIG"])
            self.assertTrue(docker_config.is_dir())
            (docker_config / "config.json").write_text("temporary credentials")
            self.docker_configs.append(docker_config)

        if command[:2] == self.command_failure:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    def main(self, *arguments):
        with patch("sys.argv", ["release", *arguments]):
            return release.main()

    def assert_restored(self):
        self.assertEqual(len(self.deployments), 2)
        self.assertEqual(json.loads(self.deployments[1]["template"]), self.previous_template)
        self.assertEqual(self.deployments[1]["parameters"], [
            f"{key}={value}" for key, value in self.previous_parameters.items()
        ])
        self.assertEqual(self.verify.call_args_list, [
            call(self.ecs, "chess", "backend-service", "new-task", NEW_IMAGE),
            call(self.ecs, "chess", "backend-service", "previous-task", PREVIOUS_IMAGE),
        ])

    def test_successful_release_verifies_expected_image(self):
        self.assertEqual(self.main("--digest", NEW_IMAGE), 0)

        self.assertEqual(len(self.deployments), 1)
        self.assertEqual(self.deployments[0]["parameters"], [
            "FoundationStack=chess-foundation", f"ImageUri={NEW_IMAGE}",
        ])
        self.verify.assert_called_once_with(
            self.ecs, "chess", "backend-service", "new-task", NEW_IMAGE,
        )
        self.ecr.get_authorization_token.assert_not_called()
        self.assertIn(NEW_IMAGE, self.stdout.getvalue())

    def test_first_release_can_succeed_without_a_previous_stack(self):
        self.first_release = True

        self.assertEqual(self.main("--digest", NEW_IMAGE), 0)
        self.assertEqual(len(self.deployments), 1)
        self.verify.assert_called_once()

    def test_ecs_verification_failure_restores_full_release_and_still_fails(self):
        self.verify.side_effect = [VerificationError("Wrong running digest"), None]

        self.assertEqual(self.main("--digest", NEW_IMAGE), 1)
        self.assert_restored()
        self.assertIn("restored and verified", self.stderr.getvalue())

    def test_http_failure_restores_full_release_and_still_fails(self):
        original_run = self.run_command
        smoke_attempts = []

        def fail_first_smoke(command, **options):
            if command[:2] == ["bash", "infra/backend/smoke.sh"]:
                smoke_attempts.append(command)
                if len(smoke_attempts) == 1:
                    raise subprocess.CalledProcessError(22, command)
            return original_run(command, **options)

        self.run.side_effect = fail_first_smoke

        self.assertEqual(self.main("--digest", NEW_IMAGE), 1)
        self.assert_restored()
        self.assertEqual(len(smoke_attempts), 2)

    def test_sdk_verification_failure_also_restores(self):
        error = ClientError({"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "DescribeTasks")
        self.verify.side_effect = [error, None]

        self.assertEqual(self.main("--digest", NEW_IMAGE), 1)
        self.assert_restored()

    def test_failed_first_release_has_nothing_to_restore(self):
        self.first_release = True
        self.verify.side_effect = VerificationError("Unhealthy container")

        self.assertEqual(self.main("--digest", NEW_IMAGE), 1)
        self.assertEqual(len(self.deployments), 1)
        self.assertIn("no previous release", self.stderr.getvalue())

    def test_cloudformation_failure_does_not_enter_restore_path(self):
        self.command_failure = ["aws", "cloudformation"]

        self.assertEqual(self.main("--digest", NEW_IMAGE), 1)
        self.assertEqual(len(self.deployments), 1)
        self.verify.assert_not_called()

    def test_output_lookup_failure_does_not_enter_restore_path(self):
        self.deployed_read_error = ClientError({"Error": {
            "Code": "AccessDenied", "Message": "Cannot read deployed stack",
        }}, "DescribeStacks")

        self.assertEqual(self.main("--digest", NEW_IMAGE), 1)
        self.assertEqual(len(self.deployments), 1)
        self.verify.assert_not_called()

    def test_previous_stack_denied_or_other_validation_error_stops_deployment(self):
        errors = [
            ("AccessDenied", "Stack with id chess-backend does not exist"),
            ("ValidationError", "Stack with id another-stack does not exist"),
            ("ValidationError", "Stack with id chess-backend does not exist: more context"),
        ]
        for code, message in errors:
            with self.subTest(code=code, message=message):
                self.previous_read_error = ClientError({"Error": {
                    "Code": code, "Message": message,
                }}, "DescribeStacks")
                self.assertEqual(self.main("--digest", NEW_IMAGE), 1)

        self.assertEqual(self.deployments, [])
        self.verify.assert_not_called()

    def test_previous_yaml_template_is_preserved_verbatim_on_restore(self):
        self.previous_template = "Resources:\n  Previous: {Type: AWS::ECS::Service}\n"
        self.verify.side_effect = [VerificationError("Unhealthy"), None]

        self.assertEqual(self.main("--digest", NEW_IMAGE), 1)
        self.assertEqual(self.deployments[1]["template"], self.previous_template)

    def test_repository_and_digest_are_validated_before_deploy(self):
        invalid_images = [
            "other-repository@sha256:" + "a" * 64,
            REPOSITORY + ":latest",
            REPOSITORY + "@sha256:short",
            REPOSITORY + "@sha256:" + "A" * 64,
            NEW_IMAGE + "@extra",
        ]
        for image in invalid_images:
            with self.subTest(image=image):
                self.assertEqual(self.main("--digest", image), 1)

        self.assertEqual(self.deployments, [])
        self.verify.assert_not_called()

    def test_changed_foundation_stops_before_publishing(self):
        self.cloudformation.get_template.side_effect = None
        self.cloudformation.get_template.return_value = {"TemplateBody": {"Resources": {}}}

        self.assertEqual(self.main("local-image"), 1)
        self.run.assert_not_called()
        self.ecr.get_authorization_token.assert_not_called()

    def test_wrong_target_stops_before_publishing(self):
        os.environ["BACKEND_STACK"] = "other-backend"

        self.assertEqual(self.main("local-image"), 1)
        self.run.assert_not_called()
        self.ecr.get_authorization_token.assert_not_called()

    def test_wrong_account_stops_before_reading_or_publishing(self):
        self.sts.get_caller_identity.return_value = {"Account": "999999999999"}

        self.assertEqual(self.main("local-image"), 1)
        self.cloudformation.describe_stacks.assert_not_called()
        self.run.assert_not_called()

    def test_missing_github_key_stops_before_creating_a_session(self):
        del os.environ["AWS_SECRET_ACCESS_KEY"]

        self.assertEqual(self.main("local-image"), 1)
        self.create_session.assert_not_called()

    def test_local_session_does_not_require_github_keys(self):
        os.environ["GITHUB_ACTIONS"] = "false"
        del os.environ["AWS_ACCESS_KEY_ID"]
        del os.environ["AWS_SECRET_ACCESS_KEY"]

        self.assertEqual(self.main("--digest", NEW_IMAGE), 0)

    def test_publish_uses_password_stdin_and_cleans_docker_credentials(self):
        self.assertEqual(self.main("local-image"), 0)

        docker_calls = [entry for entry in self.run.call_args_list if entry.args[0][0] == "docker"]
        login = docker_calls[0]
        self.assertEqual(login.args[0], [
            "docker", "login", "--username", "AWS", "--password-stdin", REPOSITORY.split("/")[0],
        ])
        self.assertEqual(login.kwargs["input"], "password:with:colons\n")
        self.assertNotIn("password:with:colons", str(login.args))
        self.assertEqual(docker_calls[2].args[0], [
            "docker", "push", "--platform", "linux/amd64", REPOSITORY + ":commit-42-2",
        ])
        self.assertTrue(self.docker_configs)
        for directory in self.docker_configs:
            self.assertFalse(directory.exists())
        self.assertNotIn("password:with:colons", self.stdout.getvalue() + self.stderr.getvalue())
        self.ecr.batch_get_image.assert_called_once_with(
            repositoryName="backend", imageIds=[{"imageTag": "commit-42-2"}],
        )

    def test_login_failure_cleans_docker_credentials_without_deploying(self):
        self.command_failure = ["docker", "login"]

        self.assertEqual(self.main("local-image"), 1)
        self.assertEqual(self.deployments, [])
        self.assertTrue(self.docker_configs)
        self.assertTrue(all(not path.exists() for path in self.docker_configs))
        self.ecr.batch_get_image.assert_not_called()

    def test_push_failure_cleans_docker_credentials_without_deploying(self):
        self.command_failure = ["docker", "push"]

        self.assertEqual(self.main("local-image"), 1)
        self.assertEqual(self.deployments, [])
        self.assertTrue(self.docker_configs)
        self.assertTrue(all(not path.exists() for path in self.docker_configs))
        self.ecr.batch_get_image.assert_not_called()

    def test_missing_or_invalid_published_digest_stops_deployment(self):
        responses = [
            {"images": []},
            {"images": [], "failures": [{"failureCode": "ImageNotFound"}]},
            {"images": [{"imageId": {"imageDigest": "not-a-digest"}}]},
        ]
        for response in responses:
            with self.subTest(response=response):
                self.ecr.batch_get_image.return_value = response
                self.assertEqual(self.main("local-image"), 1)

        self.assertEqual(self.deployments, [])
        self.assertTrue(all(not path.exists() for path in self.docker_configs))

    def test_cli_uses_verified_session_credentials_and_discards_stale_tokens(self):
        os.environ["AWS_SESSION_TOKEN"] = "stale-session-token"
        os.environ["AWS_SECURITY_TOKEN"] = "stale-security-token"

        self.assertEqual(self.main("--digest", NEW_IMAGE), 0)

        deployment = self.deployments[0]
        environment = deployment["environment"]
        self.assertEqual(environment["AWS_ACCESS_KEY_ID"], "session-access-key")
        self.assertEqual(environment["AWS_SECRET_ACCESS_KEY"], "session-secret-key")
        self.assertNotIn("AWS_SESSION_TOKEN", environment)
        self.assertNotIn("AWS_SECURITY_TOKEN", environment)
        command = deployment["command"]
        self.assertEqual(command[command.index("--region") + 1], self.session.region_name)
        self.assertEqual(command[command.index("--role-arn") + 1], "deployment-role")

    def test_temporary_session_token_is_forwarded_to_cli(self):
        self.credentials.token = "verified-session-token"
        os.environ["AWS_SESSION_TOKEN"] = "stale-token"

        self.assertEqual(self.main("--digest", NEW_IMAGE), 0)
        self.assertEqual(
            self.deployments[0]["environment"]["AWS_SESSION_TOKEN"],
            "verified-session-token",
        )


if __name__ == "__main__":
    unittest.main()
