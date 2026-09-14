# Adaptive context strategy

AIRP should minimize the expected cost of a successful task, rather than minimize context bytes in isolation. Escalate evidence only when the current evidence cannot support the next decision.

## Evidence ladder

If the injected block says `index_mode: exact-symbol fast start`, AIRP parsed only definition candidates plus a bounded edit frontier. Use it for local implementation behavior. For edits, `edit_ready=yes` means at least one declaration, direct dependency, test, or caller was included; `edit_ready=no` requires one focused contract lookup. Build the persistent graph or make a focused follow-up query before answering exhaustive callers, impact, inheritance, or dynamic wiring questions.

1. Index and request a narrow task context. Treat ranked anchors and static graph edges as hypotheses.
   If the injected block says `status: partial`, assume a focused repository read is still required. Do not expand the AIRP payload and then repeat broad discovery: inspect the missing identifier, declaration, or validation path directly. In the V19 multi-repository experiment, all three partial tasks increased model tokens while all nine sufficient tasks reduced them.
2. If the anchor is absent or wrong, reformulate with concrete domain terms or use an exact `airp_query`. Use balanced breadth once when the task spans more than one independent area.
3. Read the full target symbol or file when exact control flow, constants, decorators, comments, or module state determine the answer.
4. Use the repository's language server, type checker, compiler, generated-code workflow, or ordinary text search when AIRP reports an unsupported language or the question requires exhaustive results.
5. Run focused runtime checks, then the required test suite, when static evidence cannot establish behavior. Dynamic dispatch, reflection, dependency injection, framework registration, configuration, plugins, and monkey patching require this step.

Stop escalating when the evidence supports a specific action and its validation. If two sources disagree, prefer compiler/runtime evidence and report the static-analysis uncertainty.

## Budget and reuse

- Start with the smallest intent that answers the immediate question.
- Keep automatic response shaping for ordinary work. Request the full response only for retrieval audits; its selection and accounting fields consume additional context.
- Accept a full-file cost bypass when AIRP selects it for one small read-only target. It is chosen only when the complete file fits the budget and costs less than the compact symbol response.
- Increase breadth before greatly increasing the byte or token budget when a second subsystem is missing.
- Increase the budget when the right anchors are present but whole symbols appear under `omitted`.
- Reuse a receipt only while every omitted source block is still available in the current model context. Receipts track delivery, not model memory.
- Use exact token mode for measured experiments. Treat byte mode as a fast operational bound.

## Editing confidence

Before a Python edit, require one resolved editable target, its current hash, and enough context to preserve the contract. Preview the diff, apply inside a transaction, validate, run tests, then commit the AIRP transaction. For non-Python edits, use the normal language-aware editor and retain AIRP only for evidence gathering.

Treat `control_flow` and `error_suppression` as static proof obligations. An exception outside a protected region cannot be converted by its handler, and a lossy operation such as `errors="ignore"`, `.ok()`, or an empty catch can make the requested failure behavior unreachable. Use `edit_locations` for a narrow patch and `validation_hint` for the first focused check.
