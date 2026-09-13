"""Paired real-model code-understanding experiment with frozen exact answers."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import tiktoken

from airp.core import Repository


ROOT = Path(__file__).resolve().parents[1]


def run_codex(prompt, model, effort, timeout):
    command = ['codex', 'exec', '--json', '--ephemeral', '--ignore-user-config',
               '--skip-git-repo-check', '--sandbox', 'read-only', '--model', model,
               '-c', f'model_reasoning_effort="{effort}"', '-C', str(ROOT), '-']
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
                                       'stdout_tail': proc.stdout[-2000:],
                                       'stderr_tail': proc.stderr[-2000:]}, ensure_ascii=False))
    usage = completed['usage']
    return {'answer': messages[-1], 'input_tokens': usage['input_tokens'],
            'cached_input_tokens': usage.get('cached_input_tokens', 0),
            'output_tokens': usage['output_tokens'],
            'reasoning_output_tokens': usage.get('reasoning_output_tokens', 0),
            'latency_seconds': round(time.monotonic() - started, 4)}


def make_prompt(task, payload):
    return f'''你正在参加固定答案的只读代码理解实验。不要调用工具，不要读取文件，只使用给定工具响应。

问题：{task["question"]}

严格只输出 `<answer>值</answer>`，不得解释。

工具响应：
```json
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
```
'''


def extracted_answer(text):
    match = re.fullmatch(r'\s*<answer>\s*(.*?)\s*</answer>\s*', text,
                         flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip().casefold() if match else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', type=Path,
                        default=ROOT / 'benchmarks/model_stage_tasks_v06.json')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/model-stage-ab-v06.json')
    parser.add_argument('--model', default='gpt-5.6-luna')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5 or not 1 <= args.workers <= 4:
        raise SystemExit('repeats must be 1..5 and workers must be 1..4')
    tasks = json.loads(args.tasks.read_text(encoding='utf-8'))
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                          str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
    encoding = tiktoken.get_encoding('o200k_base')
    repo = Repository(ROOT)
    prepared = []
    try:
        repo.index()
        for task in tasks:
            symbol = repo.get(task['symbol_id'])
            source = (ROOT / symbol['path']).read_text(encoding='utf-8')
            baseline = {'matches': [{'symbol_id': symbol['id'], 'path': symbol['path'],
                                     'line': symbol['start']}],
                        'files': [{'path': symbol['path'], 'source': source}]}
            airp = repo.task_context(task['retrieval_task'], budget=3000,
                                     budget_unit='tokens', intent='understand',
                                     breadth='narrow', response_mode='auto')
            anchor = airp['anchors'][0]['id'] if airp['anchors'] else None
            if anchor != symbol['id']:
                raise RuntimeError(f'{task["id"]}: expected {symbol["id"]}, got {anchor}')
            expected = task['expected'].casefold()
            if expected not in source.casefold() or expected not in airp['text'].casefold():
                raise RuntimeError(f'{task["id"]}: expected answer absent from supplied context')
            prepared.append((task, baseline, airp))
    finally:
        repo.close()
    if args.prepare_only:
        print(json.dumps({'prepared': len(prepared), 'status': 'passed'}, ensure_ascii=False))
        return

    jobs = []
    for repeat in range(args.repeats):
        for index, (task, baseline, airp) in enumerate(prepared):
            conditions = [('baseline', baseline), ('airp', airp)]
            if (repeat + index) % 2:
                conditions.reverse()
            for order, (group, payload) in enumerate(conditions):
                jobs.append({'task': task, 'repeat': repeat + 1, 'group': group,
                             'order': order, 'payload': payload,
                             'payload_tokens': len(encoding.encode(json.dumps(
                                 payload, ensure_ascii=False, separators=(',', ':'))))})

    rows = []
    def execute(job):
        measured = run_codex(make_prompt(job['task'], job['payload']), args.model,
                             args.effort, args.timeout)
        observed = extracted_answer(measured['answer'])
        return {key: value for key, value in job.items() if key != 'payload'} | {
            'task_id': job['task']['id'], 'expected': job['task']['expected'],
            'observed': observed, 'success': observed == job['task']['expected'].casefold(),
            'model': args.model, 'effort': args.effort,
            'context_sha256': hashlib.sha256(json.dumps(
                job['payload'], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            **measured}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(execute, job) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            row.pop('task')
            rows.append(row)
            print(json.dumps({key: row[key] for key in
                              ('task_id', 'repeat', 'group', 'success', 'input_tokens',
                               'cached_input_tokens', 'reasoning_output_tokens',
                               'output_tokens')}, ensure_ascii=False), flush=True)
    rows.sort(key=lambda row: (row['repeat'], row['task_id'], row['group']))
    totals = {}
    for group in ('baseline', 'airp'):
        selected = [row for row in rows if row['group'] == group]
        totals[group] = {key: sum(row[key] for row in selected) for key in
                         ('payload_tokens', 'input_tokens', 'cached_input_tokens',
                          'reasoning_output_tokens', 'output_tokens', 'latency_seconds')}
        totals[group]['uncached_input_tokens'] = (
            totals[group]['input_tokens'] - totals[group]['cached_input_tokens'])
        totals[group]['total_model_tokens'] = (totals[group]['input_tokens']
                                                + totals[group]['output_tokens'])
        totals[group]['runs'] = len(selected)
        totals[group]['successes'] = sum(row['success'] for row in selected)
    reductions, deltas = {}, {}
    for key in ('payload_tokens', 'input_tokens', 'uncached_input_tokens',
                'reasoning_output_tokens', 'output_tokens', 'total_model_tokens'):
        baseline, airp = totals['baseline'][key], totals['airp'][key]
        deltas[key] = baseline - airp
        reductions[key] = None if not baseline else 1 - airp / baseline
    report = {'experiment': 'paired exact-answer real-model code understanding',
              'model': args.model, 'effort': args.effort, 'repeats': args.repeats,
              'tasks': len(tasks), 'order': 'alternated within pairs',
              'acceptance': 'exact full-match <answer> value fixed before execution',
              'tokenizer_for_payload': 'o200k_base', 'rows': rows, 'totals': totals,
              'token_deltas': deltas, 'reductions': reductions,
              'quality_gate': (totals['baseline']['successes'] == totals['baseline']['runs']
                               and totals['airp']['successes'] == totals['airp']['runs']),
              'limitations': ['Read-only Python implementation questions.',
                              'Prompt processing, reasoning, and final output are measured; editing, tests, and repair loops are not.',
                              'Input and payload metrics overlap and must not be added.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'totals': totals,
                      'token_deltas': deltas, 'reductions': reductions,
                      'quality_gate': report['quality_gate']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
