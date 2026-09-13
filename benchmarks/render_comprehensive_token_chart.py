"""Render the language/scale matrix with row/column means and real-model stages."""
import argparse
import html
import json
from pathlib import Path


def shade(percent):
    ratio = max(0, min(100, percent)) / 100
    return '#%02x%02x%02x' % (round(239 - 181 * ratio),
                              round(246 - 121 * ratio),
                              round(255 - 187 * ratio))


def mean(values):
    return sum(values) / len(values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--matrix', type=Path,
                        default=Path('benchmarks/runs/language-scale-tokens-v06.json'))
    parser.add_argument('--model', type=Path,
                        default=Path('benchmarks/runs/synthetic-model-stage-ab-v06.json'))
    parser.add_argument('--output', type=Path,
                        default=Path('benchmarks/runs/language-scale-stage-v06.svg'))
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding='utf-8'))
    model = json.loads(args.model.read_text(encoding='utf-8'))
    if not model['quality_gate']:
        raise SystemExit('Model quality gate failed; refusing to render reductions')
    rows = matrix['rows']
    if not all(row['correct'] for row in rows):
        raise SystemExit('Matrix correctness gate failed; refusing to render reductions')
    languages = list(dict.fromkeys(row['language'] for row in rows))
    scales = list(dict.fromkeys(row['files'] for row in rows))
    lookup = {(row['language'], row['files']): row for row in rows}

    left, top, cell_w, cell_h = 155, 100, 82, 54
    avg_w = 130
    grid_w = cell_w * len(languages) + avg_w
    stage_top = top + cell_h * (len(scales) + 1) + 65
    width, height = left + grid_w + 30, stage_top + 270
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<style>text{font-family:Microsoft YaHei,Segoe UI,Arial,sans-serif;fill:#17202a}.title{font-size:19px;font-weight:700}.sub{font-size:12px}.head{font-size:11px;font-weight:600}.cell{font-size:12px;font-weight:700}.small{font-size:10px}.stage{font-size:12px}</style>',
             '<text x="20" y="28" class="title">AIRP v0.6：语言 × 规模 × AI运行阶段 Token 实验</text>',
             f'<text x="20" y="49" class="sub">上下文矩阵 {len(rows)}/{len(rows)} 正确；真实模型 22/22 配对两组均成功；所有空缺阶段保持未测</text>',
             '<text x="20" y="68" class="sub">热力图数值为上下文交付降幅；平均格同时显示平均降幅与每任务平均节省Token</text>']
    for column, language in enumerate(languages):
        x = left + column * cell_w + cell_w / 2
        parts.append(f'<text x="{x}" y="89" text-anchor="middle" class="head">{html.escape(language)}</text>')
    parts.append(f'<text x="{left + cell_w * len(languages) + avg_w / 2}" y="89" text-anchor="middle" class="head">横向平均</text>')

    for row_index, files in enumerate(scales):
        y = top + row_index * cell_h
        sample_loc = lookup[(languages[0], files)]['loc']
        parts.append(f'<text x="145" y="{y + 21}" text-anchor="end" class="head">{files} files</text>')
        parts.append(f'<text x="145" y="{y + 38}" text-anchor="end" class="small">约 {sample_loc:,} LOC</text>')
        row_items = []
        for column, language in enumerate(languages):
            item = lookup[(language, files)]
            row_items.append(item)
            x = left + column * cell_w
            percent = item['token_reduction_percent']
            parts.append(f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" rx="4" fill="{shade(percent)}"/>')
            parts.append(f'<text x="{x + (cell_w - 2) / 2}" y="{y + 31}" text-anchor="middle" class="cell">{percent:.1f}%</text>')
        average_percent = mean([item['token_reduction_percent'] for item in row_items])
        average_delta = mean([item['baseline_tokens'] - item['airp_tokens'] for item in row_items])
        x = left + cell_w * len(languages)
        parts.append(f'<rect x="{x}" y="{y}" width="{avg_w - 2}" height="{cell_h - 2}" rx="4" fill="{shade(average_percent)}"/>')
        parts.append(f'<text x="{x + (avg_w - 2) / 2}" y="{y + 22}" text-anchor="middle" class="cell">{average_percent:.1f}%</text>')
        parts.append(f'<text x="{x + (avg_w - 2) / 2}" y="{y + 39}" text-anchor="middle" class="small">Δ {average_delta:.0f} Token/任务</text>')

    y = top + len(scales) * cell_h
    parts.append(f'<text x="145" y="{y + 22}" text-anchor="end" class="head">纵向平均</text>')
    for column, language in enumerate(languages):
        items = [lookup[(language, files)] for files in scales]
        average_percent = mean([item['token_reduction_percent'] for item in items])
        average_delta = mean([item['baseline_tokens'] - item['airp_tokens'] for item in items])
        x = left + column * cell_w
        parts.append(f'<rect x="{x}" y="{y}" width="{cell_w - 2}" height="{cell_h - 2}" rx="4" fill="{shade(average_percent)}"/>')
        parts.append(f'<text x="{x + (cell_w - 2) / 2}" y="{y + 20}" text-anchor="middle" class="cell">{average_percent:.1f}%</text>')
        parts.append(f'<text x="{x + (cell_w - 2) / 2}" y="{y + 38}" text-anchor="middle" class="small">Δ{average_delta:.0f}</text>')
    overall_percent = mean([row['token_reduction_percent'] for row in rows])
    overall_delta = mean([row['baseline_tokens'] - row['airp_tokens'] for row in rows])
    x = left + cell_w * len(languages)
    parts.append(f'<rect x="{x}" y="{y}" width="{avg_w - 2}" height="{cell_h - 2}" rx="4" fill="{shade(overall_percent)}"/>')
    parts.append(f'<text x="{x + (avg_w - 2) / 2}" y="{y + 20}" text-anchor="middle" class="cell">总平均 {overall_percent:.1f}%</text>')
    parts.append(f'<text x="{x + (avg_w - 2) / 2}" y="{y + 38}" text-anchor="middle" class="small">Δ {overall_delta:.0f} Token/任务</text>')

    totals, deltas, reductions = model['totals'], model['token_deltas'], model['reductions']
    parts.append(f'<text x="20" y="{stage_top}" class="title">真实模型运行阶段（11种语言 × 2次重复，22组配对）</text>')
    headers = [('阶段', 20), ('Baseline', 275), ('AIRP', 410), ('累计节省', 545), ('每任务节省', 695), ('降幅', 845)]
    for label, xh in headers:
        parts.append(f'<text x="{xh}" y="{stage_top + 26}" class="head">{label}</text>')
    stage_rows = [
        ('上下文载荷', 'payload_tokens'),
        ('模型输入（含固定系统上下文）', 'input_tokens'),
        ('未缓存输入（诊断，和上一行重叠）', 'uncached_input_tokens'),
        ('推理输出', 'reasoning_output_tokens'),
        ('最终答案输出', 'output_tokens'),
        ('模型总Token（输入+输出）', 'total_model_tokens'),
    ]
    for index, (label, key) in enumerate(stage_rows):
        sy = stage_top + 52 + index * 28
        base, airp = totals['baseline'][key], totals['airp'][key]
        delta, reduction = deltas[key], reductions[key]
        percent = 'N/A（两组均为0）' if reduction is None else f'{100 * reduction:.2f}%'
        parts.append(f'<rect x="15" y="{sy - 18}" width="1000" height="25" fill="{"#f5f7fa" if index % 2 == 0 else "#ffffff"}"/>')
        parts.append(f'<text x="20" y="{sy}" class="stage">{label}</text>')
        parts.append(f'<text x="275" y="{sy}" class="stage">{base:,}</text>')
        parts.append(f'<text x="410" y="{sy}" class="stage">{airp:,}</text>')
        parts.append(f'<text x="545" y="{sy}" class="stage">{delta:,}</text>')
        parts.append(f'<text x="695" y="{sy}" class="stage">{delta / 22:.1f}</text>')
        parts.append(f'<text x="845" y="{sy}" class="stage">{percent}</text>')
    note_y = stage_top + 235
    parts.append(f'<text x="20" y="{note_y}" class="sub">编辑生成、测试执行、失败修复：未测，不填估算值。上下文载荷包含在模型输入中，各行不可相加。</text>')
    parts.append(f'<text x="20" y="{note_y + 19}" class="sub">模型：{html.escape(model["model"])} / {html.escape(model["effort"])}；固定精确答案；交错顺序；合成代码；隔离空工作目录。</text>')
    parts.append('</svg>')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(parts), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'matrix_cells': len(rows),
                      'model_pairs': 22, 'overall_percent': round(overall_percent, 2),
                      'overall_delta_per_task': round(overall_delta, 2)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
