"""Verify an AIRP plugin copy using a fresh stdlib-only Python environment."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import venv

from install_local_plugin import configure_hook_interpreter


PROJECT = Path(__file__).resolve().parents[1]


def _python(environment: Path) -> Path:
    windows = environment / 'Scripts' / 'python.exe'
    return windows if windows.exists() else environment / 'bin' / 'python'


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='airp-clean-install-') as folder:
        root = Path(folder)
        environment = root / 'venv'
        plugin = root / 'plugin'
        repository = root / 'repository'
        venv.EnvBuilder(with_pip=False, clear=True).create(environment)
        shutil.copytree(PROJECT / 'plugins' / 'airp', plugin)
        shutil.copytree(
            PROJECT / 'examples' / 'shop', repository,
            ignore=shutil.ignore_patterns('.airp', '__pycache__'))
        interpreter = configure_hook_interpreter(plugin, _python(environment))
        payload = json.dumps({
            'hook_event_name': 'UserPromptSubmit',
            'cwd': str(repository),
            'prompt': 'What does the discount function return?',
        })
        completed = subprocess.run(
            [str(interpreter), str(plugin / 'hooks' / 'user_prompt_submit.py')],
            input=payload, text=True, capture_output=True, encoding='utf-8',
            timeout=30, check=True)
        output = json.loads(completed.stdout)
        context = output['hookSpecificOutput']['additionalContext']
        if 'pricing.py::discount' not in context or 'def discount' not in context:
            raise RuntimeError('clean Hook did not return the expected evidence')
        report = subprocess.run(
            [str(interpreter), '-m', 'airp', '--repo', str(repository), 'hook-report'],
            cwd=plugin / 'runtime', text=True, capture_output=True, encoding='utf-8',
            timeout=30, check=True)
        metrics = json.loads(report.stdout)
        if metrics['events'] != 1 or metrics['by_activation'] != {'enabled': 1}:
            raise RuntimeError('clean Hook event was not recorded')
        print(json.dumps({
            'clean_environment': 'passed',
            'python': str(interpreter),
            'third_party_dependencies': 0,
            'hook_activation': 'enabled',
            'diagnostic_events': metrics['events'],
        }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
