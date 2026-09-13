"""Audit task-required evidence loss separately from model answer accuracy."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import statistics
import sys

import tiktoken

from run_task_magnitude_expanded_ab import FAMILIES, LEVELS, prepare_case


ROOT = Path(__file__).resolve().parents[1]
SIGNAL = re.compile(r'def signal_(\d+)\(value=(\d+)\)')


def normalized_return(source, target_name):
    match = re.search(rf'def {re.escape(target_name)}\(\):\s*\r?\n\s*return (.+)', source)
    if not match:
        return None
    return re.sub(r'\s+', ' ', match.group(1).strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'benchmarks/runs/required-information-loss-v07.json')
    args = parser.parse_args()
    os.environ.setdefault('TIKTOKEN_CACHE_DIR',
                          str(Path(sys.prefix) / 'share' / 'airp-token-cache'))
    encoding = tiktoken.get_encoding('o200k_base')
    rows = []
    for level, label, dependencies in LEVELS:
        for case_index in range(len(FAMILIES)):
            item = prepare_case(level, label, dependencies, case_index, encoding)
            sources = [entry['source'] for entry in item['baseline']['files']]
            baseline_code = '\n'.join(sources)
            airp_text = item['airp']['text']
            baseline_facts = {int(index): int(value)
                              for index, value in SIGNAL.findall(baseline_code)}
            airp_facts = {int(index): int(value)
                          for index, value in SIGNAL.findall(airp_text)}
            missing_or_wrong = sum(airp_facts.get(index) != value
                                   for index, value in baseline_facts.items())
            target_source = item['baseline']['files'][0]['source']
            target_preserved = (normalized_return(target_source, item['target_name'])
                                == normalized_return(airp_text, item['target_name']))
            required_units = dependencies + 1
            lost_units = missing_or_wrong + (not target_preserved)
            rows.append({
                'level': level, 'label': label, 'dependencies': dependencies,
                'case_index': case_index, 'family': item['family'],
                'baseline_code_tokens': len(encoding.encode(baseline_code)),
                'baseline_nonblank_loc': sum(bool(line.strip())
                                             for line in baseline_code.splitlines()),
                'airp_evidence_tokens': len(encoding.encode(airp_text)),
                'airp_evidence_nonblank_lines': sum(bool(line.strip())
                                                    for line in airp_text.splitlines()),
                'required_units': required_units, 'lost_required_units': int(lost_units),
                'required_information_loss_rate': lost_units / required_units,
                'target_expression_preserved': target_preserved,
                'dependency_facts_expected': dependencies,
                'dependency_facts_preserved': dependencies - missing_or_wrong,
                'irrelevant_functions_expected': 24 * dependencies,
                'irrelevant_functions_retained': len(re.findall(r'\bfiller_\d+_\d+\b', airp_text)),
            })
            print(json.dumps({'level': level, 'case': case_index,
                              'lost_required_units': int(lost_units)},
                             ensure_ascii=False), flush=True)

    levels = []
    for level, label, dependencies in LEVELS:
        chosen = [row for row in rows if row['level'] == level]
        required = sum(row['required_units'] for row in chosen)
        lost = sum(row['lost_required_units'] for row in chosen)
        irrelevant = sum(row['irrelevant_functions_expected'] for row in chosen)
        retained = sum(row['irrelevant_functions_retained'] for row in chosen)
        levels.append({
            'level': level, 'label': label, 'dependencies': dependencies,
            'tasks': len(chosen),
            'mean_baseline_code_tokens': statistics.mean(
                row['baseline_code_tokens'] for row in chosen),
            'mean_baseline_nonblank_loc': statistics.mean(
                row['baseline_nonblank_loc'] for row in chosen),
            'mean_airp_evidence_tokens': statistics.mean(
                row['airp_evidence_tokens'] for row in chosen),
            'required_units': required, 'lost_required_units': lost,
            'required_information_loss_rate': lost / required,
            'irrelevant_functions': irrelevant,
            'irrelevant_functions_retained': retained,
            'irrelevant_information_removal_rate': 1 - retained / irrelevant,
        })
    required = sum(row['required_units'] for row in rows)
    lost = sum(row['lost_required_units'] for row in rows)
    irrelevant = sum(row['irrelevant_functions_expected'] for row in rows)
    retained = sum(row['irrelevant_functions_retained'] for row in rows)
    report = {
        'metric_definition': {
            'required_unit': 'one exact target return expression or one required dependency name/default-value fact',
            'loss_rate': 'lost or incorrect required units divided by all required units',
            'code_size': 'o200k_base tokens and nonblank physical source lines before AIRP',
        },
        'tasks': len(rows), 'rows': rows, 'levels': levels,
        'overall': {
            'required_units': required, 'lost_required_units': lost,
            'required_information_loss_rate': lost / required,
            'irrelevant_functions': irrelevant,
            'irrelevant_functions_retained': retained,
            'irrelevant_information_removal_rate': 1 - retained / irrelevant,
        },
        'audit_gate': (len(rows) == 60 and all(row['target_expression_preserved']
                                               for row in rows)),
        'limitations': [
            'Measures explicit task-required facts, not all possible semantic information.',
            'Synthetic Python direct-dependency tasks only.',
            'A zero observed loss rate is not a population-level zero-loss guarantee.',
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'tasks': len(rows),
                      'required_units': required, 'lost': lost,
                      'audit_gate': report['audit_gate']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
