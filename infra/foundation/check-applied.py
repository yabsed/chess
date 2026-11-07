"""Require the local foundation to match an AWS get-template response."""

import json
import sys
from pathlib import Path

from cfnlint.decode import decode_str


def comparable_template(body):
    if isinstance(body, str):
        body, errors = decode_str(body)
        if errors:
            raise ValueError(errors)

    # Root documentation does not affect resources. Resource metadata still does.
    body.pop('Metadata', None)
    body.pop('Description', None)

    # Older stacks recorded a byte hash; layout changes no longer need deployment.
    body.get('Parameters', {}).pop('TemplateRevision', None)
    body.get('Outputs', {}).pop('TemplateRevision', None)
    return body


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 infra/foundation/check-applied.py GET_TEMPLATE_JSON')

    applied = json.loads(Path(sys.argv[1]).read_text())['TemplateBody']
    expected = Path(__file__).with_name('template.yaml').read_text()

    if comparable_template(applied) != comparable_template(expected):
        raise SystemExit('Apply the foundation changes before deploying the backend.')
    print('Foundation matches the deployed template.')
