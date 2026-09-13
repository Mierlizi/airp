"""Render the optimized installed-agent real-source A/B result."""
import html
import json
from pathlib import Path

INPUT = Path('benchmarks/runs/integrated-real-source-v13.json')
OUTPUT = Path('benchmarks/runs/integrated-real-source-v13.svg')


def label(parts, x, y, value, css='body', anchor='middle'):
    parts.append(f'<text x="{x}" y="{y}" class="{css}" text-anchor="{anchor}">'
                 f'{html.escape(str(value))}</text>')


def main():
    data = json.loads(INPUT.read_text(encoding='utf-8'))
    if not data['completion_gate'] or not data['mcp_execution_gate']:
        raise SystemExit('Refusing to render an invalid comparison')
    base, airp = data['totals']['baseline'], data['totals']['airp']
    uncached_base = base['input_tokens'] - base['cached_input_tokens'] + base['output_tokens']
    uncached_airp = airp['input_tokens'] - airp['cached_input_tokens'] + airp['output_tokens']
    increase = 100 * (airp['total_model_tokens'] / base['total_model_tokens'] - 1)
    latency = 100 * (airp['latency_seconds'] / base['latency_seconds'] - 1)
    width, height = 1320, 760
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
             '<rect width="100%" height="100%" fill="#f7f9fb"/>',
             '<style>text{font-family:Microsoft YaHei,Segoe UI,Arial,sans-serif;fill:#17202a}'
             '.title{font-size:25px;font-weight:700}.sub{font-size:13px}.head{font-size:17px;font-weight:700}'
             '.value{font-size:25px;font-weight:700}.body{font-size:14px}.small{font-size:12px}</style>']
    label(parts, 24, 38, 'AIRP单入口优化：真实源码安装态Agent A/B', 'title', 'start')
    label(parts, 24, 67, '约1,850非空代码行 / 12个配对只读任务 / AIRP MCP 12/12次成功', 'sub', 'start')
    maximum = airp['total_model_tokens']
    for index, (name, value, color) in enumerate([
            ('Baseline', base['total_model_tokens'], '#95a5a6'),
            ('AIRP强制调用', airp['total_model_tokens'], '#c0392b')]):
        y = 130 + index * 105
        bar = value / maximum * 600
        label(parts, 80, y + 24, name, 'head', 'start')
        parts.append(f'<rect x="80" y="{y + 38}" width="{bar:.1f}" height="38" rx="6" fill="{color}"/>')
        label(parts, 92 + bar, y + 64, f'{value:,} Token', 'head', 'start')
    label(parts, 80, 355, f'净增加 {airp["total_model_tokens"] - base["total_model_tokens"]:,} Token（+{increase:.2f}%）', 'value', 'start')
    label(parts, 80, 386, f'每题平均 {base["total_model_tokens"]/12:,.0f} → {airp["total_model_tokens"]/12:,.0f} Token', 'body', 'start')
    cards = [
        ('语义答案通过', f'{base["successes"]}/12 → {airp["successes"]}/12', '双方均为91.7%'),
        ('平均墙钟耗时', f'{base["latency_seconds"]/12:.2f}s → {airp["latency_seconds"]/12:.2f}s', f'{latency:+.2f}%'),
        ('工具事件', f'{base["command_calls"]} → {airp["mcp_calls"]+airp["command_calls"]}', '旧版AIRP为48'),
        ('未缓存输入+输出', f'{uncached_base:,} → {uncached_airp:,}', f'{100*(uncached_airp/uncached_base-1):+.2f}%')]
    for index, (title, value, note) in enumerate(cards):
        x, y = 720 + (index % 2) * 285, 125 + (index // 2) * 160
        parts.append(f'<rect x="{x}" y="{y}" width="270" height="140" rx="10" fill="#fff" stroke="#d5d8dc"/>')
        label(parts, x + 16, y + 28, title, 'head', 'start')
        label(parts, x + 16, y + 72, value, 'value', 'start')
        label(parts, x + 16, y + 108, note, 'small', 'start')
    parts.append('<rect x="60" y="475" width="1200" height="190" rx="12" fill="#fff3cd"/>')
    label(parts, 82, 510, '结论与下一步', 'head', 'start')
    label(parts, 82, 542, '• 7个MCP模式收敛为1个，一次context自动索引；工具事件由旧版48降为23。', 'body', 'start')
    label(parts, 82, 574, '• 模块常量和大函数聚焦摘录修复证据缺口；最终遗漏修复的独立重复为3/3正确。', 'body', 'start')
    label(parts, 82, 606, '• 约1,850行仓库的精确查值不适合强制AIRP：固定轮次开销超过源码压缩收益。', 'body', 'start')
    label(parts, 82, 638, '• Skill现按任务选择：简单单文件查询用普通搜索，多文件或结构关系任务才调用AIRP。', 'body', 'start')
    label(parts, 24, 718, '边界：单一真实项目、12个只读事实问题；强制AIRP用于测开销，不代表选择性路由后的混合工作负载。', 'small', 'start')
    parts.append('</svg>')
    OUTPUT.write_text('\n'.join(parts), encoding='utf-8')
    print(json.dumps({'output': str(OUTPUT), 'increase_pct': increase}, ensure_ascii=False))


if __name__ == '__main__':
    main()
