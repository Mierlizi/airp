"""Serialized repository operations with optimistic file checks and durable history."""
import ast
import base64
import builtins
from contextlib import contextmanager
import difflib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid

from .graph import GRAPH_SCHEMA, build, decode, digest, sources, warm_languages


class AirpError(ValueError):
    pass


def compact(symbol):
    return {k: v for k, v in symbol.items() if k != 'source'}


def symbol_summary(symbol):
    """Small locator returned by discovery tools."""
    keys = ('id', 'name', 'qualname', 'kind', 'path', 'start', 'end', 'language',
            'backend', 'editable', 'analysis_stale')
    return {key: symbol.get(key) for key in keys}


def search_terms(text):
    """Split prose and identifiers without a language-specific tokenizer."""
    expanded = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text).replace('_', ' ')
    chunks = re.findall(r'[A-Za-z0-9]+|[\u3400-\u9fff]+', expanded)
    terms = []
    for chunk in chunks:
        folded = chunk.casefold()
        if '\u3400' <= chunk[0] <= '\u9fff':
            terms.append(folded)
            terms.extend(folded[index:index + 2] for index in range(len(folded) - 1))
        elif len(folded) > 1:
            terms.append(folded)
    return terms


def task_identifiers(text):
    """Extract code-like names whose presence can be checked in returned evidence."""
    candidates = re.findall(
        r'(?<![\w])[_A-Za-z][_A-Za-z0-9]*(?:\.[_A-Za-z][_A-Za-z0-9]*)*', text)
    common = {'what', 'which', 'where', 'when', 'return', 'default', 'current',
              'source', 'code', 'with', 'from', 'into', 'true', 'false'}
    call_like = re.findall(r'(?<![\w.])([_A-Za-z][_A-Za-z0-9]*)\s*\(', text)
    explicit_calls = {value.casefold() for value in call_like}
    result = []
    for value in [*candidates, *call_like]:
        leaf = value.rsplit('.', 1)[-1]
        code_like = ('_' in value or '.' in value or leaf.isupper() or
                     any(char.isupper() for char in leaf[1:]))
        if (code_like or value.casefold() in explicit_calls) and value.casefold() not in common:
            result.append(value)
    return list(dict.fromkeys(result))


def task_creation_targets(text):
    """Extract explicitly requested new symbols without treating every call as new."""
    folded = text.casefold()
    if not any(word in folded for word in (
            'add', 'create', 'introduce', 'implement', 'define', '新增', '添加', '实现')):
        return []
    targets = []
    patterns = (
        r'(?is)(?:add|create|introduce|implement|define)\b.{0,120}?'
        r'([_A-Za-z][_A-Za-z0-9]*)\s*\(',
        r'(?is)(?:add|create|introduce|implement|define)\b.{0,80}?'
        r'(?:function|method|class|api)\s+([_A-Za-z][_A-Za-z0-9]*)',
        r'(?:新增|添加|实现).{0,80}?([_A-Za-z][_A-Za-z0-9]*)\s*\(',
    )
    for pattern in patterns:
        targets.extend(match.group(1) for match in re.finditer(pattern, text))
    call_like = {value.casefold() for value in re.findall(
        r'(?<![\w.])([_A-Za-z][_A-Za-z0-9]*)\s*\(', text)}
    if call_like:
        targets = [target for target in targets if target.casefold() in call_like]
    return list(dict.fromkeys(targets))


def task_edit_targets(text):
    """Extract an existing symbol named immediately after an edit verb."""
    patterns = (
        r'(?is)(?:modify|change|fix|refactor|update|extend)\s+(?:the\s+)?'
        r'([_A-Za-z][_A-Za-z0-9]*(?:\.[_A-Za-z][_A-Za-z0-9]*)*)',
        r'(?:修改|更改|修复|重构|更新|扩展)\s*'
        r'([_A-Za-z][_A-Za-z0-9]*(?:\.[_A-Za-z][_A-Za-z0-9]*)*)',
    )
    targets = []
    for pattern in patterns:
        targets.extend(match.group(1) for match in re.finditer(pattern, text))
    return list(dict.fromkeys(targets))


def identifier_key(value):
    """Normalize dotted modules, file paths, and AIRP symbol IDs for coverage checks."""
    value = re.sub(
        r'\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|c|cc|cpp|h|hpp|cs|rb|php)(?=::|$)',
        '', value, flags=re.I)
    return re.sub(r'[^a-z0-9_]+', '', value.casefold())


def is_external_identifier(value):
    """Ignore language builtins and prose acronyms in repository coverage checks."""
    leaf = value.rsplit('.', 1)[-1]
    return hasattr(builtins, leaf) or leaf.casefold() in {
        'api', 'ascii', 'utf8', 'utf16', 'json', 'http', 'https', 'sql', 'cli', 'ui',
    }


def task_profile(task):
    """Choose breadth and estimate whether context can amortize a model tool round."""
    folded = task.casefold()
    identifiers = task_identifiers(task)
    relation_words = ('caller', 'callee', 'dependency', 'dependencies', 'impact',
                      'affected', 'call chain', '调用链', '调用者', '依赖', '影响范围', '受影响')
    multi_words = ('compare', 'between', ' across ', ' and ', '比较', '以及', '与')
    relation = any(word in folded for word in relation_words)
    behavior_words = ('calculate', 'compute', 'evaluate', 'return value', 'result',
                      '计算', '求值', '返回值', '结果')
    behavior = any(word in folded for word in behavior_words)
    qualified_target = any('.' in name and not re.search(
        r'\.(?:py|js|ts|tsx|go|rs|java|c|cc|cpp|cs|rb|php)$', name, re.I)
                           for name in identifiers)
    multi = (len(identifiers) >= 2 or any(word in folded for word in multi_words))
    breadth = ('broad' if relation else
               'narrow' if qualified_target or len(identifiers) == 1 else
               'balanced' if multi else 'narrow')
    native_reads = 4 if relation else 3 if multi else 1 if identifiers else 2
    return {'breadth': breadth, 'identifiers': identifiers,
            'behavior_task': behavior,
            'relation_task': relation,
            'estimated_native_reads': native_reads,
            'recommended': native_reads >= 3}


def function_outline(node):
    prefix = 'async ' if isinstance(node, ast.AsyncFunctionDef) else ''
    returns = f' -> {ast.unparse(node.returns)}' if node.returns else ''
    line = f'{prefix}def {node.name}({ast.unparse(node.args)}){returns}: ...'
    doc = ast.get_docstring(node, clean=True)
    if doc:
        line += f'  # {doc.splitlines()[0][:160]}'
    return line


def symbol_outline(symbol):
    """Semantic outline: signatures and class fields, never function bodies."""
    if symbol.get('language', 'python') != 'python':
        return (symbol.get('signature') or f"{symbol['kind']} {symbol['qualname']}") + ' ...'
    try:
        node = ast.parse(textwrap.dedent(symbol['source'])).body[0]
    except (SyntaxError, IndexError):
        return f"{symbol['kind']} {symbol['qualname']}"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return function_outline(node)
    if isinstance(node, ast.ClassDef):
        bases = ', '.join(ast.unparse(base) for base in node.bases)
        header = f'class {node.name}' + (f'({bases})' if bases else '') + ':'
        members = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                members.append('    ' + function_outline(child))
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                members.append(f'    {child.target.id}: {ast.unparse(child.annotation)}')
            elif isinstance(child, ast.Assign) and all(isinstance(t, ast.Name) for t in child.targets):
                members.append('    ' + ' = '.join(t.id for t in child.targets) + ' = ...')
        return '\n'.join([header, *(members or ['    ...'])])
    return ast.unparse(node)


