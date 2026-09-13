"""Run a safe, read-only paired context experiment with real Codex usage events."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

from airp.core import Repository


ROOT = Path(__file__).resolve().parents[1]


def run_codex(prompt: str, model: str, effort: str) -> dict:
    command = [
        'codex', 'exec', '--json', '--ephemeral', '--ignore-user-config',
        '--skip-git-repo-check', '--sandbox', 'read-only', '--model', model,
        '-c', f'model_reasoning_effort="{effort}"', '-C', str(ROOT), '-'
    ]
    started = time.monotonic()
    proc = subprocess.run(command, input=prompt, text=True, encoding='utf-8',
                          errors='replace', capture_output=True, timeout=180)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    completed = next((e for e in reversed(events) if e.get('type') == 'turn.completed'), None)
    messages = [e['item']['text'] for e in events
                if e.get('type') == 'item.completed' and e.get('item', {}).get('type') == 'agent_message']
    if proc.returncode or not completed or not messages:
        raise RuntimeError(json.dumps({
            'returncode': proc.returncode,
            'stdout_tail': proc.stdout[-2000:],
            'stderr_tail': proc.stderr[-2000:]
        }, ensure_ascii=False))
    usage = completed['usage']
    return {
        'answer': messages[-1],
        'input_tokens': usage['input_tokens'],
        'cached_input_tokens': usage.get('cached_input_tokens', 0),
        'output_tokens': usage['output_tokens'],
        'reasoning_output_tokens': usage.get('reasoning_output_tokens', 0),
        'latency_seconds': time.monotonic() - started,
    }


def prompt(task: dict, context: str) -> str:
    return f"""你正在参加一个只读代码理解实验。不要调用任何工具，不要读取文件，只根据下方提供的代码上下文回答。

问题：{task['question']}

要求：用中文简洁回答，明确区分代码保证与局限；不要描述实验流程。

代码上下文：
```python
{context}
```
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='gpt-5.6-luna')
    ap.add_argument('--effort', default='low')
    ap.add_argument('--output', type=Path,
                    default=ROOT / 'benchmarks/runs/context-ab-20260912.json')
    ap.add_argument('--order-offset', type=int, default=0,
                    help='Add one to reverse every pair order in a repeat run')
    args = ap.parse_args()
    tasks = json.loads((ROOT / 'benchmarks/context_tasks.json').read_text(encoding='utf-8'))
    repo = Repository(ROOT)
    try:
        repo.execute('index')
        rows = []
        # Alternate order to reduce a simple warm-cache/order bias.
        for index, task in enumerate(tasks):
            symbol = repo.execute('get', symbol_id=task['symbol_id'])
            full_file = (ROOT / symbol['path']).read_text(encoding='utf-8')
            airp = repo.execute('context', symbol_id=task['symbol_id'], budget=8000)
            if not airp['target_included']:
                raise RuntimeError(f"AIRP omitted target for {task['id']}")
            conditions = [('baseline', full_file), ('airp', airp['text'])]
            if (index + args.order_offset) % 2:
                conditions.reverse()
            for group, supplied in conditions:
                measured = run_codex(prompt(task, supplied), args.model, args.effort)
                normalized = measured['answer'].casefold()
                success = all(any(term.casefold() in normalized for term in alternatives)
                              for alternatives in task['required_terms'])
                rows.append({
                    'task_id': task['id'], 'group': group, 'model': args.model,
                    'effort': args.effort, 'success': success,
                    'context_bytes': len(supplied.encode('utf-8')),
                    **measured,
                })
                print(json.dumps({k: rows[-1][k] for k in
                                  ('task_id', 'group', 'success', 'input_tokens',
                                   'cached_input_tokens', 'output_tokens', 'context_bytes')},
                                 ensure_ascii=False), flush=True)
        by_group = {g: [r for r in rows if r['group'] == g] for g in ('baseline', 'airp')}
        totals = {}
        for group, data in by_group.items():
            totals[group] = {
                'runs': len(data),
                'successes': sum(r['success'] for r in data),
                'input_tokens': sum(r['input_tokens'] for r in data),
                'uncached_input_tokens': sum(r['input_tokens'] - r['cached_input_tokens'] for r in data),
                'output_tokens': sum(r['output_tokens'] for r in data),
                'context_bytes': sum(r['context_bytes'] for r in data),
                'latency_seconds': sum(r['latency_seconds'] for r in data),
            }
        base, airp_total = totals['baseline'], totals['airp']
        report = {
            'experiment': 'read-only context ablation',
            'model': args.model,
            'effort': args.effort,
            'rows': rows,
            'totals': totals,
            'reductions': {
                'input_tokens': 1 - airp_total['input_tokens'] / base['input_tokens'],
                'uncached_input_tokens': 1 - airp_total['uncached_input_tokens'] / base['uncached_input_tokens'],
                'context_bytes': 1 - airp_total['context_bytes'] / base['context_bytes'],
            },
            'limitations': [
                'Three single-run code-understanding tasks; no statistical significance.',
                'Baseline receives the full target file; AIRP receives compiler-selected symbols.',
                'This isolates context selection and excludes editing, tool-call overhead, retries, and MCP schemas.',
                'Keyword acceptance is deterministic but weaker than repository-level hidden tests.'
            ]
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'report': str(args.output), 'totals': totals,
                          'reductions': report['reductions']}, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == '__main__':
    main()
