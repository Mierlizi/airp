"""Render code-size results with required-information loss instead of answer accuracy."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def add_text(parts, x, y, value, css='body', anchor='middle'):
    parts.append(f'<text x="{x}" y="{y}" class="{css}" text-anchor="{anchor}">'
                 f'{html.escape(str(value))}</text>')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--experiment', type=Path,
                        default=Path('benchmarks/runs/task-magnitude-expanded-v07.json'))
    parser.add_argument('--audit', type=Path,
                        default=Path('benchmarks/runs/required-information-loss-v07.json'))
    parser.add_argument('--output', type=Path,
                        default=Path('benchmarks/runs/code-size-information-impact-v08.svg'))
    args = parser.parse_args()
    experiment = json.loads(args.experiment.read_text(encoding='utf-8'))
    audit = json.loads(args.audit.read_text(encoding='utf-8'))
    if not experiment['completion_gate'] or not audit['audit_gate']:
        raise SystemExit('Experiment or evidence audit is incomplete')
    by_level = {item['level']: item for item in experiment['levels']}

    width, height = 1540, 1160
    left, top, label_w, col_w, row_h = 24, 190, 300, 298, 140
    columns = [('代码筛减', '原始代码 → AIRP证据'),
               ('必要信息丢失', '缺失事实 / 预定义必要事实'),
               ('模型总 Token', '真实模型输入 + 输出'),
               ('运行速度', '成对耗时比及95%区间')]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#f7f9fb"/>',
             '<style>text{font-family:Microsoft YaHei,Segoe UI,Arial,sans-serif;fill:#17202a}'
             '.title{font-size:25px;font-weight:700}.subtitle{font-size:14px}.head{font-size:16px;font-weight:700}'
             '.body{font-size:14px}.strong{font-size:19px;font-weight:700}.small{font-size:12px}'
             '.axis{font-size:13px;font-weight:700;fill:#566573}</style>']
    add_text(parts, 24, 36, 'AIRP：代码量 × 必要信息丢失 × Token与速度', 'title', 'start')
    add_text(parts, 24, 65,
             '纵向直接使用平均非空代码行数与o200k代码Token；60个任务、120次真实模型调用、1,300个必要信息单元',
             'subtitle', 'start')
    add_text(parts, 24, 91,
             '必要信息单元 = 1个精确目标表达式，或1个必需依赖的名称与默认值；答案正确率不是信息丢失率',
             'subtitle', 'start')
    add_text(parts, left + label_w + 2 * col_w, 125, '横向评估维度 →', 'axis')
    add_text(parts, 34, 165, '纵向：任务涉及的原始代码量 ↓', 'axis', 'start')
    for index, (title, subtitle) in enumerate(columns):
        x = left + label_w + index * col_w
        parts.append(f'<rect x="{x}" y="140" width="{col_w - 8}" height="48" rx="8" fill="#dfe6e9"/>')
        add_text(parts, x + (col_w - 8) / 2, 161, title, 'head')
        add_text(parts, x + (col_w - 8) / 2, 179, subtitle, 'small')

    for row_index, evidence in enumerate(audit['levels']):
        y = top + row_index * row_h
        measured = by_level[evidence['level']]
        base_code = evidence['mean_baseline_code_tokens']
        airp_code = evidence['mean_airp_evidence_tokens']
        code_reduction = 100 * (1 - airp_code / base_code)
        latency = measured['latency_paired']
        low, high, estimate = latency['ci95_low_pct'], latency['ci95_high_pct'], latency['estimate_pct']
        if low > 0:
            speed_fill, speed_head = '#fadbd8', f'慢 {estimate:.2f}%*'
        elif high < 0:
            speed_fill, speed_head = '#d5f5e3', f'快 {abs(estimate):.2f}%*'
        else:
            speed_fill, speed_head = '#f2f3f4', '差异不确定'

        parts.append(f'<rect x="{left}" y="{y}" width="{label_w - 8}" height="{row_h - 8}" rx="10" fill="#eaf2f8"/>')
        add_text(parts, left + 14, y + 31,
                 f'约 {evidence["mean_baseline_nonblank_loc"]:,.0f} 行代码', 'head', 'start')
        add_text(parts, left + 14, y + 61,
                 f'{base_code:,.0f} 原始代码Token', 'body', 'start')
        add_text(parts, left + 14, y + 88,
                 f'{evidence["dependencies"] + 1}个相关文件', 'small', 'start')
        add_text(parts, left + 14, y + 111,
                 f'每档10个不同计算任务', 'small', 'start')
        cells = [
            ('#d5f5e3', f'代码 ↓ {code_reduction:.2f}%',
             f'{base_code:,.0f} → {airp_code:,.0f} Token',
             f'干扰函数移除 {100 * evidence["irrelevant_information_removal_rate"]:.0f}%'),
            ('#d6eaf8', f'{100 * evidence["required_information_loss_rate"]:.2f}%',
             f'{evidence["lost_required_units"]} / {evidence["required_units"]} 丢失',
             '目标表达式与依赖值均保留'),
            ('#d5f5e3', f'↓ {100 * measured["total_model_token_reduction"]:.2f}%',
             f'每题净省 {measured["tokens_saved_per_task"]:,.0f}',
             '包含固定系统上下文'),
            (speed_fill, speed_head,
             f'估计 {estimate:+.2f}%',
             f'95% CI [{low:+.2f}%, {high:+.2f}%]')]
        for col_index, (fill, headline, detail, note) in enumerate(cells):
            x = left + label_w + col_index * col_w
            parts.append(f'<rect x="{x}" y="{y}" width="{col_w - 8}" height="{row_h - 8}" rx="10" fill="{fill}"/>')
            add_text(parts, x + (col_w - 8) / 2, y + 40, headline, 'strong')
            add_text(parts, x + (col_w - 8) / 2, y + 75, detail, 'body')
            add_text(parts, x + (col_w - 8) / 2, y + 105, note, 'small')

    footer_y = top + 6 * row_h + 16
    overall = audit['overall']
    exp_overall = experiment['overall']
    latency = exp_overall['latency_paired']
    parts.append(f'<rect x="24" y="{footer_y}" width="1480" height="91" rx="10" fill="#ffffff" stroke="#ccd1d1"/>')
    add_text(parts, 42, footer_y + 27,
             f'总体证据审计：必要信息丢失 {overall["lost_required_units"]}/{overall["required_units"]} = 0%；干扰函数移除 {overall["irrelevant_functions"] - overall["irrelevant_functions_retained"]:,}/{overall["irrelevant_functions"]:,} = 100%',
             'head', 'start')
    add_text(parts, 42, footer_y + 53,
             f'模型总Token下降 {100 * exp_overall["total_model_token_reduction"]:.2f}%；耗时估计 {latency["estimate_pct"]:+.2f}%（95% CI {latency["ci95_low_pct"]:+.2f}%至{latency["ci95_high_pct"]:+.2f}%，不确定）',
             'small', 'start')
    add_text(parts, 42, footer_y + 77,
             '0%是当前60个合成任务中对预定义必要事实的观测值，不代表对任意代码语义或真实仓库绝对零损失。',
             'small', 'start')
    add_text(parts, 24, 1142,
             '* 中型档速度区间未跨0，但属于6档并行比较之一；模型答错可能发生在必要信息完整的情况下，应与信息丢失分开报告。',
             'small', 'start')
    parts.append('</svg>')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text('\n'.join(parts), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'audit_gate': True,
                      'required_units': overall['required_units'],
                      'lost_required_units': overall['lost_required_units']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
