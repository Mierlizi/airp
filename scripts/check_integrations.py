"""Exercise generated AIRP client integrations in isolated subprocesses."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from configure_client import configure_client


PROJECT = Path(__file__).resolve().parents[1]


def _run_hook(entry: Path, host: str, repository: Path) -> dict:
    payload = json.dumps({
        'hook_event_name': 'UserPromptSubmit',
        'cwd': str(repository),
        'prompt': 'What does the discount function return?',
    })
    completed = subprocess.run(
        [sys.executable, str(entry), '--host', host], input=payload,
        capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
    output = json.loads(completed.stdout)
    context = output['hookSpecificOutput']['additionalContext']
    if 'pricing.py::discount' not in context or 'def discount' not in context:
        raise RuntimeError(f'{host} did not receive expected AIRP evidence')
    events = (repository / '.airp' / 'hook-events.jsonl').read_text(
        encoding='utf-8').splitlines()
    event = json.loads(events[-1])
    if event.get('host') != host:
        raise RuntimeError(f'{host} diagnostic attribution was not preserved')
    return {'host': host, 'activation': event['activation'],
            'context_chars': len(context)}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='airp-client-check-') as folder:
        root = Path(folder)
        repository = root / 'repository'
        shutil.copytree(
            PROJECT / 'examples' / 'shop', repository,
            ignore=shutil.ignore_patterns('.airp', '__pycache__'))
        rows = []
        claude = root / 'claude'
        configure_client('claude-code', claude, repository, sys.executable)
        rows.append(_run_hook(
            claude / 'hooks' / 'user_prompt_submit.py', 'claude-code', repository))
        dsh = root / 'dsh'
        configure_client('deepseek-harness', dsh, repository, sys.executable)
        rows.append(_run_hook(
            dsh / 'plugin' / 'hooks' / 'user_prompt_submit.py',
            'deepseek-harness', repository))
        cursor = root / 'cursor'
        configure_client('cursor', cursor, repository, sys.executable)
        config = json.loads((cursor / 'mcp.json').read_text(encoding='utf-8'))
        server = config['mcpServers']['airp']
        status = subprocess.run(
            [server['command'], *server['args'][:-1], 'status'],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True)
        json.loads(status.stdout)
        rows.append({'host': 'cursor', 'launcher': 'passed',
                     'transport': 'stdio-mcp'})
        print(json.dumps({'cross_client_check': 'passed', 'rows': rows},
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
