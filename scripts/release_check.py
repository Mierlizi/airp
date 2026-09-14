"""Fail when the GitHub release tree contains known publication blockers."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    'README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md', 'pyproject.toml',
    'plugins/airp/.codex-plugin/plugin.json', 'plugins/airp/hooks/hooks.json',
    'plugins/airp/runtime/airp/hook.py', 'plugins/airp/runtime/airp/diagnostics.py',
    'scripts/check_clean_install.py', 'benchmarks/published/v1_verified_results.json',
    'benchmarks/published/engineering-routing-local-v36.json',
]
SCAN_ROOTS = ['airp', 'plugins', 'scripts', 'docs', 'README.md', 'pyproject.toml']
BLOCKED = [re.compile('D:' + r'/program projects', re.I),
           re.compile('C:' + r'\\Users\\23747', re.I)]


def main() -> None:
    errors = []
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            errors.append(f'missing required file: {relative}')
    if not (ROOT / 'LICENSE').is_file():
        errors.append('missing LICENSE: maintainer must select an open-source license')
    for relative in SCAN_ROOTS:
        candidate = ROOT / relative
        paths = candidate.rglob('*') if candidate.is_dir() else [candidate]
        for path in paths:
            if not path.is_file() or path.suffix.lower() in {'.png', '.jpg', '.docx', '.pyc'}:
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            for pattern in BLOCKED:
                if pattern.search(text):
                    errors.append(f'machine-specific path in {path.relative_to(ROOT)}')
                    break
    json.loads((ROOT / 'benchmarks/published/v1_verified_results.json').read_text(encoding='utf-8'))
    status = {'ready': not errors, 'errors': errors}
    print(json.dumps(status, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not errors else 1)


if __name__ == '__main__':
    main()
