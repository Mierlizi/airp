import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from airp.protocol import ContextRequest, render_hook_output
from scripts.configure_client import configure_client


PROJECT = Path(__file__).resolve().parents[1]


class CrossClientIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = self.root / 'repository'
        shutil.copytree(PROJECT / 'examples' / 'shop', self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_request_contract_normalizes_supported_hook_hosts(self):
        payload = {
            'hook_event_name': 'UserPromptSubmit',
            'cwd': str(self.repository),
            'prompt': 'Explain the discount function.',
        }
        for host in ('codex', 'claude-code', 'deepseek-harness'):
            request = ContextRequest.from_hook(payload, host=host)
            self.assertEqual(host, request.host)
            self.assertEqual(self.repository, request.root)
            self.assertEqual('Explain the discount function.', request.prompt)
        with self.assertRaises(ValueError):
            ContextRequest.from_hook(payload, host='unknown-host')

    def test_shared_hook_renderer_emits_claude_and_codex_contract(self):
        expected = {'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': '<airp-context>evidence</airp-context>',
        }}
        self.assertEqual(expected, render_hook_output(
            '<airp-context>evidence</airp-context>'))
        self.assertIsNone(render_hook_output(None))

    def test_precontext_cli_returns_auditable_generic_json(self):
        completed = subprocess.run(
            [sys.executable, '-m', 'airp', '--repo', str(self.repository),
             'precontext', 'Explain the discount function.', '--host', 'generic',
             '--format', 'json'],
            cwd=PROJECT, capture_output=True, text=True, encoding='utf-8')

        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual('generic', result['host'])
        self.assertEqual('enabled', result['activation'])
        self.assertIn('pricing.py::discount', result['context'])
        event = json.loads((self.repository / '.airp' / 'hook-events.jsonl').read_text(
            encoding='utf-8').splitlines()[-1])
        self.assertEqual('generic', event['host'])

    def test_claude_marketplace_points_to_the_bundled_plugin(self):
        marketplace = json.loads(
            (PROJECT / '.claude-plugin' / 'marketplace.json').read_text(
                encoding='utf-8'))
        entry = marketplace['plugins'][0]
        manifest = json.loads(
            (PROJECT / 'plugins' / 'airp' / '.claude-plugin' / 'plugin.json').read_text(
                encoding='utf-8'))
        self.assertEqual('airp-marketplace', marketplace['name'])
        self.assertEqual('./plugins/airp', entry['source'])
        self.assertEqual(manifest['version'], entry['version'])

    def test_claude_generator_builds_self_contained_pinned_plugin(self):
        output = self.root / 'claude-airp'
        result = configure_client(
            'claude-code', output, self.repository, sys.executable)

        manifest = json.loads((output / '.claude-plugin' / 'plugin.json').read_text(
            encoding='utf-8'))
        hooks = json.loads((output / 'claude' / 'hooks.json').read_text(
            encoding='utf-8'))
        command = hooks['hooks']['UserPromptSubmit'][0]['hooks'][0]['command']
        self.assertEqual('airp', manifest['name'])
        self.assertIn(Path(sys.executable).resolve().as_posix(), command)
        self.assertIn('--host claude-code', command)
        self.assertTrue((output / 'runtime' / 'airp' / 'hook.py').is_file())
        self.assertEqual('claude --plugin-dir', result['next_command'][0])

    def test_deepseek_generator_uses_official_codex_hook_bridge(self):
        output = self.root / 'dsh-airp'
        result = configure_client(
            'deepseek-harness', output, self.repository, sys.executable)

        hooks = json.loads((output / 'hooks.json').read_text(encoding='utf-8'))
        command = hooks['hooks']['UserPromptSubmit'][0]['hooks'][0]['command']
        patch = (output / 'cordis.patch.yml').read_text(encoding='utf-8')
        package = json.loads((output / 'package.json').read_text(encoding='utf-8'))
        self.assertIn('--host deepseek-harness', command)
        self.assertIn("@deepseek-ai/dsh-hooks-codex", patch)
        self.assertIn((output / 'hooks.json').resolve().as_posix(), patch)
        self.assertEqual('0.9.0', package['version'])
        self.assertEqual('./cordis.patch.yml', package['dsh']['bundle']['patch'])
        self.assertEqual('dsh plugin --profile', result['next_command'][0])

    def test_cursor_generator_uses_bundled_stdio_mcp_launcher(self):
        output = self.root / 'cursor-airp'
        configure_client('cursor', output, self.repository, sys.executable)

        config = json.loads((output / 'mcp.json').read_text(encoding='utf-8'))
        server = config['mcpServers']['airp']
        self.assertEqual(Path(sys.executable).resolve().as_posix(), server['command'])
        self.assertEqual(str(self.repository.resolve()), server['args'][2])
        self.assertEqual('serve', server['args'][3])
        self.assertTrue(Path(server['args'][0]).is_file())

    def test_generator_refuses_nonempty_output_directory(self):
        output = self.root / 'existing-client'
        output.mkdir()
        sentinel = output / 'keep.txt'
        sentinel.write_text('do not overwrite', encoding='utf-8')

        with self.assertRaises(FileExistsError):
            configure_client('claude-code', output, self.repository, sys.executable)

        self.assertEqual('do not overwrite', sentinel.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
