"""Deterministic fixed-balanced versus adaptive retrieval comparison."""
import json
import os
from pathlib import Path
import sys

from airp.core import Repository

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'benchmarks' / 'real_source_tasks_v10.json'
OUTPUT = ROOT / 'benchmarks' / 'runs' / 'adaptive-retrieval-v15.json'


COUNT_METHOD = 'UTF-8 bytes/4 proxy'


def encoded_size(value):
    global COUNT_METHOD
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    try:
        import tiktoken
        os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                              str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
        count = len(tiktoken.get_encoding('o200k_base').encode(raw))
        COUNT_METHOD = 'o200k_base exact'
        return count
    except Exception:
        return (len(raw.encode('utf-8')) + 3) // 4


def main():
    tasks = json.loads(TASKS.read_text(encoding='utf-8'))
    repo = Repository(ROOT)
    rows = []
    try:
        repo.execute('index')
        for task in tasks:
            fixed = repo.execute('task_context', task=task['question'],
                                 breadth='balanced', response_mode='auto')
            adaptive = repo.execute('smart_context', task=task['question'])
            rows.append({
                'task_id': task['id'],
                'fixed': {'tokens': encoded_size(fixed),
                          'anchors': len(fixed['anchors']),
                          'sufficient': fixed['sufficient']},
                'adaptive': {'tokens': encoded_size(adaptive),
                             'anchors': len(adaptive['anchors']),
                             'sufficient': adaptive['sufficient'],
                             'breadth': adaptive['routing']['selected_breadth'],
                             'recommended': adaptive['routing']['recommended'],
                             'missing_identifiers':
                                 adaptive.get('coverage', {}).get('missing_identifiers', [])}
            })
    finally:
        repo.close()
    fixed_tokens = sum(row['fixed']['tokens'] for row in rows)
    adaptive_tokens = sum(row['adaptive']['tokens'] for row in rows)
    result = {
        'experiment': 'deterministic adaptive retrieval ablation',
        'tasks': len(rows), 'rows': rows,
        'fixed_tokens': fixed_tokens, 'adaptive_tokens': adaptive_tokens,
        'response_token_reduction': 1 - adaptive_tokens / fixed_tokens,
        'fixed_sufficient': sum(row['fixed']['sufficient'] for row in rows),
        'adaptive_sufficient': sum(row['adaptive']['sufficient'] for row in rows),
        'adaptive_recommended': sum(row['adaptive']['recommended'] for row in rows),
        'count_method': COUNT_METHOD,
        'note': 'Counts complete local tool responses; does not include Agent rounds.'
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
