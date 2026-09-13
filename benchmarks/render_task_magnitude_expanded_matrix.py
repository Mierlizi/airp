"""Render the expanded v0.7 task magnitude experiment as one matrix."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


SCOPES = {
    'micro': '2文件 / 1依赖', 'small': '4文件 / 3依赖',
    'medium': '9文件 / 8依赖', 'large': '17文件 / 16依赖',
    'xlarge': '33文件 / 32依赖', 'xxlarge': '65文件 / 64依赖',
}


def add_text(parts, x, y, value, css='body', anchor='middle'):
    parts.append(f'<text x="{x}" y="{y}" class="{css}" text-anchor="{anchor}">'
                 f'{html.escape(str(value))}</text>')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path,
                        default=Path('benchmarks/runs/task-magnitude-expanded-v07.json'))
    parser.add_argument('--output', type=Path,
                        default=Path('benchmarks/runs/task-magnitude-ai-impact-v07.svg'))
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding='utf-8'))
    if report['calls'] != 120 or len(report['levels']) != 6:
        raise SystemExit('Expanded experiment is incomplete')

    width, height = 1540, 1160
    left, top, label_w, col_w, row_h = 24, 182, 292, 298, 140
    columns = [('代码上下文', '完整文件 → AIRP证据'),
               ('模型总 Token', '输入 + 模型输出'),
               ('运行速度', '成对耗时比及95%区间'),
               ('答案准确率', '每档10个不同任务')]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#f7f9fb"/>',
             '<style>text{font-family:Microsoft YaHei,Segoe UI,Arial,sans-serif;fill:#17202a}'
             '.title{font-size:25px;font-weight:700}.subtitle{font-size:14px}.head{font-size:16px;font-weight:700}'
             '.body{font-size:14px}.strong{font-size:19px;font-weight:700}.small{font-size:12px}'
             '.axis{font-size:13px;font-weight:700;fill:#566573}</style>']
    add_text(parts, 24, 36, 'AIRP v0.7 扩大实验：任务量级 × AI运行影响', 'title', 'start')
    add_text(parts, 24, 65,
             '6档 × 每档10个不同答案结构 × Baseline/AIRP = 120次真实模型调用；合成Python代码；同题成对随机顺序',
             'subtitle', 'start')
    add_text(parts, 24, 91,
             '速度：正值代表AIRP更慢，负值代表更快；置信区间跨0时标记为“不确定”',
             'subtitle', 'start')
    add_text(parts, left + label_w + 2 * col_w, 119, '横向工作维度 →', 'axis')
    add_text(parts, 34, 158, '纵向：单次任务所需代码证据量 ↓', 'axis', 'start')
    for index, (title, subtitle) in enumerate(columns):
        x = left + label_w + index * col_w
        parts.append(f'<rect x="{x}" y="132" width="{col_w - 8}" height="48" rx="8" fill="#dfe6e9"/>')
        add_text(parts, x + (col_w - 8) / 2, 153, title, 'head')
        add_text(parts, x + (col_w - 8) / 2, 171, subtitle, 'small')

    for row_index, level in enumerate(report['levels']):
        y = top + row_index * row_h
        base, airp = level['totals']['baseline'], level['totals']['airp']
        cases = level['cases']
        payload_base, payload_airp = base['payload_tokens'] / cases, airp['payload_tokens'] / cases
        token_base = base['total_model_tokens'] / cases
        token_airp = airp['total_model_tokens'] / cases
        latency = level['latency_paired']
        low, high, estimate = (latency['ci95_low_pct'], latency['ci95_high_pct'],
                               latency['estimate_pct'])
        if low > 0:
            speed_fill, speed_head = '#fadbd8', f'慢 {estimate:.2f}%*'
        elif high < 0:
            speed_fill, speed_head = '#d5f5e3', f'快 {abs(estimate):.2f}%*'
        else:
            speed_fill, speed_head = '#f2f3f4', '差异不确定'
        accuracy_delta = 100 * (airp['successes'] - base['successes']) / cases
        accuracy_fill = '#fdebd0' if accuracy_delta < 0 else '#e8daef'

        parts.append(f'<rect x="{left}" y="{y}" width="{label_w - 8}" height="{row_h - 8}" rx="10" fill="#eaf2f8"/>')
        add_text(parts, left + 14, y + 30, f'{level["label"]}任务', 'head', 'start')
        add_text(parts, left + 14, y + 57, SCOPES[level['level']], 'body', 'start')
        add_text(parts, left + 14, y + 84, f'完整载荷约 {payload_base:,.0f} Token', 'small', 'start')
        add_text(parts, left + 14, y + 108, '10种：求和/筛选/校验和等', 'small', 'start')
        cells = [
            ('#d5f5e3', f'↓ {100 * level["payload_reduction"]:.2f}%',
             f'{payload_base:,.0f} → {payload_airp:,.0f}', f'减少 {payload_base - payload_airp:,.0f} Token'),
            ('#d5f5e3', f'↓ {100 * level["total_model_token_reduction"]:.2f}%',
             f'{token_base:,.0f} → {token_airp:,.0f}', f'每题净省 {level["tokens_saved_per_task"]:,.0f}'),
            (speed_fill, speed_head,
             f'估计 {estimate:+.2f}%', f'95% CI [{low:+.2f}%, {high:+.2f}%]'),
            (accuracy_fill, f'{100 * base["successes"] / cases:.0f}% → {100 * airp["successes"] / cases:.0f}%',
             f'{base["successes"]}/{cases} → {airp["successes"]}/{cases}',
             f'变化 {accuracy_delta:+.0f} 个百分点')]
        for col_index, (fill, headline, detail, note) in enumerate(cells):
            x = left + label_w + col_index * col_w
            parts.append(f'<rect x="{x}" y="{y}" width="{col_w - 8}" height="{row_h - 8}" rx="10" fill="{fill}"/>')
            add_text(parts, x + (col_w - 8) / 2, y + 40, headline, 'strong')
            add_text(parts, x + (col_w - 8) / 2, y + 75, detail, 'body')
            add_text(parts, x + (col_w - 8) / 2, y + 105, note, 'small')

    overall = report['overall']
    ob, oa = overall['totals']['baseline'], overall['totals']['airp']
    latency = overall['latency_paired']
    footer_y = top + 6 * row_h + 16
    parts.append(f'<rect x="24" y="{footer_y}" width="1480" height="90" rx="10" fill="#ffffff" stroke="#ccd1d1"/>')
    add_text(parts, 42, footer_y + 27,
             f'总体：模型总Token ↓ {100 * overall["total_model_token_reduction"]:.2f}% ｜ 成对耗时 {latency["estimate_pct"]:+.2f}%（95% CI {latency["ci95_low_pct"]:+.2f}% 至 {latency["ci95_high_pct"]:+.2f}%，不确定） ｜ 准确率 {ob["successes"]}/60 → {oa["successes"]}/60',
             'head', 'start')
    add_text(parts, 42, footer_y + 53,
             '准确率差 −1.67个百分点；配对自助法95%区间约 −13.33 至 +10.00个百分点，McNemar精确检验 p=1.00。',
             'small', 'start')
    add_text(parts, 42, footer_y + 76,
             '* 仅中型档耗时区间未跨0，但这是6档并行比较之一；不应单独解释为稳定的规模规律。',
             'small', 'start')
    add_text(parts, 24, 1140,
             '边界：仅测只读直接依赖聚合；全部必要符号均已恢复。32/64依赖档的AIRP准确率下降是需要继续解决的风险信号。',
             'small', 'start')
    parts.append('</svg>')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(parts), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'calls': report['calls']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
