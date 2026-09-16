"""Host-neutral request and response contracts for AIRP context injection."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SUPPORTED_HOSTS = ('codex', 'claude-code', 'deepseek-harness', 'generic')


@dataclass(frozen=True)
class ContextRequest:
    host: str
    root: Path
    prompt: str
    max_chars: int = 7800

    @classmethod
    def from_hook(cls, payload: dict, host: str = 'codex') -> 'ContextRequest':
        if host not in SUPPORTED_HOSTS:
            raise ValueError(f'Unsupported AIRP host: {host}')
        if not isinstance(payload, dict):
            raise TypeError('Hook payload must be a JSON object')
        return cls(
            host=host,
            root=Path(payload.get('cwd') or '.').resolve(),
            prompt=str(payload.get('prompt') or ''),
        )


def render_hook_output(context: str | None) -> dict | None:
    """Render the shared Claude Code, Codex, and dsh hook output shape."""
    if not context:
        return None
    return {'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': context,
    }}
