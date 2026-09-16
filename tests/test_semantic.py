import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from airp.core import AirpError, Repository
from airp.semantic import index_fingerprint


PROJECT = Path(__file__).resolve().parents[1]
FAKE_LSP = PROJECT / 'tests' / 'fixtures' / 'fake_call_hierarchy_lsp.py'


class SemanticOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(PROJECT / 'examples' / 'shop', self.root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.airp', '__pycache__'))
        self.repo = Repository(self.root)
        self.repo.execute('index')

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def document(self, edges, provider='fake-lsp'):
        return {
            'schema_version': 1,
            'provider': provider,
            'index_fingerprint': self.repo.execute('semantic_status')['index_fingerprint'],
            'edges': edges,
        }

    def test_semantic_edge_replaces_matching_static_candidate(self):
        edge = {
            'source': 'checkout.py::checkout',
            'target': 'pricing.py::discount',
            'kind': 'call',
            'line': 7,
        }
        imported = self.repo.execute('semantic_import', document=self.document([edge, edge]))
        callers = self.repo.execute('callers', symbol_id='pricing.py::discount')

        matches = [item for item in callers['edges']
                   if item['source'] == 'checkout.py::checkout']
        self.assertEqual(1, len(matches))
        self.assertEqual('semantic_exact', matches[0]['confidence'])
        self.assertEqual('fake-lsp', matches[0]['provider'])
        self.assertEqual(1, imported['edges'])
        self.assertEqual('active', callers['semantic']['state'])

    def test_semantic_edge_extends_context_dependencies(self):
        document = self.document([{
            'source': 'pricing.py::discount',
            'target': 'checkout.py::checkout',
            'kind': 'call',
            'line': 3,
        }])
        self.repo.execute('semantic_import', document=document)

        result = self.repo.execute(
            'task_context', task='Explain discount behavior', intent='understand',
            breadth='narrow', budget=3000)

        self.assertIn('checkout.py::checkout', result['included'])

    def test_source_change_invalidates_overlay_and_falls_back(self):
        self.repo.execute('semantic_import', document=self.document([{
            'source': 'pricing.py::discount',
            'target': 'checkout.py::checkout',
            'kind': 'call',
            'line': 3,
        }]))
        pricing = self.root / 'pricing.py'
        pricing.write_text(pricing.read_text(encoding='utf-8') + '\n', encoding='utf-8')
        self.repo.execute('index')

        result = self.repo.execute('callees', symbol_id='pricing.py::discount')

        self.assertNotIn('checkout.py::checkout', [edge['target'] for edge in result['edges']])
        self.assertEqual('stale', result['semantic']['state'])
        self.assertIn('static', result['analysis'].casefold())

    def test_import_rejects_unknown_symbol_without_replacing_active_overlay(self):
        valid = self.document([{
            'source': 'checkout.py::checkout',
            'target': 'pricing.py::discount',
            'kind': 'reference',
            'line': 2,
        }])
        self.repo.execute('semantic_import', document=valid)
        invalid = self.document([{
            'source': 'missing.py::unknown',
            'target': 'pricing.py::discount',
            'kind': 'call',
            'line': 1,
        }])

        with self.assertRaisesRegex((AirpError, ValueError), 'not an indexed symbol'):
            self.repo.execute('semantic_import', document=invalid)

        status = self.repo.execute('semantic_status')
        self.assertEqual('active', status['state'])
        self.assertEqual(1, status['edges'])


    def test_cli_imports_overlay_and_reports_status(self):
        document = self.document([{
            'source': 'checkout.py::checkout',
            'target': 'pricing.py::discount',
            'kind': 'call',
            'line': 7,
        }], provider='fixture-lsp')
        source = self.root / 'semantic.json'
        source.write_text(json.dumps(document), encoding='utf-8')

        imported = subprocess.run(
            [sys.executable, '-m', 'airp', '--repo', str(self.root),
             'semantic-import', '--file', str(source)],
            cwd=PROJECT, capture_output=True, text=True, encoding='utf-8')
        status = subprocess.run(
            [sys.executable, '-m', 'airp', '--repo', str(self.root),
             'semantic-status'],
            cwd=PROJECT, capture_output=True, text=True, encoding='utf-8')

        self.assertEqual(0, imported.returncode, imported.stderr)
        self.assertEqual(0, status.returncode, status.stderr)
        self.assertEqual('fixture-lsp', json.loads(status.stdout)['provider'])


    def test_cli_semantic_build_reports_missing_server_without_traceback(self):
        completed = subprocess.run(
            [sys.executable, '-m', 'airp', '--repo', str(self.root),
             'semantic-build', 'rust', '--server',
             'definitely-missing-language-server'],
            cwd=PROJECT, capture_output=True, text=True, encoding='utf-8')

        self.assertEqual(1, completed.returncode)
        error = json.loads(completed.stderr)
        self.assertIn('unavailable', error['error'])
        self.assertNotIn('Traceback', completed.stderr)


    def test_semantic_build_refuses_empty_language_without_replacing_overlay(self):
        with self.assertRaisesRegex(AirpError, 'no callable symbols'):
            self.repo.execute(
                'semantic_build', language='rust', server=sys.executable,
                server_args=[str(FAKE_LSP)], max_symbols=10, timeout=3)

        self.assertEqual('absent', self.repo.execute('semantic_status')['state'])


    def test_index_fingerprint_changes_with_graph_schema(self):
        files = [{'path': 'module.py', 'hash': 'same-content'}]
        self.assertNotEqual(
            index_fingerprint({'schema_version': 1, 'files': files}),
            index_fingerprint({'schema_version': 2, 'files': files}),
        )


    def test_affected_traversal_is_not_limited_by_relation_page_size(self):
        source = 'from pricing import discount\n'
        source += ''.join(
            f'def caller_{index}():\n    return discount(10)\n'
            for index in range(205)
        )
        source += 'def test_late_caller():\n    return discount(10)\n'
        (self.root / 'test_many_callers.py').write_text(source, encoding='utf-8')
        self.repo.execute('index')

        affected = self.repo.execute('affected', symbol_id='pricing.py::discount')

        self.assertIn('test_late_caller', [item['name'] for item in affected['tests']])


if __name__ == '__main__':
    unittest.main()
