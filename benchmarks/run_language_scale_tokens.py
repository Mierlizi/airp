"""Cross-language, cross-scale context-delivery benchmark; no model is invoked."""
import argparse
import csv
import html
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import tiktoken

from airp.core import Repository


LANGUAGES = {
    'Python': ('python', '.py'),
    'JavaScript': ('javascript', '.js'),
    'TypeScript': ('typescript', '.ts'),
    'Go': ('go', '.go'),
    'Rust': ('rust', '.rs'),
    'Java': ('java', '.java'),
    'C#': ('csharp', '.cs'),
    'C': ('c', '.c'),
    'C++': ('cpp', '.cpp'),
    'Ruby': ('ruby', '.rb'),
    'PHP': ('php', '.php'),
}


def function_source(language, name, value, marker=None):
    mark = f'  # {marker}\n' if marker and language in {'python', 'ruby'} else ''
    cmark = f'    // {marker}\n' if marker else ''
    if language == 'python':
        return f'def {name}(value):\n{mark}    adjusted = value + {value}\n    return adjusted\n'
    if language in {'javascript', 'typescript'}:
        annotation = ': number' if language == 'typescript' else ''
        return f'export function {name}(value{annotation}){annotation} {{\n{cmark}    const adjusted = value + {value};\n    return adjusted;\n}}\n'
    if language == 'go':
        return f'func {name}(value int) int {{\n{cmark}    adjusted := value + {value}\n    return adjusted\n}}\n'
    if language == 'rust':
        return f'pub fn {name}(value: i32) -> i32 {{\n{cmark}    let adjusted = value + {value};\n    adjusted\n}}\n'
    if language in {'java', 'csharp'}:
        return f'  static int {name}(int value) {{\n{cmark}    int adjusted = value + {value};\n    return adjusted;\n  }}\n'
    if language in {'c', 'cpp'}:
        return f'int {name}(int value) {{\n{cmark}    int adjusted = value + {value};\n    return adjusted;\n}}\n'
    if language == 'ruby':
        return f'def {name}(value)\n{mark}  adjusted = value + {value}\n  adjusted\nend\n'
    if language == 'php':
        return f'function {name}($value) {{\n{cmark}    $adjusted = $value + {value};\n    return $adjusted;\n}}\n'
    raise ValueError(language)


def file_source(language, file_index, functions_per_file, target_name=None, marker=None):
    functions = []
    for function_index in range(functions_per_file):
        name = target_name if target_name and function_index == 0 else f'helper_{file_index}_{function_index}'
        functions.append(function_source(language, name, function_index, marker if name == target_name else None))
    body = '\n'.join(functions)
    if language == 'go':
        return 'package bench\n\n' + body
    if language == 'java':
        return f'class Module{file_index} {{\n{body}}}\n'
    if language == 'csharp':
        return f'class Module{file_index} {{\n{body}}}\n'
    if language == 'php':
        return '<?php\n\n' + body
    return body


def token_count(encoding, value):
    wire = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return len(encoding.encode(wire))


def color(percent):
    bounded = max(0.0, min(100.0, percent)) / 100
    red = round(239 - 181 * bounded)
    green = round(246 - 121 * bounded)
    blue = round(255 - 187 * bounded)
    return f'#{red:02x}{green:02x}{blue:02x}'


