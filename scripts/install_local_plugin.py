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


def validate_interpreter(executable: str | Path) -> tuple[int, int, int]:
    """Prove the Hook interpreter exists and meets AIRP's Python requirement."""
    path = Path(executable).resolve(strict=True)
    completed = subprocess.run(
        [str(path), '-I', '-c',
         'import json,sys; print(json.dumps(list(sys.version_info[:3])))'],
        text=True, capture_output=True, timeout=10, check=True)
    version = tuple(json.loads(completed.stdout))
    if version < (3, 11, 0):
        raise RuntimeError(
            f'AIRP requires Python 3.11+; {path} reports {version[0]}.{version[1]}.{version[2]}')
    return version


def configure_hook_interpreter(plugin: str | Path, executable: str | Path) -> Path:
    """Pin a validated absolute interpreter into an installed plugin copy."""
    plugin = Path(plugin).resolve(strict=True)
    interpreter = Path(executable).resolve(strict=True)
    validate_interpreter(interpreter)
    hooks_path = plugin / 'hooks' / 'hooks.json'
    hooks = json.loads(hooks_path.read_text(encoding='utf-8'))
    command_hook = hooks['hooks']['UserPromptSubmit'][0]['hooks'][0]
    command_hook['command'] = (
        f'"{interpreter.as_posix()}" "${{PLUGIN_ROOT}}/hooks/user_prompt_submit.py"')
    hooks_path.write_text(
        json.dumps(hooks, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return interpreter


def install(dry_run: bool = False) -> dict:
    interpreter = Path(sys.executable).resolve(strict=True)
    python_version = validate_interpreter(interpreter)
    home = Path.home()
    destination = home / 'plugins' / 'airp'
    marketplace = home / '.agents' / 'plugins' / 'marketplace.json'
    plan = {
        'plugin_source': str(PLUGIN),
        'plugin_destination': str(destination),
        'marketplace': str(marketplace),
        'command': ['codex', 'plugin', 'add', 'airp@personal', '--json'],
        'python': str(interpreter),
        'python_version': '.'.join(map(str, python_version)),
    }
    if dry_run:
        return {'dry_run': True, **plan}

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PLUGIN, destination, dirs_exist_ok=True)
    configure_hook_interpreter(destination, interpreter)
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
