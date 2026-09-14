"""Every command emits machine-readable JSON; errors have a nonzero exit code."""
import argparse
import json
from pathlib import Path
import sys
import sqlite3

from .core import Repository


def parser():
    p = argparse.ArgumentParser(description='AIRP local multi-language repository tools')
    p.add_argument('--repo', default='.', help='Repository root (default: current directory)')
    sub = p.add_subparsers(dest='tool', required=True)
    for name in ('index', 'status', 'metrics', 'hook-report', 'begin', 'diff', 'verify', 'commit', 'rollback', 'serve'):
        sub.add_parser(name)
    warm = sub.add_parser('warm')
    warm.add_argument('languages', nargs='+')
    find = sub.add_parser('find')
    find.add_argument('query', nargs='?', default='')
    find.add_argument('--kind')
    find.add_argument('--limit', type=int, default=30)
    pack = sub.add_parser('pack')
    pack.add_argument('task')
    pack.add_argument('--budget', type=int, default=3000)
    pack.add_argument('--budget-unit', choices=['bytes', 'tokens'], default='bytes')
    pack.add_argument('--tokenizer', default='o200k_base')
    pack.add_argument('--intent', choices=['minimal', 'understand', 'edit', 'impact', 'test'],
                      default='understand')
    pack.add_argument('--breadth', choices=['narrow', 'balanced', 'broad'], default='balanced')
    pack.add_argument('--response-mode', choices=['full', 'compact', 'auto'], default='auto')
    for name in ('get', 'refs', 'callers', 'callees', 'dependencies', 'affected', 'context', 'update'):
        command = sub.add_parser(name)
        command.add_argument('symbol_id')
        if name == 'context':
            command.add_argument('--budget', type=int, default=3000)
            command.add_argument('--budget-unit', choices=['bytes', 'tokens'], default='bytes')
            command.add_argument('--tokenizer', default='o200k_base')
            command.add_argument('--intent', choices=['minimal', 'understand', 'edit', 'impact', 'test'],
                                 default='understand')
        if name == 'update':
            command.add_argument('--source-file', required=True, help='UTF-8 file containing the complete new function')
            command.add_argument('--expected-hash', required=True, help='hash returned by get')
            command.add_argument('--apply', action='store_true', help='Write into an active transaction; default is preview')
    test = sub.add_parser('test')
    test.add_argument('--runner', choices=['unittest', 'pytest'], default='unittest')
    test.add_argument('--symbol-id')
    test.add_argument('--timeout', type=int, default=60)
    test.add_argument('--output-mode', choices=['compact', 'full'], default='compact')
    return p


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    args = vars(parser().parse_args())
    root, tool = args.pop('repo'), args.pop('tool')
    repo = None
    try:
        if tool == 'hook-report':
            from .diagnostics import hook_report
            print(json.dumps(hook_report(root), ensure_ascii=False, indent=2))
            return
        if tool == 'serve':
            from .mcp_server import serve
            serve(root)
            return
        if tool == 'update':
            args['source'] = Path(args.pop('source_file')).read_text(encoding='utf-8')
            args['dry_run'] = not args.pop('apply')
        if tool == 'pack':
            tool = 'task_context'
        repo = Repository(root)
        result = repo.execute(tool, **args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get('ok') is False:
            raise SystemExit(1)
        if tool == 'index' and result['diagnostics']:
            raise SystemExit(1)
    except (ValueError, OSError, SyntaxError, TypeError, ImportError, sqlite3.Error) as error:
        print(json.dumps({'error': str(error), 'type': type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    finally:
        if repo:
            repo.close()
