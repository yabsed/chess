"""Normalize Chess templates for comparison without changing their resource definitions."""

from copy import deepcopy
from typing import Any

from cfnlint.decode import decode_str


def comparable_template(body) -> dict[str, Any]:
    if isinstance(body, str):
        body, errors = decode_str(body)
        if errors:
            raise ValueError(errors)

    if not isinstance(body, dict):
        raise ValueError("Template must be a mapping.")

    body = deepcopy(body)

    # Root documentation does not affect resources. Resource metadata still does.
    body.pop("Metadata", None)
    body.pop("Description", None)

    # Older stacks recorded a byte hash; layout changes no longer need deployment.
    body.get("Parameters", {}).pop("TemplateRevision", None)
    body.get("Outputs", {}).pop("TemplateRevision", None)
    return body
