"""Exercise foundation plans and applies without credentials or AWS requests."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import boto3
from botocore.exceptions import ClientError, WaiterError
from botocore.stub import Stubber

from infra.foundation import deploy


ACCOUNT = "123456789012"
STACK_NAME = "foundation-test"
STACK_ID = f"arn:aws:cloudformation:ap-southeast-2:{ACCOUNT}:stack/{STACK_NAME}/stack-id"
CHANGE_SET_ARN = f"arn:aws:cloudformation:ap-southeast-2:{ACCOUNT}:changeSet/reviewed-plan/change-id"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/foundation-role"
REVISION = "commit=reviewed-commit template=reviewed-template"
TEMPLATE = "Resources: {}"
NO_CHANGES = "The submitted information didn't contain changes. Submit different information to create a change set."


def resource_change(**fields):
    resource = {
        "LogicalResourceId": "BackendSecurityGroup",
        "ResourceType": "AWS::EC2::SecurityGroup",
        "Action": "Modify",
        "Replacement": "False",
    }
    resource.update(fields)
    return {"ResourceChange": resource}


def reviewed_plan(**fields):
    plan = {
        "ChangeSetId": CHANGE_SET_ARN,
        "StackId": STACK_ID,
        "Description": REVISION,
        "Status": "CREATE_COMPLETE",
        "ExecutionStatus": "AVAILABLE",
        "Changes": [resource_change()],
    }
    plan.update(fields)
    return plan


def existing_stack(**fields):
    stack = {
        "StackId": STACK_ID,
        "StackName": STACK_NAME,
        "StackStatus": "UPDATE_COMPLETE",
        "CreationTime": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "Parameters": [
            {"ParameterKey": "ECSClusterName", "ParameterValue": "existing-cluster"},
            {"ParameterKey": "TemplateRevision", "ParameterValue": "obsolete"},
            {"ParameterKey": "DeploymentUserName", "ParameterValue": "existing-user"},
        ],
    }
    stack.update(fields)
    return {"Stacks": [stack]}


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.client = boto3.client(
            "cloudformation",
            region_name="ap-southeast-2",
            aws_access_key_id="test",
            aws_secret_access_key="test",
            aws_session_token="test",
        )
        self.stubber = Stubber(self.client)
        self.stubber.activate()
        self.addCleanup(self.stubber.deactivate)

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.plan_directory = Path(temporary.name)
        self.environment = patch.dict(os.environ, {
            "GITHUB_RUN_ID": "98765",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_STEP_SUMMARY": str(self.plan_directory / "summary.md"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.output = io.StringIO()
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def add_stack(self, **fields):
        self.stubber.add_response(
            "describe_stacks", existing_stack(**fields), {"StackName": STACK_NAME},
        )

    def add_plan(self, plan, **arguments):
        self.stubber.add_response(
            "describe_change_set", plan, {"ChangeSetName": CHANGE_SET_ARN, **arguments},
        )

    def add_creation(self):
        self.add_stack()
        self.stubber.add_response("create_change_set", {"Id": CHANGE_SET_ARN}, {
            "StackName": STACK_NAME,
            "ChangeSetName": "foundation-test-98765-2",
            "ChangeSetType": "UPDATE",
            "Description": REVISION,
            "TemplateBody": TEMPLATE,
            "Parameters": [
                {"ParameterKey": "ECSClusterName", "UsePreviousValue": True},
                {"ParameterKey": "DeploymentUserName", "UsePreviousValue": True},
            ],
            "RoleARN": ROLE,
            "Capabilities": ["CAPABILITY_NAMED_IAM"],
        })

    def run_plan(self):
        deploy.plan(self.client, STACK_NAME, ROLE, TEMPLATE, REVISION, self.plan_directory)

    def run_apply(self):
        deploy.apply(self.client, STACK_NAME, CHANGE_SET_ARN, REVISION, self.plan_directory)

    def test_plan_preserves_parameters_role_revision_and_all_pages(self):
        self.add_creation()
        self.add_plan(reviewed_plan())  # Waiter reaches CREATE_COMPLETE.
        self.add_plan(reviewed_plan(NextToken="page-two"))
        second = resource_change(LogicalResourceId="BackendLogGroup")
        self.add_plan(reviewed_plan(Changes=[second]), NextToken="page-two")
        first_event = {"EventId": "first", "EventType": "VALIDATION_ERROR"}
        second_event = {"EventId": "second", "EventType": "VALIDATION_ERROR"}
        self.stubber.add_response("describe_events", {
            "OperationEvents": [first_event], "NextToken": "events-two",
        }, {"ChangeSetName": CHANGE_SET_ARN})
        self.stubber.add_response("describe_events", {
            "OperationEvents": [second_event],
        }, {"ChangeSetName": CHANGE_SET_ARN, "NextToken": "events-two"})

        self.run_plan()

        change_set = json.loads((self.plan_directory / "changes.json").read_text())
        self.assertEqual(change_set["Description"], REVISION)
        self.assertEqual(change_set["StackId"], STACK_ID)
        self.assertEqual(change_set["Changes"], [resource_change(), second])
        self.assertNotIn("NextToken", change_set)
        validation = json.loads((self.plan_directory / "validation.json").read_text())
        self.assertEqual(validation["OperationEvents"], [first_event, second_event])
        self.assertIn(CHANGE_SET_ARN, self.output.getvalue())
        self.assertIn(REVISION, self.output.getvalue())
        self.assertIn("BackendLogGroup", (self.plan_directory / "summary.md").read_text())
        self.stubber.assert_no_pending_responses()

    def test_no_change_is_success_with_artifacts(self):
        self.add_creation()
        no_changes = reviewed_plan(Status="FAILED", StatusReason=NO_CHANGES, Changes=[])
        self.add_plan(no_changes)
        self.add_plan(no_changes)
        self.stubber.add_response("describe_events", {"OperationEvents": []}, {
            "ChangeSetName": CHANGE_SET_ARN,
        })

        self.run_plan()

        self.assertIn("Nothing to apply", self.output.getvalue())
        change_set = json.loads((self.plan_directory / "changes.json").read_text())
        self.assertEqual(change_set["Changes"], [])
        self.assertEqual(change_set["StatusReason"], NO_CHANGES)
        self.assertEqual(change_set["Description"], REVISION)
        self.assertEqual(change_set["StackId"], STACK_ID)
        self.assertTrue((self.plan_directory / "validation.json").exists())
        self.stubber.assert_no_pending_responses()

    def test_no_change_detection_rejects_other_failures(self):
        no_changes = reviewed_plan(Status="FAILED", StatusReason=NO_CHANGES, Changes=[])
        for fields in [
            {"Status": "CREATE_COMPLETE"},
            {"Changes": [resource_change()]},
            {"StatusReason": "Another failure mentioned no changes."},
            {"StatusReason": "Template validation failed."},
        ]:
            with self.subTest(fields=fields):
                self.assertFalse(deploy.has_no_changes(dict(no_changes, **fields)))

    def test_failed_plan_preserves_evidence_and_fails(self):
        self.add_creation()
        failure = reviewed_plan(Status="FAILED", StatusReason="Template validation failed.")
        self.add_plan(failure)
        self.add_plan(failure)
        self.stubber.add_response("describe_events", {"OperationEvents": []}, {
            "ChangeSetName": CHANGE_SET_ARN,
        })

        with self.assertRaisesRegex(RuntimeError, "Template validation failed"):
            self.run_plan()

        self.assertTrue((self.plan_directory / "changes.json").exists())
        self.assertTrue((self.plan_directory / "validation.json").exists())
        self.stubber.assert_no_pending_responses()

    def test_denied_validation_is_not_a_successful_plan(self):
        self.add_creation()
        self.add_plan(reviewed_plan())
        self.add_plan(reviewed_plan())
        self.stubber.add_client_error("describe_events", "AccessDenied", expected_params={
            "ChangeSetName": CHANGE_SET_ARN,
        })

        with self.assertRaises(ClientError):
            self.run_plan()

        self.assertTrue((self.plan_directory / "changes.json").exists())
        self.assertFalse((self.plan_directory / "validation.json").exists())
        self.stubber.assert_no_pending_responses()

    def test_missing_or_denied_stack_never_creates_or_executes(self):
        for action in [self.run_plan, self.run_apply]:
            for code, message in [
                ("AccessDenied", "Access denied"),
                ("ValidationError", f"Stack with id {STACK_NAME} does not exist"),
            ]:
                with self.subTest(action=action.__name__, code=code):
                    self.stubber.add_client_error(
                        "describe_stacks", code, message,
                        expected_params={"StackName": STACK_NAME},
                    )
                    with self.assertRaises(ClientError):
                        action()
                    self.stubber.assert_no_pending_responses()

    def test_apply_orders_policy_execution_and_wait_with_rollback_enabled(self):
        self.add_stack()
        self.add_plan(reviewed_plan())
        self.stubber.add_response("set_stack_policy", {}, {
            "StackName": STACK_NAME,
            "StackPolicyBody": deploy.STACK_POLICY.read_text(),
        })
        self.stubber.add_response("execute_change_set", {}, {
            "ChangeSetName": CHANGE_SET_ARN,
            "DisableRollback": False,
        })
        self.add_stack()

        self.run_apply()

        self.stubber.assert_no_pending_responses()

    def test_apply_rejects_unreviewed_or_unavailable_plans_before_mutations(self):
        for fields in [
            {"StackId": "other-stack"},
            {"Description": "commit=other template=reviewed-template"},
            {"Description": "commit=reviewed-commit template=other"},
            {"Status": "FAILED"},
            {"Status": "CREATE_IN_PROGRESS"},
            {"ExecutionStatus": "OBSOLETE"},
            {"ExecutionStatus": "EXECUTE_COMPLETE"},
        ]:
            with self.subTest(fields=fields):
                self.add_stack()
                self.add_plan(reviewed_plan(**fields))
                with self.assertRaisesRegex(ValueError, "Run plan again"):
                    self.run_apply()
                self.stubber.assert_no_pending_responses()

    def test_apply_rejects_resource_additions_removals_replacements_and_iam(self):
        for fields in [
            {"Action": "Add"},
            {"Action": "Remove"},
            {"Replacement": "True"},
            {"Replacement": "Conditional"},
            {"ResourceType": "AWS::IAM::Role"},
            {"ResourceType": "AWS::IAM::Policy"},
        ]:
            with self.subTest(fields=fields):
                self.add_stack()
                self.add_plan(reviewed_plan(Changes=[resource_change(**fields)]))
                with self.assertRaisesRegex(ValueError, "trusted operator"):
                    self.run_apply()
                self.stubber.assert_no_pending_responses()

    def test_apply_checks_changes_on_later_pages_before_any_mutation(self):
        self.add_stack()
        self.add_plan(reviewed_plan(NextToken="page-two"))
        self.add_plan(reviewed_plan(Changes=[resource_change(ResourceType="AWS::IAM::Role")]),
                      NextToken="page-two")

        with self.assertRaisesRegex(ValueError, "IAM"):
            self.run_apply()

        self.stubber.assert_no_pending_responses()

    def test_missing_resource_fields_fail_closed(self):
        for field in ["Action", "Replacement", "ResourceType"]:
            with self.subTest(field=field):
                change = resource_change()
                del change["ResourceChange"][field]
                self.add_stack()
                self.add_plan(reviewed_plan(Changes=[change]))
                with self.assertRaises(ValueError):
                    self.run_apply()
                self.stubber.assert_no_pending_responses()

    def test_denied_plan_does_not_change_policy(self):
        self.add_stack()
        self.stubber.add_client_error("describe_change_set", "AccessDenied", expected_params={
            "ChangeSetName": CHANGE_SET_ARN,
        })

        with self.assertRaises(ClientError):
            self.run_apply()

        self.stubber.assert_no_pending_responses()

    def test_policy_failure_prevents_execution(self):
        self.add_stack()
        self.add_plan(reviewed_plan())
        self.stubber.add_client_error("set_stack_policy", "AccessDenied", expected_params={
            "StackName": STACK_NAME,
            "StackPolicyBody": deploy.STACK_POLICY.read_text(),
        })

        with self.assertRaises(ClientError):
            self.run_apply()

        self.stubber.assert_no_pending_responses()

    def test_failed_update_waiter_remains_failure(self):
        self.add_stack()
        self.add_plan(reviewed_plan())
        self.stubber.add_response("set_stack_policy", {}, {
            "StackName": STACK_NAME,
            "StackPolicyBody": deploy.STACK_POLICY.read_text(),
        })
        self.stubber.add_response("execute_change_set", {}, {
            "ChangeSetName": CHANGE_SET_ARN,
            "DisableRollback": False,
        })
        self.add_stack(StackStatus="UPDATE_ROLLBACK_COMPLETE")

        with self.assertRaises(WaiterError):
            self.run_apply()

        self.stubber.assert_no_pending_responses()


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "AWS_REGION": "ap-southeast-2",
            "AWS_ACCOUNT_ID": ACCOUNT,
            "FOUNDATION_STACK": STACK_NAME,
            "FOUNDATION_ROLE": "foundation-role",
        }
        self.output = io.StringIO()

    def test_missing_settings_fail_before_session_creation(self):
        for name in self.settings:
            settings = dict(self.settings)
            del settings[name]
            with self.subTest(name=name), patch.dict(os.environ, settings, clear=True):
                with patch.object(deploy.boto3, "Session") as session, redirect_stderr(self.output):
                    self.assertEqual(deploy.main(["plan"]), 1)
                    session.assert_not_called()
                    self.assertIn(name, self.output.getvalue())

    def test_ci_requires_both_infra_secrets_before_session_creation(self):
        for key, secret in [
            ("AWS_ACCESS_KEY_ID", "AWS_INFRA_ACCESS_KEY_ID"),
            ("AWS_SECRET_ACCESS_KEY", "AWS_INFRA_SECRET_ACCESS_KEY"),
        ]:
            settings = dict(self.settings, GITHUB_ACTIONS="true",
                            AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test")
            del settings[key]
            with self.subTest(key=key), patch.dict(os.environ, settings, clear=True):
                with patch.object(deploy.boto3, "Session") as session, redirect_stderr(self.output):
                    self.assertEqual(deploy.main(["plan"]), 1)
                    session.assert_not_called()
                    self.assertIn(secret, self.output.getvalue())

    def test_wrong_account_fails_before_cloudformation_access(self):
        session = Mock()
        session.client.return_value.get_caller_identity.return_value = {"Account": "other-account"}
        with patch.dict(os.environ, self.settings, clear=True):
            with patch.object(deploy.boto3, "Session", return_value=session), redirect_stderr(self.output):
                self.assertEqual(deploy.main(["plan"]), 1)
                session.client.assert_called_once_with("sts")

    def test_local_profile_and_explicit_region_preserve_checkout_binding(self):
        session = Mock()
        session.client.return_value.get_caller_identity.return_value = {"Account": ACCOUNT}
        with tempfile.TemporaryDirectory() as temporary:
            settings = dict(self.settings, AWS_PROFILE="operator", RUNNER_TEMP=temporary)
            with patch.dict(os.environ, settings, clear=True):
                with patch.object(deploy.boto3, "Session", return_value=session) as constructor:
                    with patch.object(deploy.subprocess, "check_output", return_value="commit-id\n"):
                        with patch.object(deploy, "plan", autospec=True) as plan:
                            self.assertEqual(deploy.main(["plan", ""]), 0)

                constructor.assert_called_once_with(region_name="ap-southeast-2")
                template = deploy.TEMPLATE.read_bytes()
                digest = hashlib.sha256(template).hexdigest()
                plan.assert_called_once_with(
                    session.client.return_value, STACK_NAME, ROLE, template.decode(),
                    f"commit=commit-id template={digest}", Path(temporary),
                )

    def test_invalid_arguments_fail_before_any_aws_access(self):
        for args in [[], ["destroy"], ["apply"]]:
            with self.subTest(args=args), patch.object(deploy.boto3, "Session") as session:
                with redirect_stderr(self.output), self.assertRaises(SystemExit) as error:
                    deploy.main(args)
                self.assertEqual(error.exception.code, 2)
                session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
