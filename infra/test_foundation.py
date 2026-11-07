"""Check the shell deployment guard without making AWS calls."""

import os
import subprocess
import tempfile
import unittest

from pathlib import Path


AWS_STUB = '''
aws() {
  case "$1 $2" in
    'sts get-caller-identity')
      echo 123456789012
      ;;
    'cloudformation describe-stacks')
      echo '{"Outputs":[{"OutputKey":"ArmMigrationPrepared","OutputValue":"true"}],"Parameters":[{"ParameterKey":"ECSImageId","ParameterValue":"ami-0123456789abcdef0"}]}'
      ;;
    'cloudformation list-imports')
      printf '%s\\n' "$IMPORT_RESPONSE"
      return "$IMPORT_STATUS"
      ;;
    'cloudformation set-stack-policy' | 'cloudformation deploy')
      printf '%s\\n' "$2" >> "$DEPLOY_CALLS"
      ;;
    *)
      echo "Unexpected AWS call: $1 $2" >&2
      return 99
      ;;
  esac
}
'''


class FoundationGuardTests(unittest.TestCase):
    def deploy(self, response, status=254):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            stub  = directory / "aws.sh"
            calls = directory / "calls"
            stub.write_text(AWS_STUB)

            result = subprocess.run(
                ["bash", "infra/foundation/deploy.sh"],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "BASH_ENV":         str(stub),
                    "DEPLOY_CALLS":     str(calls),
                    "AWS_ACCOUNT_ID":  "123456789012",
                    "FOUNDATION_STACK": "foundation",
                    "FOUNDATION_ROLE":  "foundation-role",
                    "IMPORT_RESPONSE":  response,
                    "IMPORT_STATUS":    str(status),
                },
            )
            return result, calls.read_text().splitlines() if calls.exists() else []

    def test_unreferenced_export_can_deploy(self):
        response = "An error occurred (ValidationError): Export 'foundation-TargetGroup' is not imported by any stack."
        result, calls = self.deploy(response)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["set-stack-policy", "deploy"])

    def test_other_errors_stop_before_mutation(self):
        for response in [
            "An error occurred (AccessDenied): not authorized to perform cloudformation:ListImports",
            "An error occurred (ValidationError): Export 'other-TargetGroup' is not imported by any stack.",
        ]:
            with self.subTest(response=response):
                result, calls = self.deploy(response)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_existing_consumer_stops_before_mutation(self):
        result, calls = self.deploy('["chess-backend"]', status=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
