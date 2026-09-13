"""Incremental, pluggable source index with conservative relationships."""
import ast
import hashlib
import io
import os
from functools import lru_cache
from pathlib import Path
import re
import sys
import tokenize


IGNORED = {'.git', '.airp', '.venv', 'venv', '__pycache__', 'node_modules',
           'build', 'dist', '.tox', '.mypy_cache', '.pytest_cache', 'vendor'}
LANGUAGES = {
    '.py': 'python', '.pyi': 'python',
    '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript', '.tsx': 'tsx',
    '.go': 'go', '.rs': 'rust', '.java': 'java',
    '.c': 'c', '.h': 'c', '.cc': 'cpp', '.cpp': 'cpp', '.cxx': 'cpp', '.hpp': 'cpp',
    '.cs': 'csharp', '.rb': 'ruby', '.php': 'php',
}
GRAPH_SCHEMA = 12


def is_test_path(path):
    """Recognize common test locations across supported language ecosystems."""
    candidate = Path(path)
    parts = {part.casefold() for part in candidate.parts}
    stem = candidate.stem.casefold()
    return ('tests' in parts or 'test' in parts or '__tests__' in parts or
            stem in ('test', 'tests', 'spec') or
            stem.startswith(('test_', 'test-', 'test.')) or
            stem.endswith(('_test', '-test', '.test', '_spec', '-spec', '.spec')))


def enclosing_object_property(raw, start_byte):
    """Recover the key for JavaScript `{key: {value() {...}}}` definitions."""
    prefix = raw[max(0, start_byte - 240):start_byte].decode('utf-8', errors='ignore')
    match = re.search(r'([A-Za-z_$][\w$]*)\s*:\s*\{\s*$', prefix)
    return match.group(1) if match else None


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding), encoding


def normalize_newlines(text):
    return text.replace('\r\n', '\n').replace('\r', '\n')


@lru_cache(maxsize=512)
def _optional_language(filename):
    try:
        from tree_sitter_language_pack import (PackConfig, configure, detect_language,
                                                downloaded_languages)
        configure(PackConfig(cache_dir=_tree_sitter_cache()))
        detected = detect_language(filename)
        return detected if detected in set(downloaded_languages()) else None
    except (ImportError, RuntimeError):
        return None


def language_for(path):
    candidate = Path(path)
    suffix = candidate.suffix.casefold()
    explicit = LANGUAGES.get(suffix)
    probe = 'file' + suffix if suffix else candidate.name.casefold()
    return explicit or _optional_language(probe)


def sources(root):
    root = Path(root)
    ignore_file = root / '.airpignore'
    configured = []
    if ignore_file.is_file():
        configured = [line.strip().replace('\\', '/').strip('/')
                      for line in ignore_file.read_text(encoding='utf-8').splitlines()
                      if line.strip() and not line.lstrip().startswith('#')]

    def configured_ignore(path):
        relative = path.relative_to(root).as_posix()
        return any(relative == pattern or relative.startswith(pattern + '/') or
                   Path(relative).match(pattern) for pattern in configured)

    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and
                         not (Path(folder) / d).is_symlink() and
                         not (Path(folder) / d / 'pyvenv.cfg').exists() and
                         not configured_ignore(Path(folder) / d))
        for name in sorted(names):
            path = Path(folder) / name
            if language_for(path) and not path.is_symlink() and not configured_ignore(path):
                yield path


def module_name(path):
    bits = Path(path).with_suffix('').parts
    if bits[-1] == '__init__':
        bits = bits[:-1]
    return '.'.join(bits)


