"""Check destructive migration boundaries without accessing AWS."""

import copy
import json
import os
import subprocess
import tempfile
import unittest

from pathlib       import Path
from unittest.mock import patch

from infra.migrate import prepare


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.backup = Path(self.directory.name) / "before-arm"

        self.foundation = {
            "Resources": {
                "NatAddress":            {"Type": "AWS::EC2::EIP"},
                "NatGateway":            {"Type": "AWS::EC2::NatGateway"},
                "PrivateInternetRouteA": {"Type": "AWS::EC2::Route"},
                "PrivateInternetRouteB": {"Type": "AWS::EC2::Route"},
                "TargetGroup":           {"Type": "AWS::ElasticLoadBalancingV2::TargetGroup"},
                "Database": {
                    "Type": "AWS::RDS::DBInstance",
                    "DeletionPolicy": "Retain",
                    "UpdateReplacePolicy": "Retain",
                    "Properties": {"DeletionProtection": True},
                },
            },
        }
        self.backend = {
            "Resources": {
                "Service":        {"Type": "AWS::ECS::Service", "DeletionPolicy": "Retain"},
                "TaskDefinition": {"Type": "AWS::ECS::TaskDefinition", "DeletionPolicy": "Retain"},
            },
        }
        self.imports = ["chess-backend"]
        self.calls = []

        environment = patch.dict(os.environ, {
            "AWS_ACCOUNT_ID":   "123456789012",
            "FOUNDATION_STACK": "foundation",
            "FOUNDATION_ROLE":  "foundation-role",
            "BACKEND_STACK":    "chess-backend",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def read(self, service, operation, *arguments):
        if operation == "get-caller-identity":
            return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/operator"}
        if operation == "describe-stacks":
            outputs = {"ECSCluster": "chess", "BackendDeploymentRole": "backend-role"}
            return {"Stacks": [{"Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in outputs.items()]}]}
        if operation == "list-imports":
            return {"Imports": self.imports}
        if operation == "get-template":
            template = self.foundation if arguments[-1] == "foundation" else self.backend
            return {"TemplateBody": copy.deepcopy(template)}
        raise AssertionError((service, operation, arguments))

    def record(self, *arguments):
        self.calls.append(arguments)

    def prepare(self, run=None):
        with patch("infra.migrate.read_aws", side_effect=self.read):
            with patch("infra.migrate.run_aws", side_effect=run or self.record):
                prepare(self.backup)

    def test_preserves_database_and_backups_before_deleting_only_backend(self):
        self.prepare()

        before = json.loads((self.backup / "foundation-before.json").read_text())
        staged = json.loads((self.backup / "foundation-disposable.json").read_text())
        self.assertEqual(before, self.foundation)
        self.assertEqual(staged["Resources"]["Database"], before["Resources"]["Database"])
        self.assertEqual(staged["Resources"]["NatGateway"]["DeletionPolicy"], "Delete")

        operations = [call[1] for call in self.calls]
        self.assertEqual(operations, ["set-stack-policy", "deploy", "deploy", "delete-stack", "wait"])
        self.assertEqual(self.calls[-2][2:4], ("--stack-name", "chess-backend"))

    def test_rejects_another_export_consumer_before_mutating(self):
        self.imports.append("another-app")
        with self.assertRaisesRegex(ValueError, "Expected only"):
            self.prepare()
        self.assertEqual(self.calls, [])

    def test_rejects_stateful_backend_resources(self):
        self.backend["Resources"]["Database"] = {"Type": "AWS::RDS::DBInstance"}
        with self.assertRaisesRegex(ValueError, "Unexpected backend resources"):
            self.prepare()
        self.assertEqual(self.calls, [])

    def test_retention_failure_does_not_stop_backend(self):
        def fail_deploy(*arguments):
            self.record(*arguments)
            if arguments[1] == "deploy":
                raise subprocess.CalledProcessError(1, arguments)

        with self.assertRaises(subprocess.CalledProcessError):
            self.prepare(fail_deploy)
        self.assertNotIn("delete-stack", [call[1] for call in self.calls])
        self.assertTrue((self.backup / "backend-before.json").exists())


if __name__ == "__main__":
    unittest.main()
