"""Measure bounded first-use edit-frontier retrieval on fixed large repositories."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from airp.graph import sources
from airp.hook import build_prompt_context


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'benchmarks' / 'corpus-large'
CASES = {
    'django': 'Modify slugify(...) in the django utils text module to support a configurable separator while preserving its default behavior.',
    'nest': 'Modify isNil in the common shared utils module so its public behavior remains compatible with existing callers and tests.',
    'ripgrep': 'Refactor should_preprocess(...) in ripgrep core search while preserving behavior and relevant callers.',
    'redis': 'Modify stringmatchlen(...) in redis util while preserving its declaration and existing caller behavior.',
}


def measure(name: str) -> dict:
    root = CORPUS / name
    source_paths = list(sources(root))
    started = time.perf_counter()
    context = build_prompt_context(root, CASES[name]) or ''
    elapsed = time.perf_counter() - started
    frontier = next((line for line in context.splitlines()
                     if line.startswith('edit_frontier:')), '')
    anchor = next((line for line in context.splitlines()
                   if line.startswith('anchors:')), '')
    return {
        'repository': name,
        'source_files': len(source_paths),
        'source_bytes': sum(path.stat().st_size for path in source_paths),
        'latency_seconds': round(elapsed, 3),
        'context_chars': len(context),
        'status': ('sufficient' if 'status: sufficient' in context else 'partial'),
        'anchor': anchor.removeprefix('anchors: '),
        'frontier': frontier.removeprefix('edit_frontier: '),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('repositories', nargs='*', choices=sorted(CASES))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    names = args.repositories or list(CASES)
    report = {
        'experiment': 'deterministic large-repository edit-frontier retrieval',
        'model_calls': 0,
        'rows': [measure(name) for name in names],
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding='utf-8')
    print(payload, end='')


if __name__ == '__main__':
    main()
