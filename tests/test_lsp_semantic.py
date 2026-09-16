from pathlib import Path
import sys
import tempfile
import unittest

from airp.lsp_semantic import LspError, LspSemanticBackend


PROJECT = Path(__file__).resolve().parents[1]
FAKE_SERVER = PROJECT / 'tests' / 'fixtures' / 'fake_call_hierarchy_lsp.py'


class LspSemanticBackendTests(unittest.TestCase):
    def test_builds_deduplicated_call_edge_from_clean_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'a.rs').write_text('fn caller() {\n    target();\n}\n', encoding='utf-8')
            (root / 'b.rs').write_text('fn target() {}\n', encoding='utf-8')
            symbols = [
                {'id': 'a.rs::caller', 'path': 'a.rs', 'name': 'caller',
                 'language': 'rust', 'kind': 'function', 'start': 1, 'end': 3},
                {'id': 'b.rs::target', 'path': 'b.rs', 'name': 'target',
                 'language': 'rust', 'kind': 'function', 'start': 1, 'end': 1},
            ]
            manifest = {'files': [
                {'path': 'a.rs', 'hash': 'a'}, {'path': 'b.rs', 'hash': 'b'},
            ]}
            backend = LspSemanticBackend(
                root, manifest, symbols, 'rust',
                command=[sys.executable, str(FAKE_SERVER)], timeout=3)

            document = backend.build(max_symbols=2)

        self.assertEqual('lsp:rust', document['provider'])
        self.assertEqual(2, document['diagnostics']['queried'])
        self.assertEqual(1, len(document['edges']))
        self.assertEqual('a.rs::caller', document['edges'][0]['source'])
        self.assertEqual('b.rs::target', document['edges'][0]['target'])
        self.assertEqual('semantic_exact', document['edges'][0]['confidence'])

    def test_missing_server_is_an_explicit_error(self):
        backend = LspSemanticBackend(
            Path.cwd(), {'files': []}, [], 'typescript',
            command=['definitely-missing-language-server'])
        with self.assertRaisesRegex(LspError, 'unavailable'):
            backend.build()


if __name__ == '__main__':
    unittest.main()
