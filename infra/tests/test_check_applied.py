import copy
import json
import unittest
from unittest.mock import patch

from infra.foundation.check_applied import check_applied, comparable_template


class AppliedTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = {
            "Description": "Deployment documentation",
            "Metadata": {"owner": "operator"},
            "Parameters": {"Name": {"Type": "String"}},
            "Outputs": {"Name": {"Value": {"Ref": "Name"}}},
            "Resources": {
                "Server": {
                    "Type": "AWS::ECS::Service",
                    "Metadata": {"reason": "Preserve this resource metadata"},
                    "Properties": {"DesiredCount": 1},
                },
            },
        }

    def test_documentation_and_legacy_revision_do_not_change_resources(self):
        previous = copy.deepcopy(self.template)
        previous["Description"] = "Older documentation"
        previous["Metadata"] = {"owner": "previous operator"}
        previous["Parameters"]["TemplateRevision"] = {"Type": "String"}
        previous["Outputs"]["TemplateRevision"] = {"Value": "old hash"}

        self.assertEqual(comparable_template(previous), comparable_template(self.template))

    def test_comparison_keeps_the_original_response_intact(self):
        original = copy.deepcopy(self.template)
        comparable_template(self.template)
        self.assertEqual(self.template, original)

    def test_backend_accepts_bootstrap_output_changes_without_mutating_the_response(self):
        self.template["Outputs"]["BootstrapNetworkResources"] = {"Value": "current"}

        missing = copy.deepcopy(self.template)
        del missing["Outputs"]["BootstrapNetworkResources"]

        added = copy.deepcopy(self.template)
        added["Outputs"]["BootstrapSecurityGroups"] = {"Value": "new"}

        changed = copy.deepcopy(self.template)
        changed["Outputs"]["BootstrapNetworkResources"]["Value"] = "previous"

        for name, applied in [("missing", missing), ("added", added), ("changed", changed)]:
            with self.subTest(change=name):
                original = copy.deepcopy(applied)
                with patch(
                    "infra.foundation.check_applied.Path.read_text",
                    return_value=json.dumps(self.template),
                ):
                    check_applied(applied)

                self.assertEqual(applied, original)

    def test_backend_still_rejects_deployment_changes(self):
        self.template["Outputs"]["BootstrapNetworkResources"] = {
            "Value": "current",
            "Export": {"Name": "network-resources"},
        }
        changes = [
            (("Outputs", "Name", "Value"), "different output"),
            (("Outputs", "BootstrapNetworkResources", "Value"), "different export"),
            (("Resources", "Server", "Properties"), {"DesiredCount": 2}),
            (("Resources", "Server", "Metadata"), {"changed": True}),
            (("Parameters", "Name", "Type"), "Number"),
        ]

        for path, value in changes:
            with self.subTest(field=".".join(path)):
                applied = copy.deepcopy(self.template)
                parent = applied
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
                original = copy.deepcopy(applied)

                with patch(
                    "infra.foundation.check_applied.Path.read_text",
                    return_value=json.dumps(self.template),
                ):
                    with self.assertRaisesRegex(ValueError, "Apply the foundation"):
                        check_applied(applied)

                self.assertEqual(applied, original)

    def test_bootstrap_comparison_keeps_its_output_contract_strict(self):
        self.template["Outputs"]["BootstrapNetworkResources"] = {"Value": "current"}
        previous = copy.deepcopy(self.template)
        previous["Outputs"]["BootstrapNetworkResources"]["Value"] = "previous"

        comparable = comparable_template(self.template)
        self.assertEqual(
            comparable["Outputs"]["BootstrapNetworkResources"],
            {"Value": "current"},
        )
        self.assertNotEqual(comparable_template(previous), comparable)

    def test_resource_changes_and_resource_metadata_remain_significant(self):
        for field in ["Properties", "Metadata"]:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.template)
                changed["Resources"]["Server"][field] = {"changed": True}
                self.assertNotEqual(
                    comparable_template(changed),
                    comparable_template(self.template),
                )

    def test_yaml_intrinsics_equal_the_structured_sdk_response(self):
        yaml = """
Parameters:
  Name:
    Type: String
Outputs:
  Name:
    Value: !Ref Name
Resources:
  Server:
    Type: AWS::ECS::Service
    Metadata:
      reason: Preserve this resource metadata
    Properties:
      DesiredCount: 1
"""
        self.assertEqual(comparable_template(yaml), comparable_template(self.template))


if __name__ == "__main__":
    unittest.main()
