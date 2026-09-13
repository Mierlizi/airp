"""Measure AIRP token savings as the amount of required code evidence grows."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import tempfile
import sys

import tiktoken

from airp.core import Repository
from run_synthetic_model_stage_ab import answer, run_codex


ROOT = Path(__file__).resolve().parents[1]
LEVELS = [('micro', '微型', 1), ('small', '小型', 3),
          ('medium', '中型', 8), ('large', '大型', 16)]


def dependency_source(index, distractors=24):
    functions = [f'def filler_{index}_{item}(value):\n    adjusted = value + {item}\n    return adjusted\n'
                 for item in range(distractors)]
    functions.append(f'def signal_{index}(value={index}):\n    return value\n')
    return '\n'.join(functions)


def prepare_level(key, label, dependency_count):
    with tempfile.TemporaryDirectory(prefix=f'airp-magnitude-{key}-') as folder:
        root = Path(folder)
        imports = '\n'.join(f'from module_{index} import signal_{index}'
                            for index in range(1, dependency_count + 1))
        calls = ', '.join(f'signal_{index}()' for index in range(1, dependency_count + 1))
        target_name = f'aggregate_signals_{dependency_count}'
        target_source = (f'{imports}\n\ndef {target_name}():\n'
                         f'    return sum([{calls}])\n')
        (root / 'target.py').write_text(target_source, encoding='utf-8')
        paths = ['target.py']
        for index in range(1, dependency_count + 1):
            path = f'module_{index}.py'
            (root / path).write_text(dependency_source(index), encoding='utf-8')
            paths.append(path)
        repo = Repository(root)
        try:
            repo.index()
            sid = f'target.py::{target_name}'
            airp = repo.task_context(f'inspect {target_name} direct signal defaults',
                                     budget=6000, budget_unit='tokens',
                                     intent='understand', breadth='narrow',
                                     response_mode='auto')
        finally:
            repo.close()
        anchor = airp['anchors'][0]['id'] if airp['anchors'] else None
        missing = [f'signal_{index}' for index in range(1, dependency_count + 1)
                   if f'signal_{index}' not in airp['text']]
        if anchor != sid or missing:
            raise RuntimeError(f'{key}: incomplete AIRP evidence; anchor={anchor}; missing={missing}')
        baseline = {'matches': [{'symbol_id': sid, 'path': 'target.py'}],
                    'files': [{'path': path,
                               'source': (root / path).read_text(encoding='utf-8')}
                              for path in paths]}
    return {'level': key, 'label': label, 'dependencies': dependency_count,
            'target_name': target_name, 'expected': str(
                dependency_count * (dependency_count + 1) // 2),
            'baseline': baseline, 'airp': airp}


def make_prompt(item, payload):
    return f'''这是生成代码的固定答案实验。不要调用工具，只使用给定工具响应。
问题：{item["target_name"]} 直接调用的所有 signal 函数，其默认参数之和是多少？
严格只输出 `<answer>整数</answer>`，不得解释。
工具响应：
```json
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
```
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/task-magnitude-model-ab-v06.json')
    parser.add_argument('--model', default='gpt-5.6-luna')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                          str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
    encoding = tiktoken.get_encoding('o200k_base')
    prepared = [prepare_level(*level) for level in LEVELS]
    if args.prepare_only:
        print(json.dumps([{'level': item['level'], 'dependencies': item['dependencies'],
                           'baseline_tokens': len(encoding.encode(json.dumps(
                               item['baseline'], ensure_ascii=False, separators=(',', ':')))),
                           'airp_tokens': len(encoding.encode(json.dumps(
                               item['airp'], ensure_ascii=False, separators=(',', ':'))))}
                          for item in prepared], ensure_ascii=False, indent=2))
        return
    jobs = []
    for repeat in range(1, args.repeats + 1):
        for index, item in enumerate(prepared):
            conditions = [('baseline', item['baseline']), ('airp', item['airp'])]
            if (repeat + index) % 2:
                conditions.reverse()
            for order, (group, payload) in enumerate(conditions):
                jobs.append({'item': item, 'repeat': repeat, 'group': group, 'order': order,
                             'payload': payload, 'payload_tokens': len(encoding.encode(json.dumps(
                                 payload, ensure_ascii=False, separators=(',', ':'))))})
    rows = []
    with tempfile.TemporaryDirectory(prefix='airp-magnitude-isolated-') as isolated:
        isolated_root = Path(isolated)
        def execute(job):
            measured = run_codex(make_prompt(job['item'], job['payload']), args.model,
                                 args.effort, args.timeout, isolated_root)
            observed = answer(measured['answer'])
            return {'level': job['item']['level'], 'label': job['item']['label'],
                    'dependencies': job['item']['dependencies'], 'repeat': job['repeat'],
                    'group': job['group'], 'order': job['order'],
                    'expected': job['item']['expected'], 'observed': observed,
                    'success': observed == job['item']['expected'],
                    'payload_tokens': job['payload_tokens'], 'model': args.model,
                    'effort': args.effort, **measured}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(execute, job) for job in jobs]
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                print(json.dumps({key: row[key] for key in
                                  ('level', 'repeat', 'group', 'success', 'payload_tokens',
                                   'input_tokens', 'reasoning_output_tokens', 'output_tokens')},
                                 ensure_ascii=False), flush=True)
    rows.sort(key=lambda row: (row['level'], row['repeat'], row['group']))
    by_level = []
    keys = ('payload_tokens', 'input_tokens', 'uncached_input_tokens',
            'reasoning_output_tokens', 'output_tokens', 'total_model_tokens')
    for key, label, dependency_count in LEVELS:
        totals = {}
        for group in ('baseline', 'airp'):
            chosen = [row for row in rows if row['level'] == key and row['group'] == group]
            totals[group] = {name: sum(row[name] for row in chosen) for name in
                             ('payload_tokens', 'input_tokens', 'cached_input_tokens',
                              'reasoning_output_tokens', 'output_tokens', 'latency_seconds')}
            totals[group]['uncached_input_tokens'] = (
                totals[group]['input_tokens'] - totals[group]['cached_input_tokens'])
            totals[group]['total_model_tokens'] = (totals[group]['input_tokens']
                                                    + totals[group]['output_tokens'])
            totals[group]['runs'] = len(chosen)
            totals[group]['successes'] = sum(row['success'] for row in chosen)
        deltas = {name: totals['baseline'][name] - totals['airp'][name] for name in keys}
        reductions = {name: (None if not totals['baseline'][name] else
                             1 - totals['airp'][name] / totals['baseline'][name])
                      for name in keys}
        by_level.append({'level': key, 'label': label, 'dependencies': dependency_count,
                         'totals': totals, 'token_deltas': deltas,
                         'reductions': reductions,
                         'quality_gate': all(totals[group]['successes'] == totals[group]['runs']
                                             for group in ('baseline', 'airp'))})
    report = {'experiment': 'task evidence magnitude paired real-model benchmark',
              'source_policy': 'synthetic Python code only; isolated empty working directory',
              'model': args.model, 'effort': args.effort, 'repeats': args.repeats,
              'levels': by_level, 'rows': rows,
              'quality_gate': all(level['quality_gate'] for level in by_level),
              'limitations': ['One direct-dependency aggregation task shape per level.',
                              'Measures read-only understanding, not editing or repair.',
                              'Payload and model input metrics overlap and are not additive.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'quality_gate': report['quality_gate'],
                      'levels': by_level}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
