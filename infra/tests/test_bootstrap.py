import copy
import io
import json
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, call, patch

from botocore.exceptions import ClientError

from infra.bootstrap import parameters as bootstrap
from infra.templates import comparable_template


ACCOUNT = "123456789012"
REGION = "us-east-1"
STACK = "chess-foundation"
STACK_ID = f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/{STACK}/uuid"
EC2_ARN = f"arn:aws:ec2:{REGION}:{ACCOUNT}"
ELB_ARN = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}"
GROUP_ARN = f"arn:aws:autoscaling:{REGION}:{ACCOUNT}:autoScalingGroup:uuid:autoScalingGroupName/chess-asg"
SECRET_ARN = f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:database-url-suffix"


class BootstrapParametersTests(unittest.TestCase):
    def setUp(self):
        network = [
            "elastic-ip/eipalloc-abc123",
            "internet-gateway/igw-01",
            "launch-template/lt-01",
            "natgateway/nat-01",
            "route-table/rtb-01",
            "route-table/rtb-02",
            "route-table/rtb-03",
            "security-group/sg-01",
            "security-group/sg-02",
            "security-group/sg-03",
            "subnet/subnet-01",
            "subnet/subnet-02",
            "subnet/subnet-03",
            "subnet/subnet-04",
            "vpc-endpoint/vpce-01",
            "vpc/vpc-01",
        ]
        self.expected = {
            "FoundationStack": STACK,
            "AutoScalingGroupArn": GROUP_ARN,
            "CloudFrontResources": (
                f"arn:aws:cloudfront::{ACCOUNT}:distribution/distribution-01,"
                f"arn:aws:cloudfront::{ACCOUNT}:vpcorigin/origin-01"
            ),
            "NetworkResources": ",".join(f"{EC2_ARN}:{resource}" for resource in network),
            "CapacityProviderName": "chess-capacity",
            "LoadBalancerResources": (
                f"{ELB_ARN}:listener/app/01,"
                f"{ELB_ARN}:loadbalancer/app/01,"
                f"{ELB_ARN}:targetgroup/01"
            ),
            "DatabaseSecretArn": SECRET_ARN,
            "LaunchTemplateId": "lt-01",
            "BackendSecurityGroupId": "sg-02",
            "PrivateSubnetA": "subnet-03",
            "PrivateSubnetB": "subnet-04",
        }
        outputs = dict(self.expected)
        outputs["NetworkResources"] = ",".join(reversed(outputs["NetworkResources"].split(",")))
        self.stack = {
            "StackId": STACK_ID,
            "StackName": STACK,
            "StackStatus": "UPDATE_COMPLETE",
            "CreationTime": "2026-01-01T00:00:00Z",
            "LastUpdatedTime": "2026-11-07T00:00:00Z",
            "Parameters": [{"ParameterKey": "ECSClusterName", "ParameterValue": "chess"}],
            "Outputs": [
                {"OutputKey": f"Bootstrap{name}", "OutputValue": value}
                for name, value in outputs.items()
            ],
        }
        self.template = comparable_template(bootstrap.FOUNDATION.read_text())
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {
            "Account": ACCOUNT,
            "Arn": f"arn:aws:iam::{ACCOUNT}:user/operator",
        }
        self.cloudformation = Mock()
        self.cloudformation.describe_stacks.return_value = {"Stacks": [self.stack]}
        self.cloudformation.get_template.return_value = {"TemplateBody": self.template}
        self.session = Mock(region_name=REGION)
        self.session.client.side_effect = {
            "sts": self.sts,
            "cloudformation": self.cloudformation,
        }.__getitem__

    def parameters(self):
        return bootstrap.prepare_access_parameters(self.session, STACK, ACCOUNT)

    def main(self):
        environment = {
            "AWS_REGION": REGION,
            "AWS_ACCOUNT_ID": ACCOUNT,
            "FOUNDATION_STACK": STACK,
        }
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(bootstrap.boto3, "Session", return_value=self.session):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = bootstrap.main()
        return status, stdout.getvalue(), stderr.getvalue()

    def set_output(self, name, value):
        for output in self.stack["Outputs"]:
            if output["OutputKey"] == f"Bootstrap{name}":
                output["OutputValue"] = value
                return
        self.fail(f"Unknown test output: {name}")

    def test_exact_parameters_and_read_only_calls(self):
        expected = [
            {"ParameterKey": name, "ParameterValue": value}
            for name, value in self.expected.items()
        ]
        self.assertEqual(self.parameters(), expected)
        self.assertEqual(self.session.client.call_args_list, [call("sts"), call("cloudformation")])
        self.sts.get_caller_identity.assert_called_once_with()
        self.assertEqual(self.cloudformation.mock_calls, [
            call.describe_stacks(StackName=STACK),
            call.get_template(StackName=STACK_ID, TemplateStage="Original"),
            call.describe_stacks(StackName=STACK_ID),
        ])

    def test_cli_prints_only_complete_json(self):
        status, stdout, stderr = self.main()
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout), [
            {"ParameterKey": name, "ParameterValue": value}
            for name, value in self.expected.items()
        ])

    def test_missing_settings_and_wrong_account_stop_before_stack_reads(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(bootstrap.boto3, "Session") as session:
                with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(bootstrap.main(), 1)
                self.assertEqual(stdout.getvalue(), "")
                session.assert_not_called()

        self.sts.get_caller_identity.return_value["Account"] = "999999999999"
        with self.assertRaisesRegex(ValueError, "account does not match"):
            self.parameters()
        self.cloudformation.describe_stacks.assert_not_called()

    def test_only_successful_stable_stack_states_are_accepted(self):
        for status in ("CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"):
            with self.subTest(status=status):
                self.stack["StackStatus"] = status
                self.parameters()

        self.cloudformation.get_template.reset_mock()
        for status in ("UPDATE_IN_PROGRESS", "UPDATE_ROLLBACK_COMPLETE", "DELETE_FAILED", "IMPORT_IN_PROGRESS"):
            with self.subTest(status=status):
                self.stack["StackStatus"] = status
                with self.assertRaisesRegex(ValueError, "successful foundation deployment"):
                    self.parameters()
        self.cloudformation.get_template.assert_not_called()

    def test_stack_identity_must_match_account_region_partition_and_name(self):
        for identifier in (
            STACK_ID.replace(ACCOUNT, "999999999999"),
            STACK_ID.replace(REGION, "us-west-2"),
            STACK_ID.replace("arn:aws:", "arn:aws-cn:"),
            STACK_ID.replace(STACK, "other-foundation"),
        ):
            with self.subTest(identifier=identifier):
                self.stack["StackId"] = identifier
                with self.assertRaisesRegex(ValueError, "requested account, region and name"):
                    self.parameters()
        self.cloudformation.get_template.assert_not_called()

    def test_unreviewed_template_cannot_supply_permissions(self):
        changes = [
            ("Resources", "VPC", {"Type": "AWS::EC2::Subnet"}),
            ("Parameters", "ECSClusterName", {"Type": "String", "Default": "other"}),
            ("Outputs", "BootstrapNetworkResources", {"Value": f"{EC2_ARN}:vpc/vpc-other"}),
            ("Outputs", "TargetGroup", {"Value": "different-target"}),
        ]
        for section, name, value in changes:
            with self.subTest(section=section, name=name):
                changed = copy.deepcopy(self.template)
                changed[section][name] = value
                self.cloudformation.get_template.return_value = {"TemplateBody": changed}
                with self.assertRaisesRegex(ValueError, "Apply the reviewed foundation"):
                    self.parameters()

        old_template = copy.deepcopy(self.template)
        old_template["Outputs"] = {
            name: value for name, value in old_template["Outputs"].items()
            if not name.startswith("Bootstrap")
        }
        self.cloudformation.get_template.return_value = {"TemplateBody": old_template}
        with self.assertRaisesRegex(ValueError, "Apply the reviewed foundation"):
            self.parameters()

    def test_documentation_changes_do_not_require_permissions_update(self):
        applied = copy.deepcopy(self.template)
        applied["Metadata"] = {"owner": "operator"}
        applied["Description"] = "Older documentation"
        self.cloudformation.get_template.return_value = {"TemplateBody": applied}
        self.parameters()

    def test_changes_during_observation_cannot_produce_parameters(self):
        changes = {
            "StackId": STACK_ID + "-replaced",
            "StackStatus": "UPDATE_IN_PROGRESS",
            "CreationTime": "2026-11-07T01:00:00Z",
            "LastUpdatedTime": "2026-11-07T01:00:00Z",
            "Parameters": [],
            "Outputs": [],
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                confirmed = dict(self.stack, **{field: value})
                self.cloudformation.describe_stacks.side_effect = [
                    {"Stacks": [self.stack]}, {"Stacks": [confirmed]},
                ]
                status, stdout, stderr = self.main()
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn("changed while reading", stderr)

    def test_missing_output_never_prints_partial_parameters(self):
        original = self.stack["Outputs"]
        for name in self.expected:
            with self.subTest(name=name):
                self.stack["Outputs"] = [
                    output for output in original if output["OutputKey"] != f"Bootstrap{name}"
                ]
                status, stdout, stderr = self.main()
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn(f"Missing Bootstrap{name}", stderr)
                self.assertIn("Apply the foundation", stderr)

    def test_unknown_outputs_are_not_forwarded(self):
        self.stack["Outputs"].append({"OutputKey": "BootstrapAdministrator", "OutputValue": "*"})
        self.assertEqual(len(self.parameters()), 11)

    def test_destination_schema_rejects_invalid_values(self):
        for name in self.expected:
            with self.subTest(name=name):
                self.set_output(name, "*")
                status, stdout, stderr = self.main()
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn(f"Invalid bootstrap parameter: {name}", stderr)
                self.set_output(name, self.expected[name])

        self.set_output("NetworkResources", self.expected["NetworkResources"] + ",")
        with self.assertRaisesRegex(ValueError, "Invalid bootstrap parameter: NetworkResources"):
            self.parameters()

    def test_every_arn_must_belong_to_the_authenticated_target(self):
        for name, value in self.expected.items():
            if not value.startswith("arn:"):
                continue
            invalid = [
                value.replace(ACCOUNT, "999999999999"),
                value.replace("arn:aws:", "arn:aws-cn:"),
            ]
            if name == "CloudFrontResources":
                invalid.append(value.replace("cloudfront::", f"cloudfront:{REGION}:"))
            else:
                invalid.append(value.replace(REGION, "us-west-2"))
            for changed in invalid:
                with self.subTest(name=name, changed=changed):
                    self.set_output(name, changed)
                    with self.assertRaises(ValueError):
                        self.parameters()
            self.set_output(name, value)

    def test_foundation_output_cannot_target_a_different_stack(self):
        self.set_output("FoundationStack", "other-foundation")
        with self.assertRaisesRegex(ValueError, "does not match the requested foundation"):
            self.parameters()

    def test_sdk_failures_never_print_parameter_json(self):
        error = ClientError({"Error": {"Code": "AccessDenied", "Message": "Denied"}}, "Read")
        operations = (
            self.sts.get_caller_identity,
            self.cloudformation.describe_stacks,
            self.cloudformation.get_template,
        )
        for operation in operations:
            with self.subTest(operation=operation):
                operation.side_effect = error
                status, stdout, stderr = self.main()
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertIn("AccessDenied", stderr)
                operation.side_effect = None

    def test_template_outputs_preserve_the_original_resource_scopes(self):
        # Evaluate only the four intrinsic forms used in this output contract.
        # Expected values above are fixed independently of the template.
        identities = {
            "AWS::Partition": "aws",
            "AWS::Region": REGION,
            "AWS::AccountId": ACCOUNT,
            "AWS::StackName": STACK,
            "InternetGateway": "igw-01",
            "ECSLaunchTemplate": "lt-01",
            "NatGateway": "nat-01",
            "PublicRoutes": "rtb-01",
            "PrivateRoutesA": "rtb-02",
            "PrivateRoutesB": "rtb-03",
            "LoadBalancerSecurityGroup": "sg-01",
            "BackendSecurityGroup": "sg-02",
            "DatabaseSecurityGroup": "sg-03",
            "PublicSubnetA": "subnet-01",
            "PublicSubnetB": "subnet-02",
            "PrivateSubnetA": "subnet-03",
            "PrivateSubnetB": "subnet-04",
            "S3Endpoint": "vpce-01",
            "VPC": "vpc-01",
            "NatAddress.AllocationId": "eipalloc-abc123",
            "ECSAutoScalingGroup.AutoScalingGroupARN": GROUP_ARN,
            "ApiDistribution": "distribution-01",
            "ApiOrigin.Arn": f"arn:aws:cloudfront::{ACCOUNT}:vpcorigin/origin-01",
            "Listener": f"{ELB_ARN}:listener/app/01",
            "LoadBalancer": f"{ELB_ARN}:loadbalancer/app/01",
            "TargetGroup": f"{ELB_ARN}:targetgroup/01",
            "DatabaseUrl": SECRET_ARN,
            "AsgCapacityProvider": "chess-capacity",
        }

        def resolve(expression):
            operator, argument = next(iter(expression.items()))
            if operator == "Ref":
                return identities[argument]
            if operator == "Fn::GetAtt":
                return identities[".".join(argument)]
            if operator == "Fn::Sub":
                return re.sub(r"\$\{([^}]+)\}", lambda match: identities[match[1]], argument)
            if operator == "Fn::Join":
                separator, items = argument
                return separator.join(resolve(item) for item in items)
            self.fail(f"Unexpected output expression: {expression}")

        definitions = comparable_template(bootstrap.ACCESS.read_text())["Parameters"]
        self.assertEqual(list(definitions), list(self.expected))
        declared = [name for name in self.template["Outputs"] if name.startswith("Bootstrap")]
        self.assertEqual(declared, [f"Bootstrap{name}" for name in self.expected])
        for name, expected in self.expected.items():
            with self.subTest(parameter=name):
                output = self.template["Outputs"][f"Bootstrap{name}"]
                self.assertNotIn("Export", output)
                value = resolve(output["Value"])
                if name == "NetworkResources":
                    value = ",".join(sorted(value.split(",")))
                self.assertEqual(value, expected)


if __name__ == "__main__":
    unittest.main()
