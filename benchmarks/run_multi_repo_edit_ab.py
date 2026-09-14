"""Paired multi-repository benchmark for AIRP read and edit tasks."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from airp.hook import build_prompt_context


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'benchmarks' / 'corpus'
ANSWER = re.compile(r'^\s*<answer>\s*(.*?)\s*</answer>\s*$', re.I | re.S)
PROJECT_PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'


def copy_repo(name: str, destination: Path) -> None:
    shutil.copytree(CORPUS / name, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git', '.airp', 'node_modules', 'target',
                                                  '__pycache__', '.pytest_cache', '*.pyc',
                                                  'sds-test', 'sds-test.exe', 'pnpm-lock.yaml'))
    if name == 'p-limit':
        dependency = CORPUS / name / 'node_modules' / 'yocto-queue'
        shutil.copytree(dependency.resolve(), destination / 'node_modules' / 'yocto-queue')
    (destination / '.airp_edit.py').write_text(
        """from pathlib import Path
import sys
path = Path(sys.argv[1])
start, end = int(sys.argv[2]), int(sys.argv[3])
raw = path.read_bytes()
newline = '\\r\\n' if b'\\r\\n' in raw else '\\n'
text = raw.decode('utf-8').replace('\\r\\n', '\\n').replace('\\r', '\\n')
lines = text.splitlines(keepends=True)
replacement = sys.stdin.read().replace('\\r\\n', '\\n').replace('\\r', '\\n')
if not replacement:
    raise SystemExit('replacement input required')
if replacement and not replacement.endswith('\\n'):
    replacement += '\\n'
updated = ''.join(lines[:start - 1]) + replacement + ''.join(lines[end:])
path.write_bytes(updated.replace('\\n', newline).encode('utf-8'))
""", encoding='utf-8')
    (destination / '.airp_edit.ps1').write_text(
        """param([Parameter(Mandatory=$true)][string]$Path,
      [Parameter(Mandatory=$true)][int]$Start,
      [Parameter(Mandatory=$true)][int]$End,
      [Parameter(ValueFromPipeline=$true)][AllowEmptyString()][string]$ReplacementLine)
