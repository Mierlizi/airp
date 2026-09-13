"""Re-evaluate and combine saved context A/B runs using the current acceptance rules."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/context-ab-summary-20260912.json')
    args = parser.parse_args()
    tasks = {t['id']: t for t in json.loads(
        (ROOT / 'benchmarks/context_tasks.json').read_text(encoding='utf-8'))}
    rows = []
    for path in args.reports:
        report = json.loads(path.read_text(encoding='utf-8'))
        for row in report['rows']:
            normalized = row['answer'].casefold()
            row['success'] = all(any(term.casefold() in normalized for term in alternatives)
                                 for alternatives in tasks[row['task_id']]['required_terms'])
            row['source_report'] = str(path)
            rows.append(row)
    totals = {}
    for group in ('baseline', 'airp'):
        data = [r for r in rows if r['group'] == group]
        totals[group] = {
            'runs': len(data), 'successes': sum(r['success'] for r in data),
            'input_tokens': sum(r['input_tokens'] for r in data),
            'uncached_input_tokens': sum(r['input_tokens'] - r['cached_input_tokens'] for r in data),
            'output_tokens': sum(r['output_tokens'] for r in data),
            'total_tokens': sum(r['input_tokens'] + r['output_tokens'] for r in data),
            'context_bytes': sum(r['context_bytes'] for r in data),
            'latency_seconds': sum(r['latency_seconds'] for r in data),
        }
    base, airp = totals['baseline'], totals['airp']
    reductions = {key: 1 - airp[key] / base[key] for key in
                  ('input_tokens', 'uncached_input_tokens', 'output_tokens',
                   'total_tokens', 'context_bytes', 'latency_seconds')}
    output = {'reports': [str(p) for p in args.reports], 'totals': totals,
              'reductions': reductions,
              'scope': 'Read-only context selection; paired descriptive result.'}
    target = args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output | {'output': str(target)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
