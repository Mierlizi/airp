"""Render validated real-model token savings by task evidence magnitude."""
import argparse
import html
import json
from pathlib import Path

TASK_EXAMPLES = {
    'micro': '例：1个接口 → 1个辅助函数（共2个相关文件）',
    'small': '例：接口串联3个辅助函数（共4个相关文件）',
    'medium': '例：服务编排8个组件（共9个相关文件）',
    'large': '例：工作流跨16个模块（共17个相关文件）',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path,
                        default=Path('benchmarks/runs/task-magnitude-model-ab-v06.json'))
    parser.add_argument('--output', type=Path,
                        default=Path('benchmarks/runs/task-magnitude-token-v06.svg'))
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding='utf-8'))
    if not report['quality_gate'] or not all(level['quality_gate'] for level in report['levels']):
        raise SystemExit('Quality gate failed; refusing to render reductions')
    levels = report['levels']
    width, height = 1060, 650
    chart_left, chart_top, chart_height, group_w = 95, 105, 300, 220
    maximum = max(level['totals']['baseline']['total_model_tokens'] / report['repeats']
                  for level in levels)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             '<style>text{font-family:Microsoft YaHei,Segoe UI,Arial,sans-serif;fill:#17202a}.title{font-size:20px;font-weight:700}.sub{font-size:12px}.label{font-size:12px}.value{font-size:13px;font-weight:700}.tiny{font-size:10px}</style>',
             '<text x="24" y="30" class="title">AIRP v0.6：不同任务证据量的真实模型 Token 降幅</text>',
             '<text x="24" y="52" class="sub">4个量级 × 3次重复 × baseline/AIRP = 24次调用；两组均12/12成功；仅使用合成代码</text>',
             '<rect x="720" y="20" width="16" height="12" fill="#aeb6bf"/><text x="742" y="31" class="label">Baseline总Token/任务</text>',
             '<rect x="720" y="40" width="16" height="12" fill="#3a7d44"/><text x="742" y="51" class="label">AIRP总Token/任务</text>']
    for tick in range(0, 25001, 5000):
        y = chart_top + chart_height - tick / maximum * chart_height
        parts.append(f'<line x1="{chart_left}" y1="{y:.1f}" x2="985" y2="{y:.1f}" stroke="#e5e8e8"/>')
        parts.append(f'<text x="85" y="{y + 4:.1f}" text-anchor="end" class="tiny">{tick:,}</text>')
    for index, level in enumerate(levels):
        totals, reductions, deltas = level['totals'], level['reductions'], level['token_deltas']
        base = totals['baseline']['total_model_tokens'] / report['repeats']
        airp = totals['airp']['total_model_tokens'] / report['repeats']
        x = chart_left + 45 + index * group_w
        base_h, airp_h = base / maximum * chart_height, airp / maximum * chart_height
        base_y, airp_y = chart_top + chart_height - base_h, chart_top + chart_height - airp_h
        parts.append(f'<rect x="{x}" y="{base_y:.1f}" width="56" height="{base_h:.1f}" rx="4" fill="#aeb6bf"/>')
        parts.append(f'<rect x="{x + 64}" y="{airp_y:.1f}" width="56" height="{airp_h:.1f}" rx="4" fill="#3a7d44"/>')
        parts.append(f'<text x="{x + 28}" y="{base_y - 7:.1f}" text-anchor="middle" class="tiny">{base:,.0f}</text>')
        parts.append(f'<text x="{x + 92}" y="{airp_y - 7:.1f}" text-anchor="middle" class="tiny">{airp:,.0f}</text>')
        center = x + 60
        parts.append(f'<text x="{center}" y="430" text-anchor="middle" class="value">{html.escape(level["label"])}：{level["dependencies"]}个依赖</text>')
        parts.append(f'<text x="{center}" y="451" text-anchor="middle" class="label">总Token ↓ {100 * reductions["total_model_tokens"]:.2f}%</text>')
        parts.append(f'<text x="{center}" y="470" text-anchor="middle" class="label">Δ {deltas["total_model_tokens"] / report["repeats"]:,.0f} Token/任务</text>')
        payload_base = totals['baseline']['payload_tokens'] / report['repeats']
        payload_airp = totals['airp']['payload_tokens'] / report['repeats']
        parts.append(f'<text x="{center}" y="492" text-anchor="middle" class="tiny">代码载荷 {payload_base:,.0f} → {payload_airp:,.0f}</text>')
        parts.append(f'<text x="{center}" y="508" text-anchor="middle" class="tiny">载荷 ↓ {100 * reductions["payload_tokens"]:.2f}%</text>')
        parts.append(f'<text x="{center}" y="530" text-anchor="middle" class="tiny">{html.escape(TASK_EXAMPLES[level["level"]])}</text>')
    parts.append('<text x="24" y="576" class="sub">量级按一次任务必须理解的相关代码定义，不按仓库总大小定义；业务例子是与实验依赖结构相近的直观类比。</text>')
    parts.append('<text x="24" y="598" class="sub">固定系统上下文约1.25万Token，因此任务越大，总Token降幅越接近代码载荷降幅。</text>')
    parts.append('<text x="24" y="620" class="sub">推理与最终输出没有稳定下降；大型档AIRP输出比baseline累计多3 Token，已计入总Token。编辑、测试和修复循环未测。</text>')
    parts.append('</svg>')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(parts), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'levels': len(levels),
                      'quality_gate': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
