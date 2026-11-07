"""Check the safety rules for the three infrastructure templates."""

import json
from pathlib import Path

from guardpycfn import validate_with_guard


infra = Path(__file__).parent
rules = (infra / 'safety.guard').read_text()

templates = (
    'backend/template.yaml',
    'foundation/template.yaml',
    'bootstrap/access.yaml',
)

for name in templates:
    path = infra / name
    report = validate_with_guard(path.read_text(), rules, False)
    if not report['success'] or report['result']['status'] != 'PASS':
        raise SystemExit(json.dumps(report, indent=2))
    print(f'infra/{name}: safety rules passed')
