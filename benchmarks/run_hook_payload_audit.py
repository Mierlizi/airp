"""Deterministic token audit of MCP JSON versus pre-model hook context."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from airp.core import Repository
from airp.hook import build_prompt_context


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'benchmarks/real_source_tasks_v17.json'
OUTPUT = ROOT / 'benchmarks/runs/hook-payload-v17.json'
INCLUDE = ('airp', 'tests', 'examples', 'plugins', 'README.md', 'pyproject.toml')


def copy_source(destination):
    for name in INCLUDE:
        source, target = ROOT / name, destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                '.airp', '__pycache__', '.pytest_cache', '*.pyc'))
        else:
            shutil.copy2(source, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', type=Path, default=TASKS)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    import tiktoken
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                          str(Path(sys.prefix) / 'share/airp-token-cache'))
    encoding = tiktoken.get_encoding('o200k_base')
    tasks = json.loads(args.tasks.read_text(encoding='utf-8'))
    rows = []
    with tempfile.TemporaryDirectory(prefix='airp-payload-audit-') as folder:
        source_root = Path(folder)
        copy_source(source_root)
        repo = Repository(source_root)
        try:
            repo.execute('index')
            for task in tasks:
                mcp_result = repo.execute('smart_context', task=task['question'])
                mcp_text = json.dumps(mcp_result, ensure_ascii=False, separators=(',', ':'))
                hook_text = build_prompt_context(source_root, task['question']) or ''
                rows.append({
                    'task_id': task['id'], 'category': task.get('category', 'unspecified'),
                    'mcp_response_tokens': len(encoding.encode(mcp_text)),
                    'hook_context_tokens': len(encoding.encode(hook_text)),
                    'hook_status': ('sufficient' if 'status: sufficient' in hook_text
                                    else 'partial' if hook_text else 'none'),
                    'hook_chars': len(hook_text),
                })
        finally:
            repo.close()
    mcp_tokens = sum(row['mcp_response_tokens'] for row in rows)
    hook_tokens = sum(row['hook_context_tokens'] for row in rows)
    report = {
        'experiment': 'deterministic MCP-response versus pre-model-hook payload audit',
        'tasks': len(rows), 'rows': rows, 'mcp_response_tokens': mcp_tokens,
        'hook_context_tokens': hook_tokens,
        'payload_reduction': 1 - hook_tokens / mcp_tokens,
        'hook_sufficient': sum(row['hook_status'] == 'sufficient' for row in rows),
        'count_method': 'o200k_base exact',
        'source_scope': list(INCLUDE),
        'note': 'Payload-only audit; the paired Agent benchmark measures total model tokens.',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
