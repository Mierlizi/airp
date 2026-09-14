"""Pre-model AIRP context injection for Codex UserPromptSubmit hooks."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys
import textwrap

from .core import Repository, identifier_key, symbol_summary, task_identifiers, task_profile
from .graph import analyze, sources


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
    role_labels = {
        'declarations': 'declaration', 'dependencies': 'dependency',
        'tests': 'test', 'callers': 'caller',
    }
    for role in ('declarations', 'dependencies', 'tests', 'callers'):
        for symbol in evidence[role]:
            source = _frontier_excerpt(symbol, leaf)
            block = f'\n# edit-{role_labels[role]}: {symbol["id"]}\n{source}\n'
            size = len(block.encode('utf-8'))
            if used + size > budget:
                continue
            text += block
            used += size
            included[role].append(symbol)
    counts = {role: len(items) for role, items in included.items()}
    counts['contract_evidence'] = sum(counts.values())
    counts['dependency_names'] = [symbol['name'] for symbol in included['dependencies']]
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
    }

def build_prompt_context(root: str | Path, prompt: str, max_chars: int = 7800) -> str | None:
    """Build compact evidence before the first model call, without an MCP round trip."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not prompt.strip() or not is_code_task(prompt):
        return None
    if not next(iter(sources(root)), None):
        return None
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
                                        intent=_intent_for(prompt), breadth='auto')
        derived_facts = _derived_facts(repo, result, prompt)
        if _intent_for(prompt) == 'edit':
            for anchor in result.get('anchors', []):
                try:
                    symbol = repo.symbol(anchor['id'])
                    edit_locations.append((symbol['path'], symbol['start'], symbol['end']))
                except Exception:
                    if all(key in anchor for key in ('path', 'start', 'end')):
                        edit_locations.append((anchor['path'], anchor['start'], anchor['end']))
            control_flow_hints = _control_flow_hints(repo, result, prompt)
            error_suppression_hints = _error_suppression_hints(repo, result, prompt)
    finally:
        repo.close()
    if not result.get('text') or not result.get('anchors'):
        return None
    anchor_ids = ', '.join(anchor['id'] for anchor in result['anchors'])
    missing = result.get('coverage', {}).get('missing_identifiers', [])
    status = 'sufficient' if result.get('sufficient') else 'partial'
    header = (
        '<airp-context>\n'
        'AIRP computed this local source evidence before the first model request. '
        'Use it before repository tools and do not repeat reads already answered by it. '
        'Inspect source only when this evidence is partial or runtime behavior must be verified.\n'
        f'status: {status}\nanchors: {anchor_ids}\n'
    )
    if result.get('quick_index'):
        header += ('index_mode: exact-symbol fast start; repository-wide relationships are not '
                   'available until the persistent graph is built.\n')
    frontier = result.get('edit_frontier')
    if frontier is not None:
        header += ('edit_frontier: declarations={declarations}, dependencies={dependencies}, '
                   'tests={tests}, callers={callers}; '
                   'edit_ready={ready}.\n'.format(
                       **frontier, ready='yes' if frontier['contract_evidence'] else 'no'))
    if _intent_for(prompt) == 'edit':
        edit_paths = list(dict.fromkeys(anchor['id'].split('::', 1)[0]
                                       for anchor in result['anchors']))
        if edit_paths:
            validation_hint = _validation_hint(root, result)
            header += ('edit_scope: ' + ', '.join(edit_paths) + '\n'
                       'edit_sequence: patch the named target from this evidence; run one focused '
                       'behavior check; expand source reads or tests only if that check fails. '
                       'A failed edit command or check means the task is incomplete: repair the '
                       'change and rerun the check before finishing.\n'
                       'edit_format: write actual newline characters; never insert literal shell '
                       'newline escape text into source.\n')
            if validation_hint:
                header += 'validation_hint: ' + validation_hint + '\n'
            if edit_locations:
                header += 'edit_locations: ' + '; '.join(
                    f'{path}:{start}-{end}' for path, start, end in edit_locations) + '\n'
            if control_flow_hints:
                header += 'control_flow: ' + ' '.join(control_flow_hints) + '\n'
            if error_suppression_hints:
                header += 'error_suppression: ' + ' '.join(error_suppression_hints) + '\n'
    if missing:
        header += 'missing_identifiers: ' + ', '.join(missing) + '\n'
    if derived_facts:
        header += 'derived_facts: ' + '; '.join(derived_facts) + '\n'
    suffix = '\n</airp-context>'
    available = max(0, max_chars - len(header) - len(suffix))
    return header + result['text'][:available] + suffix


def process_hook(payload: dict) -> dict | None:
    """Convert a Codex hook payload into optional additional developer context."""
    prompt = payload.get('prompt') or ''
    root = payload.get('cwd') or '.'
    context = build_prompt_context(root, prompt)
    if not context:
        return None
    return {'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': context,
    }}


def main() -> None:
    try:
        output = process_hook(json.load(sys.stdin))
        if output:
            print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
    except Exception:
        # Retrieval is an optimization. A hook failure must not block the user's task.
        return


if __name__ == '__main__':
    main()
