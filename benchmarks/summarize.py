"""Aggregate actual paired agent runs. Never fabricate token or task-success data."""
import argparse
import json
from pathlib import Path
from statistics import mean


def summarize(rows):
    if not rows:
        raise ValueError('No measured runs supplied')
    required = {'task_id', 'group', 'model', 'revision', 'run', 'success', 'input_tokens',
                'output_tokens', 'cost_usd', 'tool_calls', 'latency_seconds'}
    groups = {'baseline': {}, 'airp': {}}
    for row in rows:
        if required - row.keys():
            raise ValueError('Missing fields: ' + ', '.join(sorted(required - row.keys())))
        if row['group'] not in groups or type(row['success']) is not bool:
            raise ValueError('group must be baseline/airp and success must be a boolean')
        for key in ('input_tokens', 'output_tokens', 'cost_usd', 'tool_calls', 'latency_seconds'):
            import math
            if type(row[key]) not in (int, float) or not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError(f'{key} must be a finite nonnegative number')
        key = (row['task_id'], row['model'], row['revision'], row['run'])
        if key in groups[row['group']]:
            raise ValueError(f'Duplicate run: {key}')
        groups[row['group']][key] = row
    if groups['baseline'].keys() != groups['airp'].keys():
        raise ValueError('Each run needs a matching task/model/revision/run in both groups')
    result = {}
    for group, mapping in groups.items():
        data = list(mapping.values())
        successes = sum(r['success'] for r in data)
        result[group] = dict(runs=len(data), successes=successes, success_rate=successes / len(data),
                             total_tokens=sum(r['input_tokens'] + r['output_tokens'] for r in data),
                             cost_per_success_usd=sum(r['cost_usd'] for r in data) / successes if successes else None,
                             mean_tool_calls=mean(r['tool_calls'] for r in data),
                             mean_latency_seconds=mean(r['latency_seconds'] for r in data))
    base, airp = result['baseline'], result['airp']
    result['token_reduction'] = 1 - airp['total_tokens'] / base['total_tokens'] if base['total_tokens'] else None
    a, b = base['cost_per_success_usd'], airp['cost_per_success_usd']
    result['cost_per_success_reduction'] = 1 - b / a if a and b is not None else None
    result['quality_noninferior_observed'] = airp['success_rate'] >= base['success_rate']
    result['note'] = 'Descriptive paired results only; no statistical significance or commercial validation claim.'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('runs', type=Path, help='Actual measured runs as JSONL')
    args = parser.parse_args()
    try:
        rows = [json.loads(line) for line in args.runs.read_text(encoding='utf-8').splitlines() if line.strip()]
        print(json.dumps(summarize(rows), indent=2, ensure_ascii=False))
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
