import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from airp.core import AirpError, Repository, task_identifiers
from airp.hook import _intent_for, _validation_hint, build_prompt_context, is_code_task, process_hook

PROJECT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(PROJECT / 'examples/shop', self.root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.airp', '__pycache__'))
        self.repo = Repository(self.root)
        self.call = self.repo.execute
        self.call('index')

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def replace(self, source, apply=True, sid='pricing.py::discount'):
        return self.call('update', symbol_id=sid, source=source,
                         expected_hash=self.call('get', symbol_id=sid)['hash'], dry_run=not apply)

    def test_graph_cross_file_and_tests(self):
        sid = 'pricing.py::discount'
        self.assertEqual(self.call('find', query='discount')['total'], 2)
        callers = self.call('callers', symbol_id=sid)['edges']
        self.assertIn('checkout.py::checkout', [e['source'] for e in callers])
        affected = self.call('affected', symbol_id=sid)
        self.assertFalse(affected['complete'])
        self.assertIn('test_checkout', [s['name'] for s in affected['tests']])
        self.assertIn('test_discount', [s['name'] for s in affected['tests']])

    def test_unique_import_suffix_resolves_nested_script_package(self):
        nested = self.root / 'examples' / 'shop'
        nested.mkdir(parents=True)
        (nested / 'nested_price.py').write_text(
            'def nested_discount(x):\n    return x - 1\n', encoding='utf-8')
        (nested / 'checkout.py').write_text(
            'from nested_price import nested_discount\n'
            'def checkout(x):\n    return nested_discount(x)\n',
            encoding='utf-8')
        self.call('index')
        edges = self.call('callees', symbol_id='examples/shop/checkout.py::checkout')['edges']
        self.assertIn('examples/shop/nested_price.py::nested_discount',
                      [edge['target'] for edge in edges])

    def test_module_constants_are_searchable_context(self):
        (self.root / 'settings.py').write_text(
            'GRAPH_SCHEMA = 4\nIGNORED = {"build", "dist", "vendor"}\n', encoding='utf-8')
        self.call('index')
        found = self.call('find', query='GRAPH_SCHEMA')
        self.assertEqual('constant', found['symbols'][0]['kind'])
        result = self.call('task_context', task='What value is GRAPH_SCHEMA?',
                           breadth='narrow', response_mode='auto')
        self.assertIn('GRAPH_SCHEMA = 4', result['text'])

    def test_typing_overloads_do_not_discard_runtime_implementation(self):
        (self.root / 'overloaded.py').write_text(
            'import typing as t\nclass Codec:\n'
            '    @t.overload\n    def decode(self, value: str) -> str: ...\n'
            '    @t.overload\n    def decode(self, value: bytes) -> bytes: ...\n'
            '    def decode(self, value):\n        return value\n', encoding='utf-8')
        result = self.call('index')
        self.assertFalse(any(item['path'] == 'overloaded.py' for item in result['diagnostics']))
        found = self.call('find', query='Codec.decode')
        self.assertEqual(1, found['total'])
        symbol = self.call('get', symbol_id='overloaded.py::Codec.decode')
        self.assertIn('return value', symbol['source'])
        context = self.call('smart_context', task='How does Codec.decode handle TypeError?',
                            budget=1800)
        self.assertEqual('overloaded.py::Codec.decode', context['anchors'][0]['id'])
        self.assertTrue(context['sufficient'])

    def test_indexed_symbol_sources_normalize_crlf(self):
        (self.root / 'windows.py').write_bytes(b'def windows():\r\n    return 1\r\n')
        self.call('index')
        source = self.call('get', symbol_id='windows.py::windows')['source']
        self.assertNotIn('\r', source)
        self.assertEqual('def windows():\n    return 1\n', source)

    def test_smart_context_creates_and_refreshes_index(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'module.py'
            path.write_text('VALUE = 1\n', encoding='utf-8')
            repo = Repository(root)
            try:
                first = repo.execute('smart_context', task='VALUE', breadth='narrow')
                self.assertEqual('created', first['index_state'])
                path.write_text('VALUE = 2\n', encoding='utf-8')
                second = repo.execute('smart_context', task='VALUE', breadth='narrow')
                self.assertEqual('refreshed', second['index_state'])
                self.assertIn('VALUE = 2', second['text'])
            finally:
                repo.close()

    def test_edit_context_uses_one_named_target_and_focused_tests(self):
        result = self.call('smart_context',
                           task='Modify discount so invalid totals raise TypeError.',
                           intent='edit', budget=2600)
        self.assertEqual(['pricing.py::discount'], [anchor['id'] for anchor in result['anchors']])
        self.assertIn('def discount', result['text'])
        self.assertLessEqual(result['text'].count('# test:'), 2)

    def test_edit_context_requires_available_direct_test_evidence(self):
        result = self.call(
            'task_context',
            task='Modify discount so invalid totals raise TypeError.',
            intent='edit',
            breadth='narrow',
            budget=350,
        )

        self.assertIn('pricing.py::discount', result['included'])
        self.assertNotIn('tests/test_checkout.py::TestPricing.test_discount',
                         result['included'])
        self.assertFalse(result['sufficient'])
        self.assertEqual('blocked-partial', result['evidence_state'])
        self.assertTrue(any(
            item.startswith('direct-test:tests/test_checkout.py::')
            for item in result['evidence']['missing']
        ))

    def test_large_task_anchor_uses_focused_exact_excerpt(self):
        path = self.root / 'large.py'
        path.write_text(
            'def configure(mode="safe"):\n'
            + ''.join(f'    filler_{i} = {i}\n' for i in range(300))
            + '    limits = {"narrow": 1, "balanced": 2, "broad": 3}\n'
            + '    return limits[mode]\n', encoding='utf-8')
        self.call('index')
        result = self.call('task_context',
                           task='configure narrow balanced broad limits',
                           budget=1200, breadth='narrow', response_mode='auto')
        self.assertIn('"narrow": 1', result['text'])
        self.assertIn('"broad": 3', result['text'])
        self.assertTrue(result['sufficient'])

    def test_focused_excerpt_keeps_control_condition_before_match(self):
        (self.root / 'shape.py').write_text(
            'def shape(intent):\n'
            + ''.join(f'    filler_{i} = {i}\n' for i in range(200))
            + "    if intent not in ('minimal', 'understand'):\n"
            + '        return "compact"\n'
            + '    delivery = "full-file-cost-bypass"\n'
            + '    return delivery\n', encoding='utf-8')
        self.call('index')
        result = self.call('task_context',
                           task='shape full-file-cost-bypass intents',
                           budget=1600, breadth='narrow', response_mode='auto')
        self.assertIn("intent not in ('minimal', 'understand')", result['text'])

    def test_smart_context_adapts_breadth_and_reports_routing(self):
        exact = self.call('smart_context', task='What is pricing.py discount?',
                          budget=2000)
        self.assertEqual('narrow', exact['routing']['selected_breadth'])
        self.assertFalse(exact['routing']['recommended'])
        joined = self.call('smart_context',
                           task='compare discount and shipping dependencies',
                           budget=2500)
        self.assertEqual('broad', joined['routing']['selected_breadth'])
        self.assertTrue(joined['routing']['recommended'])

    def test_qualified_identifier_outranks_incidental_test_mentions(self):
        (self.root / 'module.py').write_text(
            'class Repository:\n    def context(self, budget=3000):\n        return budget\n',
            encoding='utf-8')
        (self.root / 'test_module.py').write_text(
            'class RepositoryTests:\n    def test_context(self):\n'
            '        assert Repository().context() == 3000\n', encoding='utf-8')
        self.call('index')
        result = self.call('smart_context',
                           task='Repository.context method default budget', budget=2200)
        self.assertEqual('module.py::Repository.context', result['anchors'][0]['id'])
        self.assertTrue(result['sufficient'])

    def test_focused_excerpt_prioritizes_exact_camelcase_identifier(self):
        (self.root / 'queue.js').write_text(
            'export function createQueue(options) {\n'
            '  let rejectOnClear = options.rejectOnClear;\n'
            + ''.join(f'  const rejectOnClear{i} = false;\n' for i in range(80))
            + '  return {clearQueue() { if (rejectOnClear) return "aborted"; }};\n'
            '}\n', encoding='utf-8')
        self.call('index')
        result = self.call('task_context', task='How does clearQueue use rejectOnClear?',
                           budget=1200, breadth='narrow', response_mode='auto')
        self.assertIn('clearQueue()', result['text'])

    def test_sufficiency_requires_named_identifier_coverage(self):
        result = self.call('task_context',
                           task='discount and DEFINITELY_MISSING_IDENTIFIER',
                           budget=1200, breadth='narrow', response_mode='full')
        self.assertFalse(result['sufficient'])
        self.assertIn('DEFINITELY_MISSING_IDENTIFIER',
                      result['coverage']['missing_identifiers'])

    def test_coverage_equates_dotted_module_and_symbol_path(self):
        result = self.call('smart_context',
                           task='pricing.discount invalid inputs', budget=2200)
        self.assertEqual('pricing.py::discount', result['anchors'][0]['id'])
        self.assertTrue(result['sufficient'])

    def test_stable_address_and_stale_index(self):
        sid = 'pricing.py::discount'
        before = self.call('get', symbol_id=sid)
        path = self.root / 'pricing.py'
        path.write_bytes(b'\n\n' + path.read_bytes())
        with self.assertRaisesRegex(AirpError, 'stale'):
            self.call('get', symbol_id=sid)
        self.call('index')
        after = self.call('get', symbol_id=sid)
        self.assertEqual(before['hash'], after['hash'])
        self.assertEqual(before['start'] + 2, after['start'])

    def test_budget_whole_symbols(self):
        small = self.call('context', symbol_id='pricing.py::discount', budget=10)
        self.assertFalse(small['target_included'])
        self.assertEqual(small['text'], '')
        large = self.call('context', symbol_id='pricing.py::discount', budget=1500)
        self.assertTrue(large['target_included'])
        self.assertLessEqual(len(large['text'].encode()), 1500)

    def test_exact_token_budget(self):
        try:
            import tiktoken
        except ImportError:
            self.skipTest('tiktoken is not installed')
        result = self.call('context', symbol_id='pricing.py::discount', budget=80,
                           budget_unit='tokens', tokenizer='o200k_base')
        actual = len(tiktoken.get_encoding('o200k_base').encode(result['text']))
        self.assertEqual(actual, result['used_tokens'])
        self.assertLessEqual(actual, 80)
        self.assertEqual('exact tokenizer count', result['token_estimate'])

    def test_context_intent_outlines_and_hash_reuse(self):
        understand = self.call('context', symbol_id='pricing.py::discount', budget=3000,
                               intent='understand')
        self.assertNotIn('test_discount', understand['text'])
        edit = self.call('context', symbol_id='pricing.py::discount', budget=3000, intent='edit')
        self.assertIn('def checkout(', edit['text'])
        self.assertIn('def test_discount(', edit['text'])
        current = self.call('get', symbol_id='pricing.py::discount')
        reused = self.call('context', symbol_id=current['id'], budget=3000,
                           known_hashes=[current['hash']])
        self.assertTrue(reused['target_reused'])
        self.assertNotIn(current['source'], reused['text'])

    def test_context_receipt_reuses_exact_symbols(self):
        first = self.call('context', symbol_id='pricing.py::discount', budget=3000)
        second = self.call('context', symbol_id='pricing.py::discount', budget=3000,
                           receipt_id=first['receipt_id'])
        self.assertTrue(second['target_reused'])
        self.assertIn('pricing.py::discount', second['reused'])
        self.assertNotIn(self.call('get', symbol_id='pricing.py::discount')['source'], second['text'])

    def test_task_context_resolves_prose_in_one_call_and_reuses_receipt(self):
        result = self.call('task_context', task='change the fractional discount calculation',
                           intent='edit', budget=3000)
        self.assertEqual('pricing.py::discount', result['anchors'][0]['id'])
        self.assertIn('def discount(', result['text'])
        self.assertTrue(result['sufficient'])
        self.assertLessEqual(len(result['text'].encode()), 3000)
        followup = self.call('task_context', task='change the fractional discount calculation',
                             intent='edit', budget=3000, receipt_id=result['receipt_id'])
        self.assertIn('pricing.py::discount', followup['reused'])
        self.assertNotIn(self.call('get', symbol_id='pricing.py::discount')['source'],
                         followup['text'])

    def test_task_context_auto_response_reduces_protocol_overhead(self):
        full = self.call('task_context', task='fractional discount calculation',
                         breadth='narrow', response_mode='full')
        auto = self.call('task_context', task='fractional discount calculation',
                         breadth='narrow', response_mode='auto')
        full_bytes = len(json.dumps(full, separators=(',', ':')).encode())
        auto_bytes = len(json.dumps(auto, separators=(',', ':')).encode())
        self.assertLess(auto_bytes, full_bytes)
        self.assertEqual(full['anchors'][0]['id'], auto['anchors'][0]['id'])
        self.assertIn(auto['delivery'], ('symbol-pack', 'full-file-cost-bypass'))

    def test_task_context_auto_bypasses_symbol_wrapper_for_tiny_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = 'def tiny_target():\n    return 7\n'
            (root / 'tiny.py').write_text(source, encoding='utf-8')
            repo = Repository(root)
            try:
                repo.index()
                result = repo.task_context('tiny target', breadth='narrow',
                                           response_mode='auto')
            finally:
                repo.close()
        self.assertEqual('full-file-cost-bypass', result['delivery'])
        self.assertEqual(source.splitlines(), result['text'].splitlines())

    def test_cost_router_stops_before_redundant_callers_fill_budget(self):
        (self.root / 'target_api.py').write_text(
            'def normalize_api(value):\n'
            '    return value.strip()\n', encoding='utf-8')
        for index in range(4):
            (self.root / f'caller_{index}.py').write_text(
                'from target_api import normalize_api\n\n'
                f'def caller_{index}(value):\n'
                '    return normalize_api(value)\n', encoding='utf-8')
        self.call('index')

        result = self.call(
            'task_context',
            task='Modify normalize_api implementation without changing its public API.',
            intent='edit',
            breadth='narrow',
            budget=5000,
        )

        caller_ids = [
            sid for sid, detail in result['selection'].items()
            if detail['role'] == 'caller'
        ]
        self.assertLessEqual(len(caller_ids), 1)
        self.assertGreaterEqual(result['routing_cost']['pruned_low_value'], 1)
        self.assertTrue(result['sufficient'])

    def test_task_context_balances_multiple_task_anchors(self):
        result = self.call('task_context', task='compare discount and shipping behavior',
                           breadth='balanced', budget=3000)
        anchor_ids = {item['id'] for item in result['anchors']}
        self.assertIn('pricing.py::discount', anchor_ids)
        self.assertIn('pricing.py::shipping', anchor_ids)
        self.assertLessEqual(len(result['text'].encode()), 3000)

    def test_task_context_exact_token_budget(self):
        try:
            import tiktoken
        except ImportError:
            self.skipTest('tiktoken is not installed')
        result = self.call('task_context', task='discount calculation', breadth='narrow',
                           budget=100, budget_unit='tokens', tokenizer='o200k_base')
        actual = len(tiktoken.get_encoding('o200k_base').encode(result['text']))
        self.assertEqual(actual, result['used_tokens'])
        self.assertLessEqual(actual, 100)

    def test_task_context_handles_cjk_and_snake_case_terms(self):
        (self.root / 'refund_service.py').write_text(
            'def calculate_refund(total):\n'
            '    """计算订单退款金额。"""\n'
            '    return total\n', encoding='utf-8')
        self.call('index')
        chinese = self.call('task_context', task='修改订单退款金额计算', breadth='narrow')
        snake = self.call('task_context', task='change calculate_refund', breadth='narrow')
        self.assertEqual('refund_service.py::calculate_refund', chinese['anchors'][0]['id'])
        self.assertEqual('refund_service.py::calculate_refund', snake['anchors'][0]['id'])

    def test_task_search_uses_and_refreshes_fts_when_available(self):
        if not self.repo.has_fts:
            self.skipTest('SQLite FTS5 is not available')
        result = self.repo.task_matches('fractional discount calculation')
        self.assertIn('SQLite FTS5', result['method'])
        self.assertIsNotNone(result['candidate_pool'])
        symbol_count = self.repo.db.execute('SELECT COUNT(*) FROM symbol_lookup').fetchone()[0]
        fts_count = self.repo.db.execute('SELECT COUNT(*) FROM symbol_fts').fetchone()[0]
        self.assertEqual(symbol_count, fts_count)
        (self.root / 'temporary.py').write_text(
            'def unique_ephemeral_marker():\n    return 1\n', encoding='utf-8')
        self.call('index')
        self.assertEqual('temporary.py::unique_ephemeral_marker',
                         self.repo.task_matches('unique ephemeral marker')['matches'][0]['id'])
        (self.root / 'temporary.py').unlink()
        self.call('index')
        self.assertEqual([], self.repo.task_matches('unique ephemeral marker')['matches'])

    def test_minimal_context_skips_impact_analysis(self):
        with patch.object(self.repo, 'affected', side_effect=AssertionError('should not run')):
            result = self.call('context', symbol_id='pricing.py::discount', intent='minimal')
        self.assertTrue(result['target_included'])

    def test_method_context_does_not_duplicate_parent_body(self):
        (self.root / 'service.py').write_text(
            'class Service:\n'
            '    def target(self):\n'
            '        marker = "UNIQUE_BODY_MARKER"\n'
            '        return marker\n\n'
            '    def unrelated(self):\n'
            '        return "LARGE_UNRELATED_BODY"\n', encoding='utf-8')
        self.call('index')
        result = self.call('context', symbol_id='service.py::Service.target', budget=3000)
        self.assertEqual(1, result['text'].count('UNIQUE_BODY_MARKER'))
        self.assertNotIn('LARGE_UNRELATED_BODY', result['text'])
        self.assertIn('def unrelated(self): ...', result['text'])

    def test_prepare_combines_resolution_hash_and_context(self):
        result = self.call('prepare', query='shipping', budget=2000)
        self.assertTrue(result['ready'])
        self.assertEqual('pricing.py::shipping', result['target']['id'])
        self.assertEqual(self.call('get', symbol_id=result['target']['id'])['hash'], result['hash'])
        ambiguous = self.call('prepare', query='test')
        self.assertFalse(ambiguous['ready'])

    def test_preview_and_byte_exact_rollback(self):
        path = self.root / 'pricing.py'
        original = path.read_bytes()
        change = 'def discount(total, rate):\n    return 0\n'
        self.assertIn('+    return 0', self.replace(change, apply=False)['diff'])
        self.assertEqual(original, path.read_bytes())
        with self.assertRaisesRegex(AirpError, 'begin'):
            self.replace(change)
        self.call('begin')
        self.replace(change)
        self.assertNotEqual(original, path.read_bytes())
        self.assertIn('+    return 0', self.call('diff')['diff'])
        self.call('rollback')
        self.assertEqual(original, path.read_bytes())

    def test_invalid_replacement_does_not_touch_source(self):
        original = (self.root / 'pricing.py').read_bytes()
        self.call('begin')
        for source in ('def discount(:', 'def other():\n    pass',
                       'def discount():\n    pass\nx=1', 'async def discount():\n    pass'):
            with self.assertRaises((AirpError, SyntaxError)):
                self.replace(source)
        self.assertEqual(original, (self.root / 'pricing.py').read_bytes())

    def test_wrong_hash(self):
        with self.assertRaisesRegex(AirpError, 'hash mismatch'):
            self.call('update', symbol_id='pricing.py::discount', source='def discount(): pass', expected_hash='old')

    def test_external_edit_blocks_rollback(self):
        self.call('begin')
        self.replace('def discount(total, rate):\n    return 0\n')
        path = self.root / 'pricing.py'
        path.write_bytes(path.read_bytes() + b'\n# User work\n')
        outside = path.read_bytes()
        with self.assertRaisesRegex(AirpError, 'External change'):
            self.call('rollback')
        self.assertEqual(outside, path.read_bytes())

    def test_commit_requires_current_passing_tests(self):
        self.call('begin')
        with self.assertRaisesRegex(AirpError, 'passing tests'):
            self.call('commit')
        self.assertTrue(self.call('test')['ok'])
        old = self.call('get', symbol_id='pricing.py::discount')['source']
        self.replace(old.replace('Apply a fractional discount', 'Calculate a fractional discount'))
        with self.assertRaisesRegex(AirpError, 'passing tests'):
            self.call('commit')
        self.assertTrue(self.call('test')['ok'])
        self.assertEqual('committed', self.call('commit')['status'])
        self.assertIsNone(self.call('status')['transaction'])

    def test_failure_then_rollback(self):
        self.call('begin')
        self.replace('def discount(total, rate):\n    return 0\n')
        self.assertFalse(self.call('test')['ok'])
        with self.assertRaises(AirpError):
            self.call('commit')
        self.call('rollback')
        self.assertTrue(self.call('test')['ok'])

    def test_successful_test_output_is_compact(self):
        compact_result = self.call('test')
        full_result = self.call('test', output_mode='full')
        self.assertTrue(compact_result['ok'])
        self.assertEqual(4, compact_result['tests_run'])
        self.assertLess(compact_result['output_bytes'], len(full_result['output'].encode('utf-8')))
        self.assertNotIn('test_discount', compact_result['output'])
        self.assertIn('test_discount', full_result['output'])

    def test_multifile_transaction_survives_reopen(self):
        originals = {p: (self.root / p).read_bytes() for p in ('pricing.py', 'checkout.py')}
        self.call('begin')
        self.replace('def discount(total, rate):\n    return 0\n')
        self.replace('def checkout(total, rate=0):\n    return 0\n', sid='checkout.py::checkout')
        self.repo.close()
        self.repo = Repository(self.root)
        self.call = self.repo.execute
        self.call('rollback')
        for p, raw in originals.items():
            self.assertEqual(raw, (self.root / p).read_bytes())

    def test_decorated_async_method_crlf_unicode(self):
        path = self.root / 'service.py'
        raw = ('class Service:\r\n    @staticmethod\r\n    async def run(value):\r\n'
               '        """中文说明"""\r\n        return value\r\n\r\n'
               '    def other(self):\r\n        return 7\r\n').encode('utf-8')
        path.write_bytes(raw)
        self.call('index')
        self.call('begin')
        self.replace('@staticmethod\nasync def run(value):\n    return value + 1\n', sid='service.py::Service.run')
        changed = path.read_bytes()
        self.assertNotIn(b'\n', changed.replace(b'\r\n', b''))
        self.assertIn(b'def other(self):\r\n        return 7', changed)
        self.call('rollback')
        self.assertEqual(raw, path.read_bytes())

    def test_parse_diagnostics_and_duplicate_symbols(self):
        (self.root / 'broken.py').write_text('def broken(:', encoding='utf-8')
        (self.root / 'duplicate.py').write_text('def f(): pass\ndef f(): pass', encoding='utf-8')
        result = self.call('index')
        self.assertEqual(2, len(result['diagnostics']))
        self.assertFalse(self.call('verify')['ok'])
        self.assertEqual(0, self.call('find', query='duplicate.py')['total'])

    def test_property_setter_does_not_invalidate_python_module(self):
        (self.root / 'property_module.py').write_text(
            'class Record:\n'
            '    @property\n'
            '    def value(self):\n        return self._value\n'
            '    @value.setter\n'
            '    def value(self, new_value):\n        self._value = new_value\n'
            '    def save(self):\n        return self.value\n', encoding='utf-8')
        self.call('index')
        found = self.call('find', query='Record.value')['symbols']
        ids = {item['id'] for item in found}
        self.assertIn('property_module.py::Record.value', ids)
        self.assertIn('property_module.py::Record.value.setter', ids)
        self.assertIn('property_module.py::Record.save',
                      {item['id'] for item in self.call('find', query='Record.save')['symbols']})

    def test_airpignore_excludes_large_generated_or_corpus_tree(self):
        ignored = self.root / 'benchmarks' / 'corpus-large'
        ignored.mkdir(parents=True)
        (ignored / 'huge.py').write_text('def hidden(): return 1\n', encoding='utf-8')
        (self.root / '.airpignore').write_text('benchmarks/corpus-large\n', encoding='utf-8')
        self.call('index')
        self.assertEqual(0, self.call('find', query='hidden')['total'])

    def test_repeated_branch_local_helper_keeps_enclosing_module_indexable(self):
        (self.root / 'branch_helpers.py').write_text(
            'def choose(flag):\n'
            '    if flag:\n'
            '        def convert(value):\n            return value + 1\n'
            '    else:\n'
            '        def convert(value):\n            return value - 1\n'
            '    return convert(3)\n', encoding='utf-8')
        self.call('index')
        self.assertIn('branch_helpers.py::choose',
                      {item['id'] for item in self.call('find', query='choose')['symbols']})
        helpers = self.call('find', query='convert')['symbols']
        self.assertEqual(2, len(helpers))
        self.assertTrue(any('#local2' in item['id'] for item in helpers))

    def test_incremental_index_and_last_good_recovery(self):
        unchanged = self.call('index')['incremental']
        self.assertFalse(unchanged['changed'])
        self.assertIn('pricing.py', unchanged['reused'])
        path = self.root / 'recover.py'
        path.write_text('def recover():\n    return 1\n', encoding='utf-8')
        changed = self.call('index')['incremental']
        self.assertEqual(['recover.py'], changed['changed'])
        path.write_text('def recover(:\n', encoding='utf-8')
        result = self.call('index')
        self.assertTrue(any(d.get('using_last_good') for d in result['diagnostics']))
        symbol = self.call('get', symbol_id='recover.py::recover')
        self.assertTrue(symbol['analysis_stale'])
        with self.assertRaisesRegex(AirpError, 'last good'):
            self.call('update', symbol_id=symbol['id'], source=symbol['source'],
                      expected_hash=symbol['hash'])

    def test_tree_sitter_javascript_read_only_index(self):
        path = self.root / 'web.js'
        path.write_text('export function greet(name) { return `Hi ${name}`; }\n'
                        'class Service { run() { return greet("x"); } }\n', encoding='utf-8')
        result = self.call('index')
        if any(d.get('language') == 'javascript' and 'not cached' in d['error']
               for d in result['diagnostics']):
            self.skipTest('JavaScript Tree-sitter parser is not cached')
        self.assertIn('javascript', result['capabilities']['languages'])
        found = self.call('find', query='greet')
        self.assertEqual(1, found['total'])
        sid = found['symbols'][0]['id']
        self.assertFalse(found['symbols'][0]['editable'])
        context = self.call('context', symbol_id=sid, intent='minimal', budget=1000)
        self.assertIn('function greet', context['text'])
        with self.assertRaisesRegex(AirpError, 'read-only'):
            self.call('update', symbol_id=sid, source='function greet() {}',
                      expected_hash=self.call('get', symbol_id=sid)['hash'])

    def test_tree_sitter_test_file_symbols_are_deprioritized(self):
        (self.root / 'index.js').write_text(
            'export function clearQueue() { return "implementation"; }\n', encoding='utf-8')
        (self.root / 'test.js').write_text(
            'test("clearQueue", async t => { clearQueue(); t.pass(); });\n', encoding='utf-8')
        result = self.call('index')
        if any(d.get('language') == 'javascript' and 'not cached' in d['error']
               for d in result['diagnostics']):
            self.skipTest('JavaScript Tree-sitter parser is not cached')
        matches = self.call('task_matches', task='How does clearQueue behave?')['matches']
        self.assertEqual('index.js', matches[0]['path'])
        self.assertEqual('clearQueue', matches[0]['name'])
        test_symbols = [symbol for symbol in matches if symbol['path'] == 'test.js']
        self.assertTrue(test_symbols)
        self.assertTrue(all(self.call('get', symbol_id=symbol['id'])['is_test']
                            for symbol in test_symbols))

    def test_new_c_api_gets_source_and_header_creation_pack(self):
        (self.root / 'mini.c').write_text(
            '#include <string.h>\n#include "mini.h"\n'
            'int mini_cmp(const char *a,const char *b){return memcmp(a,b,1);}\n',
            encoding='utf-8')
        (self.root / 'mini.h').write_text(
            '#include <stddef.h>\nint mini_cmp(const char *a,const char *b);\n',
            encoding='utf-8')
        indexed = self.call('index')
        if any(d.get('language') == 'c' and 'not cached' in d['error']
               for d in indexed['diagnostics']):
            self.skipTest('C Tree-sitter parser is not cached')
        result = self.call('smart_context',
                           task='Add public API int mini_startswith(const char *s).',
                           intent='edit', budget=2400)
        self.assertTrue(result['sufficient'])
        self.assertIn('# planned-symbol: mini_startswith', result['text'])
        self.assertIn('# candidate-file: mini.c', result['text'])
        self.assertIn('# candidate-file: mini.h', result['text'])

    def test_tree_sitter_overloads_have_unique_ids(self):
        (self.root / 'Overload.java').write_text(
            'class Overload { int run(int x) { return x; } '
            'String run(String x) { return x; } }\n', encoding='utf-8')
        result = self.call('index')
        if any(d.get('language') == 'java' and 'not cached' in d['error']
               for d in result['diagnostics']):
            self.skipTest('Java Tree-sitter parser is not cached')
        found = self.call('find', query='Overload.run')
        self.assertEqual(2, found['total'])
        self.assertEqual(2, len({s['id'] for s in found['symbols']}))

    def test_path_boundary(self):
        for path in ('../outside.py', str(self.root / 'pricing.py'), '.airp/program.sqlite3'):
            with self.assertRaises(AirpError):
                self.repo.path(path)

    def test_new_and_deleted_sources_invalidate_index(self):
        path = self.root / 'new.py'
        path.write_text('def new(): pass', encoding='utf-8')
        self.assertTrue(self.call('status')['stale'])
        self.call('index')
        path.unlink()
        with self.assertRaises(AirpError):
            self.call('find')

    def test_interrupted_second_write_remains_recoverable(self):
        path = self.root / 'pricing.py'
        original = path.read_bytes()
        self.call('begin')
        self.replace('def discount(total, rate):\n    return 0\n')
        with patch.object(self.repo, 'atomic_write', side_effect=OSError('simulated interrupted write')):
            with self.assertRaises(OSError):
                self.replace('def discount(total, rate):\n    return 1\n')
        self.call('rollback')
        self.assertEqual(original, path.read_bytes())

    def test_empty_suite_cannot_authorize_commit(self):
        (self.root / 'tests/test_checkout.py').write_text('', encoding='utf-8')
        self.call('index')
        self.call('begin')
        self.assertFalse(self.call('test')['ok'])
        with self.assertRaises(AirpError):
            self.call('commit')

    def test_cli_json_and_nonzero_error(self):
        result = subprocess.run([sys.executable, '-m', 'airp', '--repo', str(self.root), 'find', 'discount'],
                                cwd=PROJECT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('symbols', json.loads(result.stdout))
        result = subprocess.run([sys.executable, '-m', 'airp', '--repo', str(self.root),
                                 'pack', 'change discount calculation', '--breadth', 'narrow'],
                                cwd=PROJECT, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('pricing.py::discount', json.loads(result.stdout)['anchors'][0]['id'])
        result = subprocess.run([sys.executable, '-m', 'airp', '--repo', str(self.root), 'get', 'missing'],
                                cwd=PROJECT, capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('error', json.loads(result.stderr))

    def test_hook_recognizes_broad_code_tasks_but_skips_chat(self):
        self.assertTrue(is_code_task('Repository.context 的默认 budget 是多少？'))
        self.assertTrue(is_code_task('请检查 pricing.py 中的折扣函数'))
        self.assertTrue(is_code_task('refactor the checkout function'))
        self.assertEqual(['slugify'], task_identifiers('modify slugify(...) behavior'))
        self.assertFalse(is_code_task('你好，请介绍一下自己'))
        self.assertEqual('impact', _intent_for('find callers and affected tests'))
        self.assertEqual('edit', _intent_for('refactor this function'))
        self.assertEqual('edit', _intent_for('modify this function and check callers'))
        self.assertEqual('test', _intent_for('which tests cover this method'))
        self.assertEqual('understand', _intent_for('explain this function'))

    def test_hook_injects_context_before_model_without_protocol_metadata(self):
        context = build_prompt_context(
            self.root, 'pricing.py 中 discount 函数返回什么？')
        self.assertIn('<airp-context>', context)
        self.assertIn('pricing.py::discount', context)
        self.assertIn('def discount', context)
        self.assertNotIn('receipt_id', context)
        self.assertNotIn('routing', context)
        self.assertRegex(context, r'payload_hash: [0-9a-f]{16}')
        self.assertRegex(context, r'evidence_chars: [1-9][0-9]*')
        repeated = build_prompt_context(
            self.root,
            'pricing.py \u4e2d discount \u51fd\u6570\u8fd4\u56de\u4ec0\u4e48\uff1f',
        )
        first_hash = re.search(r'payload_hash: ([0-9a-f]{16})', context).group(1)
        second_hash = re.search(r'payload_hash: ([0-9a-f]{16})', repeated).group(1)
        self.assertEqual(first_hash, second_hash)

    def test_hook_abstains_when_context_cost_exceeds_direct_read(self):
        root = self.root / 'tiny-low-benefit'
        root.mkdir()
        (root / 'tiny.py').write_text(
            'def tiny_target():\n'
            '    return 7\n', encoding='utf-8')

        context = build_prompt_context(
            root, 'What does tiny_target(...) return?')

        self.assertIsNone(context)

    def test_c_validation_hint_uses_target_and_bounded_dependency_stubs(self):
        result = {
            'anchors': [{'id': 'src/util.c::stringmatchlen',
                         'name': 'stringmatchlen'}],
            'edit_frontier': {'dependency_names': ['stringmatchlen_impl']},
        }
        hint = _validation_hint(self.root, result)
        self.assertIn('only stringmatchlen', hint)
        self.assertIn('stub direct callees (stringmatchlen_impl)', hint)
        self.assertIn('Do not search or build the full project', hint)
    def test_edit_hook_infers_src_layout_validation_environment(self):
        root = self.root / 'layout'
        (root / 'src').mkdir(parents=True)
        (root / 'src' / 'module.py').write_text(
            'def convert(value):\n    return value\n', encoding='utf-8')
        context = build_prompt_context(root, 'Modify convert to validate the value.')
        self.assertIn('edit_scope: src/module.py', context)
        self.assertIn('edit_locations: src/module.py:1-2', context)
        self.assertIn('PYTHONPATH', context)
        self.assertIn('actual newline characters', context)

    def test_edit_hook_marks_statements_outside_exception_handler(self):
        root = self.root / 'exception-boundary'
        root.mkdir()
        (root / 'encoding.py').write_text(
            'def decode(value):\n'
            '    value = value.encode("ascii")\n'
            '    try:\n'
            '        return parse(value)\n'
            '    except ValueError as error:\n'
            '        raise BadData() from error\n', encoding='utf-8')
        context = build_prompt_context(
            root, 'Modify decode so UnicodeEncodeError raises BadData instead of leaking.')
        self.assertIn('control_flow:', context)
        self.assertIn('lines 2-2 execute before its try/except', context)
        self.assertIn('cannot be converted by that handler', context)

    def test_edit_hook_marks_lossy_error_suppression(self):
        root = self.root / 'error-suppression'
        root.mkdir()
        (root / 'encoding.py').write_text(
            'def decode(value):\n'
            '    value = value.encode("ascii", errors="ignore")\n'
            '    try:\n'
            '        return parse(value)\n'
            '    except ValueError as error:\n'
            '        raise BadData() from error\n', encoding='utf-8')
        context = build_prompt_context(
            root, 'Modify decode to reject invalid input and raise BadData.')
        self.assertIn('error_suppression:', context)
        self.assertIn('discards conversion failures', context)

    def test_large_unindexed_repository_uses_exact_symbol_fast_start(self):
        root = self.root / 'large-fast-start'
        root.mkdir()
        for index in range(500):
            (root / f'module_{index}.py').write_text(
                ('class Target:\n    def execute(self):\n        return 7\n'
                 if index == 321 else f'VALUE_{index} = {index}\n'), encoding='utf-8')
        context = build_prompt_context(root, 'Explain Target.execute behavior.')
        self.assertIn('index_mode: exact-symbol fast start', context)
        self.assertIn('module_321.py::Target.execute', context)
        self.assertIn('return 7', context)
        self.assertFalse((root / '.airp' / 'program.sqlite3-wal').stat().st_size
                         if (root / '.airp' / 'program.sqlite3-wal').exists() else 0)

    def test_large_by_bytes_repository_uses_fast_start(self):
        root = self.root / 'large-bytes-fast-start'
        root.mkdir()
        (root / 'target.py').write_text(
            'def process_value(value):\n'
            '    return value + 1\n', encoding='utf-8')
        payload = '# filler\n' * 2500
        for index in range(50):
            (root / f'filler_{index}.py').write_text(payload, encoding='utf-8')

        context = build_prompt_context(
            root, 'Modify process_value(...) to preserve its contract.')
        self.assertIn('index_mode: exact-symbol fast start', context)
        self.assertIn('target.py::process_value', context)
    def test_large_edit_fast_start_builds_bounded_edit_frontier(self):
        root = self.root / 'large-edit-frontier'
        root.mkdir()
        (root / 'target.py').write_text(
            'def normalize(value):\n'
            '    return value.strip()\n\n'
            'class Target:\n'
            '    def execute(self, value):\n'
            '        return normalize(value)\n', encoding='utf-8')
        (root / 'caller.py').write_text(
            'from target import Target\n\n'
            'def run(value):\n'
            '    return Target().execute(value)\n', encoding='utf-8')
        (root / 'test_target.py').write_text(
            'from target import Target\n\n'
            'def test_execute_strips_input():\n'
            '    assert Target().execute(" value ") == "value"\n', encoding='utf-8')
        for index in range(497):
            (root / f'filler_{index}.py').write_text(
                f'VALUE_{index} = {index}\n', encoding='utf-8')

        context = build_prompt_context(
            root, 'Modify Target.execute to preserve normalized output.')
        self.assertIn('status: sufficient', context)
        self.assertIn('edit_ready=yes', context)
        self.assertIn('# task-anchor: target.py::Target.execute', context)
        self.assertIn('# edit-dependency: target.py::normalize', context)
        self.assertIn('# edit-test: test_target.py::reference@', context)
        self.assertIn('# edit-caller: caller.py::reference@', context)
        self.assertLess(len(context), 7801)

    def test_large_edit_frontier_prunes_redundant_test_blocks(self):
        root = self.root / 'large-edit-redundant-tests'
        root.mkdir()
        (root / 'target.py').write_text(
            'def normalize(value):\n'
            '    return value.strip()\n', encoding='utf-8')
        for index in range(2):
            (root / f'test_target_{index}.py').write_text(
                'from target import normalize\n\n'
                f'def test_normalize_{index}():\n'
                '    assert normalize(" value ") == "value"\n', encoding='utf-8')
        for index in range(497):
            (root / f'filler_{index}.py').write_text(
                f'VALUE_{index} = {index}\n', encoding='utf-8')

        context = build_prompt_context(
            root, 'Modify normalize(...) to preserve stripped output.')

        self.assertEqual(1, context.count('# edit-test:'), context)
        self.assertIn('tests=1', context)
        self.assertIn('pruned_low_value=1', context)

    def test_large_edit_fast_start_requires_contract_evidence(self):
        root = self.root / 'large-edit-without-contract'
        root.mkdir()
        (root / 'target.py').write_text(
            'class Target:\n'
            '    def execute(self):\n'
            '        return 7\n', encoding='utf-8')
        for index in range(499):
            (root / f'filler_{index}.py').write_text(
                f'VALUE_{index} = {index}\n', encoding='utf-8')

        context = build_prompt_context(root, 'Modify Target.execute to return 8.')
        self.assertIn('status: partial', context)
        self.assertIn('edit_ready=no', context)
        self.assertIn('edit_frontier: declarations=0, dependencies=0, tests=0, callers=0',
                      context)

    def test_hook_revalidates_sufficiency_after_atomic_budget_packing(self):
        root = self.root / 'large-edit-atomic-pack'
        root.mkdir()
        (root / 'target.py').write_text(
            'def normalize(value):\n'
            '    return value.strip()\n\n'
            'class Target:\n'
            '    def execute(self, value):\n'
            '        return normalize(value)\n', encoding='utf-8')
        (root / 'caller.py').write_text(
            'from target import Target\n\n'
            'def run(value):\n'
            '    return Target().execute(value)\n', encoding='utf-8')
        (root / 'test_target.py').write_text(
            'from target import Target\n\n'
            'def test_execute_strips_input():\n'
            '    assert Target().execute(" value ") == "value"\n', encoding='utf-8')
        for index in range(497):
            (root / f'filler_{index}.py').write_text(
                f'VALUE_{index} = {index}\n', encoding='utf-8')

        context = build_prompt_context(
            root, 'Modify Target.execute to preserve normalized output.', max_chars=550)

        self.assertLessEqual(len(context), 550)
        self.assertNotIn('status: sufficient', context)
        self.assertIn('evidence_state: blocked-partial', context)
        self.assertRegex(context, r'omitted_blocks: [1-9][0-9]*')
        self.assertIn('def normalize(value):', context)
        self.assertNotIn('# edit-test:', context)
        self.assertNotIn('# edit-caller:', context)
    def test_behavior_context_includes_dependency_bodies_and_requires_them(self):
        context = build_prompt_context(self.root, '计算 checkout(50, 0.1) 的返回值')
        self.assertIn('def checkout', context)
        self.assertIn('def discount', context)
        self.assertIn('def shipping', context)
        self.assertIn('status: sufficient', context)

    def test_hook_derives_collection_cardinality(self):
        context = build_prompt_context(
            PROJECT, 'Repository.execute中的read_only集合包含多少个操作？')
        self.assertIn('derived_facts: read_only cardinality = 11', context)

    def test_hook_protocol_output_and_quiet_non_code_path(self):
        output = process_hook({
            'hook_event_name': 'UserPromptSubmit',
            'cwd': str(self.root),
            'prompt': 'What does the discount function do?',
        })
        self.assertEqual('UserPromptSubmit',
                         output['hookSpecificOutput']['hookEventName'])
        self.assertIn('additionalContext', output['hookSpecificOutput'])
        self.assertIsNone(process_hook({
            'cwd': str(self.root), 'prompt': 'hello there',
        }))


if __name__ == '__main__':
    unittest.main()
