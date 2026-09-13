"""Paired Agent benchmark for ordinary reads versus AIRP pre-model injection."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from airp.hook import build_prompt_context


ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ('airp', 'tests', 'examples', 'plugins', 'README.md', 'pyproject.toml')
ANSWER = re.compile(r'^\s*<answer>\s*(.*?)\s*</answer>\s*$', re.I | re.S)


def copy_source(destination: Path) -> None:
    for name in INCLUDE:
        source, target = ROOT / name, destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                '.airp', '__pycache__', '.pytest_cache', '*.pyc'))
        else:
            shutil.copy2(source, target)


def semantic_match(task: dict, observed: str | None) -> bool:
    if observed is None:
        return False
    rule = task.get('acceptance', 'exact')
    if rule == 'number_sequence':
        return re.findall(r'\d+', observed) == re.findall(r'\d+', task['expected'])
    if rule == 'numeric_sequence':
        numbers = lambda value: [float(item) for item in re.findall(r'-?\d+(?:\.\d+)?', value)]
        return numbers(observed) == numbers(task['expected'])
    if rule == 'labeled_sequence':
        values = lambda value: [item.rsplit('=', 1)[-1].strip() for item in value.split('|')]
        return values(observed) == values(task['expected'])
    if rule == 'path_categories':
        folded = observed.casefold()
        return (('absolute' in folded or '绝对路径' in observed)
                and ('internal' in folded or '内部状态' in observed)
                and ('symlink' in folded or '符号链接' in observed))
    if rule == 'ast_type_sequence':
        normalize = lambda value: value.casefold().replace('ast.', '').replace(' ', '')
        return normalize(observed) == normalize(task['expected'])
    return observed == task['expected']


def make_prompt(task: dict, group: str, context: str | None) -> str:
    common = (f'这是当前真实项目源码的只读代码问题。\n问题：{task["question"]}\n'
              '最终严格只输出 `<answer>答案</answer>`，不要解释。')
    if group == 'baseline':
        return common + '\n使用普通shell搜索或文件读取检查源码，最多执行3次源码检查命令。'
    return (common + '\n以下上下文由AIRP在首次模型请求前本地生成。若status=sufficient，'
            '不得调用shell或其他源码工具重复读取；若partial，仅查询缺失事实。\n' +
            (context or '<airp-context>status: partial</airp-context>'))


def run_agent(task: dict, group: str, repo: Path, model: str, effort: str,
              timeout: int) -> dict:
    started = time.monotonic()
    context = build_prompt_context(repo, task['question']) if group == 'premodel' else None
    command = ['codex', 'exec', '--json', '--ephemeral', '--approve-for-me',
               '--ignore-user-config', '--skip-git-repo-check', '--model', model,
               '-c', f'model_reasoning_effort="{effort}"', '-C', str(repo),
               make_prompt(task, group, context)]
    proc = subprocess.run(command, text=True, encoding='utf-8', errors='replace',
                          capture_output=True, timeout=timeout)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    completed = next((event for event in reversed(events)
                      if event.get('type') == 'turn.completed'), None)
    messages = [event['item']['text'] for event in events
                if event.get('type') == 'item.completed'
                and event.get('item', {}).get('type') == 'agent_message']
    if proc.returncode or completed is None or not messages:
        raise RuntimeError(json.dumps({'task': task['id'], 'group': group,
                                       'returncode': proc.returncode,
                                       'stdout': proc.stdout[-2000:],
                                       'stderr': proc.stderr[-2000:]}, ensure_ascii=False))
    final = messages[-1]
    match = ANSWER.fullmatch(final)
    usage = completed['usage']
    commands = sum(event.get('item', {}).get('type') == 'command_execution'
                   for event in events if event.get('type') == 'item.completed')
    return {'answer': final, 'observed': match.group(1).strip() if match else None,
            'input_tokens': usage['input_tokens'], 'output_tokens': usage['output_tokens'],
            'cached_input_tokens': usage.get('cached_input_tokens', 0),
            'reasoning_output_tokens': usage.get('reasoning_output_tokens', 0),
            'command_calls': commands, 'context_chars': len(context or ''),
            'context_status': ('none' if not context else
                               'sufficient' if 'status: sufficient' in context else 'partial'),
            'latency_seconds': round(time.monotonic() - started, 4)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', type=Path, default=ROOT / 'benchmarks/real_source_tasks_v17.json')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/premodel-hook-v17.json')
    parser.add_argument('--model', default='gpt-5.6-luna')
    parser.add_argument('--effort', default='low')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--resume', action='store_true',
                        help='Reuse completed task-pair checkpoints for this output path')
    args = parser.parse_args()
    tasks = json.loads(args.tasks.read_text(encoding='utf-8'))[:args.limit]
    parts_dir = args.output.parent / (args.output.stem + '.parts')
    parts_dir.mkdir(parents=True, exist_ok=True)

    completed_rows = []
    pending_tasks = []
    for index, task in enumerate(tasks):
        part = parts_dir / f'{task["id"]}.json'
        if args.resume and part.exists():
            saved = json.loads(part.read_text(encoding='utf-8'))
            if len(saved) == 2 and {row['group'] for row in saved} == {'baseline', 'premodel'}:
                completed_rows.extend(saved)
                continue
        pending_tasks.append((index, task))

    def shard_run(shard_id: int, selected: list[tuple[int, dict]]) -> list[dict]:
        base = ROOT / 'benchmarks/runs/.premodel-workspaces'
        base.mkdir(parents=True, exist_ok=True)
        rows = []
        with tempfile.TemporaryDirectory(prefix=f'airp-hook-{shard_id}-', dir=base) as folder:
            repo = Path(folder)
            copy_source(repo)
            for index, task in selected:
                groups = ['baseline', 'premodel']
                if index % 2:
                    groups.reverse()
                for order, group in enumerate(groups):
                    measured = run_agent(task, group, repo, args.model, args.effort, args.timeout)
                    row = {'task_id': task['id'], 'category': task.get('category', 'unspecified'),
                           'question': task['question'],
                           'expected': task['expected'], 'group': group, 'order': order,
                           **measured}
                    row['success'] = semantic_match(task, row['observed'])
                    row['strict_success'] = row['observed'] == task['expected']
                    rows.append(row)
                pair = rows[-2:]
                part = parts_dir / f'{task["id"]}.json'
                temporary = part.with_suffix('.tmp')
                temporary.write_text(json.dumps(pair, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8')
                temporary.replace(part)
                print(json.dumps({'task': task['id'],
                                  'tokens': {r['group']: r['input_tokens'] + r['output_tokens']
                                             for r in pair},
                                  'success': {r['group']: r['success'] for r in pair}},
                                 ensure_ascii=False), flush=True)
        return rows

    shards = [[] for _ in range(args.workers)]
    for index, task in pending_tasks:
        shards[index % args.workers].append((index, task))
    rows = list(completed_rows)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(shard_run, index, shard)
                   for index, shard in enumerate(shards) if shard]
        for future in as_completed(futures):
            rows.extend(future.result())
    rows.sort(key=lambda row: (row['task_id'], row['group']))
    totals = {}
    for group in ('baseline', 'premodel'):
        selected = [row for row in rows if row['group'] == group]
        totals[group] = {key: sum(row[key] for row in selected) for key in
                         ('input_tokens', 'output_tokens', 'cached_input_tokens',
                          'reasoning_output_tokens', 'command_calls', 'context_chars',
                          'latency_seconds')}
        totals[group]['total_model_tokens'] = (totals[group]['input_tokens'] +
                                                totals[group]['output_tokens'])
        totals[group]['successes'] = sum(row['success'] for row in selected)
        totals[group]['runs'] = len(selected)
    reduction = 1 - totals['premodel']['total_model_tokens'] / totals['baseline']['total_model_tokens']
    category_totals = {}
    for category in sorted({row['category'] for row in rows}):
        category_totals[category] = {}
        for group in ('baseline', 'premodel'):
            selected = [row for row in rows
                        if row['category'] == category and row['group'] == group]
            tokens = sum(row['input_tokens'] + row['output_tokens'] for row in selected)
            category_totals[category][group] = {
                'runs': len(selected), 'successes': sum(row['success'] for row in selected),
                'total_model_tokens': tokens,
                'command_calls': sum(row['command_calls'] for row in selected),
                'latency_seconds': sum(row['latency_seconds'] for row in selected),
            }
        before = category_totals[category]['baseline']['total_model_tokens']
        after = category_totals[category]['premodel']['total_model_tokens']
        category_totals[category]['token_reduction'] = 1 - after / before
    report = {
        'experiment': 'paired real-source pre-model context injection benchmark',
        'authorization': 'user explicitly authorized future model experiments and expanded selected-source transmission on 2026-09-13',
        'model': args.model, 'effort': args.effort, 'tasks': len(tasks), 'rows': rows,
        'totals': totals, 'category_totals': category_totals,
        'total_model_token_reduction': reduction,
        'completion_gate': len(rows) == 2 * len(tasks),
        'premodel_context_gate': all(row['context_status'] != 'none'
                                     for row in rows if row['group'] == 'premodel'),
        'limitations': [
            'One real repository and exact-answer read-only tasks.',
            'The benchmark injects the exact hook output into the first prompt under an isolated config; role framing differs from host developer-context injection.',
            'Indexing latency is included; local index computation consumes no model tokens.',
        ],
    }
    report['valid_comparison'] = report['completion_gate'] and report['premodel_context_gate']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'valid': report['valid_comparison'],
                      'totals': totals, 'reduction': reduction}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
