"""Run a complete operation loop on a disposable copy of the demo repository."""
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from airp.core import Repository


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='airp-demo-') as folder:
        root = Path(folder)
        shutil.copytree(Path(__file__).resolve().parents[1] / 'examples/shop', root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.airp', '__pycache__'))
        repo = Repository(root)
        results = {}
        try:
            def call(tool, **args):
                result = repo.execute(tool, **args)
                results[tool] = result
                return result
            call('index')
            call('find', query='discount')
            symbol = call('get', symbol_id='pricing.py::discount')
            call('callers', symbol_id=symbol['id'])
            call('context', symbol_id=symbol['id'], budget=1500)
            call('begin')
            replacement = symbol['source'].replace('Apply a fractional discount', 'Calculate a fractional discount')
            call('update', symbol_id=symbol['id'], source=replacement, expected_hash=symbol['hash'], dry_run=False)
            assert call('verify')['ok']
            assert call('test')['ok']
            call('diff')
            call('commit')
            call('begin')
            current = call('get', symbol_id=symbol['id'])
            call('update', symbol_id=symbol['id'], source='def discount(total, rate):\n    return 0\n',
                 expected_hash=current['hash'], dry_run=False)
            failed = call('test')
            assert not failed['ok']
            call('rollback')
            assert call('test')['ok']
            print(json.dumps({'demo': 'passed', 'index': results['index'],
                              'context_bytes': results['context']['used_bytes'],
                              'commit': results['commit']['status'],
                              'intentional_bad_edit_test_failed': not failed['ok'],
                              'rollback': results['rollback']['status'],
                              'final_tests_passed': True,
                              'metrics': call('metrics')}, ensure_ascii=False, indent=2))
        finally:
            repo.close()


if __name__ == '__main__':
    main()
