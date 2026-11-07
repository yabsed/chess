import copy
import unittest

from infra.foundation.check_applied import comparable_template


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
