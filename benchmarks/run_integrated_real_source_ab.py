"""Paired real-source Agent benchmark with AIRP genuinely installed and invoked."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ('airp', 'tests', 'examples', 'plugins', 'README.md', 'pyproject.toml')
ANSWER = re.compile(r'^\s*<answer>\s*(.*?)\s*</answer>\s*$', re.I | re.S)


def copy_source(destination):
    for name in INCLUDE:
        source = ROOT / name
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                '.airp', '__pycache__', '.pytest_cache', '*.pyc'))
        else:
            shutil.copy2(source, target)
    return {'copied_entries': list(INCLUDE)}


def prompt(task, group):
    common = f'''这是当前真实项目源码的只读代码问题。
问题：{task["question"]}
最终严格只输出 `<answer>答案</answer>`，不要解释。'''
    if group == 'airp':
        method = '''必须调用一次已安装的AIRP `airp` MCP工具，operation=context，task使用上面的问题；该调用会自动建立或刷新索引。
不得用shell、Get-Content、rg或其他文件读取命令读取项目源码，也不得执行第二次源码查询。'''
    else:
        method = '''AIRP不可用。使用普通shell搜索或文件读取检查项目源码。
最多执行3次源码检查命令，禁止重复相同命令。'''
    return common + '\n' + method


def semantic_match(task, observed):
    if observed is None:
        return False
    rule = task.get('acceptance', 'exact')
    if rule == 'number_sequence':
        return re.findall(r'\d+', observed) == re.findall(r'\d+', task['expected'])
    if rule == 'ast_type_sequence':
        normalize = lambda value: value.casefold().replace('ast.', '').replace(' ', '')
        return normalize(observed) == normalize(task['expected'])
    return observed == task['expected']


def run_agent(task, group, repo_root, model, effort, timeout):
    command = ['codex', 'exec', '--json', '--ephemeral', '--approve-for-me',
               '--skip-git-repo-check', '--model', model,
               '-c', f'model_reasoning_effort="{effort}"', '-C', str(repo_root)]
    if group == 'baseline':
        command.append('--ignore-user-config')
    command.append(prompt(task, group))
    started = time.monotonic()
    proc = subprocess.run(command, text=True, encoding='utf-8', errors='replace',
                          capture_output=True, timeout=timeout)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    completed = next((event for event in reversed(events)
                      if event.get('type') == 'turn.completed'), None)
    messages = [event['item']['text'] for event in events
                if event.get('type') == 'item.completed'
                and event.get('item', {}).get('type') == 'agent_message']
    if proc.returncode or completed is None or not messages:
        raise RuntimeError(json.dumps({'group': group, 'task': task['id'],
                                       'returncode': proc.returncode,
                                       'stdout_tail': proc.stdout[-2000:],
                                       'stderr_tail': proc.stderr[-2000:]}, ensure_ascii=False))
    usage = completed['usage']
    final = messages[-1]
    match = ANSWER.fullmatch(final)
    trace = []
    for event in events:
        if event.get('type') != 'item.completed':
            continue
        item = event.get('item', {})
        kind = item.get('type')
        if kind == 'mcp_tool_call':
            result_text = None
            content = (item.get('result') or {}).get('content') or []
            if item.get('status') == 'failed' and content and content[0].get('type') == 'text':
                result_text = content[0].get('text')
            trace.append({'type': kind, 'server': item.get('server'),
                          'tool': item.get('tool'), 'status': item.get('status'),
                          'error': (item.get('error') or {}).get('message') or result_text})
        elif kind == 'command_execution':
            trace.append({'type': kind, 'status': item.get('status')})
    return {'answer': final, 'observed': match.group(1).strip() if match else None,
            'input_tokens': usage['input_tokens'],
            'cached_input_tokens': usage.get('cached_input_tokens', 0),
            'output_tokens': usage['output_tokens'],
            'reasoning_output_tokens': usage.get('reasoning_output_tokens', 0),
            'latency_seconds': round(time.monotonic() - started, 4),
            'trace': trace,
            'mcp_calls': sum(item['type'] == 'mcp_tool_call' for item in trace),
            'command_calls': sum(item['type'] == 'command_execution' for item in trace)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', type=Path,
                        default=ROOT / 'benchmarks/real_source_tasks_v10.json')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/integrated-real-source-v10.json')
    parser.add_argument('--model', default='gpt-5.6-luna')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    tasks = json.loads(args.tasks.read_text(encoding='utf-8'))

    def execute_shard(shard_index, shard_tasks):
        workspaces = ROOT / 'benchmarks' / 'runs' / '.real-source-workspaces'
        workspaces.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f'airp-real-shard-{shard_index}-',
                                         dir=workspaces) as folder:
            repo_root = Path(folder)
            indexed = copy_source(repo_root)
            shard_rows = []
            for index, task in shard_tasks:
                conditions = ['baseline', 'airp']
                if index % 2:
                    conditions.reverse()
                pair = []
                for order, group in enumerate(conditions):
                    measured = run_agent(task, group, repo_root, args.model,
                                         args.effort, args.timeout)
                    pair.append({'task_id': task['id'], 'question': task['question'],
                                 'expected': task['expected'], 'group': group, 'order': order,
                                 'strict_success': measured['observed'] == task['expected'],
                                 'success': semantic_match(task, measured['observed']),
                                 'indexed': indexed, 'shard': shard_index, **measured})
                shard_rows.extend(pair)
                print(json.dumps({'task_id': task['id'],
                                  'successes': sum(row['success'] for row in pair),
                                  'tokens': {row['group']: row['input_tokens'] + row['output_tokens']
                                             for row in pair},
                                  'calls': {row['group']: row['mcp_calls'] + row['command_calls']
                                            for row in pair}}, ensure_ascii=False), flush=True)
            return shard_rows

    rows = []
    shards = [[] for _ in range(args.workers)]
    for index, task in enumerate(tasks):
        shards[index % args.workers].append((index, task))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(execute_shard, index, shard)
                   for index, shard in enumerate(shards) if shard]
        for future in as_completed(futures):
            rows.extend(future.result())
    rows.sort(key=lambda row: (row['task_id'], row['group']))
    totals = {}
    for group in ('baseline', 'airp'):
        selected = [row for row in rows if row['group'] == group]
        totals[group] = {key: sum(row[key] for row in selected) for key in
                         ('input_tokens', 'cached_input_tokens', 'output_tokens',
                          'reasoning_output_tokens', 'latency_seconds',
                          'mcp_calls', 'command_calls')}
        totals[group]['total_model_tokens'] = (totals[group]['input_tokens']
                                                + totals[group]['output_tokens'])
        totals[group]['runs'] = len(selected)
        totals[group]['successes'] = sum(row['success'] for row in selected)
    baseline, airp = totals['baseline'], totals['airp']
    report = {'experiment': 'real project source installed-agent paired benchmark',
              'authorization': 'user explicitly allowed sending project source on 2026-09-13',
              'source_scope': list(INCLUDE),
              'mode': f'{len(shards)} persistent shards; first AIRP task cold auto-index, later tasks reuse index',
              'model': args.model, 'effort': args.effort, 'tasks': len(tasks),
              'calls': len(rows), 'rows': rows, 'totals': totals,
              'total_model_token_reduction': 1 - airp['total_model_tokens'] / baseline['total_model_tokens'],
              'completion_gate': len(rows) == 2 * len(tasks),
              'mcp_execution_gate': all(
                  any(item['type'] == 'mcp_tool_call' and item['status'] == 'completed'
                      for item in row['trace'])
                  for row in rows if row['group'] == 'airp'),
              'limitations': ['One real repository only.',
                              'Twelve exact-answer read-only tasks.',
                              'Cold indexing is included inside the first AIRP context call per shard.',
                              'Baseline and AIRP use different tool sets by design.']}
    report['valid_comparison'] = report['completion_gate'] and report['mcp_execution_gate']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'completion_gate': report['completion_gate'],
                      'mcp_execution_gate': report['mcp_execution_gate'],
                      'totals': totals,
                      'total_model_token_reduction': report['total_model_token_reduction']},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
