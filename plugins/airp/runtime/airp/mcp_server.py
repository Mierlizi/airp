"""Compact MCP adapter: one schema and one model round for ordinary tasks."""
from .core import Repository


def create_server(root):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise ImportError('Install the MCP adapter: python -m pip install -e "[mcp]"') from error
    server = FastMCP('AIRP', instructions='Compact repository context and controlled Python edits.')

    def call(tool, **args):
        repo = Repository(root)
        try:
            return repo.execute(tool, **args)
        finally:
            repo.close()

    @server.tool()
    def airp(task: str = '', operation: str = 'context', symbol_id: str = '',
             source: str = '', expected_hash: str = '', options: dict | None = None) -> dict:
        """Use context for one-shot auto-indexed, adaptive evidence. Other operations: find, get, refs, callers, callees, dependencies, affected, prepare, update, verify, test, begin, diff, commit, rollback, status."""
        opts = dict(options or {})
        if operation == 'context':
            if not task:
                raise ValueError('context requires task')
            return call('smart_context', task=task, **{
                key: opts[key] for key in ('budget', 'intent', 'breadth') if key in opts})
        if operation == 'find':
            return call('find', query=task, **{
                key: opts[key] for key in ('kind', 'limit') if key in opts})
        if operation == 'get':
            return call('get', symbol_id=symbol_id)
        if operation in ('refs', 'callers', 'callees', 'dependencies'):
            return call(operation, symbol_id=symbol_id, **opts)
        if operation == 'affected':
            return call('affected', symbol_id=symbol_id)
        if operation == 'prepare':
            return call('prepare', symbol_id=symbol_id or None, query=task or None, **opts)
        if operation == 'update':
            return call('update', symbol_id=symbol_id, source=source,
                        expected_hash=expected_hash, **opts)
        if operation in ('verify', 'test', 'begin', 'diff', 'commit', 'rollback', 'status'):
            return call(operation, **opts)
        raise ValueError('Unsupported operation')

    return server


def serve(root):
    create_server(root).run(transport='stdio')
