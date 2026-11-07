"""Require the local foundation to match the template deployed in AWS."""

from copy import deepcopy
from pathlib import Path

from cfnlint.decode import decode_str


def comparable_template(body):
    if isinstance(body, str):
        body, errors = decode_str(body)
        if errors:
            raise ValueError(errors)

    body = deepcopy(body)

    # Root documentation does not affect resources. Resource metadata still does.
    body.pop("Metadata", None)
    body.pop("Description", None)

    # Older stacks recorded a byte hash; layout changes no longer need deployment.
    body.get("Parameters", {}).pop("TemplateRevision", None)
    body.get("Outputs", {}).pop("TemplateRevision", None)
    return body


def check_applied(applied):
    expected = comparable_template(Path(__file__).with_name("template.yaml").read_text())
    applied = comparable_template(applied)

    # Bootstrap-only outputs do not participate in backend releases. Bootstrap checks
    # the entire template separately before using them to prepare IAM parameters.
    for template in (applied, expected):
        outputs = template.get("Outputs", {})
        for name, output in list(outputs.items()):
            if name.startswith("Bootstrap") and "Export" not in output:
                del outputs[name]

    if applied != expected:
        raise ValueError("Apply the foundation changes before deploying the backend.")
    print("Foundation matches the deployed template.")
