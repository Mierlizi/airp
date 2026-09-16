"""Privacy-preserving local diagnostics for AIRP hook field trials."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean


EVENT_FILE = 'hook-events.jsonl'
MAX_EVENT_BYTES = 1_048_576
_ALLOWED_FIELDS = {
    'host', 'activation', 'reason', 'evidence_state', 'intent', 'breadth',
    'source_file_count', 'context_chars', 'evidence_chars', 'omitted_blocks',
    'expected_airp_units', 'expected_native_units', 'expected_followup_units',
    'expected_saving', 'minimum_saving', 'elapsed_ms', 'exception_type',
}


def _event_path(root: str | Path) -> Path:
    return Path(root).resolve(strict=True) / '.airp' / EVENT_FILE


def record_hook_event(root: str | Path, decision: dict) -> bool:
    """Append one bounded event containing no prompt, path, symbol, or source text."""
    try:
        path = _event_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size >= MAX_EVENT_BYTES:
            rotated = path.with_name(EVENT_FILE + '.1')
            try:
                path.replace(rotated)
            except OSError:
                return False
        event = {
            'schema_version': 1,
            'timestamp': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        }
        for key in _ALLOWED_FIELDS:
            value = decision.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                event[key] = value
        encoded = json.dumps(event, ensure_ascii=True, separators=(',', ':')) + '\n'
        with path.open('a', encoding='utf-8', newline='') as stream:
            stream.write(encoded)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def hook_report(root: str | Path) -> dict:
    """Aggregate locally observed routing decisions without exposing repository data."""
    path = _event_path(root)
    events = []
    for candidate in (path.with_name(EVENT_FILE + '.1'), path):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get('schema_version') == 1:
                events.append(event)
    hosts = Counter(event.get('host', 'unknown') for event in events)
    activations = Counter(event.get('activation', 'unknown') for event in events)
    reasons = Counter(event.get('reason', 'unknown') for event in events)
    latencies = [float(event['elapsed_ms']) for event in events
                 if isinstance(event.get('elapsed_ms'), (int, float))]
    enabled = activations.get('enabled', 0)
    count = len(events)
    airp_units = sum(int(event.get('expected_airp_units') or 0) for event in events)
    native_units = sum(int(event.get('expected_native_units') or 0) for event in events)
    return {
        'schema_version': 1,
        'events': count,
        'by_host': dict(sorted(hosts.items())),
        'by_activation': dict(sorted(activations.items())),
        'by_reason': dict(sorted(reasons.items())),
        'activation_rate': round(enabled / count, 4) if count else 0.0,
        'latency_ms': {
            'mean': round(mean(latencies), 3) if latencies else 0.0,
            'p50': _percentile(latencies, 0.50),
            'p95': _percentile(latencies, 0.95),
        },
        'estimated_units': {
            'airp': airp_units,
            'native': native_units,
            'saving': round(1.0 - airp_units / native_units, 4) if native_units else None,
            'basis': 'UTF-8 bytes plus deterministic tool-envelope estimate; not billed tokens',
        },
        'first_event_at': events[0].get('timestamp') if events else None,
        'last_event_at': events[-1].get('timestamp') if events else None,
        'privacy': 'No prompt, repository path, symbol name, or source text is recorded.',
    }
