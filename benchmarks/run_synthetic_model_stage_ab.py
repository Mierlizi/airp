"""Real-model paired stage metrics using generated code only."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import tiktoken

from airp.core import Repository
from run_language_scale_tokens import LANGUAGES, file_source


ROOT = Path(__file__).resolve().parents[1]


def run_codex(prompt, model, effort, timeout, isolated_root):
    command = ['codex', 'exec', '--json', '--ephemeral', '--ignore-user-config',
               '--skip-git-repo-check', '--sandbox', 'read-only', '--model', model,
               '-c', f'model_reasoning_effort="{effort}"', '-C', str(isolated_root), '-']
    started = time.monotonic()
    proc = subprocess.run(command, input=prompt, text=True, encoding='utf-8',
                          errors='replace', capture_output=True, timeout=timeout)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    completed = next((event for event in reversed(events)
                      if event.get('type') == 'turn.completed'), None)
    messages = [event['item']['text'] for event in events
                if event.get('type') == 'item.completed'
                and event.get('item', {}).get('type') == 'agent_message']
    if proc.returncode or not completed or not messages:
        raise RuntimeError(json.dumps({'returncode': proc.returncode,
                                       'stdout_tail': proc.stdout[-1000:],
                                       'stderr_tail': proc.stderr[-1000:]}, ensure_ascii=False))
    usage = completed['usage']
    return {'answer': messages[-1], 'input_tokens': usage['input_tokens'],
            'cached_input_tokens': usage.get('cached_input_tokens', 0),
            'output_tokens': usage['output_tokens'],
            'reasoning_output_tokens': usage.get('reasoning_output_tokens', 0),
            'latency_seconds': round(time.monotonic() - started, 4)}


def answer(text):
    match = re.fullmatch(r'\s*<answer>\s*(.*?)\s*</answer>\s*', text,
                         flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip().casefold() if match else None


def prompt(item, payload):
    return f'''这是合成代码的固定答案实验。不要调用工具，只使用给定工具响应。
问题：函数 {item["target_name"]} 的注释标记是什么？
严格只输出 `<answer>标记</answer>`，不得解释。
工具响应：
```json
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
```
'''


def prepare_items(functions_per_file):
    scales = (10, 100, 500, 1000)
    prepared = []
    for index, (display, (language, extension)) in enumerate(LANGUAGES.items()):
        scale = scales[index % len(scales)]
        target_name = f'calculate_refund_{scale}'
        marker = f'REFUND_SENTINEL_{language.upper()}_{scale}'
        filename = f'module_{scale - 1:05d}{extension}'
        source = file_source(language, scale - 1, functions_per_file, target_name, marker)
        with tempfile.TemporaryDirectory(prefix='airp-synthetic-stage-') as folder:
            root = Path(folder)
            (root / filename).write_text(source, encoding='utf-8')
            repo = Repository(root)
            try:
                repo.index()
                row = repo.db.execute('SELECT sid FROM symbol_lookup WHERE name=?',
                                      (target_name,)).fetchone()
                if row is None:
                    raise RuntimeError(f'No generated target for {display}')
                sid = row[0]
                airp = repo.task_context(f'inspect {target_name} {marker}', budget=3000,
                                         budget_unit='tokens', intent='understand',
                                         breadth='narrow', response_mode='auto')
            finally:
                repo.close()
        anchor = airp['anchors'][0]['id'] if airp['anchors'] else None
        if anchor != sid or marker not in airp['text']:
            raise RuntimeError(f'Generated AIRP evidence failed for {display}')
        baseline = {'matches': [{'symbol_id': sid, 'path': filename}],
                    'files': [{'path': filename, 'source': source}]}
        prepared.append({'language': display, 'backend_language': language,
                         'scale_files': scale, 'target_name': target_name,
                         'expected': marker.casefold(), 'baseline': baseline, 'airp': airp})
    return prepared


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/synthetic-model-stage-ab-v06.json')
    parser.add_argument('--model', default='gpt-5.6-luna')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--functions-per-file', type=int, default=24)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5 or not 1 <= args.workers <= 4:
        raise SystemExit('repeats must be 1..5 and workers must be 1..4')
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                          str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
    encoding = tiktoken.get_encoding('o200k_base')
    prepared = prepare_items(args.functions_per_file)
    if args.prepare_only:
        print(json.dumps({'prepared': len(prepared), 'languages': [x['language'] for x in prepared]}))
        return
    jobs = []
    for repeat in range(1, args.repeats + 1):
        for index, item in enumerate(prepared):
            conditions = [('baseline', item['baseline']), ('airp', item['airp'])]
            if (repeat + index) % 2:
                conditions.reverse()
            for order, (group, payload) in enumerate(conditions):
                jobs.append({'item': item, 'repeat': repeat, 'group': group, 'order': order,
                             'payload': payload, 'payload_tokens': len(encoding.encode(
                                 json.dumps(payload, ensure_ascii=False, separators=(',', ':'))))})
    rows = []
    with tempfile.TemporaryDirectory(prefix='airp-model-isolated-') as isolated:
        isolated_root = Path(isolated)
        def execute(job):
            measured = run_codex(prompt(job['item'], job['payload']), args.model, args.effort,
                                 args.timeout, isolated_root)
            observed = answer(measured['answer'])
            return {'language': job['item']['language'],
                    'scale_files': job['item']['scale_files'], 'repeat': job['repeat'],
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
                                  ('language', 'scale_files', 'repeat', 'group', 'success',
                                   'input_tokens', 'cached_input_tokens',
                                   'reasoning_output_tokens', 'output_tokens')},
                                 ensure_ascii=False), flush=True)
    rows.sort(key=lambda row: (row['repeat'], row['language'], row['group']))
    totals = {}
    for group in ('baseline', 'airp'):
        chosen = [row for row in rows if row['group'] == group]
        totals[group] = {key: sum(row[key] for row in chosen) for key in
                         ('payload_tokens', 'input_tokens', 'cached_input_tokens',
                          'reasoning_output_tokens', 'output_tokens', 'latency_seconds')}
        totals[group]['uncached_input_tokens'] = (
            totals[group]['input_tokens'] - totals[group]['cached_input_tokens'])
        totals[group]['total_model_tokens'] = (totals[group]['input_tokens']
                                                + totals[group]['output_tokens'])
        totals[group]['runs'] = len(chosen)
        totals[group]['successes'] = sum(row['success'] for row in chosen)
    keys = ('payload_tokens', 'input_tokens', 'uncached_input_tokens',
            'reasoning_output_tokens', 'output_tokens', 'total_model_tokens')
    deltas = {key: totals['baseline'][key] - totals['airp'][key] for key in keys}
    reductions = {key: (None if not totals['baseline'][key] else
                        1 - totals['airp'][key] / totals['baseline'][key]) for key in keys}
    quality_gate = all(totals[group]['successes'] == totals[group]['runs']
                       for group in ('baseline', 'airp'))
    report = {'experiment': 'generated-code paired real-model stage measurement',
              'source_policy': 'synthetic code only; isolated empty working directory',
              'model': args.model, 'effort': args.effort, 'languages': len(prepared),
              'scales_sampled': sorted({item['scale_files'] for item in prepared}),
              'repeats': args.repeats, 'order': 'alternated within pairs',
              'acceptance': 'exact full-match frozen marker', 'rows': rows,
              'totals': totals, 'token_deltas': deltas, 'reductions': reductions,
              'quality_gate': quality_gate,
              'limitations': ['One exact-answer understanding task shape per language.',
                              'Editing, test execution, and repair loops are not measured.',
                              'Payload tokens are contained in model input and are not additive.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'totals': totals,
                      'token_deltas': deltas, 'reductions': reductions,
                      'quality_gate': quality_gate}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
