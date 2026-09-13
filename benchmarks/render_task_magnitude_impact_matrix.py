"""Render one matrix covering token, latency, and accuracy by task magnitude."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


EXAMPLES = {
    'micro': ('2个相关文件 / 1个依赖', '接口核对1个辅助函数'),
    'small': ('4个相关文件 / 3个依赖', '接口串联鉴权、定价、日志'),
    'medium': ('9个相关文件 / 8个依赖', '服务编排8个组件'),
    'large': ('17个相关文件 / 16个依赖', '工作流跨16个模块'),
}


def add_text(parts, x, y, value, css='body', anchor='middle'):
    parts.append(f'<text x="{x}" y="{y}" class="{css}" text-anchor="{anchor}">'
                 f'{html.escape(str(value))}</text>')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path,
                        default=Path('benchmarks/runs/task-magnitude-model-ab-v06.json'))
    parser.add_argument('--output', type=Path,
                        default=Path('benchmarks/runs/task-magnitude-ai-impact-v06.svg'))
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding='utf-8'))
    levels = report['levels']
    if not report['quality_gate'] or not all(item['quality_gate'] for item in levels):
        raise SystemExit('Quality gate failed; refusing to render impact matrix')

    width, height = 1500, 920
    left, top, label_w, col_w, row_h = 24, 170, 292, 288, 142
    columns = [('代码上下文交付', '插件直接控制的代码载荷'),
               ('模型总 Token', '输入 + 推理/最终输出'),
               ('AI 运行速度', '真实调用墙钟耗时'),
               ('答案准确率', '冻结答案精确匹配')]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#f7f9fb"/>',
             '<style>text{font-family:Microsoft YaHei,Segoe UI,Arial,sans-serif;fill:#17202a}'
             '.title{font-size:25px;font-weight:700}.subtitle{font-size:14px}.head{font-size:16px;font-weight:700}'
             '.body{font-size:14px}.strong{font-size:20px;font-weight:700}.small{font-size:12px}'
             '.axis{font-size:13px;font-weight:700;fill:#566573}</style>']
    add_text(parts, 24, 36, 'AIRP v0.6：任务量级 × AI 运行影响', 'title', 'start')
    add_text(parts, 24, 64,
             '真实模型配对实验：4个量级 × 每组3次；Baseline与AIRP均12/12正确；数值来自同一份原始记录',
             'subtitle', 'start')
    add_text(parts, left + label_w + 2 * col_w, 105, '横向工作维度 →', 'axis')
    add_text(parts, 35, 145, '纵向：一次任务必须理解的代码量 ↓', 'axis', 'start')
    for index, (title, subtitle) in enumerate(columns):
        x = left + label_w + index * col_w
        parts.append(f'<rect x="{x}" y="120" width="{col_w - 8}" height="48" rx="8" fill="#dfe6e9"/>')
        add_text(parts, x + (col_w - 8) / 2, 141, title, 'head')
        add_text(parts, x + (col_w - 8) / 2, 159, subtitle, 'small')

    base_total = airp_total = base_latency = airp_latency = 0
    base_successes = airp_successes = runs = 0
    for row_index, level in enumerate(levels):
        y = top + row_index * row_h
        base, airp = level['totals']['baseline'], level['totals']['airp']
        repeats = report['repeats']
        base_total += base['total_model_tokens']
        airp_total += airp['total_model_tokens']
        base_latency += base['latency_seconds']
        airp_latency += airp['latency_seconds']
        base_successes += base['successes']
        airp_successes += airp['successes']
        runs += base['runs']

        parts.append(f'<rect x="{left}" y="{y}" width="{label_w - 8}" height="{row_h - 8}" rx="10" fill="#eaf2f8"/>')
        add_text(parts, left + 14, y + 29, f'{level["label"]}任务', 'head', 'start')
        scope, example = EXAMPLES[level['level']]
        add_text(parts, left + 14, y + 55, scope, 'body', 'start')
        add_text(parts, left + 14, y + 79, example, 'small', 'start')
        add_text(parts, left + 14, y + 109,
                 f'任务代码载荷 {base["payload_tokens"] / repeats:,.0f} Token', 'small', 'start')

        payload_reduction = 100 * level['reductions']['payload_tokens']
        total_reduction = 100 * level['reductions']['total_model_tokens']
        base_payload, airp_payload = base['payload_tokens'] / repeats, airp['payload_tokens'] / repeats
        base_tokens = base['total_model_tokens'] / repeats
        airp_tokens = airp['total_model_tokens'] / repeats
        saved = level['token_deltas']['total_model_tokens'] / repeats
        base_seconds, airp_seconds = base['latency_seconds'] / repeats, airp['latency_seconds'] / repeats
        latency_change = 100 * (airp_seconds / base_seconds - 1)
        cells = [
            ('#d5f5e3', f'↓ {payload_reduction:.2f}%',
             f'{base_payload:,.0f} → {airp_payload:,.0f} Token', f'减少 {base_payload - airp_payload:,.0f}'),
            ('#d5f5e3', f'↓ {total_reduction:.2f}%',
             f'{base_tokens:,.0f} → {airp_tokens:,.0f} Token', f'净减少 {saved:,.0f}'),
            ('#d5f5e3' if latency_change < 0 else '#fdebd0',
             ('快' if latency_change < 0 else '慢') + f' {abs(latency_change):.2f}%',
             f'{base_seconds:.2f}s → {airp_seconds:.2f}s', f'耗时 {airp_seconds - base_seconds:+.2f}s'),
            ('#e8daef', '准确率持平',
             f'{base["successes"]}/{base["runs"]} → {airp["successes"]}/{airp["runs"]}',
             '观测变化 0 个百分点')]
        for col_index, (fill, headline, detail, note) in enumerate(cells):
            x = left + label_w + col_index * col_w
            parts.append(f'<rect x="{x}" y="{y}" width="{col_w - 8}" height="{row_h - 8}" rx="10" fill="{fill}"/>')
            add_text(parts, x + (col_w - 8) / 2, y + 40, headline, 'strong')
            add_text(parts, x + (col_w - 8) / 2, y + 75, detail, 'body')
            add_text(parts, x + (col_w - 8) / 2, y + 105, note, 'small')

    overall_token_reduction = 100 * (1 - airp_total / base_total)
    overall_latency_change = 100 * (airp_latency / base_latency - 1)
    footer_y = top + len(levels) * row_h + 18
    parts.append(f'<rect x="24" y="{footer_y}" width="1440" height="68" rx="10" fill="#ffffff" stroke="#d5d8dc"/>')
    add_text(parts, 42, footer_y + 25,
             f'四档合计：模型总Token ↓ {overall_token_reduction:.2f}% ｜ 平均耗时 {base_latency / runs:.2f}s → {airp_latency / runs:.2f}s（慢 {overall_latency_change:.2f}%） ｜ 准确率 {base_successes}/{runs} → {airp_successes}/{runs}',
             'head', 'start')
    add_text(parts, 42, footer_y + 51,
             '速度受模型服务、缓存和调度波动影响；每档仅3次，当前结果不能证明稳定加速。准确率样本也不足以证明普遍等效。',
             'small', 'start')
    add_text(parts, 24, 892,
             '边界：仅测合成Python直接依赖聚合的只读理解任务；编辑、测试、失败修复和动态调用尚未进入这张量级配对图。',
             'small', 'start')
    parts.append('</svg>')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(parts), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'quality_gate': True,
                      'overall_token_reduction_pct': overall_token_reduction,
                      'overall_latency_change_pct': overall_latency_change,
                      'accuracy': [base_successes, airp_successes, runs]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
