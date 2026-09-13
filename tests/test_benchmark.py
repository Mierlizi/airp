import unittest
from benchmarks.summarize import summarize
from benchmarks.run_premodel_hook_ab import semantic_match


class BenchmarkTests(unittest.TestCase):
    def row(self, group, success=True):
        return dict(task_id='one', group=group, model='fixed', revision='snapshot', run=1,
                    success=success, input_tokens=100, output_tokens=20, cost_usd=0.1,
                    tool_calls=4, latency_seconds=2)

    def test_requires_matched_pairs(self):
        with self.assertRaises(ValueError):
            summarize([self.row('baseline')])

    def test_failed_costs_count_and_zero_success_is_null(self):
        report = summarize([self.row('baseline'), self.row('airp', False)])
        self.assertEqual(0.1, report['baseline']['cost_per_success_usd'])
        self.assertIsNone(report['airp']['cost_per_success_usd'])
        self.assertFalse(report['quality_noninferior_observed'])

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            summarize([self.row('baseline'), self.row('baseline'), self.row('airp')])

    def test_numeric_sequence_accepts_equivalent_decimal_spelling(self):
        task = {'expected': '8.0|0.0', 'acceptance': 'numeric_sequence'}
        self.assertTrue(semantic_match(task, '8|0'))
        self.assertFalse(semantic_match(task, '0|8'))

    def test_semantic_graders_separate_format_from_information(self):
        labeled = {'expected': 'created|refreshed|current',
                   'acceptance': 'labeled_sequence'}
        self.assertTrue(semantic_match(
            labeled, 'first=created|stale=refreshed|current=current'))
        paths = {'expected': 'absolute|internal|symlink',
                 'acceptance': 'path_categories'}
        self.assertTrue(semantic_match(paths, '绝对路径、内部状态路径、符号链接路径'))