begin { $replacementParts = [System.Collections.Generic.List[string]]::new() }
process { $replacementParts.Add($ReplacementLine) }
end {
if ($replacementParts.Count -eq 0) { throw "replacement input required" }
$resolved = (Resolve-Path -LiteralPath $Path).Path
$raw = [System.IO.File]::ReadAllText($resolved)
$newline = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
$normalized = $raw.Replace("`r`n", "`n").Replace("`r", "`n")
$lines = $normalized.Split("`n")
$replacement = ($replacementParts -join "`n").Replace("`r`n", "`n").Replace("`r", "`n")
if ($replacement -and -not $replacement.EndsWith("`n")) { $replacement += "`n" }
$prefix = if ($Start -gt 1) { ($lines[0..($Start - 2)] -join "`n") + "`n" } else { "" }
$suffix = if ($End -lt $lines.Count) { $lines[$End..($lines.Count - 1)] -join "`n" } else { "" }
$updated = $prefix + $replacement + $suffix
[System.IO.File]::WriteAllText($resolved, $updated.Replace("`n", $newline), [System.Text.UTF8Encoding]::new($false))
}
""", encoding='utf-8')


def make_prompt(task: dict, group: str, context: str | None) -> str:
    if task['type'] == 'read':
        body = (f"Answer this question about the checked-out repository:\n{task['question']}\n"
                'Your entire final response must be one XML element: '
                '`<answer>ACTUAL ANSWER HERE</answer>`. Replace ACTUAL ANSWER HERE with your '
                'answer; do not output those placeholder words and do not add text outside the element.')
    else:
        body = (f"Implement this change in the checked-out repository:\n{task['question']}\n"
                'Inspect the relevant code, make the edit, and run a focused check if practical. '
                'For reliable line-based edits in this shell, pipe the complete replacement source '
                'with real newlines to `& ./.airp_edit.ps1 -Path PATH -Start START_LINE -End END_LINE`; do not invoke '
                '`apply_patch` as a shell command. '
                'Your entire final response must be exactly `<answer>done</answer>`.')
    if group == 'baseline':
        return body + '\nUse ordinary repository tools to gather the source evidence you need.'
    return (body + '\nAIRP generated the following local evidence before this first model request. '
            'Use it first; inspect only missing details or verify runtime behavior.\n' +
            (context or '<airp-context>status: partial</airp-context>'))


def parse_events(stdout: str) -> tuple[dict, str, int, list[dict]]:
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    completed = next((event for event in reversed(events)
                      if event.get('type') == 'turn.completed'), None)
    messages = [event['item']['text'] for event in events
                if event.get('type') == 'item.completed'
                and event.get('item', {}).get('type') == 'agent_message']
    if completed is None or not messages:
        raise RuntimeError('agent did not produce a completed turn')
    commands = sum(event.get('item', {}).get('type') == 'command_execution'
                   for event in events if event.get('type') == 'item.completed')
    command_trace = []
    for event in events:
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and item.get('type') == 'command_execution':
            command_trace.append({key: item.get(key) for key in
                                  ('command', 'exit_code', 'status') if key in item})
    return completed['usage'], messages[-1], commands, command_trace


def run_command(command: list[str], cwd: Path, timeout: int = 180,
                env: dict | None = None) -> tuple[bool, str]:
    proc = subprocess.run(command, cwd=cwd, text=True, encoding='utf-8', errors='replace',
                          capture_output=True, timeout=timeout, env=env)
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-5000:]


def validate_edit(task: dict, repo: Path) -> tuple[bool, str]:
    task_id = task['id']
    if task_id == 'django-slugify-lowercase-option':
        source = (repo / 'django/utils/text.py').read_text(encoding='utf-8')
        match = re.search(
            r'((?:@keep_lazy_text\n)+def slugify\(.*?)(?=\n\ndef )', source, re.S)
        if not match or match.group(1).count('@keep_lazy_text') != 1:
            return False, 'slugify definition missing or keep_lazy_text duplicated'
        namespace = {
            'keep_lazy_text': lambda function: function,
            're': re,
            'unicodedata': __import__('unicodedata'),
        }
        try:
            exec(match.group(1), namespace)
            slugify = namespace['slugify']
            assert slugify('Hello World') == 'hello-world'
            assert slugify('Hello World', lowercase=False) == 'Hello-World'
            assert slugify('Ärger Test', allow_unicode=True, lowercase=False) == 'Ärger-Test'
        except Exception as error:
            return False, repr(error)
        return True, match.group(1)[-1500:]
    if task_id == 'nest-isnil-strict-comparison':
        source = (repo / 'packages/common/utils/shared.utils.ts').read_text(encoding='utf-8')
        match = re.search(r'export const isNil\s*=.*?;\s*$', source, re.M | re.S)
        if not match:
            return False, 'isNil definition missing'
        body = match.group(0).split('export const isEmpty', 1)[0]
        success = ('val === undefined' in body and 'val === null' in body and
                   'isUndefined(val)' not in body and
                   'val is null | undefined' in body)
        return success, body[-1000:]
    if task_id == 'ripgrep-should-preprocess-is-none':
        source = (repo / 'crates/core/search.rs').read_text(encoding='utf-8')
        match = re.search(r'fn should_preprocess\(.*?\n    \}', source, re.S)
        if not match:
            return False, 'should_preprocess definition missing'
        body = match.group(0)
        success = ('.is_none()' in body and
                   '!self.config.preprocessor.is_some()' not in body)
        return success, body[-1200:]
    if task_id == 'redis-stringmatchlen-negative-length':
        source = (repo / 'src/util.c').read_text(encoding='utf-8')
        match = re.search(
            r'int stringmatchlen\(const char \*pattern, int patternLen,\s*'
            r'const char \*string, int stringLen, int nocase\)\s*\{.*?\n\}',
            source, re.S)
        if not match:
            return False, 'stringmatchlen definition missing'
        function = match.group(0)
        if not re.search(r'\nint stringmatch\(const char \*pattern, const char \*string, int nocase\)', source):
            return False, 'adjacent stringmatch function was removed'
        harness = repo / '.airp-hidden-test.c'
        executable = repo / '.airp-hidden-test.exe'
        harness.write_text(
            '#include <assert.h>\n'
            'static int calls = 0;\n'
            'static int stringmatchlen_impl(const char *p,int plen,const char *s,int slen,'
            'int nocase,int *skip,int nesting){(void)p;(void)plen;(void)s;(void)slen;'
            '(void)nocase;(void)skip;(void)nesting;calls++;return 1;}\n' +
            function +
            '\nint main(void){assert(stringmatchlen("a",-1,"a",1,0)==0);'
            'assert(stringmatchlen("a",1,"a",-1,0)==0);assert(calls==0);'
            'assert(stringmatchlen("a",1,"a",1,0)==1);assert(calls==1);return 0;}\n',
            encoding='utf-8')
        try:
            ok, output = run_command(
                ['gcc', '-o', str(executable), str(harness), '-Wall', '-std=c99', '-O2'],
                repo)
            if not ok:
                return ok, output
            return run_command([str(executable)], repo)
        finally:
            harness.unlink(missing_ok=True)
            executable.unlink(missing_ok=True)
    if task_id == 'itsdangerous-strict-nonascii-base64':
        code = ('from itsdangerous.encoding import base64_decode\n'
                'from itsdangerous.exc import BadData\n'
                'assert base64_decode("YQ") == b"a"\n'
                'assert base64_decode(b"YQ") == b"a"\n'
                'try: base64_decode("YQé")\n'
                'except BadData as e: assert str(e) == "Invalid base64-encoded data"\n'
                'else: raise AssertionError("non-ASCII input accepted")\n')
        env = os.environ.copy()
        env['PYTHONPATH'] = str(repo / 'src')
        return run_command([str(PROJECT_PYTHON), '-c', code], repo, env=env)
    if task_id == 'p-limit-custom-clear-reason':
        script = repo / '.airp-hidden-test.mjs'
        script.write_text(
            "import assert from 'node:assert/strict';\n"
            "import pLimit from './index.js';\n"
            "const limit=pLimit({concurrency:1,rejectOnClear:true});\n"
            "const running=limit(()=>new Promise(r=>setTimeout(r,30)));\n"
            "const marker=new Error('custom'); const pending=limit(()=>42);\n"
            "await Promise.resolve(); limit.clearQueue(marker);\n"
            "await assert.rejects(pending,e=>e===marker); await running;\n"
            "const fallback=pLimit({concurrency:1,rejectOnClear:true});\n"
            "const active=fallback(()=>new Promise(r=>setTimeout(r,30)));\n"
            "const queued=fallback(()=>1); await Promise.resolve(); fallback.clearQueue();\n"
            "await assert.rejects(queued,{name:'AbortError'}); await active;\n",
            encoding='utf-8')
        try:
            return run_command(['node', str(script)], repo)
        finally:
            script.unlink(missing_ok=True)
    if task_id == 'fd-invalid-uppercase-conservative':
        path = repo / 'src' / 'regex_helper.rs'
        original = path.read_text(encoding='utf-8')
        path.write_text(original + '\n#[test]\nfn airp_hidden_invalid_uppercase() {\n'
                        '    assert!(pattern_has_uppercase_char("[A"));\n'
                        '    assert!(!pattern_has_uppercase_char("[a"));\n}\n', encoding='utf-8')
        env = os.environ.copy()
        env['CARGO_TARGET_DIR'] = str(CORPUS / 'fd' / 'target')
        try:
            return run_command(['cargo', 'test', '--offline', 'airp_hidden_invalid_uppercase'],
                               repo, timeout=300, env=env)
        finally:
            path.write_text(original, encoding='utf-8')
    if task_id == 'sds-binary-prefix':
        harness = repo / '.airp-hidden-test.c'
        executable = repo / '.airp-hidden-test.exe'
        harness.write_text(
            '#include "sds.h"\n#include <assert.h>\nint main(void){\n'
            'const char data[]={\'a\',\'b\',0,\'c\',\'d\'}; const char p[]={\'a\',\'b\',0};\n'
            'sds s=sdsnewlen(data,5); assert(sdsstartswith(s,p,3));\n'
            'assert(sdsstartswith(s,p,0)); assert(!sdsstartswith(s,p,6));\n'
            'const char bad[]={\'a\',\'c\'}; assert(!sdsstartswith(s,bad,2)); sdsfree(s); return 0;}\n',
            encoding='utf-8')
        try:
            ok, output = run_command(['gcc', '-o', str(executable), 'sds.c', str(harness),
                                      '-Wall', '-std=c99', '-pedantic', '-O2'], repo)
            if not ok:
                return ok, output
            return run_command([str(executable)], repo)
        finally:
            harness.unlink(missing_ok=True)
            executable.unlink(missing_ok=True)
    return False, 'unknown edit validator'


def validate_read(task: dict, final: str) -> tuple[bool, str | None]:
    match = ANSWER.fullmatch(final)
    observed = html.unescape(match.group(1).strip()) if match else None
    if observed is None:
        return False, None
    rubric = task.get('rubric')
    if rubric:
        if task.get('rubric_scope') == 'all':
            folded = re.sub(r'\s+', ' ', observed.casefold()).strip()
            return all(re.search(pattern, folded, re.I) for pattern in rubric), observed
        raw_clauses = [observed] if len(rubric) == 1 else observed.split('|')
        clauses = [re.sub(r'\s+', ' ', clause.casefold()).strip()
                   for clause in raw_clauses]
        success = (len(clauses) == len(rubric) and
                   all(re.search(pattern, clause, re.I)
                       for pattern, clause in zip(rubric, clauses)))
        return success, observed
    normalize = lambda value: re.sub(r'\s+', ' ', value.casefold()).strip()
    return normalize(observed) == normalize(task['expected']), observed


def source_diff(name: str, repo: Path) -> str:
    extensions = {'.py', '.js', '.ts', '.rs', '.c', '.h'}
    original = CORPUS / name
    changed = []
    for path in repo.rglob('*'):
        if (not path.is_file() or path.suffix not in extensions or
                path.name.startswith('.airp_') or
                any(part in {'.airp', 'node_modules', 'target'} for part in path.parts)):
            continue
        relative = path.relative_to(repo)
        before = original / relative
        old = before.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True) if before.exists() else []
        new = path.read_text(encoding='utf-8', errors='replace').splitlines(keepends=True)
        if old != new:
            changed.extend(difflib.unified_diff(old, new, fromfile=str(relative),
                                                tofile=str(relative), n=3))
    return ''.join(changed)


def run_one(task: dict, group: str, model: str, effort: str, timeout: int,
            workspace_root: Path) -> dict:
    started = time.monotonic()
    repo = workspace_root / f"{task['id']}-{group}"
    copy_repo(task['repo'], repo)
    context_started = time.monotonic()
    context = build_prompt_context(repo, task['question']) if group == 'premodel' else None
    context_seconds = time.monotonic() - context_started
    command = ['codex', 'exec', '--json', '--ephemeral', '--approve-for-me',
               '--ignore-user-config', '--skip-git-repo-check', '--model', model,
               '-c', f'model_reasoning_effort="{effort}"', '-C', str(repo),
               make_prompt(task, group, context)]
    proc = subprocess.run(command, text=True, encoding='utf-8', errors='replace',
                          capture_output=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(json.dumps({'task': task['id'], 'group': group,
                                       'returncode': proc.returncode,
                                       'stdout': proc.stdout[-2000:],
                                       'stderr': proc.stderr[-2000:]}, ensure_ascii=False))
    usage, final, commands, command_trace = parse_events(proc.stdout)
    if task['type'] == 'edit':
        success, validation = validate_edit(task, repo)
        observed = 'hidden validator passed' if success else 'hidden validator failed'
    else:
        success, observed = validate_read(task, final)
        validation = ''
    diff = source_diff(task['repo'], repo)
    return {
        'answer': final, 'observed': observed, 'success': success,
        'validation_output': validation[-2000:], 'diff_tail': diff[-4000:] if task['type'] == 'edit' else '',
        'input_tokens': usage['input_tokens'], 'output_tokens': usage['output_tokens'],
        'cached_input_tokens': usage.get('cached_input_tokens', 0),
        'reasoning_output_tokens': usage.get('reasoning_output_tokens', 0),
        'command_calls': commands, 'context_chars': len(context or ''),
        'command_trace': command_trace,
        'context_status': ('none' if not context else
                           'sufficient' if 'status: sufficient' in context else 'partial'),
        'context_seconds': round(context_seconds, 4),
        'latency_seconds': round(time.monotonic() - started, 4),
    }


def aggregate(rows: list[dict], key: str, value: str) -> dict:
    output = {}
    for item in sorted({row[key] for row in rows}):
        output[item] = {}
        for group in ('baseline', 'premodel'):
            selected = [row for row in rows if row[key] == item and row['group'] == group]
            tokens = sum(row['input_tokens'] + row['output_tokens'] for row in selected)
            output[item][group] = {'runs': len(selected),
                                   'successes': sum(row['success'] for row in selected),
                                   'tokens': tokens,
                                   'latency_seconds': round(sum(row['latency_seconds'] for row in selected), 4),
                                   'command_calls': sum(row['command_calls'] for row in selected)}
        before, after = output[item]['baseline']['tokens'], output[item]['premodel']['tokens']
        output[item]['token_reduction'] = None if not before else 1 - after / before
    return output


def main() -> None:
    global CORPUS
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', type=Path, default=ROOT / 'benchmarks/multi_repo_tasks_v19.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'benchmarks/runs/multi-repo-edit-v19.json')
    parser.add_argument('--model', default='gpt-5.6-luna')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=360)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--reuse-baseline-from', type=Path,
                        help='Reuse successful baseline rows from a prior compatible report.')
    parser.add_argument('--corpus', type=Path, default=CORPUS)
    args = parser.parse_args()
    CORPUS = args.corpus.resolve()
    tasks = json.loads(args.tasks.read_text(encoding='utf-8'))[:args.limit]
    reused_baselines = {}
    if args.reuse_baseline_from:
        prior = json.loads(args.reuse_baseline_from.read_text(encoding='utf-8'))
        reused_baselines = {row['task_id']: row for row in prior.get('rows', [])
                            if row.get('group') == 'baseline' and row.get('success')}
    parts = args.output.parent / (args.output.stem + '.parts')
    parts.mkdir(parents=True, exist_ok=True)
    pending, rows = [], []
    for index, task in enumerate(tasks):
        part = parts / f"{task['id']}.json"
        if args.resume and part.exists():
            saved = json.loads(part.read_text(encoding='utf-8'))
            if len(saved) == 2 and {row['group'] for row in saved} == {'baseline', 'premodel'}:
                rows.extend(saved)
                continue
        pending.append((index, task))

    workspaces = ROOT / 'benchmarks' / 'runs' / '.multi-repo-workspaces'
    workspaces.mkdir(parents=True, exist_ok=True)

    def run_pair(index: int, task: dict) -> list[dict]:
        pair = []
        with tempfile.TemporaryDirectory(prefix='airp-multi-', dir=workspaces,
                                             ignore_cleanup_errors=True) as folder:
            reused = reused_baselines.get(task['id'])
            if reused:
                pair.append(reused)
                groups = ['premodel']
            else:
                groups = ['baseline', 'premodel'] if index % 2 == 0 else ['premodel', 'baseline']
            for order, group in enumerate(groups):
                measured = run_one(task, group, args.model, args.effort, args.timeout, Path(folder))
                row = {key: task[key] for key in ('id', 'repo', 'language', 'source_loc',
                                                  'scale', 'type', 'category')}
                row.update(task_id=task['id'], group=group,
                           order=(1 if reused else order),
                           question=task['question'], expected=task.get('expected'), **measured)
                pair.append(row)
            part = parts / f"{task['id']}.json"
            temporary = part.with_suffix('.tmp')
            temporary.write_text(json.dumps(pair, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(part)
            print(json.dumps({'task': task['id'],
                              'tokens': {r['group']: r['input_tokens'] + r['output_tokens'] for r in pair},
                              'success': {r['group']: r['success'] for r in pair}}, ensure_ascii=False), flush=True)
        return pair

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_pair, index, task) for index, task in pending]
        for future in as_completed(futures):
            rows.extend(future.result())
    tasks_by_id = {task['id']: task for task in tasks}
    for row in rows:
        if row['type'] == 'read':
            row['success'], row['observed'] = validate_read(tasks_by_id[row['task_id']],
                                                              row['answer'])
    rows.sort(key=lambda row: (row['task_id'], row['group']))
    totals = {}
    for group in ('baseline', 'premodel'):
        selected = [row for row in rows if row['group'] == group]
        tokens = sum(row['input_tokens'] + row['output_tokens'] for row in selected)
        totals[group] = {'runs': len(selected), 'successes': sum(row['success'] for row in selected),
                         'total_model_tokens': tokens,
                         'command_calls': sum(row['command_calls'] for row in selected),
                         'latency_seconds': round(sum(row['latency_seconds'] for row in selected), 4)}
    report = {
        'experiment': 'paired multi-repository read-and-edit benchmark',
        'authorization': 'user authorized expanded model experiments and selected source transmission',
        'model': args.model, 'effort': args.effort, 'task_count': len(tasks),
        'repositories': sorted({task['repo'] for task in tasks}), 'rows': rows, 'totals': totals,
        'by_repository': aggregate(rows, 'repo', 'repository'),
        'by_language': aggregate(rows, 'language', 'language'),
        'by_scale': aggregate(rows, 'scale', 'scale'),
        'by_task_type': aggregate(rows, 'type', 'type'),
        'total_model_token_reduction': 1 - totals['premodel']['total_model_tokens'] / totals['baseline']['total_model_tokens'],
        'completion_gate': len(rows) == 2 * len(tasks),
        'premodel_context_gate': all(row['context_status'] != 'none' for row in rows if row['group'] == 'premodel'),
        'limitations': ['Four fixed public repository commits and twelve tasks are broader than the prior single-repository study but are not a population estimate.',
                        'Read answers use exact normalized rubrics; edit tasks use executable hidden validators.',
                        'The first-request context is injected into the benchmark prompt; host hook role framing can differ.']
    }
    report['valid_comparison'] = report['completion_gate'] and report['premodel_context_gate']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'valid': report['valid_comparison'],
                      'totals': totals, 'reduction': report['total_model_token_reduction']},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
