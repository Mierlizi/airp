"""Exercise initialization, discovery and tools over real subprocess stdio."""
import asyncio
import json
from pathlib import Path
import shutil
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    with tempfile.TemporaryDirectory(prefix='airp-mcp-') as folder:
        shutil.copytree(Path(__file__).resolve().parents[1] / 'examples/shop', folder, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.airp', '__pycache__'))
        params = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'airp', '--repo', folder, 'serve'])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                names = {tool.name for tool in listing.tools}
                assert names == {'airp'}

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    assert not result.isError, result
                    return json.loads(result.content[0].text)

                packed = await call('airp', {'task': 'change discount calculation',
                                             'options': {'breadth': 'narrow'}})
                found = await call('airp', {'operation': 'find', 'task': 'discount'})
                assert packed['anchors'][0]['id'] == 'pricing.py::discount', packed
                prepared = await call('airp', {'operation': 'prepare', 'symbol_id': 'pricing.py::discount'})
                symbol = await call('airp', {'operation': 'get', 'symbol_id': 'pricing.py::discount'})
                await call('airp', {'operation': 'begin'})
                await call('airp', {'operation': 'update', 'symbol_id': symbol['id'],
                                    'source': symbol['source'], 'expected_hash': symbol['hash'],
                                    'options': {'dry_run': False}})
                assert prepared['ready']
                assert (await call('airp', {'operation': 'verify'}))['ok']
                tested = await call('airp', {'operation': 'test', 'options': {'timeout': 10}})
                assert tested['ok'], tested
                committed = await call('airp', {'operation': 'commit'})
                bad = await session.call_tool('airp', {'operation': 'get', 'symbol_id': 'missing'})
                assert bad.isError
                print(json.dumps({'mcp_stdio': 'passed', 'tools': len(names),
                                  'find_matches': found['total'], 'transaction': committed['status'],
                                  'tool_error_propagation': 'passed'}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