class Extractor(ast.NodeVisitor):
    def __init__(self, path, text):
        self.path, self.lines = path, text.splitlines(keepends=True)
        self.stack, self.symbols, self.edges, self.imports = [], [], [], []
        self.bindings = {}

    def definition(self, node, kind):
        decorators = []
        for decorator in node.decorator_list:
            try:
                decorators.append(ast.unparse(decorator).rsplit('.', 1)[-1].casefold())
            except Exception:
                pass
        if kind in ('function', 'async_function') and 'overload' in decorators:
            # Runtime behavior lives in the implementation following its typing
            # overloads. Indexing every stub creates duplicate stable addresses
            # and previously caused the entire module to be discarded.
            return
        qualname = '.'.join([s['name'] for s in self.stack] + [node.name])
        sid = f'{self.path}::{qualname}'
        ambiguous_local = False
        if any(s['id'] == sid for s in self.symbols):
            accessor = next((name for name in decorators if name in ('setter', 'deleter')), None)
            if accessor:
                # A property getter and its setter/deleter intentionally share a
                # Python function name. Give accessors distinct semantic IDs so
                # one property does not invalidate every symbol in a large module.
                qualname += f'.{accessor}'
                sid = f'{self.path}::{qualname}'
            elif self.stack and self.stack[-1]['kind'] in ('function', 'async_function'):
                # Branch-local helpers can legitimately repeat a name inside one
                # function. Keep the enclosing production symbol and expose the
                # helpers as read-only, ordinal locations.
                base = sid
                ordinal = 2 + sum(item['id'].startswith(base + '#local')
                                  for item in self.symbols)
                qualname += f'#local{ordinal}'
                sid = f'{self.path}::{qualname}'
                ambiguous_local = True
            else:
                raise ValueError(f'Duplicate definition: {sid}; ambiguous edits are disabled')
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        code = normalize_newlines(''.join(self.lines[start - 1:node.end_lineno]))
        symbol = dict(id=sid, path=self.path, name=node.name, qualname=qualname,
                      kind=kind, start=start, end=node.end_lineno, column=node.col_offset,
                      definition_line=node.lineno, source=code, hash=digest(code.encode()),
                      parent=self.stack[-1]['id'] if self.stack else None,
                      is_test=is_test_path(self.path) or node.name.startswith('test'),
                      language='python', backend='python-ast',
                      editable=kind in ('function', 'async_function') and not ambiguous_local,
                      signature=None, analysis_stale=False)
        self.symbols.append(symbol)
        self.stack.append(symbol)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self.definition(node, 'function')

    def visit_AsyncFunctionDef(self, node):
        self.definition(node, 'async_function')

    def visit_ClassDef(self, node):
        self.definition(node, 'class')

    def module_assignment(self, node, targets):
        """Index simple module bindings without turning local state into symbols."""
        if self.stack:
            return
        code = normalize_newlines(''.join(self.lines[node.lineno - 1:node.end_lineno]))
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            sid = f'{self.path}::{target.id}'
            if any(symbol['id'] == sid for symbol in self.symbols):
                continue
            self.symbols.append(dict(
                id=sid, path=self.path, name=target.id, qualname=target.id,
                kind='constant' if target.id.isupper() else 'variable',
                start=node.lineno, end=node.end_lineno, column=node.col_offset,
                definition_line=node.lineno, source=code, hash=digest(code.encode()),
                parent=None, is_test=is_test_path(self.path), language='python', backend='python-ast',
                editable=False, signature=None, analysis_stale=False))

    def visit_Assign(self, node):
        self.module_assignment(node, node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self.module_assignment(node, [node.target])
        self.generic_visit(node)

    def visit_Import(self, node):
        for item in node.names:
            local = item.asname or item.name.split('.')[0]
            target = item.name if item.asname else local
            if not self.stack:
                self.bindings[local] = target
            self.imports.append(dict(path=self.path, line=node.lineno, module=item.name,
                                     source=ast.unparse(node), language='python'))

    def visit_ImportFrom(self, node):
        base = node.module or ''
        if node.level:
            package = module_name(self.path).split('.')
            if Path(self.path).name != '__init__.py':
                package = package[:-1]
            package = package[:len(package) - node.level + 1]
            base = '.'.join(package + ([base] if base else []))
        for item in node.names:
            if not self.stack:
                self.bindings[item.asname or item.name] = '.'.join(filter(None, [base, item.name]))
        self.imports.append(dict(path=self.path, line=node.lineno, module=base,
                                 source=ast.unparse(node), language='python'))

    def edge(self, node, kind, name):
        self.edges.append(dict(source=self.stack[-1]['id'] if self.stack else self.path + '::<module>',
                               path=self.path, line=node.lineno, name=name, kind=kind,
                               target=None, confidence='unresolved'))

    def visit_Call(self, node):
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            self.edge(node, 'call', ast.unparse(node.func))
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.edge(node, 'reference', node.id)

    def visit_Attribute(self, node):
        if isinstance(node.ctx, ast.Load):
            self.edge(node, 'reference', ast.unparse(node))
        self.generic_visit(node)


def file_record(root, path, raw, language):
    stat = path.stat()
    return dict(path=path.relative_to(root).as_posix(), hash=digest(raw), size=len(raw),
                mtime_ns=stat.st_mtime_ns, language=language,
                backend='python-ast' if language == 'python' else 'tree-sitter')


def analyze_python(relative, raw):
    text, _ = decode(raw)
    tree = ast.parse(text, filename=relative)
    compile(tree, relative, 'exec')
    extractor = Extractor(relative, text)
    extractor.visit(tree)
    return extractor.symbols, extractor.imports, extractor.edges, []


def _tree_sitter_cache():
    return os.environ.get('AIRP_PARSER_CACHE') or str(Path(sys.prefix) / 'share' / 'airp-parser-cache')


def analyze_tree_sitter(relative, raw, language):
    try:
        from tree_sitter_language_pack import (PackConfig, configure, downloaded_languages,
                                                process)
    except ImportError:
        return [], [], [], [dict(path=relative, language=language,
                                  error='Optional tree-sitter-language-pack is not installed')]
    configure(PackConfig(cache_dir=_tree_sitter_cache()))
    if language not in set(downloaded_languages()):
        return [], [], [], [dict(path=relative, language=language,
                                  error=f'Tree-sitter parser is not cached for {language}')]
    text = raw.decode('utf-8', errors='replace')
    result = process(text, {'language': language, 'symbols': True, 'diagnostics': True,
                            'max_source_bytes': 10_000_000, 'parse_timeout_ms': 5000})
    symbols = []
    used_ids = set()

    def add(items, parent=None, prefix=()):
        for item in items:
            name = item.name
            if language in ('javascript', 'typescript', 'tsx') and name == 'value':
                name = enclosing_object_property(raw, item.span.start_byte) or name
            if not name and item.signature:
                matches = re.findall(r'([A-Za-z_$][\w$]*)\s*\(', item.signature)
                name = matches[-1] if matches else None
            name = name or '<anonymous>'
            qualname = '.'.join((*prefix, name))
            sid = f'{relative}::{qualname}'
            if sid in used_ids:
                suffix = digest((item.signature or str(item.span.start_byte)).encode())[:8]
                sid = f'{sid}~{suffix}'
                counter = 2
                while sid in used_ids:
                    sid = f'{relative}::{qualname}~{suffix}-{counter}'
                    counter += 1
            used_ids.add(sid)
            source = normalize_newlines(
                raw[item.span.start_byte:item.span.end_byte].decode('utf-8', errors='replace'))
            kind_name = str(item.kind).split('.')[-1].casefold()
            kind = {'method': 'function', 'constructor': 'function'}.get(kind_name, kind_name)
            symbols.append(dict(id=sid, path=relative, name=name, qualname=qualname, kind=kind,
                                start=item.span.start_line + 1, end=item.span.end_line + 1,
                                column=item.span.start_column, definition_line=item.span.start_line + 1,
                                source=source, hash=digest(source.encode('utf-8')), parent=parent,
                                is_test=is_test_path(relative) or name.casefold().startswith('test'),
                                language=language, backend='tree-sitter', editable=False,
                                signature=item.signature, analysis_stale=False))
            add(item.children, sid, (*prefix, name))

    add(result.structure)
    imports = [dict(path=relative, line=i.span.start_line + 1, module='', source=i.source,
                    language=language) for i in result.imports]
    diagnostics = [dict(path=relative, language=language, error=str(d)) for d in result.diagnostics]
    return symbols, imports, [], diagnostics


def warm_languages(languages):
    try:
        from tree_sitter_language_pack import (PackConfig, configure, downloaded_languages,
                                                prefetch)
    except ImportError as error:
        raise ValueError('Install the universal dependency before warming parsers') from error
    requested = sorted(set(languages))
    if not requested:
        raise ValueError('Provide at least one language')
    configure(PackConfig(cache_dir=_tree_sitter_cache()))
    prefetch(requested)
    _optional_language.cache_clear()
    cached = set(downloaded_languages())
    return {'requested': requested, 'ready': [name for name in requested if name in cached],
            'cache': _tree_sitter_cache()}


def analyze(root, path, raw):
    language = language_for(path)
    relative = path.relative_to(root).as_posix()
    record = file_record(root, path, raw, language)
    try:
        if language == 'python':
            symbols, imports, edges, diagnostics = analyze_python(relative, raw)
        else:
            symbols, imports, edges, diagnostics = analyze_tree_sitter(relative, raw, language)
    except (ImportError, SyntaxError, UnicodeError, LookupError, ValueError, RuntimeError) as error:
        symbols, imports, edges = [], [], []
        diagnostics = [dict(path=relative, language=language, error=str(error))]
    return dict(file=record, symbols=symbols, imports=imports, edges=edges,
                diagnostics=diagnostics, analysis_stale=False)


def resolve_edges(symbols, fragments):
    by_id = {s['id']: s for s in symbols}
    full = {}
    for symbol in symbols:
        if symbol['language'] == 'python':
            key = '.'.join(filter(None, [module_name(symbol['path']), symbol['qualname']]))
            full.setdefault(key, []).append(symbol['id'])
    resolved = []
    for fragment in fragments.values():
        bindings = {}
        if fragment['file']['language'] == 'python':
            for item in fragment['imports']:
                try:
                    node = ast.parse(item['source']).body[0]
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            bindings[alias.asname or alias.name.split('.')[0]] = alias.name
                    else:
                        for alias in node.names:
                            bindings[alias.asname or alias.name] = '.'.join(filter(None, [item['module'], alias.name]))
                except (SyntaxError, AttributeError):
                    pass
        for original in fragment['edges']:
            edge = dict(original, target=None, confidence='unresolved')
            name, scope = edge['name'], by_id.get(edge['source'])
            candidates = []
            if '.' not in name:
                prefix = scope['qualname'].split('.') if scope else []
                while prefix:
                    key = edge['path'] + '::' + '.'.join(prefix + [name])
                    if key in by_id:
                        candidates = [key]
                        break
                    prefix.pop()
                if not candidates and edge['path'] + '::' + name in by_id:
                    candidates = [edge['path'] + '::' + name]
            elif name.startswith(('self.', 'cls.')) and scope:
                parent = by_id.get(scope['parent'])
                if parent and parent['kind'] == 'class':
                    key = parent['id'] + '.' + name.split('.', 1)[1]
                    if key in by_id:
                        candidates = [key]
            if not candidates:
                head, *tail = name.split('.')
                if head in bindings:
                    imported = '.'.join([bindings[head]] + tail)
                    candidates = full.get(imported, [])
                    if not candidates:
                        # Script-style packages often import a sibling by its short
                        # name while the indexed root includes parent directories.
                        # Resolve only a unique suffix to avoid guessing.
                        suffix = '.' + imported
                        suffix_matches = [sid for key, ids in full.items()
                                          if key.endswith(suffix) for sid in ids]
                        if len(suffix_matches) == 1:
                            candidates = suffix_matches
            if len(candidates) == 1:
                edge.update(target=candidates[0], confidence='static_candidate')
            resolved.append(edge)
    return resolved


def build(root, previous=None):
    root = Path(root)
    old = previous.get('fragments', {}) if previous and previous.get('schema_version') == GRAPH_SCHEMA else {}
    fragments, changed, reused = {}, [], []
    for path in sources(root):
        relative = path.relative_to(root).as_posix()
        stat, prior = path.stat(), old.get(relative)
        if prior and prior['file'].get('size') == stat.st_size and prior['file'].get('mtime_ns') == stat.st_mtime_ns:
            fragments[relative] = dict(prior)
            reused.append(relative)
            continue
        raw = path.read_bytes()
        if prior and prior['file']['hash'] == digest(raw):
            fragment = dict(prior)
            fragment['file'] = file_record(root, path, raw, language_for(path))
            fragments[relative] = fragment
            reused.append(relative)
            continue
        fragment = analyze(root, path, raw)
        if fragment['diagnostics'] and prior and prior['symbols'] and not fragment['symbols']:
            current_file, diagnostics = fragment['file'], fragment['diagnostics']
            fragment = {k: prior[k] for k in ('symbols', 'imports', 'edges')}
            fragment.update(file=current_file,
                            diagnostics=[dict(d, using_last_good=True) for d in diagnostics],
                            analysis_stale=True)
            fragment['symbols'] = [dict(s, analysis_stale=True) for s in fragment['symbols']]
        fragments[relative] = fragment
        changed.append(relative)
    deleted = sorted(set(old) - set(fragments))
    symbols = [s for f in fragments.values() for s in f['symbols']]
    imports = [i for f in fragments.values() for i in f['imports']]
    diagnostics = [d for f in fragments.values() for d in f['diagnostics']]
    edges = resolve_edges(symbols, fragments)
    languages = sorted({f['file']['language'] for f in fragments.values()})
    return dict(schema_version=GRAPH_SCHEMA,
                files=[f['file'] for f in fragments.values()], symbols=symbols, imports=imports,
                edges=edges, diagnostics=diagnostics, fragments=fragments,
                capabilities={'languages': languages, 'editable_languages': ['python'],
                              'relationship_languages': ['python'],
                              'analysis': 'syntax-precise symbols; conservative Python relationships'},
                index_stats={'changed': changed, 'reused': reused, 'deleted': deleted})
