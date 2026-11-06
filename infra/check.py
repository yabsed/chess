"""Check the infrastructure invariants that schema validation cannot express."""

import json
from pathlib import Path

from guardpycfn import validate_with_guard


rules = Path('infra/safety.guard').read_text()

for name in ('foundation', 'backend', 'deployment-access'):
    path = Path(f'infra/{name}.yaml')
    report = validate_with_guard(path.read_text(), rules, False)
    if not report['success'] or report['result']['status'] != 'PASS':
        raise SystemExit(json.dumps(report, indent=2))
    print(f'{path}: safety rules passed')