def write_svg(rows, languages, scales, output):
    left, top, cell_w, cell_h = 150, 95, 92, 52
    width, height = left + cell_w * len(languages) + 30, top + cell_h * len(scales) + 75
    correct = sum(row['correct'] for row in rows)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<style>text{font-family:Microsoft YaHei,Segoe UI,Arial,sans-serif;fill:#17202a}.title{font-size:18px;font-weight:600}.label{font-size:12px}.cell{font-size:13px;font-weight:600}</style>',
             '<text x="20" y="28" class="title">AIRP：语言 × 仓库规模的上下文 Token 减少幅度</text>',
             f'<text x="20" y="49" class="label">配对检索协议；仅在正确恢复预期符号和答案标记时显示降幅（{correct}/{len(rows)} 通过）</text>']
    lookup = {(row['language'], row['files']): row for row in rows}
    for column, language in enumerate(languages):
        x = left + column * cell_w + cell_w / 2
        parts.append(f'<text x="{x}" y="82" text-anchor="middle" class="label">{html.escape(language)}</text>')
    for row_index, files in enumerate(scales):
        y = top + row_index * cell_h
        sample = lookup[(languages[0], files)]
        parts.append(f'<text x="140" y="{y + 22}" text-anchor="end" class="label">{files} files</text>')
        parts.append(f'<text x="140" y="{y + 38}" text-anchor="end" class="label">~{sample["loc"]:,} LOC</text>')
        for column, language in enumerate(languages):
            item = lookup[(language, files)]
            x = left + column * cell_w
            fill = color(item['token_reduction_percent']) if item['correct'] else '#d5d8dc'
            label = f'{item["token_reduction_percent"]:.1f}%' if item['correct'] else 'FAIL'
            parts.append(f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" rx="4" fill="{fill}"/>')
            parts.append(f'<text x="{x + (cell_w - 2) / 2}" y="{y + 31}" text-anchor="middle" class="cell">{label}</text>')
    parts.append(f'<text x="{left}" y="{height - 26}" class="label">指标：100 ×（1 − AIRP 响应 Token / 基线搜索与完整文件响应 Token），o200k_base</text>')
    parts.append('</svg>')
    output.write_text('\n'.join(parts), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scales', default='10,100,500,1000')
    parser.add_argument('--functions-per-file', type=int, default=24)
    parser.add_argument('--output', type=Path,
                        default=Path('benchmarks/runs/language-scale-tokens-v06.json'))
    parser.add_argument('--svg', type=Path,
                        default=Path('benchmarks/runs/language-scale-tokens-v06.svg'))
    args = parser.parse_args()
    scales = [int(item) for item in args.scales.split(',')]
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                          str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
    encoding = tiktoken.get_encoding('o200k_base')
    rows = []
    for display, (language, extension) in LANGUAGES.items():
        for files in scales:
            with tempfile.TemporaryDirectory(prefix=f'airp-matrix-{language}-') as folder:
                root = Path(folder)
                target_name = f'calculate_refund_{files}'
                marker = f'REFUND_SENTINEL_{language.upper()}_{files}'
                target_path = root / f'module_{files - 1:05d}{extension}'
                loc = 0
                for index in range(files):
                    source = file_source(language, index, args.functions_per_file,
                                         target_name if index == files - 1 else None, marker)
                    (root / f'module_{index:05d}{extension}').write_text(source, encoding='utf-8')
                    loc += len(source.splitlines())
                repo = Repository(root)
                started = time.perf_counter()
                try:
                    indexed = repo.index()
                    expected_row = repo.db.execute(
                        'SELECT sid,data FROM symbol_lookup WHERE name=?', (target_name,)).fetchone()
                    diagnostics = indexed['diagnostics']
                    if expected_row is None:
                        packed, expected = {}, None
                        airp_tokens, anchor, marker_found = 0, None, False
                    else:
                        expected = expected_row[0]
                        packed = repo.task_context(
                            f'inspect {target_name} {marker}', budget=3000,
                            budget_unit='tokens', intent='understand', breadth='narrow',
                            response_mode='auto')
                        airp_tokens = token_count(encoding, packed)
                        anchor = packed['anchors'][0]['id'] if packed['anchors'] else None
                        marker_found = marker in packed['text']
                    target_source = target_path.read_text(encoding='utf-8')
                    line = next(i for i, text in enumerate(target_source.splitlines(), 1)
                                if target_name in text)
                    baseline = {'matches': [{'path': target_path.name, 'line': line,
                                              'preview': target_source.splitlines()[line - 1]}],
                                'files': [{'path': target_path.name, 'source': target_source}]}
                    baseline_tokens = token_count(encoding, baseline)
                    correct = anchor == expected and marker_found and marker in target_source
                    reduction = 100 * (1 - airp_tokens / baseline_tokens) if correct else 0.0
                finally:
                    repo.close()
                row = {'language': display, 'backend_language': language, 'files': files,
                       'loc': loc, 'symbols': indexed['symbols'], 'expected': expected,
                       'anchor': anchor, 'correct': correct, 'diagnostics': diagnostics,
                       'baseline_tokens': baseline_tokens, 'airp_tokens': airp_tokens,
                       'token_reduction_percent': round(reduction, 2),
                       'elapsed_seconds': round(time.perf_counter() - started, 4)}
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    summary = {'method': 'deterministic paired context-delivery benchmark; no model calls',
               'tokenizer': 'o200k_base', 'baseline': 'search match plus complete target file',
               'airp': 'one auto-mode task_context response with a 3000-token body budget',
               'functions_per_file': args.functions_per_file,
               'correct_cells': sum(row['correct'] for row in rows),
               'total_cells': len(rows), 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    csv_path = args.output.with_suffix('.csv')
    with csv_path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            'language', 'files', 'loc', 'symbols', 'correct', 'baseline_tokens',
            'airp_tokens', 'token_reduction_percent', 'elapsed_seconds'])
        writer.writeheader()
        writer.writerows({key: row[key] for key in writer.fieldnames} for row in rows)
    write_svg(rows, list(LANGUAGES), scales, args.svg)
    print(json.dumps({'output': str(args.output), 'csv': str(csv_path), 'svg': str(args.svg),
                      'correct_cells': summary['correct_cells'],
                      'total_cells': summary['total_cells']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
