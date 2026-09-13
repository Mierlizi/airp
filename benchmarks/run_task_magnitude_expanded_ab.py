"""Expanded paired benchmark for scale, latency uncertainty, and accuracy."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import tempfile

import tiktoken

from airp.core import Repository
from run_synthetic_model_stage_ab import answer, run_codex


ROOT = Path(__file__).resolve().parents[1]
LEVELS = [('micro', '微型', 1), ('small', '小型', 3),
          ('medium', '中型', 8), ('large', '大型', 16),
          ('xlarge', '超大型', 32), ('xxlarge', '极大型', 64)]
FAMILIES = ('sum', 'max', 'even_count', 'odd_sum', 'span',
            'checksum', 'squares', 'above_50', 'alternating', 'product_mod')


def dependency_source(index, value, distractors=24):
    fillers = [f'def filler_{index}_{item}(value):\n'
               f'    adjusted = value + {item + index}\n'
               f'    return adjusted\n' for item in range(distractors)]
    fillers.append(f'def signal_{index}(value={value}):\n    return value\n')
    return '\n'.join(fillers)


def expression(family, calls):
    values = f'[{", ".join(calls)}]'
    if family == 'sum':
        return f'sum({values})'
    if family == 'max':
        return f'max({values})'
    if family == 'even_count':
        return f'sum(1 for value in {values} if value % 2 == 0)'
    if family == 'odd_sum':
        return f'sum(value for value in {values} if value % 2 == 1)'
    if family == 'span':
        return f'max({values}) - min({values})'
    if family == 'checksum':
        return f'sum((index + 1) * value for index, value in enumerate({values})) % 997'
    if family == 'squares':
        return f'sum(value * value for value in {values}) % 997'
    if family == 'above_50':
        return f'sum(1 for value in {values} if value > 50)'
    if family == 'alternating':
        return f'sum(value if index % 2 == 0 else -value for index, value in enumerate({values}))'
    return f'math.prod({values}) % 997'


def expected_value(family, values):
    if family == 'sum':
        return sum(values)
    if family == 'max':
        return max(values)
    if family == 'even_count':
        return sum(value % 2 == 0 for value in values)
    if family == 'odd_sum':
        return sum(value for value in values if value % 2 == 1)
    if family == 'span':
        return max(values) - min(values)
    if family == 'checksum':
        return sum((index + 1) * value for index, value in enumerate(values)) % 997
    if family == 'squares':
        return sum(value * value for value in values) % 997
    if family == 'above_50':
        return sum(value > 50 for value in values)
    if family == 'alternating':
        return sum(value if index % 2 == 0 else -value
                   for index, value in enumerate(values))
    return math.prod(values) % 997


def prepare_case(level, label, dependency_count, case_index, encoding):
    family = FAMILIES[case_index]
    values = [((index * 37 + case_index * 19 + dependency_count * 7) % 97) + 1
              for index in range(1, dependency_count + 1)]
    target_name = f'aggregate_{level}_{family}_{case_index}'
    with tempfile.TemporaryDirectory(prefix=f'airp-expanded-{level}-{case_index}-') as folder:
        root = Path(folder)
        imports = ['import math'] if family == 'product_mod' else []
        imports += [f'from module_{index} import signal_{index}'
                    for index in range(1, dependency_count + 1)]
        calls = [f'signal_{index}()' for index in range(1, dependency_count + 1)]
        target_source = ('\n'.join(imports) + f'\n\ndef {target_name}():\n'
                         f'    return {expression(family, calls)}\n')
        (root / 'target.py').write_text(target_source, encoding='utf-8')
        paths = ['target.py']
        for index, value in enumerate(values, 1):
            path = f'module_{index}.py'
            (root / path).write_text(dependency_source(index, value), encoding='utf-8')
            paths.append(path)
        repo = Repository(root)
        try:
            repo.index()
            airp = repo.task_context(f'inspect {target_name} direct signal defaults',
                                     budget=12000, budget_unit='tokens',
                                     intent='understand', breadth='narrow',
                                     response_mode='auto')
        finally:
            repo.close()
        expected_symbols = [f'signal_{index}' for index in range(1, dependency_count + 1)]
        missing = [name for name in expected_symbols if name not in airp['text']]
        if missing:
            raise RuntimeError(f'{level}/{case_index}: missing {len(missing)} dependencies')
        baseline = {'files': [{'path': path,
                               'source': (root / path).read_text(encoding='utf-8')}
                              for path in paths]}
    compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return {'level': level, 'label': label, 'dependencies': dependency_count,
            'case_index': case_index, 'family': family, 'target_name': target_name,
            'expected': str(expected_value(family, values)),
            'baseline': baseline, 'airp': airp,
            'baseline_payload_tokens': len(encoding.encode(compact(baseline))),
            'airp_payload_tokens': len(encoding.encode(compact(airp)))}


def make_prompt(item, payload):
    return f'''这是隔离合成代码的固定答案实验。不要调用工具，只使用给定代码响应。
问题：执行 {item["target_name"]}() 会返回哪个整数？
严格只输出 `<answer>整数</answer>`，不得解释。
代码响应：
```json
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
```
'''


def bootstrap_log_ratio(pairs, samples=5000, seed=20260912):
    logs = [math.log(airp / baseline) for baseline, airp in pairs]
    rng = random.Random(seed + len(pairs))
    draws = []
    for _ in range(samples):
        chosen = [logs[rng.randrange(len(logs))] for _ in logs]
        draws.append(100 * (math.exp(statistics.mean(chosen)) - 1))
    draws.sort()
    estimate = 100 * (math.exp(statistics.mean(logs)) - 1)
    return {'estimate_pct': estimate,
            'ci95_low_pct': draws[int(samples * 0.025)],
            'ci95_high_pct': draws[int(samples * 0.975)],
            'median_pair_pct': statistics.median(
                100 * (airp / baseline - 1) for baseline, airp in pairs)}


def wilson(successes, total, z=1.959963984540054):
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total
                           + z * z / (4 * total * total)) / denominator
    return [center - margin, center + margin]


def summarize(rows, level, label, dependencies):
    chosen = [row for row in rows if row['level'] == level]
    totals = {}
    for group in ('baseline', 'airp'):
        group_rows = [row for row in chosen if row['group'] == group]
        totals[group] = {key: sum(row[key] for row in group_rows) for key in
                         ('payload_tokens', 'input_tokens', 'cached_input_tokens',
                          'reasoning_output_tokens', 'output_tokens', 'latency_seconds')}
        totals[group]['total_model_tokens'] = (totals[group]['input_tokens']
                                                + totals[group]['output_tokens'])
        totals[group]['runs'] = len(group_rows)
        totals[group]['successes'] = sum(row['success'] for row in group_rows)
        totals[group]['median_latency_seconds'] = statistics.median(
            row['latency_seconds'] for row in group_rows)
        totals[group]['accuracy_wilson95'] = wilson(totals[group]['successes'],
                                                    totals[group]['runs'])
    by_pair = {}
    for row in chosen:
        by_pair.setdefault(row['pair_id'], {})[row['group']] = row
    latency_pairs = [(pair['baseline']['latency_seconds'], pair['airp']['latency_seconds'])
                     for pair in by_pair.values()]
    return {'level': level, 'label': label, 'dependencies': dependencies,
            'cases': len(by_pair), 'totals': totals,
            'payload_reduction': 1 - totals['airp']['payload_tokens'] / totals['baseline']['payload_tokens'],
            'total_model_token_reduction': 1 - totals['airp']['total_model_tokens'] / totals['baseline']['total_model_tokens'],
            'tokens_saved_per_task': (totals['baseline']['total_model_tokens']
                                      - totals['airp']['total_model_tokens']) / len(by_pair),
            'latency_paired': bootstrap_log_ratio(latency_pairs),
            'completion_gate': (totals['baseline']['runs'] == len(by_pair)
                                and totals['airp']['runs'] == len(by_pair)),
            'all_answers_correct': (totals['baseline']['successes'] == len(by_pair)
                                    and totals['airp']['successes'] == len(by_pair))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/task-magnitude-expanded-v07.json')
    parser.add_argument('--model', default='gpt-5.6-luna')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.workers <= 3:
        raise SystemExit('workers must be 1..3')
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                          str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
    encoding = tiktoken.get_encoding('o200k_base')
    prepared = [prepare_case(level, label, dependencies, case_index, encoding)
                for level, label, dependencies in LEVELS
                for case_index in range(len(FAMILIES))]
    if args.prepare_only:
        print(json.dumps([{'level': item['level'], 'case': item['case_index'],
                           'baseline_tokens': item['baseline_payload_tokens'],
                           'airp_tokens': item['airp_payload_tokens']}
                          for item in prepared], ensure_ascii=False))
        return

    rows = []
    with tempfile.TemporaryDirectory(prefix='airp-expanded-isolated-') as isolated:
        isolated_root = Path(isolated)

        def execute_pair(item):
            conditions = [('baseline', item['baseline']), ('airp', item['airp'])]
            if (item['case_index'] + item['dependencies']) % 2:
                conditions.reverse()
            pair_rows = []
            for order, (group, payload) in enumerate(conditions):
                measured = run_codex(make_prompt(item, payload), args.model, args.effort,
                                     args.timeout, isolated_root)
                observed = answer(measured['answer'])
                pair_rows.append({'pair_id': f'{item["level"]}-{item["case_index"]}',
                                  'level': item['level'], 'label': item['label'],
                                  'dependencies': item['dependencies'],
                                  'case_index': item['case_index'], 'family': item['family'],
                                  'group': group, 'order': order,
                                  'expected': item['expected'], 'observed': observed,
                                  'success': observed == item['expected'],
                                  'payload_tokens': item[f'{group}_payload_tokens'],
                                  'model': args.model, 'effort': args.effort, **measured})
            return pair_rows

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(execute_pair, item) for item in prepared]
            for completed, future in enumerate(as_completed(futures), 1):
                pair_rows = future.result()
                rows.extend(pair_rows)
                print(json.dumps({'completed_pairs': completed, 'total_pairs': len(prepared),
                                  'pair_id': pair_rows[0]['pair_id'],
                                  'successes': sum(row['success'] for row in pair_rows)},
                                 ensure_ascii=False), flush=True)
    rows.sort(key=lambda row: (row['dependencies'], row['case_index'], row['group']))
    levels = [summarize(rows, level, label, dependencies)
              for level, label, dependencies in LEVELS]
    # Summarize all rows through a temporary common level label.
    overall = summarize([{**row, 'level': 'all'} for row in rows],
                        'all', '总体', None)
    report = {'experiment': 'expanded task magnitude paired real-model benchmark',
              'source_policy': 'synthetic Python only; isolated empty working directory',
              'model': args.model, 'effort': args.effort,
              'levels_definition': LEVELS, 'families': FAMILIES,
              'pairs': len(prepared), 'calls': len(rows), 'rows': rows,
              'levels': levels, 'overall': overall,
              'completion_gate': (len(rows) == 2 * len(prepared)
                                  and all(level['completion_gate'] for level in levels)),
              'all_answers_correct': all(level['all_answers_correct'] for level in levels),
              'limitations': ['Ten unique exact-answer cases per scale.',
                              'Wall-clock latency includes client and service variance.',
                              'Synthetic read-only Python dependency aggregation only.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'pairs': report['pairs'],
                      'calls': report['calls'],
                      'completion_gate': report['completion_gate'],
                      'all_answers_correct': report['all_answers_correct']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
