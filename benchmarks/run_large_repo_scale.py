"""Measure AIRP indexing and bounded evidence retrieval on fixed large repositories."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time

from airp.core import Repository
from airp.graph import is_test_path, sources
from airp.hook import build_prompt_context


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'benchmarks' / 'corpus-large'
CASES = {
    'django': ('Python', 'Explain how QuerySet.get handles multiple matching objects.'),
    'nest': ('TypeScript', 'Explain how NestFactoryStatic.create builds an application.'),
    'ripgrep': ('Rust', 'Which matcher variants does PatternMatcher support?'),
    'redis': ('C', 'Explain how dictFind locates a key in the hash table.'),
}


def commit(root: Path) -> str:
    return subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()


def measure(name: str, full_index: bool = False) -> dict:
    language, task = CASES[name]
    root = CORPUS / name
    paths = list(sources(root))
    loc = 0
    production_loc = 0
    for path in paths:
        count = len(path.read_text(encoding='utf-8', errors='replace').splitlines())
        loc += count
        if not is_test_path(path.relative_to(root)):
            production_loc += count
    stats = None
    if full_index:
        repo = Repository(root)
        started = time.perf_counter()
        stats = repo.index()
        index_seconds = time.perf_counter() - started
        repo.close()
    else:
        index_seconds = None
    started = time.perf_counter()
    context = build_prompt_context(root, task, max_chars=7800)
    context_seconds = time.perf_counter() - started
    anchor_match = __import__('re').search(r'(?m)^anchors: (.+)$', context or '')
    status_match = __import__('re').search(r'(?m)^status: (.+)$', context or '')
    return {
        'repository': name,
        'commit': commit(root),
        'language': language,
        'source_files': len(paths),
        'indexed_loc': loc,
        'production_loc': production_loc,
        'index_seconds': None if index_seconds is None else round(index_seconds, 4),
        'files': None if stats is None else stats['files'],
        'symbols': None if stats is None else stats['symbols'],
        'imports': None if stats is None else stats['imports'],
        'edges': None if stats is None else stats['edges'],
        'diagnostic_count': None if stats is None else len(stats['diagnostics']),
        'task': task,
        'context_seconds': round(context_seconds, 4),
        'context_chars': len(context or ''),
        'status': status_match.group(1) if status_match else 'none',
        'anchors': [] if not anchor_match else anchor_match.group(1).split(', '),
        'fast_start': bool(context and 'index_mode: exact-symbol fast start' in context),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/large-repo-scale-v27.json')
    parser.add_argument('repositories', nargs='*', choices=sorted(CASES))
    parser.add_argument('--full-index', action='store_true')
    args = parser.parse_args()
    names = args.repositories or list(CASES)
    rows = []
    for name in names:
        row = measure(name, full_index=args.full_index)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {'experiment': 'large-repository deterministic scale benchmark',
              'mode': 'full-index' if args.full_index else 'exact-symbol-fast-start',
              'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')


if __name__ == '__main__':
    main()