def focused_excerpt(symbol, task, byte_limit=1400):
    """Keep a signature plus task-matching lines when a whole symbol is too large."""
    source_lines = symbol.get('source', '').splitlines()
    if not source_lines:
        return symbol_outline(symbol)
    terms = [term for term in search_terms(task) if len(term) >= 3]
    identifiers = [value.casefold() for value in task_identifiers(task)]
    scored = []
    for index, line in enumerate(source_lines):
        folded = line.casefold().replace('_', ' ')
        score = sum(1 for term in terms if term in folded)
        # Keep the actual implementation site for camelCase and qualified names.
        # Split lexical terms such as "clear" and "queue" also occur throughout
        # nearby setup code and can otherwise crowd out the defining property.
        score += 20 * sum(identifier in line.casefold() for identifier in identifiers)
        if score:
            scored.append((score, index))
    selected = {0}
    for identifier in identifiers:
        match = next((index for index, line in enumerate(source_lines)
                      if identifier in line.casefold()), None)
        if match is not None:
            selected.update(range(max(0, match - 4), min(len(source_lines), match + 5)))
    for _, index in sorted(scored, key=lambda item: (-item[0], item[1]))[:4]:
        selected.update(range(max(0, index - 4), min(len(source_lines), index + 5)))
    rendered = []
    for index in sorted(selected):
        line = source_lines[index]
        candidate = '\n'.join([*rendered, line])
        if len(candidate.encode('utf-8')) > byte_limit:
            break
        rendered.append(line)
    if len(rendered) == 1:
        outline = symbol_outline(symbol)
        if len(outline.encode('utf-8')) <= byte_limit:
            return outline
    return '# focused excerpt\n' + '\n'.join(rendered)


