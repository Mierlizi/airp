"""Deterministic protocol benchmark for legacy discovery versus task context packs."""
import argparse
import json
from pathlib import Path

from airp.core import Repository


PROJECT = Path(__file__).resolve().parents[1]


def wire_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default=str(PROJECT))
    parser.add_argument('--tasks', default=str(PROJECT / 'benchmarks/context_tasks.json'))
    parser.add_argument('--output', default=str(PROJECT / 'benchmarks/runs/task-pack-v04.json'))
    parser.add_argument('--budget', type=int, default=3000)
    args = parser.parse_args()
    tasks = json.loads(Path(args.tasks).read_text(encoding='utf-8'))
    repo = Repository(args.repo)
    rows = []
    try:
        repo.execute('index')
        for task in tasks:
            sid = task['symbol_id']
            name = sid.rsplit('.', 1)[-1].rsplit('::', 1)[-1]
            found = repo.execute('find', query=name, limit=10)
            legacy = repo.execute('context', symbol_id=sid, budget=args.budget)
            packed = repo.execute('task_context', task=task['question'], budget=args.budget,
                                  breadth='narrow')
            followup = repo.execute('task_context', task=task['question'], budget=args.budget,
                                    breadth='narrow', receipt_id=packed['receipt_id'])
            rows.append({
                'task_id': task['id'],
                'expected': sid,
                'pack_anchor': packed['anchors'][0]['id'] if packed['anchors'] else None,
                'retrieval_correct': bool(packed['anchors'] and packed['anchors'][0]['id'] == sid),
                'legacy_tool_calls': 2,
                'pack_tool_calls': 1,
                'legacy_wire_bytes': wire_bytes(found) + wire_bytes(legacy),
                'pack_wire_bytes': wire_bytes(packed),
                'legacy_context_bytes': len(legacy['text'].encode('utf-8')),
                'pack_context_bytes': len(packed['text'].encode('utf-8')),
                'followup_delta_context_bytes': len(followup['text'].encode('utf-8')),
            })
    finally:
        repo.close()
    totals = {
        'tasks': len(rows),
        'retrieval_correct': sum(row['retrieval_correct'] for row in rows),
        'legacy_tool_calls': sum(row['legacy_tool_calls'] for row in rows),
        'pack_tool_calls': sum(row['pack_tool_calls'] for row in rows),
        'legacy_wire_bytes': sum(row['legacy_wire_bytes'] for row in rows),
        'pack_wire_bytes': sum(row['pack_wire_bytes'] for row in rows),
        'pack_context_bytes': sum(row['pack_context_bytes'] for row in rows),
        'followup_delta_context_bytes': sum(row['followup_delta_context_bytes'] for row in rows),
    }
    totals['tool_call_reduction_percent'] = round(
        100 * (1 - totals['pack_tool_calls'] / totals['legacy_tool_calls']), 2)
    totals['wire_byte_reduction_percent'] = round(
        100 * (1 - totals['pack_wire_bytes'] / totals['legacy_wire_bytes']), 2)
    totals['followup_context_reduction_percent'] = round(
        100 * (1 - totals['followup_delta_context_bytes'] / totals['pack_context_bytes']), 2)
    report = {'method': 'local deterministic protocol benchmark; no model calls',
              'budget_bytes_per_task': args.budget, 'rows': rows, 'totals': totals}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
