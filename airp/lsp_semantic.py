"""Bounded LSP Call Hierarchy adapter for optional semantic relationships."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote, urlparse

from .semantic import SEMANTIC_SCHEMA, index_fingerprint


DEFAULT_SERVERS = {
    'rust': ('rust-analyzer',),
    'typescript': ('typescript-language-server', '--stdio'),
}
CALLABLE_KINDS = {'function', 'method'}


class LspError(RuntimeError):
    pass


def _path_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _uri_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != 'file':
        return None
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f'//{parsed.netloc}{path}'
    if len(path) >= 3 and path[0] == '/' and path[2] == ':':
        path = path[1:]
    return Path(path).resolve()


class LspClient:
    def __init__(self, command: list[str], root: Path, timeout: float = 10.0):
        if not command:
            raise LspError('Language server command is empty')
        executable = command[0]
        if not Path(executable).is_file() and shutil.which(executable) is None:
            raise LspError(f'Language server executable is unavailable: {executable}')
        self.command = command
        self.root = root.resolve()
        self.timeout = timeout
        self.next_id = 1
        self.messages: queue.Queue = queue.Queue()
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self.root)
        except OSError as error:
            raise LspError(f'Could not start language server: {error}') from error
        self.reader = threading.Thread(target=self._read_messages, daemon=True)
        self.reader.start()

    def _read_messages(self):
        stream = self.process.stdout
        try:
            while stream:
                headers = {}
                while True:
                    line = stream.readline()
                    if not line:
                        self.messages.put(None)
                        return
                    if line in (b'\r\n', b'\n'):
                        break
                    name, _, value = line.decode('ascii', errors='replace').partition(':')
                    headers[name.casefold().strip()] = value.strip()
                length = int(headers.get('content-length', '0'))
                payload = stream.read(length)
                if len(payload) != length:
                    self.messages.put(None)
                    return
                self.messages.put(json.loads(payload.decode('utf-8')))
        except Exception as error:
            self.messages.put(error)

    def _send(self, message: dict):
        if self.process.poll() is not None or self.process.stdin is None:
            raise LspError('Language server exited before completing the request')
        payload = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.process.stdin.write(f'Content-Length: {len(payload)}\r\n\r\n'.encode('ascii'))
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def notify(self, method: str, params: dict | None = None):
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params or {}})

    def _answer_server_request(self, message: dict):
        method = message.get('method')
        params = message.get('params') or {}
        if method == 'workspace/configuration':
            result = [None for _ in params.get('items', [])]
        elif method == 'workspace/workspaceFolders':
            result = [{'uri': _path_uri(self.root), 'name': self.root.name}]
        elif method == 'workspace/applyEdit':
            result = {'applied': False, 'failureReason': 'AIRP semantic indexing is read-only'}
        else:
            result = None
        self._send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})

    def wait_for_notification(self, method: str, predicate, timeout: float | None = None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LspError(f'Language server did not become ready: {method}')
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty as error:
                raise LspError(f'Language server did not become ready: {method}') from error
            if message is None:
                raise LspError(f'Language server exited while waiting for {method}')
            if isinstance(message, Exception):
                raise LspError(f'Invalid language server response: {message}')
            if message.get('method') and message.get('id') is not None:
                self._answer_server_request(message)
                continue
            if message.get('method') == method and predicate(message.get('params') or {}):
                return message.get('params') or {}

    def request(self, method: str, params: dict | None = None):
        request_id = self.next_id
        self.next_id += 1
        self._send({'jsonrpc': '2.0', 'id': request_id,
                    'method': method, 'params': params or {}})
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LspError(f'Language server timed out during {method}')
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty as error:
                raise LspError(f'Language server timed out during {method}') from error
            if message is None:
                raise LspError(f'Language server exited during {method}')
            if isinstance(message, Exception):
                raise LspError(f'Invalid language server response: {message}')
            if message.get('method') and message.get('id') is not None:
                self._answer_server_request(message)
                continue
            if message.get('id') != request_id:
                continue
            if message.get('error'):
                detail = message['error'].get('message', str(message['error']))
                raise LspError(f'{method} failed: {detail}')
            return message.get('result')

    def initialize(self):
        result = self.request('initialize', {
            'processId': None,
            'rootUri': _path_uri(self.root),
            'capabilities': {
                'textDocument': {'callHierarchy': {}},
                'experimental': {'serverStatusNotification': True},
            },
            'workspaceFolders': [{'uri': _path_uri(self.root), 'name': self.root.name}],
        }) or {}
        self.notify('initialized')
        return result

    def close(self):
        if self.process.poll() is None:
            try:
                self.request('shutdown', {})
                self.notify('exit')
                self.process.wait(timeout=2)
            except (LspError, subprocess.TimeoutExpired, OSError):
                self.process.kill()
        if self.process.stdin:
            self.process.stdin.close()


class LspSemanticBackend:
    def __init__(self, root: Path, manifest: dict, symbols: list[dict], language: str,
                 command: list[str] | None = None, timeout: float = 10.0):
        if language not in DEFAULT_SERVERS:
            raise LspError(f'Unsupported LSP semantic language: {language}')
        self.root = root.resolve()
        self.manifest = manifest
        self.symbols = symbols
        self.language = language
        self.command = list(command or DEFAULT_SERVERS[language])
        self.timeout = timeout
        self.by_path = {}
        for symbol in symbols:
            self.by_path.setdefault(symbol['path'], []).append(symbol)

    def _symbol_for_item(self, item: dict) -> dict | None:
        path = _uri_path(str(item.get('uri', '')))
        if path is None or not path.is_relative_to(self.root):
            return None
        relative = path.relative_to(self.root).as_posix()
        line = int(item.get('selectionRange', item.get('range', {}))
                   .get('start', {}).get('line', -1)) + 1
        candidates = [symbol for symbol in self.by_path.get(relative, [])
                      if symbol['start'] <= line <= symbol['end']]
        if not candidates:
            return None
        name = str(item.get('name', ''))
        candidates.sort(key=lambda symbol: (
            symbol['name'] != name, symbol['end'] - symbol['start'], symbol['id']))
        return candidates[0]

    def _position(self, symbol: dict) -> dict:
        path = self.root / symbol['path']
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        line_index = max(0, symbol['start'] - 1)
        line = lines[line_index] if line_index < len(lines) else ''
        column = max(0, line.find(symbol['name']))
        return {'line': line_index, 'character': column}

    def build(self, max_symbols: int = 200) -> dict:
        if not 1 <= max_symbols <= 5000:
            raise LspError('max_symbols must be between 1 and 5000')
        executable = self.command[0] if self.command else ''
        if (not executable or
                (not Path(executable).is_file() and shutil.which(executable) is None)):
            raise LspError(f'Language server executable is unavailable: {executable}')
        selected = [symbol for symbol in self.symbols
                    if symbol.get('language') == self.language
                    and symbol.get('kind') in CALLABLE_KINDS][:max_symbols]
        if not selected:
            raise LspError(f'no callable symbols indexed for {self.language}')
        client = LspClient(self.command, self.root, timeout=self.timeout)
        edges, opened, failures, queried = {}, set(), [], 0
        try:
            capabilities = client.initialize().get('capabilities', {})
            if not capabilities.get('callHierarchyProvider'):
                raise LspError('Language server does not advertise Call Hierarchy support')
            if self.language == 'rust':
                status = client.wait_for_notification(
                    'experimental/serverStatus',
                    lambda params: bool(params.get('quiescent')),
                    timeout=max(self.timeout, 30.0))
                if status.get('health') == 'error':
                    raise LspError(
                        'rust-analyzer could not load the workspace: '
                        + str(status.get('message') or 'unknown error'))
            for symbol in selected:
                path = (self.root / symbol['path']).resolve()
                uri = _path_uri(path)
                if uri not in opened:
                    client.notify('textDocument/didOpen', {'textDocument': {
                        'uri': uri, 'languageId': self.language, 'version': 1,
                        'text': path.read_text(encoding='utf-8', errors='replace'),
                    }})
                    opened.add(uri)
                try:
                    prepared = client.request('textDocument/prepareCallHierarchy', {
                        'textDocument': {'uri': uri},
                        'position': self._position(symbol),
                    }) or []
                    if not prepared:
                        continue
                    item = next((entry for entry in prepared
                                 if entry.get('name') == symbol['name']), prepared[0])
                    queried += 1
                    outgoing = client.request('callHierarchy/outgoingCalls', {'item': item}) or []
                    incoming = client.request('callHierarchy/incomingCalls', {'item': item}) or []
                    for record, endpoint, source_is_target in (
                            *((row, row.get('to'), True) for row in outgoing),
                            *((row, row.get('from'), False) for row in incoming)):
                        other = self._symbol_for_item(endpoint or {})
                        if other is None:
                            continue
                        source = symbol['id'] if source_is_target else other['id']
                        target = other['id'] if source_is_target else symbol['id']
                        ranges = record.get('fromRanges') or []
                        line = (int(ranges[0].get('start', {}).get('line', symbol['start'] - 1)) + 1
                                if ranges else symbol['start'])
                        edges[(source, target, 'call')] = {
                            'source': source, 'target': target, 'kind': 'call',
                            'name': other['name'] if source_is_target else symbol['name'],
                            'line': line, 'confidence': 'semantic_exact',
                        }
                except (LspError, OSError, UnicodeError, ValueError) as error:
                    failures.append({'symbol': symbol['id'], 'error': str(error)[:240]})
        finally:
            client.close()
        if not queried:
            raise LspError(
                f'{self.language} server prepared no Call Hierarchy items; '
                'the semantic overlay was not replaced')
        return {
            'schema_version': SEMANTIC_SCHEMA,
            'provider': f'lsp:{self.language}',
            'index_fingerprint': index_fingerprint(self.manifest),
            'edges': list(edges.values()),
            'diagnostics': {'eligible': len(selected), 'queried': queried,
                            'failures': failures[:20], 'truncated': len(selected) == max_symbols},
        }
