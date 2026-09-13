"""Verify structural indexing across the prewarmed universal language set."""
import json
from pathlib import Path
import tempfile

from airp.core import Repository


SAMPLES = {
    'sample.js': 'export function greet(name) { return name; }\n',
    'sample.ts': 'export function greetTs(name: string): string { return name; }\n',
    'sample.tsx': 'export function Card() { return <div />; }\n',
    'sample.go': 'package sample\nfunc GreetGo(name string) string { return name }\n',
    'sample.rs': 'pub fn greet_rust(name: &str) -> &str { name }\n',
    'Sample.java': ('class Sample { static String greetJava(String name) { return name; } '
                    'static int greetJava(int value) { return value; } }\n'),
    'sample.c': 'int greet_c(int value) { return value; }\n',
    'sample.cpp': 'int greet_cpp(int value) { return value; }\n',
    'Sample.cs': 'class Sample { static string GreetCs(string name) { return name; } }\n',
    'sample.rb': 'def greet_ruby(name)\n  name\nend\n',
    'sample.php': '<?php function greet_php($name) { return $name; }\n',
}


def main():
    with tempfile.TemporaryDirectory(prefix='airp-languages-') as folder:
        root = Path(folder)
        for name, source in SAMPLES.items():
            (root / name).write_text(source, encoding='utf-8')
        repo = Repository(root)
        try:
            result = repo.execute('index')
            found = repo.execute('find', query='', limit=100)
        finally:
            repo.close()
    by_language = {}
    for symbol in found['symbols']:
        by_language[symbol['language']] = by_language.get(symbol['language'], 0) + 1
    expected = {'javascript', 'typescript', 'tsx', 'go', 'rust', 'java',
                'c', 'cpp', 'csharp', 'ruby', 'php'}
    missing = sorted(expected - set(by_language))
    report = {'languages_expected': len(expected), 'languages_indexed': len(by_language),
              'symbols': found['total'], 'by_language': by_language,
              'missing': missing, 'diagnostics': result['diagnostics']}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if missing:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
