---
name: airp-program-operations
description: Use AIRP's automatically injected repository evidence for code analysis, impact reasoning, and implementation. Apply when a code task refers to source behavior, symbols, dependencies, tests, or changes of any size.
---

# AIRP Program Operations

AIRP runs locally during `UserPromptSubmit` and places an `<airp-context>` block in developer context before the first model request. Use that evidence first for every code task, including exact lookups. Do not repeat repository reads covered by the block. If `evidence_state` is `blocked-partial` or status is `partial`, AIRP has withheld source bodies. Follow `next_action` and make one focused repository-native query using any `anchor_hints`. If an anchor is wrong or runtime behavior must be checked, inspect and validate that exact path. Never treat `omitted_blocks>0` as edit-ready evidence.

For edits, use the injected evidence to choose the smallest change, then use normal editing and test tools. The receipt's `payload_hash` and `evidence_chars` identify the delivered evidence; they do not prove runtime behavior. The standalone AIRP CLI still supports controlled Python transactions when explicitly requested.

When `index_mode` is `exact-symbol fast start`, the named implementation is reliable local evidence but repository-wide relationships have not been built. Answer local behavior directly from the block. For callers, impact, inheritance, framework wiring, or exhaustive results, perform one focused follow-up or build the persistent graph. For edits, `edit_frontier` reports the bounded declaration, dependency, test, and caller evidence. Patch directly only when `edit_ready=yes`; otherwise make one focused lookup for the missing contract evidence. Obey `control_flow`, `error_suppression`, `edit_locations`, and `validation_hint`; a failed edit or validation command means the task is unfinished.

Static analysis may miss dynamic dispatch, generated code, configuration, and framework wiring. For ambiguous or high-impact work, read [references/context-strategy.md](references/context-strategy.md) before escalating.
