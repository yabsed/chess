"""Validate templates, or compare the foundation with an AWS get-template response."""

import json
import sys
from pathlib import Path

from cfnlint.decode import decode_str
from guardpycfn import validate_with_guard


def template(body):
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
    if len(sys.argv) == 2:
        applied = json.loads(Path(sys.argv[1]).read_text())['TemplateBody']
        expected = Path('infra/foundation.yaml').read_text()
        if template(applied) != template(expected):
            raise SystemExit('Apply the foundation changes before deploying the backend.')
        print('Foundation matches the deployed resources.')

    else:
        rules = Path('infra/safety.guard').read_text()
        for path in sorted(Path('infra').glob('*.yaml')):
            report = validate_with_guard(path.read_text(), rules, False)
            if not report['success'] or report['result']['status'] != 'PASS':
                raise SystemExit(json.dumps(report, indent=2))
            print(f'{path}: safety rules passed')
