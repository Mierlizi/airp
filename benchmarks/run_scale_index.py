"""Local synthetic scaling check for incremental indexing; no model is invoked."""
import argparse
import json
from pathlib import Path
import tempfile
import time

from airp.core import Repository


def timed(repo, tool, **arguments):
    started = time.perf_counter()
    result = repo.execute(tool, **arguments)
    return result, time.perf_counter() - started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--files', type=int, default=1000)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 10 <= args.files <= 20000:
        raise SystemExit('--files must be between 10 and 20000')
    with tempfile.TemporaryDirectory(prefix='airp-scale-') as folder:
        root = Path(folder)
        for index in range(args.files):
            dependency = f'func_{index - 1}()' if index else '0'
            (root / f'module_{index:05d}.py').write_text(
                f'def func_{index}():\n    return {dependency}\n', encoding='utf-8')
        repo = Repository(root)
        try:
            first, first_seconds = timed(repo, 'index')
            unchanged, unchanged_seconds = timed(repo, 'index')
            target = root / f'module_{args.files // 2:05d}.py'
            target.write_text(target.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
            changed, changed_seconds = timed(repo, 'index')
            context, context_seconds = timed(
                repo, 'context', symbol_id=f'module_{args.files - 1:05d}.py::func_{args.files - 1}',
                intent='understand', budget=3000)
            task_context, task_context_seconds = timed(
                repo, 'task_context', task=f'change func_{args.files - 1}',
                breadth='narrow', intent='understand', budget=3000)
            report = {
                'files': args.files,
                'initial_seconds': first_seconds,
                'unchanged_seconds': unchanged_seconds,
                'one_file_changed_seconds': changed_seconds,
                'unchanged_reused_files': len(unchanged['incremental']['reused']),
                'changed_files_after_one_edit': changed['incremental']['changed'],
                'context_seconds': context_seconds,
                'context_bytes': context['used_bytes'],
                'task_context_seconds': task_context_seconds,
                'task_context_bytes': task_context['used_bytes'],
                'task_context_anchor': task_context['anchors'][0]['id'],
                'symbols': first['symbols'],
                'edges': first['edges'],
            }
        finally:
            repo.close()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
