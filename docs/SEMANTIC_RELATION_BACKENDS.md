# Semantic relationship backends

AIRP v0.9 adds an optional language-semantic overlay above the existing
Tree-sitter and Python AST graph. Static relationships remain the zero-dependency
fallback. Exact facts from a language server replace matching static candidates
and extend callers, callees, affected tests, task context, and edit frontiers.

## Data and invalidation model

Every overlay records the provider, normalized symbol endpoints, confidence,
and a SHA-256 fingerprint of the indexed file paths and content hashes. AIRP
ignores the complete overlay as soon as the source snapshot changes. It never
mixes semantic facts from one revision with source from another revision.

Supported confidence levels are `semantic_exact` and `semantic_inferred`.
Imported endpoints must resolve to existing AIRP symbol IDs. Duplicate
`source`/`target`/`kind` edges are collapsed, and an exact semantic edge takes
precedence over the matching `static_candidate` edge.

Inspect the current snapshot before importing an external provider document:

```shell
airp --repo TARGET index
airp --repo TARGET semantic-status
airp --repo TARGET semantic-import --file semantic-edges.json
```

`semantic-status` reports `absent`, `active`, or `stale` and exposes the current
index fingerprint without source text.

## Rust Analyzer

Install the official component and build the overlay after indexing:

```shell
rustup component add rust-analyzer
airp --repo TARGET index
airp --repo TARGET semantic-build rust
```

AIRP advertises rust-analyzer's server-status extension and waits for
`quiescent: true` before requesting Call Hierarchy. This avoids accepting empty
results while Cargo metadata and the semantic database are still loading.

## TypeScript language server

Install TypeScript and the language server in the target project, then point
AIRP at the local executable when it is not on `PATH`:

```shell
npm install --save-dev typescript@5.9.3 typescript-language-server@6.0.0
airp --repo TARGET index
airp --repo TARGET semantic-build typescript \
  --server TARGET/node_modules/.bin/typescript-language-server \
  --server-arg=--stdio
```

On Windows, use `typescript-language-server.cmd`. The exact TypeScript 5.9.3
and language-server 6.0.0 pair is covered by the real-process verification.
TypeScript 7.0.2 did not expose the `tsserver.js` interface required by that
language-server release and is therefore not claimed as supported.

## Bounded execution

`semantic-build` examines at most 200 callable symbols by default and accepts
`--max-symbols` up to 5,000. Each LSP request has a bounded timeout. Per-symbol
failures are returned as diagnostics; server startup, capability, workspace, or
timeout failures abort the import, preserving the previous active overlay.

Run the protocol tests and real backend check with:

```powershell
python -m pytest -q tests/test_semantic.py tests/test_lsp_semantic.py
python scripts/check_semantic_backends.py
```

The real check creates disposable Rust and TypeScript projects. It requires
`rust-analyzer`, npm, network access for the pinned temporary TypeScript
packages, and AIRP's optional Tree-sitter language pack.

## Security and current limits

Language servers execute as local processes and can read the repository. Use
them only with trusted projects and trusted executables. AIRP does not send
source to a model or remote service, but package managers and language servers
have their own network and telemetry behavior.

The current adapter imports Call Hierarchy facts for functions and methods. It
does not yet import type hierarchies, definitions, generated-code mappings,
macro expansion relationships, or framework runtime registrations. The
real-process check proves protocol and edge correctness on small repositories;
it is not a new Token-saving benchmark or evidence of universal completeness.
