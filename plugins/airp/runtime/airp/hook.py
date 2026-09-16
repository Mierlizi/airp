"""Pre-model AIRP context injection for Codex UserPromptSubmit hooks."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import sys
import textwrap
import time

from .core import (
    Repository,
    identifier_key,
    search_terms,
    symbol_summary,
    task_identifiers,
    task_profile,
)
from .graph import analyze, sources
from .protocol import ContextRequest, render_hook_output


CODE_SIGNALS = (
    'code', 'source', 'repository', 'repo', 'function', 'method', 'class', 'symbol',
    'caller', 'callee', 'dependency', 'impact', 'test', 'bug', 'fix', 'refactor',
    'implement', 'modify', 'update', 'extend', 'module', 'package', 'api',
    '代码', '源码', '仓库', '函数', '方法',
    '类', '符号', '调用', '依赖', '影响', '测试', '修复', '修改', '更新', '扩展', '重构', '实现', '模块',
    '计算', '求值', '返回值',
)
SOURCE_REFERENCE = re.compile(
    r'(?i)(?:^|[\\/\s`])[^\s`]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|c|cc|cpp|h|hpp|cs|rb|php)(?=$|[\s`:：，。?,])')
IDENTIFIER_REFERENCE = re.compile(
    r'(?<![\w])(?:[_A-Za-z][_A-Za-z0-9]*\.)+[_A-Za-z][_A-Za-z0-9]*(?![\w])')


def is_code_task(prompt: str) -> bool:
    """Broad local gate: exclude chat, not small or exact code questions."""
    folded = prompt.casefold()
    return (any(signal in folded for signal in CODE_SIGNALS)
            or bool(SOURCE_REFERENCE.search(prompt))
            or bool(IDENTIFIER_REFERENCE.search(prompt))
            or bool(task_identifiers(prompt)))


def _budget_for(prompt: str) -> int:
    profile = task_profile(prompt)
    return {'narrow': 2200, 'balanced': 4000, 'broad': 6000}[profile['breadth']]


def _intent_for(prompt: str) -> str:
    folded = prompt.casefold()
    if any(word in folded for word in (
            'edit', 'change', 'modify', 'refactor', 'implement', '修改', '更改', '重构', '实现')):
        return 'edit'
    if any(word in folded for word in (
            'impact', 'affected', 'caller', 'call chain', '影响', '受影响', '调用者', '调用链')):
        return 'impact'
    if any(word in folded for word in ('test', 'tests', '测试')):
        return 'test'
    return 'understand'


def _derived_facts(repo: Repository, result: dict, prompt: str) -> list[str]:
    """Compute cheap exact facts that models otherwise infer unreliably from syntax."""
    folded = prompt.casefold()
    if not any(word in folded for word in ('how many', 'count', '多少', '几个', '几项')):
        return []
    facts = []
    for anchor in result.get('anchors', []):
        symbol = repo.symbol(anchor['id'])
        if symbol.get('language') != 'python':
            continue
        try:
            tree = ast.parse(textwrap.dedent(symbol['source']))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
                continue
            size = len(value.keys) if isinstance(value, ast.Dict) else len(value.elts)
            for target in targets:
                if isinstance(target, ast.Name) and target.id.casefold() in folded:
                    facts.append(f'{target.id} cardinality = {size}')
    return list(dict.fromkeys(facts))


def _validation_hint(root: Path, result: dict) -> str | None:
    """Infer the minimum repository-local validation environment from its layout."""
    paths = [anchor['id'].split('::', 1)[0] for anchor in result.get('anchors', [])]
    if any(path.endswith(('.py', '.pyi')) for path in paths):
        if (root / 'src').is_dir():
            return ("For direct Python checks in this src-layout project, set PYTHONPATH to the "
                    "repository's ./src directory so the edited checkout is imported.")
        return 'Run direct Python checks from the repository root so the edited checkout is imported.'
    if any(path.endswith(('.js', '.jsx', '.ts', '.tsx')) for path in paths):
        return 'Run a focused Node or package-script check from the repository root.'
    if any(path.endswith('.rs') for path in paths) and (root / 'Cargo.toml').exists():
        return 'Run a focused cargo test from the repository root; reuse locally cached dependencies.'
    if any(path.endswith(('.c', '.h', '.cc', '.cpp', '.hpp')) for path in paths):
        target = result.get('anchors', [{}])[0].get('name', 'the edited function')
        dependencies = result.get('edit_frontier', {}).get('dependency_names', [])
        stubs = f"; stub direct callees ({', '.join(dependencies)})" if dependencies else ''
        return (f'Compile a temporary harness containing only {target}{stubs}, then run its '
                'boundary cases. Do not search or build the full project unless that focused '
                'check proves insufficient.')
    return None


def _control_flow_hints(repo: Repository, result: dict, prompt: str) -> list[str]:
    """Expose exception boundaries that are easy to miss in compact edit packs."""
    folded = prompt.casefold()
    if not any(word in folded for word in (
            'error', 'exception', 'raise', 'throw', 'leak', 'fail',
            '错误', '异常', '抛出', '失败')):
        return []
    hints = []
    for anchor in result.get('anchors', []):
        try:
            symbol = repo.symbol(anchor['id'])
        except Exception:
            continue
        source = textwrap.dedent(symbol.get('source', ''))
        language = symbol.get('language')
        if language == 'python':
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            function = next((node for node in tree.body
                             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
            if function is None:
                continue
            first_try_index = next((index for index, node in enumerate(function.body)
                                    if isinstance(node, ast.Try) and node.handlers), None)
            if first_try_index is None:
                continue
            exposed = [node for node in function.body[:first_try_index]
                       if not (isinstance(node, ast.Expr)
                               and isinstance(node.value, ast.Constant)
                               and isinstance(node.value.value, str))]
            if exposed:
                first = exposed[0].lineno + symbol['start'] - 1
                last = exposed[-1].end_lineno + symbol['start'] - 1
                hints.append(
                    f"{anchor['id']} lines {first}-{last} execute before its try/except; "
                    'an exception raised there cannot be converted by that handler.')
        elif language in {'javascript', 'typescript', 'java', 'c', 'cpp'}:
            match = re.search(r'(?m)^\s*try\s*\{', source)
            if match and source[:match.start()].strip():
                line = symbol['start'] + source[:match.start()].count('\n')
                hints.append(
                    f"{anchor['id']} has executable statements before the try block near line "
                    f'{line}; an exception thrown there cannot be converted by its catch.')
    return hints


def _error_suppression_hints(repo: Repository, result: dict, prompt: str) -> list[str]:
    """Point out local operations that suppress the failure named by an edit task."""
    folded = prompt.casefold()
    if not any(word in folded for word in (
            'invalid', 'reject', 'error', 'exception', 'raise', 'throw', 'leak',
            '无效', '拒绝', '错误', '异常', '抛出')):
        return []
    patterns = (
        (re.compile(r'errors\s*=\s*["\']ignore["\']'),
         'uses errors="ignore", which discards conversion failures and prevents them from reaching an error handler'),
        (re.compile(r'\.ok\(\)'),
         'converts a Result to an Option with .ok(), discarding its error'),
        (re.compile(r'catch\s*\([^)]*\)\s*\{\s*\}', re.S),
         'contains an empty catch block that discards the exception'),
    )
    hints = []
    for anchor in result.get('anchors', []):
        try:
            symbol = repo.symbol(anchor['id'])
        except Exception:
            continue
        source = symbol.get('source', '')
        for pattern, explanation in patterns:
            match = pattern.search(source)
            if match:
                line = symbol['start'] + source[:match.start()].count('\n')
                hints.append(f"{anchor['id']} line {line} {explanation}.")
    return hints


def _frontier_excerpt(symbol: dict, target_name: str, limit: int = 1200) -> str:
    """Return a bounded symbol excerpt centered on its target reference."""
    source = symbol.get('source', '').rstrip()
    if len(source.encode('utf-8')) <= limit:
        return source
    lines = source.splitlines()
    hit = next((index for index, line in enumerate(lines)
                if re.search(r'(?<![A-Za-z0-9_])' + re.escape(target_name) +
                             r'(?![A-Za-z0-9_])', line, re.I)), 0)
    start = max(0, hit - 8)
    end = min(len(lines), hit + 13)
    excerpt = '\n'.join(lines[start:end])
    return (f'# excerpt lines {symbol["start"] + start}-{symbol["start"] + end - 1}\n'
            f'{excerpt}')


def _fast_edit_frontier(root: Path, paths: list[Path], target: dict,
                        target_fragment: dict, matches: list[dict],
                        budget: int) -> tuple[str, dict]:
    """Collect a bounded editing workset without building the persistent graph."""
    leaf = target['name']
    token = re.compile(rb'(?<![A-Za-z0-9_])' + re.escape(leaf.encode().lower()) +
                       rb'(?![A-Za-z0-9_])')
    evidence: dict[str, list[dict]] = {
        'declarations': [], 'dependencies': [], 'tests': [], 'callers': [],
    }
    folded_target = target.get('source', '').casefold()
    dependencies = []
    for symbol in target_fragment.get('symbols', []):
        if symbol['id'] == target['id'] or target['id'].startswith(symbol['id'] + '.'):
            continue
        name = symbol.get('name', '')
        if len(name) >= 3 and re.search(r'(?<![\w])' + re.escape(name) + r'(?![\w])',
                                        folded_target, re.I):
            dependencies.append(symbol)
    dependencies.sort(key=lambda item: (len(item.get('source', '')), item['id']))
    evidence['dependencies'] = dependencies[:2]
    declarations = [item for item in matches if item['id'] != target['id'] and
                    item.get('source', '').strip().endswith(';')]
    declarations.sort(key=lambda item: (len(item.get('source', '')), item['id']))
    evidence['declarations'] = declarations[:1]

    target_path = root / target['path']
    reference_paths = []
    for path in paths:
        if path == target_path:
            continue
        raw = path.read_bytes()
        if token.search(raw.lower()):
            relative = path.relative_to(root).as_posix().casefold()
            is_test = any(part in relative for part in ('test', 'spec', '__tests__', 'testing'))
            same_parent = path.parent == target_path.parent
            reference_paths.append((not is_test, not same_parent,
                                    len(Path(relative).parts), relative, path, raw))
    reference_paths.sort(key=lambda row: row[:4])
    test_paths = [row for row in reference_paths if not row[0]][:6]
    local_paths = [row for row in reference_paths if row[0] and not row[1]][:4]
    other_paths = [row for row in reference_paths if row[0] and row[1]][:4]
    selected_paths = test_paths + local_paths + other_paths
    seen_paths = set()
    for not_test, _, _, _, path, raw in selected_paths:
        relative = path.relative_to(root).as_posix()
        if relative in seen_paths:
            continue
        lines = raw.decode('utf-8', errors='replace').replace('\r\n', '\n').splitlines()
        hit = next((index for index, line in enumerate(lines)
                    if re.search(r'(?<![A-Za-z0-9_])' + re.escape(leaf) +
                                 r'(?![A-Za-z0-9_])', line, re.I)), None)
        if hit is None:
            continue
        start = max(0, hit - 6)
        end = min(len(lines), hit + 7)
        symbol = {
            'id': f'{relative}::reference@{hit + 1}', 'path': relative,
            'name': leaf, 'start': start + 1, 'end': end,
            'source': '\n'.join(lines[start:end]), 'is_test': not not_test,
        }
        role = 'tests' if not not_test else 'callers'
        evidence[role].append(symbol)
        seen_paths.add(relative)
        if len(evidence['tests']) >= 2 and len(evidence['callers']) >= 1:
            break
    evidence['tests'] = evidence['tests'][:2]
    evidence['callers'] = evidence['callers'][:1]

    text = ''
    used = len(target.get('source', '').encode('utf-8'))
    included = {role: [] for role in evidence}
    selected_terms: list[set[str]] = []
    pruned_low_value = 0
    marginal_value_floor = 2.0
    role_score = {
        'declarations': 900,
        'dependencies': 800,
        'tests': 820,
        'callers': 620,
    }
    role_cost = {
        'declarations': 60,
        'dependencies': 100,
        'tests': 40,
        'callers': 160,
    }
    role_labels = {
        'declarations': 'declaration', 'dependencies': 'dependency',
        'tests': 'test', 'callers': 'caller',
    }
    for role in ('declarations', 'dependencies', 'tests', 'callers'):
        for symbol in evidence[role]:
            source = _frontier_excerpt(symbol, leaf)
            block = f'\n# edit-{role_labels[role]}: {symbol["id"]}\n{source}\n'
            size = len(block.encode('utf-8'))
            terms = set(search_terms(source))
            overlap = max(
                (len(terms & prior) / max(1, len(terms | prior))
                 for prior in selected_terms),
                default=0,
            )
            novelty = max(0.15, 1.0 - overlap)
            marginal_value = (
                (role_score[role] - 260 * overlap) * novelty
                / max(1, size + role_cost[role])
            )
            required = not included[role]
            if not required and marginal_value < marginal_value_floor:
                pruned_low_value += 1
                continue
            if used + size > budget:
                continue
            text += block
            used += size
            included[role].append(symbol)
            selected_terms.append(terms)
    counts = {role: len(items) for role, items in included.items()}
    counts['contract_evidence'] = sum(counts.values())
    counts['dependency_names'] = [symbol['name'] for symbol in included['dependencies']]
    counts['pruned_low_value'] = pruned_low_value
    return text, counts


def _fast_exact_context(root: Path, prompt: str, budget: int) -> dict | None:
    """Build a first-use exact-symbol pack without constructing a repository-wide graph."""
    identifiers = task_identifiers(prompt)
    if not identifiers:
        return None
    paths = list(sources(root))
    if len(paths) < 500 and sum(path.stat().st_size for path in paths) < 1_000_000:
        return None
    wanted = [(value, value.rsplit('.', 1)[-1]) for value in identifiers]
    candidates = []
    fragments = {}
    for path in paths:
        raw = path.read_bytes()
        folded = raw.lower()
        defining = []
        for value, leaf in wanted:
            name = re.escape(leaf.encode().lower())
            patterns = (
                rb'\b(?:def|class|fn|function|interface|struct|enum|trait|type)\s+' + name +
                rb'(?![A-Za-z0-9_])',
                rb'\b(?:const|let|var)\s+' + name + rb'\s*[=:]',
                rb'(?<![A-Za-z0-9_])' + name +
                rb'\s*\([^;{}]{0,1000}\)\s*(?:\{|;|=>|:)',
            )
            if any(re.search(pattern, folded, re.S) for pattern in patterns):
                defining.append((value, leaf))
        if not defining:
            continue
        qualified = [value for value, leaf in defining if '.' in value and
                     value.rsplit('.', 1)[0].encode().lower() in folded and
                     leaf.encode().lower() in folded]
        if any('.' in value for value, _ in wanted) and not qualified:
            continue
        fragment = analyze(root, path, raw)
        fragments[path.relative_to(root).as_posix()] = fragment
        candidates.extend(fragment['symbols'])
    matches = []
    for symbol in candidates:
        for value, leaf in wanted:
            if symbol['name'].casefold() != leaf.casefold():
                continue
            if '.' in value:
                qualified = symbol['qualname'].casefold()
                target_name = value.casefold()
                if qualified != target_name and not qualified.endswith('.' + target_name):
                    continue
            matches.append(symbol)
            break

    def match_rank(item: dict) -> tuple:
        path = Path(item['path'])
        path_parts = {part.casefold() for part in path.parts}
        vendor = bool(path_parts & {'deps', 'vendor', 'third_party', 'third-party'})
        declaration = (item['language'] in {'c', 'cpp'} and
                       item['source'].strip().endswith(';'))
        prompt_folded = prompt.casefold().replace('\\', '/')
        path_score = sum(1 for part in path_parts
                         if len(part) >= 3 and part in prompt_folded)
        return (item['is_test'], vendor, declaration, -path_score,
                len(path.parts), item['id'])

    matches.sort(key=match_rank)
    if not matches:
        return None
    selected = matches[:1] if len(wanted) == 1 else matches[:2]
    text, used = '', 0
    included = []
    for symbol in selected:
        block = f"\n# task-anchor: {symbol['id']}\n{symbol['source'].rstrip()}\n"
        size = len(block.encode('utf-8'))
        if used + size > budget and included:
            break
        text += block
        used += size
        included.append(symbol)
    present = {identifier_key(symbol['id']) for symbol in included}
    missing = [value for value in identifiers
               if not any(identifier_key(value) in key for key in present)]
    frontier = None
    if _intent_for(prompt) == 'edit' and included:
        target = included[0]
        extra, frontier = _fast_edit_frontier(
            root, paths, target, fragments[target['path']], matches, budget)
        text += extra
    sufficient = bool(included) and not missing
    if frontier is not None:
        sufficient = sufficient and frontier['contract_evidence'] > 0
    return {
        'text': text,
        'anchors': [symbol_summary(symbol) for symbol in included],
        'coverage': {'missing_identifiers': missing},
        'sufficient': sufficient,
        'quick_index': True,
        'edit_frontier': frontier,
        'source_file_count': len(paths),
    }

_CONTEXT_BLOCK = re.compile(r'(?=\n# [^\n]+\n)')


def _estimate_context_cost(
        root: Path, result: dict, prompt: str, source_count: int) -> dict:
    """Estimate whether injected evidence beats ordinary search and file reads."""
    symbol_ids = list(result.get('included', []))
    symbol_ids.extend(anchor['id'] for anchor in result.get('anchors', []))
    symbol_ids.extend(re.findall(
        r'^# [^:]+: (.+)$', result.get('text', ''), flags=re.MULTILINE
    ))
    paths = list(dict.fromkeys(
        sid.split('::', 1)[0] for sid in symbol_ids if '::' in sid
    ))
    source_units = 0
    for relative in paths:
        path = root / relative
        try:
            source_units += path.stat().st_size
        except OSError:
            continue
    profile = task_profile(prompt)
    native_reads = max(1, profile['estimated_native_reads'], len(paths))
    if _intent_for(prompt) == 'edit':
        native_reads = max(3, native_reads)
    expected_native = (
        source_units
        + native_reads * 320
        + 160
        + min(1200, source_count * 2)
    )
    evidence_units = len(result.get('text', '').encode('utf-8'))
    expected_followup = 0 if result.get('sufficient') else max(600, evidence_units // 2)
    expected_airp = evidence_units + 420 + expected_followup
    saving = 1.0 - expected_airp / max(1, expected_native)
    return {
        'expected_airp_units': expected_airp,
        'expected_native_units': expected_native,
        'expected_followup_units': expected_followup,
        'expected_saving': round(saving, 4),
        'minimum_saving': 0.15,
        'basis': 'UTF-8 bytes plus deterministic tool-envelope estimate',
        'activate': result.get('sufficient', False) and saving >= 0.15,
    }


def _atomic_context_blocks(text: str) -> list[str]:
    """Split rendered evidence at block boundaries without slicing source."""
    if not text:
        return []
    blocks = [block for block in _CONTEXT_BLOCK.split(text) if block]
    return blocks if len(blocks) > 1 or blocks[0].startswith('\n# ') else [text]



def _estimated_saving_percent(activation: str, expected_saving: float) -> float | None:
    """Return a display-safe percentage only for an enabled, beneficial route."""
    if activation != 'enabled' or not isinstance(expected_saving, (int, float)):
        return None
    if expected_saving < 0:
        return None
    return round(min(1.0, float(expected_saving)) * 100, 1)


def _attach_saving_footer(payload: str, route: dict) -> tuple[str, dict, float | None]:
    """Attach an honest, fixed-point saving estimate to sufficient context."""
    base = payload
    candidate = payload
    for _ in range(5):
        effective = _effective_route(route, len(candidate), False)
        percent = _estimated_saving_percent('enabled', effective['expected_saving'])
        if percent is None:
            return base, effective, None
        footer = (
            f'estimated_token_saving_percent: {percent:.1f}\n'
            f'response_footer: AIRP 估算本轮上下文 Token 节省：{percent:.1f}%'
            '（相对原生检索基线）\n'
        )
        updated = base.replace('\n</airp-context>', '\n' + footer + '</airp-context>', 1)
        if updated == candidate:
            return updated, effective, percent
        candidate = updated
    effective = _effective_route(route, len(candidate), False)
    percent = _estimated_saving_percent('enabled', effective['expected_saving'])
    return candidate, effective, percent


def _base_decision(started: float, **values) -> dict:
    decision = {
        'context': None,
        'activation': 'skipped',
        'reason': 'unknown',
        'evidence_state': 'unavailable',
        'intent': None,
        'breadth': None,
        'source_file_count': 0,
        'context_chars': 0,
        'evidence_chars': 0,
        'omitted_blocks': 0,
        'expected_airp_units': 0,
        'expected_native_units': 0,
        'expected_followup_units': 0,
        'expected_saving': 0.0,
        'estimated_token_saving_percent': None,
        'minimum_saving': 0.15,
    }
    decision.update(values)
    decision['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
    return decision


def _effective_route(route: dict, context_chars: int = 0,
                     requires_native_followup: bool = False) -> dict:
    """Convert candidate routing estimates into the cost of the chosen action."""
    native = int(route.get('expected_native_units') or 0)
    followup = native if requires_native_followup else 0
    airp = context_chars + followup
    return {
        'expected_airp_units': airp,
        'expected_native_units': native,
        'expected_followup_units': followup,
        'expected_saving': round(1.0 - airp / native, 4) if native else 0.0,
        'minimum_saving': route.get('minimum_saving', 0.15),
    }


def _diagnostic_context(result: dict, reason: str, omitted_blocks: int,
                        max_chars: int) -> str | None:
    """Explain abstention without forwarding incomplete source evidence."""
    frontier = result.get('edit_frontier')
    lines = [
        '<airp-context>',
        'status: partial',
        'evidence_state: blocked-partial',
        'activation: abstained',
        f'reason: {reason}',
        f'omitted_blocks: {omitted_blocks}',
        'evidence_chars: 0',
        'payload_hash: e3b0c44298fc1c14',
    ]
    if result.get('quick_index'):
        lines.append('index_mode: exact-symbol fast start; source evidence withheld.')
    anchor_hints = [anchor.get('id', '') for anchor in result.get('anchors', [])[:3]]
    if any(anchor_hints):
        lines.append('anchor_hints: ' + ', '.join(filter(None, anchor_hints)))
    if frontier is not None:
        lines.append(
            'edit_frontier: declarations={declarations}, dependencies={dependencies}, '
            'tests={tests}, callers={callers}; edit_ready={ready}.'.format(
                **frontier, ready='yes' if frontier['contract_evidence'] else 'no'))
    missing = result.get('coverage', {}).get('missing_identifiers', [])
    if missing:
        lines.append(f'missing_identifier_count: {len(missing)}')
    lines.append('next_action: use repository-native search and validation for missing evidence.')
    lines.append('</airp-context>')
    payload = '\n'.join(lines)
    return payload if len(payload) <= max_chars else None


def build_prompt_decision(root: str | Path, prompt: str,
                          max_chars: int = 7800) -> dict:
    """Return an auditable Hook decision while keeping repository evidence local."""
    started = time.perf_counter()
    intent = _intent_for(prompt) if prompt.strip() else None
    breadth = task_profile(prompt)['breadth'] if prompt.strip() else None
    try:
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            return _base_decision(started, activation='failed', reason='invalid_repository',
                                  exception_type='NotADirectoryError', intent=intent,
                                  breadth=breadth)
        if not prompt.strip() or not is_code_task(prompt):
            return _base_decision(started, reason='non_code_task', intent=intent,
                                  breadth=breadth)
        if not next(iter(sources(root)), None):
            return _base_decision(started, reason='empty_repository', intent=intent,
                                  breadth=breadth)
        repo = Repository(root)
        edit_locations = []
        control_flow_hints = []
        error_suppression_hints = []
        try:
            budget = _budget_for(prompt)
            result = (_fast_exact_context(root, prompt, budget)
                      if repo.load('manifest') is None else None)
            if result is None:
                result = repo.smart_context(task=prompt, budget=budget,
                                            intent=intent, breadth='auto')
            source_count = result.get('source_file_count')
            if source_count is None:
                source_count = repo.db.execute(
                    'SELECT COUNT(*) FROM file_lookup').fetchone()[0]
            routing_cost = _estimate_context_cost(root, result, prompt, source_count)
            result['routing_cost'] = routing_cost
            route = {
                key: routing_cost[key] for key in (
                    'expected_airp_units', 'expected_native_units',
                    'expected_followup_units', 'expected_saving', 'minimum_saving')
            }
            if result.get('sufficient') and not routing_cost['activate']:
                return _base_decision(
                    started, reason='low_expected_saving', evidence_state='sufficient',
                    intent=intent, breadth=breadth, source_file_count=source_count,
                    **_effective_route(route, requires_native_followup=True))
            derived_facts = _derived_facts(repo, result, prompt)
            if intent == 'edit':
                for anchor in result.get('anchors', []):
                    try:
                        symbol = repo.symbol(anchor['id'])
                        edit_locations.append((symbol['path'], symbol['start'], symbol['end']))
                    except Exception:
                        if all(key in anchor for key in ('path', 'start', 'end')):
                            edit_locations.append(
                                (anchor['path'], anchor['start'], anchor['end']))
                control_flow_hints = _control_flow_hints(repo, result, prompt)
                error_suppression_hints = _error_suppression_hints(repo, result, prompt)
        finally:
            repo.close()
        if not result.get('text') or not result.get('anchors'):
            return _base_decision(
                started, reason='no_evidence', intent=intent, breadth=breadth,
                source_file_count=source_count,
                **_effective_route(route, requires_native_followup=True))

        blocks = _atomic_context_blocks(result['text'])
        if not result.get('sufficient'):
            context = _diagnostic_context(result, 'blocked_partial', len(blocks), max_chars)
            return _base_decision(
                started, context=context, reason='blocked_partial',
                evidence_state='blocked-partial', intent=intent, breadth=breadth,
                source_file_count=source_count, context_chars=len(context or ''),
                omitted_blocks=len(blocks),
                **_effective_route(route, len(context or ''), True))

        anchor_ids = ', '.join(anchor['id'] for anchor in result['anchors'])
        header = (
            '<airp-context>\n'
            'AIRP local evidence. Reuse it before repository reads; inspect more only '
            'when runtime verification requires it.\n'
            f'status: sufficient\nanchors: {anchor_ids}\n'
        )
        if result.get('quick_index'):
            header += ('index_mode: exact-symbol fast start; repository-wide relationships are not '
                       'available until the persistent graph is built.\n')
        frontier = result.get('edit_frontier')
        if frontier is not None:
            header += ('edit_frontier: declarations={declarations}, dependencies={dependencies}, '
                       'tests={tests}, callers={callers}; '
                       'pruned_low_value={pruned_low_value}; edit_ready={ready}.\n'.format(
                           **frontier, ready='yes' if frontier['contract_evidence'] else 'no'))
        if intent == 'edit':
            edit_paths = list(dict.fromkeys(anchor['id'].split('::', 1)[0]
                                           for anchor in result['anchors']))
            if edit_paths:
                validation_hint = _validation_hint(root, result)
                header += (
                    'edit_scope: ' + ', '.join(edit_paths) + '\n'
                    'edit_sequence: patch the target; run one focused check; expand only '
                    'for missing evidence or failure; repair and rerun failed checks.\n'
                    'edit_format: write actual newline characters, never shell escape text.\n'
                )
                if validation_hint:
                    header += 'validation_hint: ' + validation_hint + '\n'
                if edit_locations:
                    header += 'edit_locations: ' + '; '.join(
                        f'{path}:{start}-{end}' for path, start, end in edit_locations) + '\n'
                if control_flow_hints:
                    header += 'control_flow: ' + ' '.join(control_flow_hints) + '\n'
                if error_suppression_hints:
                    header += 'error_suppression: ' + ' '.join(error_suppression_hints) + '\n'
        missing = result.get('coverage', {}).get('missing_identifiers', [])
        if missing:
            header += 'missing_identifiers: ' + ', '.join(missing) + '\n'
        if derived_facts:
            header += 'derived_facts: ' + '; '.join(derived_facts) + '\n'
        suffix = '\n</airp-context>'
        conservative_receipt = (
            'evidence_state: blocked-partial\nactivation: abstained\n'
            'reason: atomic_budget_exhausted\nomitted_blocks: 9999999\n'
            'evidence_chars: 9999999\npayload_hash: 0000000000000000\n')

        def pack(prefix: str) -> tuple[list[str], int]:
            selected = []
            used = len(prefix) + len(conservative_receipt) + len(suffix)
            for block in blocks:
                if used + len(block) > max_chars:
                    break
                selected.append(block)
                used += len(block)
            return selected, len(blocks) - len(selected)

        selected, omitted_blocks = pack(header)
        if omitted_blocks:
            context = _diagnostic_context(
                result, 'atomic_budget_exhausted', omitted_blocks, max_chars)
            return _base_decision(
                started, context=context, reason='atomic_budget_exhausted',
                evidence_state='blocked-partial', intent=intent, breadth=breadth,
                source_file_count=source_count, context_chars=len(context or ''),
                omitted_blocks=omitted_blocks,
                **_effective_route(route, len(context or ''), True))

        evidence_text = ''.join(selected)
        payload_hash = hashlib.sha256(evidence_text.encode('utf-8')).hexdigest()[:16]
        receipt = (
            'evidence_state: sufficient\nactivation: enabled\n'
            'reason: evidence_complete\n'
            'omitted_blocks: 0\n'
            f'evidence_chars: {len(evidence_text)}\n'
            f'payload_hash: {payload_hash}\n')
        if len(header) + len(receipt) + len(suffix) > max_chars:
            header = '<airp-context>\nstatus: sufficient\nanchors: ' + anchor_ids + '\n'
            selected, omitted_blocks = pack(header)
            if omitted_blocks:
                context = _diagnostic_context(
                    result, 'atomic_budget_exhausted', omitted_blocks, max_chars)
                return _base_decision(
                    started, context=context, reason='atomic_budget_exhausted',
                    evidence_state='blocked-partial', intent=intent, breadth=breadth,
                    source_file_count=source_count, context_chars=len(context or ''),
                    omitted_blocks=omitted_blocks,
                    **_effective_route(route, len(context or ''), True))
            evidence_text = ''.join(selected)
            payload_hash = hashlib.sha256(evidence_text.encode('utf-8')).hexdigest()[:16]
            receipt = (
                'evidence_state: sufficient\nactivation: enabled\n'
                'reason: evidence_complete\nomitted_blocks: 0\n'
                f'evidence_chars: {len(evidence_text)}\n'
                f'payload_hash: {payload_hash}\n')
        payload = header + receipt + evidence_text + suffix
        payload, effective_route, estimated_percent = _attach_saving_footer(payload, route)
        if len(payload) > max_chars:
            return _base_decision(
                started, reason='payload_limit', evidence_state='blocked-partial',
                intent=intent, breadth=breadth, source_file_count=source_count,
                omitted_blocks=len(blocks),
                **_effective_route(route, requires_native_followup=True))
        return _base_decision(
            started, context=payload, activation='enabled', reason='evidence_complete',
            evidence_state='sufficient', intent=intent, breadth=breadth,
            source_file_count=source_count, context_chars=len(payload),
            evidence_chars=len(evidence_text),
            estimated_token_saving_percent=estimated_percent,
            **effective_route)
    except Exception as error:
        return _base_decision(
            started, activation='failed', reason='internal_error',
            exception_type=type(error).__name__, intent=intent, breadth=breadth)


def build_prompt_context(root: str | Path, prompt: str,
                         max_chars: int = 7800) -> str | None:
    """Backward-compatible context-only API."""
    return build_prompt_decision(root, prompt, max_chars)['context']


def process_hook(payload: dict, host: str = 'codex') -> dict | None:
    """Convert a supported host payload into optional additional context."""
    from .diagnostics import record_hook_event

    request = ContextRequest.from_hook(payload, host=host)
    decision = build_prompt_decision(request.root, request.prompt, request.max_chars)
    decision['host'] = request.host
    record_hook_event(request.root, decision)
    return render_hook_output(decision.get('context'))


def main(host: str = 'codex') -> None:
    try:
        output = process_hook(json.load(sys.stdin), host=host)
        if output:
            print(json.dumps(output, ensure_ascii=True, separators=(',', ':')))
    except Exception:
        # Retrieval is an optimization. A hook failure must not block the user's task.
        return


if __name__ == '__main__':
    main()
