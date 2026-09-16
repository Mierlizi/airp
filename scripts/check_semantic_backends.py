"""Run end-to-end AIRP semantic indexing against real Rust and TypeScript LSPs."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from airp.core import Repository


def _assert_call(repo: Repository, caller_name: str, target_name: str) -> dict:
    caller = repo.find(caller_name)['symbols'][0]['id']
    target = repo.find(target_name)['symbols'][0]['id']
    result = repo.callees(caller)
    matching = [edge for edge in result['edges'] if edge['target'] == target]
    if not matching or matching[0]['confidence'] != 'semantic_exact':
        raise RuntimeError(f'Semantic call edge missing: {caller} -> {target}')
    return {'caller': caller, 'target': target,
            'provider': matching[0].get('provider'),
            'confidence': matching[0]['confidence']}


def _rust(root: Path) -> dict:
    (root / 'src').mkdir(parents=True)
    (root / 'Cargo.toml').write_text(
        '[package]\nname = "airp_semantic_fixture"\nversion = "0.1.0"\n'
        'edition = "2021"\n', encoding='utf-8')
    (root / 'src' / 'lib.rs').write_text(
        'pub fn target_value() -> i32 { 7 }\n'
        'pub fn caller_value() -> i32 { target_value() }\n', encoding='utf-8')
    repo = Repository(root)
    try:
        indexed = repo.index()
        if indexed['diagnostics']:
            raise RuntimeError(f'Rust parser unavailable: {indexed["diagnostics"]}')
        built = repo.semantic_build('rust', max_symbols=20, timeout=30)
        return {'build': built, 'edge': _assert_call(repo, 'caller_value', 'target_value')}
    finally:
        repo.close()


def _typescript(root: Path) -> dict:
    (root / 'package.json').write_text(
        '{"name":"airp-semantic-fixture","private":true}\n', encoding='utf-8')
    (root / 'tsconfig.json').write_text(
        '{"compilerOptions":{"target":"ES2022","module":"ESNext","strict":true},'
        '"include":["src"]}\n', encoding='utf-8')
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'index.ts').write_text(
        'export function targetValue(): number { return 7; }\n'
        'export function callerValue(): number { return targetValue(); }\n',
        encoding='utf-8')
    npm = shutil.which('npm')
    if not npm:
        raise RuntimeError('npm is unavailable')
    subprocess.run(
        [npm, 'install', '--no-save', '--ignore-scripts', '--no-audit', '--no-fund',
         'typescript@5.9.3', 'typescript-language-server@6.0.0'],
        cwd=root, capture_output=True, text=True, encoding='utf-8',
        timeout=120, check=True)
    server = root / 'node_modules' / '.bin' / (
        'typescript-language-server.cmd' if sys.platform == 'win32'
        else 'typescript-language-server')
    repo = Repository(root)
    try:
        indexed = repo.index()
        if indexed['diagnostics']:
            raise RuntimeError(f'TypeScript parser unavailable: {indexed["diagnostics"]}')
        built = repo.semantic_build(
            'typescript', server=server, server_args=['--stdio'],
            max_symbols=20, timeout=30)
        return {'build': built, 'edge': _assert_call(repo, 'callerValue', 'targetValue')}
    finally:
        repo.close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='airp-rust-lsp-') as folder:
        rust = _rust(Path(folder))
    with tempfile.TemporaryDirectory(prefix='airp-ts-lsp-') as folder:
        typescript = _typescript(Path(folder))
    print(json.dumps({'semantic_backends': 'passed', 'rust': rust,
                      'typescript': typescript}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
