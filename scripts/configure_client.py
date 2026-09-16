"""Generate self-contained AIRP integrations for supported coding-agent hosts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

try:
    from .install_local_plugin import validate_interpreter
except ImportError:
    from install_local_plugin import validate_interpreter


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins' / 'airp'
TARGETS = ('claude-code', 'deepseek-harness', 'cursor')


def _copy_plugin(destination: Path) -> None:
    shutil.copytree(
        PLUGIN, destination, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))


def _hook_config(command: str) -> dict:
    return {
        'description': 'Inject compact AIRP source evidence before model requests.',
        'hooks': {'UserPromptSubmit': [{'hooks': [{
            'type': 'command', 'command': command, 'timeout': 30,
        }]}]},
    }


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')


def configure_client(target: str, output: str | Path, repository: str | Path,
                     executable: str | Path = sys.executable) -> dict:
    """Create a local, pinned integration without changing host settings."""
    if target not in TARGETS:
        raise ValueError(f'Unsupported client target: {target}')
    output = Path(output).resolve()
    repository = Path(repository).resolve(strict=True)
    if not repository.is_dir():
        raise NotADirectoryError(repository)
    interpreter = Path(executable).resolve(strict=True)
    validate_interpreter(interpreter)
    python = interpreter.as_posix()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(
            f'Output directory is not empty: {output}. Choose an empty directory.')
    output.mkdir(parents=True, exist_ok=True)

    if target == 'claude-code':
        _copy_plugin(output)
        hooks_path = output / 'claude' / 'hooks.json'
        command = (
            f'"{python}" "${{CLAUDE_PLUGIN_ROOT}}/hooks/user_prompt_submit.py" '
            '--host claude-code')
        _write_json(hooks_path, _hook_config(command))
        return {
            'target': target,
            'output': str(output),
            'python': str(interpreter),
            'next_command': ['claude --plugin-dir', str(output)],
        }

    bundled = output / 'plugin'
    _copy_plugin(bundled)
    launcher = (bundled / 'bin' / 'airp.py').resolve()
    if target == 'cursor':
        _write_json(output / 'mcp.json', {
            'mcpServers': {'airp': {
                'command': python,
                'args': [str(launcher), '--repo', str(repository), 'serve'],
            }},
        })
        return {
            'target': target,
            'output': str(output),
            'python': str(interpreter),
            'config': str(output / 'mcp.json'),
            'next_step': (
                'Merge mcpServers.airp from the generated config into '
                f'{repository / ".cursor" / "mcp.json"}.'),
        }

    hook_entry = (bundled / 'hooks' / 'user_prompt_submit.py').resolve().as_posix()
    hooks_path = (output / 'hooks.json').resolve()
    command = f'"{python}" "{hook_entry}" --host deepseek-harness'
    _write_json(hooks_path, _hook_config(command))
    _write_json(output / 'package.json', {
        'name': 'airp-dsh-adapter',
        'version': '0.8.0',
        'private': True,
        'dsh': {'bundle': {'patch': './cordis.patch.yml'}},
    })
    config_path = hooks_path.as_posix()
    patch = (
        '- insert:\n'
        '    - id: airp-context-hook\n'
        "      name: '@deepseek-ai/dsh-hooks-codex'\n"
        '      config:\n'
        f'        configPath: {json.dumps(config_path)}\n'
        "        model: ''\n")
    (output / 'cordis.patch.yml').write_text(patch, encoding='utf-8')
    return {
        'target': target,
        'output': str(output),
        'python': str(interpreter),
        'next_command': ['dsh plugin --profile', 'web', 'add', str(output)],
        'alternative_command': ['dsh', '--patch', str(output / 'cordis.patch.yml')],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate a pinned AIRP integration for a coding-agent host.')
    parser.add_argument('target', choices=TARGETS)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    print(json.dumps(configure_client(
        args.target, args.output, args.repo, args.python),
        ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

