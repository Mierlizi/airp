"""Validate the hook-first AIRP plugin against a disposable repository."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]


def check(plugin: Path) -> None:
    manifest = json.loads((plugin / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    assert manifest['name'] == 'airp'
    assert 'mcpServers' not in manifest
    assert not (plugin / '.mcp.json').exists()
    config = json.loads((plugin / 'hooks/hooks.json').read_text(encoding='utf-8'))
    handlers = config['hooks']['UserPromptSubmit'][0]['hooks']
    assert len(handlers) == 1 and handlers[0]['type'] == 'command'
    assert '${PLUGIN_ROOT}' in handlers[0]['command']
    source_plugin = (PROJECT / 'plugins' / 'airp').resolve()
    if plugin.resolve() == source_plugin:
        # Published source must remain portable. The installer intentionally
        # records the user's actual interpreter path in the installed copy.
        assert ('D:' + '/program projects') not in handlers[0]['command']
    assert (plugin / 'runtime/airp/hook.py').is_file()
    with tempfile.TemporaryDirectory(prefix='airp-plugin-') as folder:
        shutil.copytree(PROJECT / 'examples/shop', folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.airp', '__pycache__'))
        payload = json.dumps({
            'hook_event_name': 'UserPromptSubmit',
            'cwd': folder,
            'prompt': 'What does the discount function return?',
        })
        proc = subprocess.run(
            [sys.executable, str(plugin / 'hooks/user_prompt_submit.py')],
            input=payload, capture_output=True, text=True, encoding='utf-8', cwd=PROJECT)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        context = result['hookSpecificOutput']['additionalContext']
        assert 'pricing.py::discount' in context
        assert 'def discount' in context
        assert 'receipt_id' not in context
        print(json.dumps({'plugin_hook': 'passed', 'anchors': 1,
                          'context_chars': len(context), 'mcp_servers': 0}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--plugin', type=Path, default=PROJECT / 'plugins/airp')
    args = parser.parse_args()
    check(args.plugin.resolve())


if __name__ == '__main__':
    main()
