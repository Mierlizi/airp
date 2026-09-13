"""Install AIRP from a Git checkout into the user's personal Codex marketplace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins' / 'airp'


def install(dry_run: bool = False) -> dict:
    home = Path.home()
    destination = home / 'plugins' / 'airp'
    marketplace = home / '.agents' / 'plugins' / 'marketplace.json'
    plan = {
        'plugin_source': str(PLUGIN),
        'plugin_destination': str(destination),
        'marketplace': str(marketplace),
        'command': ['codex', 'plugin', 'add', 'airp@personal', '--json'],
        'python': sys.executable,
    }
    if dry_run:
        return {'dry_run': True, **plan}

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PLUGIN, destination, dirs_exist_ok=True)
    hooks_path = destination / 'hooks' / 'hooks.json'
    hooks = json.loads(hooks_path.read_text(encoding='utf-8'))
    command_hook = hooks['hooks']['UserPromptSubmit'][0]['hooks'][0]
    interpreter = sys.executable.replace('\\', '/')
    command_hook['command'] = f'"{interpreter}" "${{PLUGIN_ROOT}}/hooks/user_prompt_submit.py"'
    hooks_path.write_text(json.dumps(hooks, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    if marketplace.exists():
        document = json.loads(marketplace.read_text(encoding='utf-8'))
    else:
        document = {'name': 'personal', 'interface': {'displayName': 'Personal'},
                    'plugins': []}
    plugins = [item for item in document.setdefault('plugins', [])
               if item.get('name') != 'airp']
    plugins.append({
        'name': 'airp',
        'source': {'source': 'local', 'path': './plugins/airp'},
        'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'},
        'category': 'Productivity',
    })
    document['plugins'] = plugins
    marketplace.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    completed = subprocess.run(plan['command'], text=True, capture_output=True, check=True)
    return {'dry_run': False, **plan, 'codex': json.loads(completed.stdout)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(install(args.dry_run), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