class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self._manifest = None
        self._index_checked = False
        if not self.root.is_dir():
            raise AirpError('Repository must be a directory')
        self.state = self.root / '.airp'
        if self.state.is_symlink():
            raise AirpError('.airp must not be a symlink')
        self.state.mkdir(exist_ok=True)
        db_path = self.state / 'program.sqlite3'
        if db_path.is_symlink():
            raise AirpError('Database must not be a symlink')
        self.db = sqlite3.connect(db_path, timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, tool TEXT, elapsed_ms REAL, response_bytes INTEGER, ok INTEGER, created REAL)')
        for table in ('files', 'symbols', 'imports', 'edges', 'diagnostics'):
            self.db.execute(f'CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS file_lookup (path TEXT PRIMARY KEY, hash TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, language TEXT NOT NULL, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS symbol_lookup (sid TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, language TEXT NOT NULL, is_test INTEGER NOT NULL, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS edge_lookup (source TEXT NOT NULL, target TEXT, kind TEXT NOT NULL, confidence TEXT NOT NULL, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS import_lookup (path TEXT NOT NULL, data TEXT NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS edge_source_idx ON edge_lookup(source,kind)')
        self.db.execute('CREATE INDEX IF NOT EXISTS edge_target_idx ON edge_lookup(target,kind)')
        self.db.execute('CREATE INDEX IF NOT EXISTS symbol_name_idx ON symbol_lookup(name,kind)')
        self.db.execute('CREATE INDEX IF NOT EXISTS symbol_test_idx ON symbol_lookup(is_test)')
        self.db.execute('CREATE INDEX IF NOT EXISTS import_path_idx ON import_lookup(path)')
        try:
            self.db.execute(
                'CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts '
                'USING fts5(sid UNINDEXED, identity, body)')
            self.has_fts = True
        except sqlite3.OperationalError:
            # Some minimal Python/SQLite builds omit FTS5. Keep the portable
            # in-process scorer as a correctness fallback.
            self.has_fts = False
        self.db.commit()

    def close(self):
        self.db.close()

    def load(self, key, default=None):
        row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def save(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO state VALUES (?, ?)', (key, json.dumps(value, ensure_ascii=False)))

    def path(self, relative):
        if Path(relative).is_absolute():
            raise AirpError('Expected a repository-relative path')
        path = self.root / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or self.state in resolved.parents:
            raise AirpError('Path escapes repository or accesses internal state')
        if any(p.is_symlink() for p in [path, *path.parents] if p != self.root):
            raise AirpError('Symlink paths are not supported')
        return path

    def fingerprint(self):
        return {p.relative_to(self.root).as_posix(): digest(p.read_bytes()) for p in sources(self.root)}

    def inventory(self):
        return {p.relative_to(self.root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in sources(self.root)}

    def is_stale(self, manifest):
        if manifest.get('schema_version') != GRAPH_SCHEMA:
            return True
        expected = {f['path']: (f['size'], f.get('mtime_ns')) for f in manifest['files']}
        return expected != self.inventory()

    def require_index(self):
        if self._index_checked:
            return self._manifest
        manifest = self.load('manifest')
        if manifest is None:
            raise AirpError('Run index first')
        if self.is_stale(manifest):
            raise AirpError('Index is stale: run index again before querying or editing')
        self._manifest = manifest
        self._index_checked = True
        return manifest

    def graph(self):
        self.require_index()
        graph = self.load('graph')
        return graph

    def symbol(self, sid):
        self.require_index()
        row = self.db.execute('SELECT data FROM symbol_lookup WHERE sid=?', (sid,)).fetchone()
        if row is None:
            raise AirpError(f'Unknown symbol: {sid}')
        symbol = json.loads(row[0])
        file_row = self.db.execute('SELECT hash FROM file_lookup WHERE path=?',
                                   (symbol['path'],)).fetchone()
        if not file_row or digest(self.path(symbol['path']).read_bytes()) != file_row[0]:
            raise AirpError('Index is stale: target file content changed; run index again')
        return symbol

    def index(self):
        graph = build(self.root, previous=self.load('graph'))
        changed_paths = set(graph['index_stats']['changed'])
        deleted_paths = set(graph['index_stats']['deleted'])
        fts_full_rebuild = False
        old_fts_sids = []
        if self.has_fts:
            fts_count = self.db.execute('SELECT COUNT(*) FROM symbol_fts').fetchone()[0]
            fts_full_rebuild = bool(graph['symbols']) and fts_count == 0
            if not fts_full_rebuild and (changed_paths or deleted_paths):
                affected_paths = changed_paths | deleted_paths
                old_fts_sids = [sid for sid, path in self.db.execute(
                    'SELECT sid,path FROM symbol_lookup').fetchall() if path in affected_paths]
        self.save('graph', graph)
        manifest = {key: graph[key] for key in ('schema_version', 'files', 'diagnostics', 'capabilities')}
        self.save('manifest', manifest)
        self._manifest = manifest
        self._index_checked = True
        for table in ('files', 'symbols', 'imports', 'edges', 'diagnostics'):
            self.db.execute(f'DELETE FROM {table}')
            self.db.executemany(f'INSERT INTO {table}(data) VALUES (?)',
                                [(json.dumps(item, ensure_ascii=False),) for item in graph[table]])
        for table in ('file_lookup', 'symbol_lookup', 'edge_lookup', 'import_lookup'):
            self.db.execute(f'DELETE FROM {table}')
        self.db.executemany('INSERT INTO file_lookup VALUES (?,?,?,?,?,?)',
                            [(f['path'], f['hash'], f['size'], f['mtime_ns'], f['language'],
                              json.dumps(f, ensure_ascii=False)) for f in graph['files']])
        self.db.executemany('INSERT INTO symbol_lookup VALUES (?,?,?,?,?,?,?)',
                            [(s['id'], s['path'], s['name'], s['kind'], s['language'], int(s['is_test']),
                              json.dumps(s, ensure_ascii=False)) for s in graph['symbols']])
        if self.has_fts and fts_full_rebuild:
            self.db.execute('DELETE FROM symbol_fts')
            self.db.executemany(
                'INSERT INTO symbol_fts(sid,identity,body) VALUES (?,?,?)',
                [(s['id'],
                  ' '.join(search_terms(' '.join(
                      (s['name'], s['qualname'], s['path'], s['id'])))),
                  ' '.join(search_terms(s.get('source', ''))))
                 for s in graph['symbols']])
        elif self.has_fts and (changed_paths or deleted_paths):
            self.db.executemany('DELETE FROM symbol_fts WHERE sid=?',
                                [(sid,) for sid in old_fts_sids])
            self.db.executemany(
                'INSERT INTO symbol_fts(sid,identity,body) VALUES (?,?,?)',
                [(s['id'],
                  ' '.join(search_terms(' '.join(
                      (s['name'], s['qualname'], s['path'], s['id'])))),
                  ' '.join(search_terms(s.get('source', ''))))
                 for s in graph['symbols'] if s['path'] in changed_paths])
        self.db.executemany('INSERT INTO edge_lookup VALUES (?,?,?,?,?)',
                            [(e['source'], e['target'], e['kind'], e['confidence'],
                              json.dumps(e, ensure_ascii=False)) for e in graph['edges']])
        self.db.executemany('INSERT INTO import_lookup VALUES (?,?)',
                            [(i['path'], json.dumps(i, ensure_ascii=False)) for i in graph['imports']])
        return ({k: len(graph[k]) for k in ('files', 'symbols', 'imports', 'edges')} |
                {'diagnostics': graph['diagnostics'], 'incremental': graph['index_stats'],
                 'capabilities': graph['capabilities']})

    def smart_context(self, task, budget=3000, intent='understand', breadth='auto'):
        """Auto-refresh the index and answer a prose task in one agent tool call."""
        manifest = self.load('manifest')
        index_state = 'current'
        if manifest is None:
            self.index()
            index_state = 'created'
        elif self.is_stale(manifest):
            self.index()
            index_state = 'refreshed'
        profile = task_profile(task)
        selected_breadth = profile['breadth'] if breadth == 'auto' else breadth
        creation_targets = [target for target in task_creation_targets(task)
                            if not self._symbol_exists(target)] if intent == 'edit' else []
        if creation_targets:
            result = self.creation_context(task, creation_targets[0], budget=budget)
            result = self._shape_task_response(result, 'auto', None, budget, intent)
        else:
            result = self.task_context(task=task, budget=budget, intent=intent,
                                       breadth=selected_breadth, response_mode='auto')
        anchor_paths = {anchor['id'].split('::', 1)[0] for anchor in result['anchors']}
        if not profile['relation_task']:
            profile['estimated_native_reads'] = (
                1 if len(anchor_paths) <= 1 else 1 + len(anchor_paths))
            profile['recommended'] = profile['estimated_native_reads'] >= 3
        profile['anchor_paths'] = len(anchor_paths)
        result['index_state'] = index_state
        result['routing'] = profile | {'selected_breadth': selected_breadth,
                                       'creation_targets': creation_targets}
        return result

    def find(self, query='', kind=None, limit=30):
        if not 1 <= limit <= 200:
            raise AirpError('limit must be between 1 and 200')
        self.require_index()
        rows = self.db.execute('SELECT data FROM symbol_lookup').fetchall()
        items = [json.loads(row[0]) for row in rows]
        items = [s for s in items if query.casefold() in s['id'].casefold()
                 and (kind is None or s['kind'] == kind)]
        items.sort(key=lambda s: (s['name'].casefold() != query.casefold(), s['id']))
        return {'symbols': [symbol_summary(s) for s in items[:limit]], 'total': len(items)}

    def _exact_task_symbols(self, task):
        """Fetch named symbols before bounded FTS retrieval can discard them."""
        found = {}
        for requested in task_identifiers(task):
            leaf = requested.rsplit('.', 1)[-1]
            rows = self.db.execute(
                'SELECT data FROM symbol_lookup WHERE name = ? COLLATE NOCASE',
                (leaf,)).fetchall()
            for row in rows:
                symbol = json.loads(row[0])
                identities = {symbol['name'].casefold(), symbol['qualname'].casefold(),
                              symbol['id'].split('::', 1)[-1].casefold()}
                if requested.casefold() in identities or '.' not in requested:
                    found[symbol['id']] = symbol
        return list(found.values())

    def _symbol_exists(self, name):
        leaf = name.rsplit('.', 1)[-1]
        return self.db.execute(
            'SELECT 1 FROM symbol_lookup WHERE name = ? COLLATE NOCASE LIMIT 1',
            (leaf,)).fetchone() is not None

    def creation_context(self, task, target, budget=3000):
        """Build a compact implementation/declaration pack for a requested new symbol."""
        rows = self.db.execute('SELECT data FROM symbol_lookup WHERE is_test=0').fetchall()
        symbols = [json.loads(row[0]) for row in rows]
        target_folded = target.casefold()
        task_terms = {term for term in search_terms(task) if len(term) >= 3}
        ranked = []
        for symbol in symbols:
            name = symbol['name'].casefold()
            prefix = len(os.path.commonprefix((target_folded, name)))
            similarity = difflib.SequenceMatcher(None, target_folded, name).ratio()
            source_terms = set(search_terms(symbol.get('source', '')))
            semantic = len(task_terms & source_terms)
            score = prefix * 12 + similarity * 20 + semantic * 2
            if ('startswith' in target_folded or 'prefix' in task_terms):
                folded_source = symbol.get('source', '').casefold()
                if 'memcmp' in folded_source:
                    score += 80
                if name.endswith('cmp'):
                    score += 50
                if name.endswith('len'):
                    score += 25
            if score:
                ranked.append((score, symbol))
        ranked.sort(key=lambda row: (-row[0], row[1]['id']))
        path_scores = {}
        for score, symbol in ranked:
            path_scores[symbol['path']] = max(path_scores.get(symbol['path'], 0), score)
        best_path = max(path_scores, key=lambda path: (path_scores[path], path), default=None)
        selected_paths = [best_path] if best_path else []
        if best_path:
            stem = Path(best_path).stem.casefold()
            companions = sorted(path for path in path_scores
                                if path not in selected_paths and Path(path).stem.casefold() == stem)
            selected_paths.extend(companions[:2])
        if len(selected_paths) < 2:
            for path, _ in sorted(path_scores.items(), key=lambda row: (-row[1], row[0])):
                if path not in selected_paths:
                    selected_paths.append(path)
                if len(selected_paths) == 2:
                    break

        blocks, anchors, used = [], [], 0
        per_path_budget = max(700, (budget - 120) // max(1, len(selected_paths)))
        for path in selected_paths:
            path_symbols = [(score, symbol) for score, symbol in ranked if symbol['path'] == path]
            chosen = path_symbols[:1] if ('startswith' in target_folded or 'prefix' in task_terms) else path_symbols[:3]
            source_path = self.path(path)
            head = source_path.read_text(encoding='utf-8', errors='replace').splitlines()
            imports = [line for line in head[:100]
                       if re.match(r'\s*(?:#\s*include|import\b|from\b|use\b|package\b)', line)]
            body = [f'# candidate-file: {path}']
            if imports:
                body.extend(imports[:12])
            for score, symbol in chosen:
                excerpt = symbol.get('source', '').strip()
                if len(excerpt.encode('utf-8')) > 520:
                    excerpt = focused_excerpt(symbol, task, 520)
                addition = f'\n# nearby-symbol: {symbol["id"]}\n{excerpt}'
                if len(('\n'.join([*body, addition])).encode('utf-8')) > per_path_budget:
                    continue
                body.append(addition)
                if not any(anchor['id'].split('::', 1)[0] == path for anchor in anchors):
                    anchors.append(symbol_summary(symbol) |
                                   {'score': round(score, 3), 'evidence': ['new-symbol-neighbor']})
            block = '\n'.join(body).rstrip() + '\n'
            size = len(block.encode('utf-8'))
            if used + size <= budget:
                blocks.append(block)
                used += size
        sufficient = bool(blocks) and (len(blocks) >= 2 or
                                       Path(selected_paths[0]).suffix.casefold() not in {'.c', '.cc', '.cpp'})
        return {'text': f'# planned-symbol: {target}\n' + '\n'.join(blocks),
                'anchors': anchors, 'included': [anchor['id'] for anchor in anchors],
                'omitted': [], 'reused': [], 'selection': {}, 'intent': 'edit',
                'breadth': 'balanced', 'budget': budget, 'budget_unit': 'UTF-8 bytes',
                'used_bytes': used, 'used_tokens': None, 'estimated_tokens': (used + 3) // 4,
                'receipt_id': None, 'sufficient': sufficient, 'complete': False,
                'coverage': {'missing_identifiers': [] if sufficient else [target]},
                'retrieval': 'local new-symbol file planning; no embeddings or model call'}

    def _rank_task_documents(self, task, query_terms, symbols, limit):
        """Portable scorer used for FTS candidates and FTS-less SQLite builds."""
        requested_identifiers = {value.casefold() for value in task_identifiers(task)}
        documents = []
        frequencies = {term: 0 for term in query_terms}
        for symbol in symbols:
            identity = ' '.join((symbol['name'], symbol['qualname'], symbol['path'], symbol['id']))
            identity_terms = search_terms(identity)
            source_terms = search_terms(symbol.get('source', ''))
            all_terms = set(identity_terms) | set(source_terms)
            for term in query_terms:
                frequencies[term] += term in all_terms
            documents.append((symbol, identity_terms, source_terms))
        count = max(1, len(documents))
        ranked = []
        folded_task = task.casefold()
        for symbol, identity_terms, source_terms in documents:
            name_terms = set(search_terms(symbol['name']))
            path_terms = set(search_terms(symbol['path']))
            identity_set, source_set = set(identity_terms), set(source_terms)
            evidence, score = [], 0.0
            exact_identities = {
                symbol['name'].casefold(), symbol['qualname'].casefold(),
                symbol['id'].casefold(), symbol['id'].split('::', 1)[-1].casefold(),
            }
            exact_requested = requested_identifiers & exact_identities
            if exact_requested:
                # A qualified name supplied by the user is stronger evidence than
                # many incidental mentions in tests or comments.
                score += 80.0
                evidence.append('exact-identifier')
            if symbol['name'].casefold() in folded_task:
                score += 12.0
                evidence.append('exact-name')
            for term in query_terms:
                idf = math.log((count + 1) / (frequencies[term] + 1)) + 1
                weight = (8 if term in name_terms else 5 if term in identity_set
                          else 3 if term in path_terms else 1 if term in source_set else 0)
                if weight:
                    score += weight * idf
                    evidence.append(term)
            if score:
                if symbol.get('is_test') and not {'test', 'tests', '测试'} & set(query_terms):
                    score *= 0.72
                ranked.append((score, symbol, list(dict.fromkeys(evidence))))
        ranked.sort(key=lambda row: (-row[0], row[1]['id']))
        return ranked[:limit], len(ranked)

    def task_matches(self, task, limit=20):
        """Rank symbols for a prose task with an indexed lexical candidate stage."""
        if not task or not task.strip():
            raise AirpError('task must not be empty')
        if not 1 <= limit <= 100:
            raise AirpError('limit must be between 1 and 100')
        self.require_index()
        query_terms = list(dict.fromkeys(search_terms(task)))
        if not query_terms:
            raise AirpError('task has no searchable terms')
        method = 'portable in-process lexical IDF fallback; no embeddings or model call'
        candidate_count = None
        if self.has_fts:
            escaped = [term.replace('"', '""') for term in query_terms]
            match = ' OR '.join(f'"{term}"' for term in escaped)
            candidate_limit = min(500, max(50, limit * 5))
            rows = self.db.execute(
                'SELECT l.data FROM symbol_fts f JOIN symbol_lookup l ON l.sid=f.sid '
                'WHERE symbol_fts MATCH ? ORDER BY bm25(symbol_fts,0.0,8.0,1.0) LIMIT ?',
                (match, candidate_limit)).fetchall()
            symbols = [json.loads(row[0]) for row in rows]
            candidate_count = len(symbols)
            method = 'SQLite FTS5 candidate retrieval plus local lexical reranking; no embeddings or model call'
        else:
            rows = self.db.execute('SELECT data FROM symbol_lookup').fetchall()
            symbols = [json.loads(row[0]) for row in rows]
        exact = self._exact_task_symbols(task)
        symbols = list({symbol['id']: symbol for symbol in [*exact, *symbols]}.values())
        if exact:
            method += '; exact named symbols pinned before reranking'
        ranked, total = self._rank_task_documents(task, query_terms, symbols, limit)
        return {'matches': [symbol_summary(symbol) | {'score': round(score, 3),
                                                       'evidence': evidence[:8]}
                            for score, symbol, evidence in ranked],
                'query_terms': query_terms, 'total': total,
                'candidate_pool': candidate_count,
                'method': method}

    def get(self, symbol_id):
        return self.symbol(symbol_id)

    def relations(self, symbol_id, direction='out', kind=None, limit=50, include_unresolved=True):
        self.symbol(symbol_id)
        if direction not in ('in', 'out'):
            raise AirpError('direction must be in or out')
        if not 1 <= limit <= 200:
            raise AirpError('limit must be between 1 and 200')
        key = 'target' if direction == 'in' else 'source'
        clauses, values = [f'{key}=?'], [symbol_id]
        if kind is not None:
            clauses.append('kind=?')
            values.append(kind)
        if not include_unresolved:
            clauses.append('target IS NOT NULL')
        where = ' AND '.join(clauses)
        total = self.db.execute(f'SELECT COUNT(*) FROM edge_lookup WHERE {where}', values).fetchone()[0]
        rows = self.db.execute(f'SELECT data FROM edge_lookup WHERE {where} LIMIT ?',
                               (*values, limit)).fetchall()
        edges = [json.loads(row[0]) for row in rows]
        compact_edges = [{k: e[k] for k in ('source', 'target', 'name', 'kind', 'line', 'confidence')}
                         for e in edges]
        return {'edges': compact_edges, 'total': total, 'truncated': total > limit,
                'complete': False, 'analysis': 'Static candidates; dynamic edges may be missing.'}

    def refs(self, symbol_id):
        return self.relations(symbol_id, 'in', 'reference')

    def callers(self, symbol_id):
        return self.relations(symbol_id, 'in', 'call')

    def callees(self, symbol_id):
        return self.relations(symbol_id, 'out', 'call')

    def dependencies(self, symbol_id):
        return self.relations(symbol_id)

    def affected(self, symbol_id):
        self.symbol(symbol_id)
        seen, queue = {symbol_id}, [symbol_id]
        while queue:
            current = queue.pop()
            rows = self.db.execute('SELECT source FROM edge_lookup WHERE target=?', (current,)).fetchall()
            for (source,) in rows:
                if source not in seen:
                    seen.add(source)
                    queue.append(source)
        if not seen:
            tests = []
        else:
            placeholders = ','.join('?' for _ in seen)
            rows = self.db.execute(f'SELECT data FROM symbol_lookup WHERE is_test=1 AND sid IN ({placeholders})',
                                   tuple(seen)).fetchall()
            tests = [symbol_summary(json.loads(row[0])) for row in rows]
        return {'tests': tests, 'complete': False, 'safe_scope': 'all',
                'reason': 'Static test candidates are advisory; execution defaults to the full suite.'}

    def relevant_tests(self, symbol_id, task, limit=2):
        """Rank affected tests by task and target evidence instead of returning graph fan-out."""
        target = self.symbol(symbol_id)
        terms = {term for term in search_terms(task) if len(term) >= 3}
        ranked = []
        for summary in self.affected(symbol_id)['tests']:
            symbol = self.symbol(summary['id'])
            identity = ' '.join((symbol['id'], symbol['name'], symbol.get('source', '')))
            identity_terms = set(search_terms(identity))
            score = len(terms & identity_terms)
            if target['name'].casefold() in identity.casefold():
                score += 8
            ranked.append((score, symbol))
        ranked.sort(key=lambda row: (-row[0], row[1]['id']))
        return [symbol_summary(symbol) for score, symbol in ranked[:limit] if score]

    def relevant_imports(self, target):
        rows = self.db.execute('SELECT data FROM import_lookup WHERE path=?',
                               (target['path'],)).fetchall()
        imports = [json.loads(row[0]) for row in rows]
        if target.get('language', 'python') != 'python':
            return [item['source'] for item in imports]
        try:
            loaded = {n.id for n in ast.walk(ast.parse(textwrap.dedent(target['source'])))
                      if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        except SyntaxError:
            return []
        selected = []
        for item in imports:
            try:
                node = ast.parse(item['source']).body[0]
                if isinstance(node, ast.Import):
                    bound = {alias.asname or alias.name.split('.')[0] for alias in node.names}
                else:
                    bound = {alias.asname or alias.name for alias in node.names}
            except (SyntaxError, AttributeError):
                continue
            if loaded & bound:
                selected.append(item['source'])
        return list(dict.fromkeys(selected))

    def save_receipt(self, symbols):
        receipt_id = digest(json.dumps(symbols, sort_keys=True).encode())[:24]
        self.save('receipt:' + receipt_id, {'symbols': symbols, 'created': time.time()})
        rows = self.db.execute("SELECT key,value FROM state WHERE key LIKE 'receipt:%'").fetchall()
        receipts = []
        for key, value in rows:
            try:
                receipts.append((json.loads(value).get('created', 0), key))
            except json.JSONDecodeError:
                receipts.append((0, key))
        cutoff = time.time() - 86400
        ordered = sorted(receipts)
        expired = {key for created, key in ordered if created < cutoff}
        expired.update(key for _, key in ordered[:-100])
        if expired:
            self.db.executemany('DELETE FROM state WHERE key=?',
                                [(key,) for key in sorted(expired)])
        return receipt_id

    def _shape_task_response(self, result, response_mode, encoding, budget, intent):
        if response_mode == 'full':
            return result
        anchors = [{key: anchor[key] for key in ('id', 'score', 'evidence')}
                   for anchor in result['anchors']]
        compact_result = {'text': result['text'], 'anchors': anchors,
                          'receipt_id': result['receipt_id'],
                          'sufficient': result['sufficient'], 'delivery': 'symbol-pack'}
        for key in ('omitted', 'reused'):
            if result[key]:
                compact_result[key] = result[key]
        if result.get('coverage', {}).get('missing_identifiers'):
            compact_result['coverage'] = result['coverage']
        if response_mode == 'compact' or not result['anchors'] or result['reused']:
            return compact_result

        # A tiny one-file answer can be cheaper than a structured symbol block.
        # Only bypass for read-only intents and when every delivered symbol came
        # from that file, so graph evidence is not silently discarded.
        if intent not in ('minimal', 'understand') or len(result['anchors']) != 1:
            return compact_result
        delivered = result['included']
        if not delivered or result['omitted']:
            return compact_result
        symbols = [self.symbol(sid) for sid in delivered]
        paths = {symbol['path'] for symbol in symbols}
        if len(paths) != 1:
            return compact_result
        raw = self.path(next(iter(paths))).read_bytes()
        file_text, _ = decode(raw)
        fits = (len(encoding.encode(file_text)) <= budget
                if encoding else len(raw) <= budget)
        if not fits:
            return compact_result
        direct_result = {'text': file_text, 'anchors': anchors,
                         'receipt_id': result['receipt_id'], 'sufficient': True,
                         'delivery': 'full-file-cost-bypass'}
        measure = (lambda value: len(encoding.encode(json.dumps(
            value, ensure_ascii=False, separators=(',', ':'))))) if encoding else (
            lambda value: len(json.dumps(value, ensure_ascii=False,
                                         separators=(',', ':')).encode('utf-8')))
        return direct_result if measure(direct_result) < measure(compact_result) else compact_result

    def task_context(self, task, budget=3000, intent='understand', breadth='balanced',
                     known_hashes=None, known_symbols=None, receipt_id=None,
                     budget_unit='bytes', tokenizer='o200k_base', response_mode='full'):
        """Compile one context pack directly from a prose task.

        Ranking fuses lexical evidence with the existing symbol graph. Selection uses
        a deterministic diversity penalty so near-duplicate symbols do not consume the
        pack before a second relevant area is represented.
        """
        if not 1 <= budget <= 1000000:
            raise AirpError('budget must be between 1 and 1000000')
        if budget_unit not in ('bytes', 'tokens'):
            raise AirpError('budget_unit must be bytes or tokens')
        if response_mode not in ('full', 'compact', 'auto'):
            raise AirpError('response_mode must be full, compact or auto')
        if intent not in ('minimal', 'understand', 'edit', 'impact', 'test'):
            raise AirpError('intent must be minimal, understand, edit, impact or test')
        anchor_limits = {'narrow': 1, 'balanced': 2, 'broad': 3}
        if breadth not in anchor_limits:
            raise AirpError('breadth must be narrow, balanced or broad')
        encoding = None
        if budget_unit == 'tokens':
            try:
                import tiktoken
            except ImportError as error:
                raise AirpError('Token budgets require the optional tokens dependency') from error
            try:
                os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                                      str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
                encoding = tiktoken.get_encoding(tokenizer)
            except Exception as error:
                raise AirpError(f'Tokenizer {tokenizer} is unavailable; prewarm its local cache') from error

        match_result = self.task_matches(task, limit=30)
        profile = task_profile(task)
        matches = match_result['matches']
        if not matches:
            result = {'text': '', 'anchors': [], 'included': [], 'omitted': [], 'reused': [],
                      'intent': intent, 'breadth': breadth, 'budget': budget,
                      'budget_unit': f'{tokenizer} tokens' if encoding else 'UTF-8 bytes',
                      'used_bytes': 0, 'used_tokens': 0 if encoding else None,
                      'estimated_tokens': 0, 'receipt_id': None, 'sufficient': False,
                      'complete': False, 'retrieval': match_result['method']}
            return self._shape_task_response(result, response_mode, encoding, budget, intent)
        edit_names = {target.rsplit('.', 1)[-1].casefold()
                      for target in task_edit_targets(task)} if intent == 'edit' else set()
        preferred = [match for match in matches if match['name'].casefold() in edit_names]
        if preferred:
            matches = [*preferred, *(match for match in matches if match not in preferred)]
        threshold = matches[0]['score'] * 0.30
        anchors = []
        for match in matches:
            if match['score'] < threshold:
                break
            if any(match['id'].startswith(anchor['id'] + '.') or
                   anchor['id'].startswith(match['id'] + '.') for anchor in anchors):
                continue
            anchors.append(match)
            effective_limit = 1 if preferred else anchor_limits[breadth]
            if len(anchors) == effective_limit:
                break

        candidates = {}
        required_obligations = {
            anchor['id']: f"target:{anchor['id']}" for anchor in anchors
        }

        def offer(sid, score, role, detail):
            previous = candidates.get(sid)
            if previous is None or score > previous[0]:
                candidates[sid] = (score, role, detail)

        for rank, anchor in enumerate(anchors):
            sid = anchor['id']
            symbol = self.symbol(sid)
            anchor_detail = ('outline' if intent in ('impact', 'test') else
                             'full' if rank == 0 or 'exact-name' in anchor['evidence'] else 'outline')
            offer(sid, 1200 - rank * 80 + min(anchor['score'], 100), 'task-anchor', anchor_detail)
            if symbol.get('parent'):
                offer(symbol['parent'], 900 - rank * 20, 'parent', 'outline')
            if intent in ('understand', 'edit', 'impact'):
                for edge in self.relations(sid, 'out', limit=200,
                                           include_unresolved=False)['edges']:
                    dependency_detail = 'full' if profile['behavior_task'] else 'outline'
                    offer(edge['target'], 720 - rank * 20 + (12 if edge['kind'] == 'call' else 4),
                          'dependency', dependency_detail)
                    if profile['behavior_task']:
                        required_obligations[edge['target']] = (
                            f"behavior-dependency:{edge['target']}")
            if intent in ('edit', 'impact'):
                incoming = self.relations(sid, 'in', limit=200,
                                          include_unresolved=False)['edges']
                incoming.sort(key=lambda edge: (
                    edge['source'].split('::', 1)[0] != symbol['path'], edge['source']))
                for edge in incoming[:4]:
                    offer(edge['source'], 640 - rank * 20, 'caller', 'outline')
            if intent in ('edit', 'impact', 'test'):
                detail = 'full' if intent in ('edit', 'test') else 'outline'
                relevant_tests = self.relevant_tests(sid, task)
                if intent == 'edit' and relevant_tests:
                    direct_test = relevant_tests[0]['id']
                    required_obligations[direct_test] = f'direct-test:{direct_test}'
                for test in relevant_tests:
                    offer(test['id'], 820 - rank * 20, 'test', detail)

        known_hashes = set(known_hashes or [])
        known_symbols = dict(known_symbols or {})
        if receipt_id:
            receipt = self.load('receipt:' + receipt_id)
            if receipt:
                known_symbols.update(receipt.get('symbols', {}))
        content, included, omitted, reused, selected_terms = '', [], [], [], []
        used = 0
        pending = dict(candidates)
        rendered = {}
        pruned_low_value = 0
        marginal_value_floor = 2.0
        role_cost = {
            'task-anchor': 0,
            'test': 40,
            'dependency': 100,
            'parent': 120,
            'caller': 160,
        }
        while pending:
            choices = []
            low_value = set()
            for sid, (base_score, role, detail) in pending.items():
                try:
                    symbol = self.symbol(sid)
                except AirpError:
                    omitted.append(sid)
                    continue
                body = symbol['source'] if detail == 'full' else symbol_outline(symbol)
                if (detail == 'full' and role == 'task-anchor' and
                        len(body.encode('utf-8')) > max(1400, budget // 2)):
                    body = focused_excerpt(symbol, task, max(700, min(2000, budget // 2)))
                terms = set(search_terms(body))
                overlap = max((len(terms & prior) / max(1, len(terms | prior))
                               for prior in selected_terms), default=0)
                novelty = max(0.15, 1.0 - overlap)
                block_size = len(
                    f"\n# {role}: {sid}\n{body.rstrip()}\n".encode('utf-8'))
                marginal_value = (
                    (base_score - 260 * overlap) * novelty
                    / max(1, block_size + role_cost.get(role, 120))
                )
                if (sid not in required_obligations and role != 'task-anchor'
                        and marginal_value < marginal_value_floor):
                    low_value.add(sid)
                    continue
                requirement_rank = (
                    2 if role == 'task-anchor'
                    else 1 if sid in required_obligations
                    else 0
                )
                choices.append((requirement_rank, marginal_value, base_score, sid, role,
                                detail, symbol, body, terms))
            for sid in low_value:
                pending.pop(sid, None)
                omitted.append(sid)
                pruned_low_value += 1
            for sid in set(pending) - {choice[3] for choice in choices}:
                pending.pop(sid, None)
            if not choices:
                break
            (_, marginal_value, base_score, sid, role,
             detail, symbol, body, terms) = max(
                choices, key=lambda row: (row[0], row[1], row[2], row[3]))
            pending.pop(sid)
            if known_symbols.get(sid) == symbol['hash'] or symbol['hash'] in known_hashes:
                reused.append(sid)
                selected_terms.append(terms)
                continue
            block = f"\n# {role}: {sid}\n{body.rstrip()}\n"
            block_size = len(block.encode('utf-8'))
            fits = (len(encoding.encode(content + block)) <= budget
                    if encoding else used + block_size <= budget)
            if fits:
                content += block
                used += block_size
                included.append(sid)
                rendered[sid] = {
                    'role': role,
                    'detail': detail,
                    'score': round(base_score, 3),
                    'marginal_value': round(marginal_value, 3),
                }
                selected_terms.append(terms)
            else:
                omitted.append(sid)

        imports = []
        for anchor in anchors:
            imports.extend(self.relevant_imports(self.symbol(anchor['id'])))
        imports = list(dict.fromkeys(imports))
        if imports:
            block = '\n# imports\n' + '\n'.join(imports) + '\n'
            block_size = len(block.encode('utf-8'))
            fits = (len(encoding.encode(content + block)) <= budget
                    if encoding else used + block_size <= budget)
            if fits:
                content += block
                used += block_size

        receipt_symbols = dict(known_symbols)
        for sid in [*included, *reused]:
            receipt_symbols[sid] = self.symbol(sid)['hash']
        new_receipt = self.save_receipt(receipt_symbols)
        used_tokens = len(encoding.encode(content)) if encoding else None
        anchor_ids = [anchor['id'] for anchor in anchors]
        represented = set(included) | set(reused)
        required_identifiers = [name for name in task_identifiers(task)
                                if not is_external_identifier(name)]
        searchable_evidence = identifier_key(content + '\n' + '\n'.join(anchor_ids))
        missing_identifiers = [
            name for name in required_identifiers
            if identifier_key(name) not in searchable_evidence]
        missing_evidence = [
            label for sid, label in required_obligations.items()
            if sid not in represented
        ]
        missing_evidence.extend(f'identifier:{name}' for name in missing_identifiers)
        evidence_state = 'sufficient' if not missing_evidence else 'blocked-partial'
        result = {'text': content, 'anchors': anchors, 'included': included,
                  'omitted': list(dict.fromkeys(omitted)), 'reused': reused,
                  'selection': rendered, 'intent': intent, 'breadth': breadth, 'budget': budget,
                  'budget_unit': f'{tokenizer} tokens' if encoding else 'UTF-8 bytes',
                  'used_bytes': used, 'used_tokens': used_tokens,
                  'estimated_tokens': used_tokens if encoding else (used + 3) // 4,
                  'token_estimate': ('exact tokenizer count' if encoding
                                     else 'bytes/4; model tokenizer may differ'),
                  'receipt_id': new_receipt,
                  'sufficient': evidence_state == 'sufficient',
                  'evidence_state': evidence_state,
                  'routing_cost': {
                      'pruned_low_value': pruned_low_value,
                      'marginal_value_floor': marginal_value_floor,
                  },
                  'evidence': {
                      'required': list(required_obligations.values()),
                      'represented': [
                          label for sid, label in required_obligations.items()
                          if sid in represented
                      ],
                      'missing': missing_evidence,
                  },
                  'coverage': {'required_identifiers': required_identifiers,
                               'missing_identifiers': missing_identifiers},
                  'complete': False, 'retrieval': match_result['method']}
        return self._shape_task_response(result, response_mode, encoding, budget, intent)

    def context(self, symbol_id, budget=3000, intent='understand', known_hashes=None,
                known_symbols=None, receipt_id=None, budget_unit='bytes', tokenizer='o200k_base'):
        if not 1 <= budget <= 1000000:
            raise AirpError('budget must be between 1 and 1000000')
        if budget_unit not in ('bytes', 'tokens'):
            raise AirpError('budget_unit must be bytes or tokens')
        encoding = None
        if budget_unit == 'tokens':
            try:
                import tiktoken
            except ImportError as error:
                raise AirpError('Token budgets require the optional tokens dependency') from error
            try:
                os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                                      str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
                encoding = tiktoken.get_encoding(tokenizer)
            except Exception as error:
                raise AirpError(f'Tokenizer {tokenizer} is unavailable; prewarm its local cache') from error
        if intent not in ('minimal', 'understand', 'edit', 'impact', 'test'):
            raise AirpError('intent must be minimal, understand, edit, impact or test')
        known_hashes = set(known_hashes or [])
        known_symbols = dict(known_symbols or {})
        if receipt_id:
            receipt = self.load('receipt:' + receipt_id)
            if receipt:
                known_symbols.update(receipt.get('symbols', {}))
        target = self.symbol(symbol_id)
        outgoing_edges = (self.relations(symbol_id, direction='out', limit=200,
                                         include_unresolved=False)['edges']
                          if intent in ('understand', 'edit', 'impact') else [])
        caller_edges = (self.relations(symbol_id, direction='in', limit=200,
                                       include_unresolved=False)['edges']
                        if intent in ('edit', 'impact') else [])
        tests = ([s['id'] for s in self.affected(symbol_id)['tests']]
                 if intent in ('edit', 'impact', 'test') else [])
        candidates = [(1000, 'target', symbol_id,
                       'outline' if intent in ('impact', 'test') else 'full')]
        if target['parent']:
            candidates.append((900, 'parent', target['parent'], 'outline'))
        if intent in ('understand', 'edit', 'impact'):
            counts = {}
            for edge in outgoing_edges:
                counts[edge['target']] = counts.get(edge['target'], 0) + (3 if edge['kind'] == 'call' else 1)
            candidates += [(700 + min(weight, 30), 'dependency', sid, 'outline')
                           for sid, weight in counts.items()]
        if intent in ('edit', 'impact'):
            counts = {}
            for edge in caller_edges:
                counts[edge['source']] = counts.get(edge['source'], 0) + 1
            candidates += [(600 + min(weight, 30), 'caller', sid, 'outline')
                           for sid, weight in counts.items()]
        if intent in ('edit', 'impact', 'test'):
            detail = 'full' if intent == 'test' else 'outline'
            candidates += [(800, 'test', sid, detail) for sid in tests]
        candidates.sort(key=lambda item: (-item[0], item[2]))
        content, included, omitted, reused, seen = '', [], [], [], set()
        used = 0
        # A byte ceiling is conservative for byte-level tokenizers; no exact tokenizer claim.
        for score, role, sid, detail in candidates:
            if sid in seen:
                continue
            seen.add(sid)
            try:
                symbol = self.symbol(sid)
            except AirpError:
                omitted.append(sid)
                continue
            if known_symbols.get(sid) == symbol['hash'] or symbol['hash'] in known_hashes:
                reused.append(sid)
                continue
            body = symbol['source'] if detail == 'full' else symbol_outline(symbol)
            block = f"\n# {role}: {sid}\n{body.rstrip()}\n"
            block_size = len(block.encode('utf-8'))
            fits = (len(encoding.encode(content + block)) <= budget
                    if encoding else used + block_size <= budget)
            if fits:
                content += block
                included.append(sid)
                used += block_size
            else:
                omitted.append(sid)
        imports = '\n'.join(self.relevant_imports(target))
        if imports:
            block = f'\n# imports\n{imports}\n'
            block_size = len(block.encode('utf-8'))
            fits = (len(encoding.encode(content + block)) <= budget
                    if encoding else used + block_size <= budget)
            if fits:
                content += block
                used += block_size
        receipt_symbols = dict(known_symbols)
        for sid in [*included, *reused]:
            try:
                receipt_symbols[sid] = self.symbol(sid)['hash']
            except AirpError:
                pass
        new_receipt = self.save_receipt(receipt_symbols)
        used_tokens = len(encoding.encode(content)) if encoding else None
        estimated_tokens = used_tokens if encoding else (used + 3) // 4
        return dict(text=content, included=included, omitted=omitted, reused=reused,
                    target_included=symbol_id in included, target_reused=symbol_id in reused,
                    intent=intent, budget=budget,
                    budget_unit=(f'{tokenizer} tokens' if encoding else 'UTF-8 bytes'),
                    used_bytes=used, used_tokens=used_tokens, estimated_tokens=estimated_tokens,
                    token_estimate=('exact tokenizer count' if encoding else 'bytes/4; model tokenizer may differ'),
                    receipt_id=new_receipt, sufficient=False, complete=False)

    def prepare(self, symbol_id=None, query=None, budget=3000, known_hashes=None,
                known_symbols=None, receipt_id=None, budget_unit='bytes', tokenizer='o200k_base'):
        if symbol_id is None:
            if not query:
                raise AirpError('Provide symbol_id or query')
            matches = self.find(query=query, limit=10)
            exact = [item for item in matches['symbols'] if item['name'].casefold() == query.casefold()]
            if len(exact) == 1:
                symbol_id = exact[0]['id']
            elif matches['total'] == 1:
                symbol_id = matches['symbols'][0]['id']
            else:
                return {'ready': False, 'reason': 'ambiguous_or_missing', **matches}
        target = self.symbol(symbol_id)
        context = self.context(symbol_id, budget=budget, intent='edit', known_hashes=known_hashes,
                               known_symbols=known_symbols, receipt_id=receipt_id,
                               budget_unit=budget_unit, tokenizer=tokenizer)
        return {'ready': context['target_included'] or context['target_reused'],
                'target': symbol_summary(target), 'hash': target['hash'], 'context': context}

    def warm(self, languages):
        return warm_languages(languages)

    def active(self):
        tx = self.load('active')
        if tx is None:
            raise AirpError('No active transaction; run begin first')
        return tx

    def begin(self):
        if self.load('active') is not None:
            raise AirpError('A transaction is already active')
        self.graph()
        tx = dict(id=uuid.uuid4().hex, files={}, started=time.time(), tests=None)
        self.save('active', tx)
        return {'transaction': tx['id'], 'status': 'active'}

    def atomic_write(self, path, raw):
        mode = path.stat().st_mode
        fd, name = tempfile.mkstemp(prefix='.airp-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, mode)
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def update(self, symbol_id, source, expected_hash, dry_run=True):
        symbol = self.symbol(symbol_id)
        if symbol.get('analysis_stale'):
            raise AirpError('Symbol comes from the last good index; fix syntax and reindex before editing')
        if not symbol.get('editable', symbol['kind'] in ('function', 'async_function')):
            raise AirpError('This backend provides read-only symbols; use an ordinary language-aware editor')
        if symbol['hash'] != expected_hash:
            raise AirpError('Symbol hash mismatch; retrieve the current symbol first')
        replacement = textwrap.dedent(source).strip() + '\n'
        tree = ast.parse(replacement)
        if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
            raise AirpError('Replacement must contain exactly one function')
        node = tree.body[0]
        if node.name != symbol['name'] or isinstance(node, ast.AsyncFunctionDef) != (symbol['kind'] == 'async_function'):
            raise AirpError('Replacement must preserve the function name and async kind')
        path = self.path(symbol['path'])
        raw = path.read_bytes()
        text, encoding = decode(raw)
        lines = text.splitlines(keepends=True)
        line = lines[symbol['definition_line'] - 1]
        indent = line[:len(line) - len(line.lstrip())]
        newline = '\r\n' if b'\r\n' in raw else '\n'
        replacement = textwrap.indent(replacement, indent).replace('\n', newline)
        updated = ''.join(lines[:symbol['start'] - 1]) + replacement + ''.join(lines[symbol['end']:])
        compile(updated, str(path), 'exec')
        new_raw = updated.encode(encoding)
        diff = ''.join(difflib.unified_diff(text.splitlines(True), updated.splitlines(True),
                                          fromfile=symbol['path'], tofile=symbol['path']))
        if dry_run:
            return {'dry_run': True, 'diff': diff}
        tx = self.active()
        previous = tx['files'].get(symbol['path'])
        if previous and digest(raw) != previous['after_hash']:
            raise AirpError('Transaction conflict: file was changed outside AIRP')
        entry = previous or {'before': base64.b64encode(raw).decode()}
        entry['recovery_hashes'] = list(dict.fromkeys(entry.get('recovery_hashes', []) + [digest(raw), digest(new_raw)]))
        entry['after_hash'] = digest(new_raw)
        tx['files'][symbol['path']] = entry
        tx['tests'] = None
        # Save recovery intent before touching source. A crash cannot erase the backup.
        self.save('active', tx)
        self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        if path.read_bytes() != raw:
            raise AirpError('Concurrent external edit detected before write')
        self.atomic_write(path, new_raw)
        self.index()
        return {'dry_run': False, 'transaction': tx['id'], 'diff': diff}

    def diff(self):
        tx = self.active()
        output = ''
        for relative, entry in tx['files'].items():
            before, _ = decode(base64.b64decode(entry['before']))
            after, _ = decode(self.path(relative).read_bytes())
            output += ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                  fromfile=relative, tofile=relative))
        return {'transaction': tx['id'], 'diff': output}

    def verify(self):
        graph = build(self.root)
        blocking = [d for d in graph['diagnostics'] if d.get('language', 'python') == 'python']
        return {'ok': not blocking, 'files': len(graph['files']),
                'diagnostics': graph['diagnostics'], 'checks': ['Python parse', 'Python compile'],
                'not_checked': ['type checking', 'runtime behavior', 'dynamic references',
                                'non-Python semantic validation']}

    def test(self, runner='unittest', symbol_id=None, timeout=60, output_mode='compact'):
        if runner not in ('unittest', 'pytest') or not 1 <= timeout <= 600:
            raise AirpError('runner must be unittest or pytest; timeout must be 1..600')
        if output_mode not in ('compact', 'full'):
            raise AirpError('output_mode must be compact or full')
        before = self.fingerprint()
        candidates = self.affected(symbol_id) if symbol_id else None
        if runner == 'unittest':
            command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        else:
            command = [sys.executable, '-m', 'pytest', '-q']
        if runner == 'unittest' and not (self.root / 'tests').is_dir():
            raise AirpError('unittest runner requires a tests directory; use pytest for other layouts')
        started = time.monotonic()
        try:
            with tempfile.TemporaryFile() as output:
                result = subprocess.run(command, cwd=self.root, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=subprocess.STDOUT,
                                        timeout=timeout, env={**os.environ, 'PYTHONIOENCODING': 'utf-8',
                                                              'PYTHONDONTWRITEBYTECODE': '1'})
                size = output.tell()
                read_limit = 20000 if output_mode == 'full' else 6000
                output.seek(max(0, size - read_limit))
                log = output.read().decode('utf-8', errors='replace')
            ok = result.returncode == 0 and not (runner == 'unittest' and 'Ran 0 tests' in log)
            tests_match = re.search(r'Ran (\d+) tests?', log) if runner == 'unittest' else None
            tests_run = int(tests_match.group(1)) if tests_match else None
            if ok and output_mode == 'compact':
                lines = [line for line in log.splitlines() if line.strip()]
                if runner == 'unittest':
                    summary = [line for line in lines if line.startswith(('Ran ', 'OK'))][-2:]
                else:
                    summary = lines[-1:]
                returned_output = '\n'.join(summary)
            else:
                returned_output = log
            report = dict(ok=ok, returncode=result.returncode, tests_run=tests_run,
                          output=returned_output, output_bytes=len(returned_output.encode('utf-8')),
                          output_truncated=size > read_limit,
                          output_omitted_bytes=max(0, size - len(returned_output.encode('utf-8'))))
        except subprocess.TimeoutExpired:
            report = dict(ok=False, returncode=None, output='Test timeout exceeded')
        after = self.fingerprint()
        report.update(scope='all', candidates=candidates, elapsed_seconds=time.monotonic() - started,
                      source_unchanged=before == after)
        report['ok'] = report['ok'] and before == after
        tx = self.load('active')
        if tx is not None:
            tx['tests'] = {'ok': report['ok'], 'fingerprint': after}
            self.save('active', tx)
        return report

    def check_conflicts(self, tx):
        for relative, entry in tx['files'].items():
            path = self.path(relative)
            actual = digest(path.read_bytes()) if path.exists() else None
            before_hash = digest(base64.b64decode(entry['before']))
            if actual not in [entry['after_hash'], before_hash, *entry.get('recovery_hashes', [])]:
                raise AirpError(f'External change conflicts with transaction: {relative}')

    def rollback(self):
        tx = self.active()
        self.check_conflicts(tx)
        for relative, entry in tx['files'].items():
            self.atomic_write(self.path(relative), base64.b64decode(entry['before']))
        self.save('history:' + tx['id'], tx | {'status': 'rolled_back'})
        self.save('active', None)
        self.index()
        return {'transaction': tx['id'], 'status': 'rolled_back'}

    def commit(self):
        tx = self.active()
        self.check_conflicts(tx)
        if not self.verify()['ok']:
            raise AirpError('Verification failed; fix or rollback')
        if not tx['tests'] or not tx['tests']['ok'] or tx['tests']['fingerprint'] != self.fingerprint():
            raise AirpError('Run passing tests on the current source before commit')
        self.save('history:' + tx['id'], tx | {'status': 'committed'})
        self.save('active', None)
        self.index()
        return {'transaction': tx['id'], 'status': 'committed', 'git_commit': False}

    def status(self):
        tx = self.load('active')
        manifest = self.load('manifest')
        return {'root': str(self.root), 'indexed': manifest is not None,
                'stale': manifest is not None and self.is_stale(manifest),
                'capabilities': None if manifest is None else manifest.get('capabilities'),
                'diagnostics': [] if manifest is None else manifest.get('diagnostics', []),
                'transaction': None if tx is None else {k: v for k, v in tx.items() if k != 'files'} |
                {'files': list(tx['files'])}}

    def metrics(self):
        rows = self.db.execute('SELECT tool, COUNT(*), SUM(response_bytes), SUM(elapsed_ms), SUM(ok) FROM events GROUP BY tool').fetchall()
        return {'tools': [dict(zip(('tool', 'calls', 'response_bytes', 'elapsed_ms', 'successful_calls'), r)) for r in rows],
                'note': 'Local tool telemetry only; model tokens, task success and costs require an agent experiment.'}

    @contextmanager
    def locked(self, exclusive=True):
        lock_path = self.state / 'operation.lock'
        if lock_path.is_symlink():
            raise AirpError('Lock file must not be a symlink')
        with lock_path.open('a+b') as handle:
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + 30
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK
                        msvcrt.locking(handle.fileno(), mode, 1)
                    else:
                        import fcntl
                        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                        fcntl.flock(handle, mode | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise AirpError('Repository is busy; retry after the active operation finishes')
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def execute(self, tool, **arguments):
        read_only = {'find', 'task_matches', 'get', 'relations', 'refs', 'callers', 'callees',
                     'dependencies', 'affected', 'status', 'metrics'}
        with self.locked(exclusive=tool not in read_only):
            self._index_checked = False
            self._manifest = None
            return self._execute(tool, _read_only=tool in read_only, **arguments)

    def _execute(self, tool, _read_only=False, **arguments):
        allowed = {'index', 'find', 'task_matches', 'get', 'relations', 'refs', 'callers', 'callees', 'dependencies', 'affected',
                   'context', 'task_context', 'smart_context', 'prepare', 'warm', 'begin', 'update', 'diff', 'verify', 'test', 'commit',
                   'rollback', 'status', 'metrics'}
        if tool not in allowed:
            raise AirpError(f'Unknown tool: {tool}')
        started = time.monotonic()
        if not _read_only:
            self.db.execute('BEGIN IMMEDIATE')
        try:
            result = getattr(self, tool)(**arguments)
            self.db.execute('INSERT INTO events(tool,elapsed_ms,response_bytes,ok,created) VALUES (?,?,?,?,?)',
                            (tool, (time.monotonic() - started) * 1000,
                             len(json.dumps(result, ensure_ascii=False).encode()), 1, time.time()))
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            self.db.execute('INSERT INTO events(tool,elapsed_ms,response_bytes,ok,created) VALUES (?,?,?,?,?)',
                            (tool, (time.monotonic() - started) * 1000, 0, 0, time.time()))
            self.db.commit()
            raise
