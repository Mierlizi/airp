"""Validated semantic-edge overlays produced by optional language backends."""
from __future__ import annotations

import hashlib
import json


SEMANTIC_SCHEMA = 1
SEMANTIC_CONFIDENCE = ('semantic_exact', 'semantic_inferred')
SEMANTIC_KINDS = ('call', 'reference', 'dependency', 'implementation', 'type')


def index_fingerprint(manifest: dict) -> str:
    """Identify the exact indexed source snapshot without exposing source text."""
    files = sorted((item['path'], item['hash']) for item in manifest.get('files', []))
    snapshot = {'graph_schema': manifest.get('schema_version'), 'files': files}
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def normalize_overlay(document: dict, manifest: dict, symbols: list[dict]) -> dict:
    """Validate an untrusted provider document against the current AIRP index."""
    if not isinstance(document, dict):
        raise TypeError('Semantic overlay must be a JSON object')
    if document.get('schema_version') != SEMANTIC_SCHEMA:
        raise ValueError(f'Semantic overlay schema_version must be {SEMANTIC_SCHEMA}')
    provider = document.get('provider')
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError('Semantic overlay provider must be a non-empty string')
    expected = index_fingerprint(manifest)
    if document.get('index_fingerprint') != expected:
        raise ValueError('Semantic overlay does not match the current source index')
    known = {item['id']: item for item in symbols}
    raw_edges = document.get('edges')
    if not isinstance(raw_edges, list):
        raise TypeError('Semantic overlay edges must be a list')
    normalized = {}
    for raw in raw_edges:
        if not isinstance(raw, dict):
            raise TypeError('Each semantic edge must be a JSON object')
        source, target = raw.get('source'), raw.get('target')
        if source not in known or target not in known:
            raise ValueError(f'Semantic edge endpoint is not an indexed symbol: {source} -> {target}')
        kind = raw.get('kind')
        if kind not in SEMANTIC_KINDS:
            raise ValueError(f'Unsupported semantic edge kind: {kind}')
        confidence = raw.get('confidence', 'semantic_exact')
        if confidence not in SEMANTIC_CONFIDENCE:
            raise ValueError(f'Unsupported semantic confidence: {confidence}')
        line = raw.get('line', known[source].get('start', 1))
        if not isinstance(line, int) or line < 1:
            raise ValueError('Semantic edge line must be a positive integer')
        edge = {
            'source': source,
            'target': target,
            'name': str(raw.get('name') or known[target]['name']),
            'kind': kind,
            'line': line,
            'confidence': confidence,
            'provider': provider.strip(),
        }
        key = (source, target, kind)
        previous = normalized.get(key)
        if previous is None or confidence == 'semantic_exact':
            normalized[key] = edge
    edges = sorted(normalized.values(), key=lambda item: (
        item['source'], item['target'], item['kind'], item['line']))
    return {
        'schema_version': SEMANTIC_SCHEMA,
        'provider': provider.strip(),
        'index_fingerprint': expected,
        'edges': edges,
    }


def merge_edges(static_edges: list[dict], semantic_edges: list[dict]) -> list[dict]:
    """Prefer semantic facts over matching static candidates and keep both otherwise."""
    semantic_keys = {
        (edge['source'], edge.get('target'), edge['kind']) for edge in semantic_edges
    }
    static = [edge for edge in static_edges
              if (edge['source'], edge.get('target'), edge['kind']) not in semantic_keys]
    confidence_order = {'semantic_exact': 0, 'semantic_inferred': 1,
                        'static_candidate': 2, 'unresolved': 3}
    return sorted([*semantic_edges, *static], key=lambda edge: (
        confidence_order.get(edge.get('confidence'), 9),
        edge['source'], edge.get('target') or '', edge['kind'], edge.get('line', 0)))
