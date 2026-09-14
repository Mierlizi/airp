# V35 Reliability-Constrained Context Routing

Date: 2026-09-14

## Scope

V35 targets Python, TypeScript/JavaScript, and Rust. C/C++ remains outside the
stable commercial-benefit claim. The implementation adds:

1. task-specific evidence obligations for targets, behavior dependencies, and
   available direct tests;
2. marginal information-value selection with required-evidence priority;
3. low-value pruning in both persistent-index and large-repository fast paths;
4. atomic context blocks with post-pack sufficiency revalidation;
5. deterministic total-cost estimation and a 15% activation margin;
6. compact receipts with activation reason, omitted-block count, evidence size,
   and a stable payload hash.

## Deterministic regression result

The repositories and tasks are identical to V34. No model call was used for this
measurement.

| Repository | Language | V34 chars | V35 chars | Change | V35 status |
| --- | --- | ---: | ---: | ---: | --- |
| Django | Python | 2,727 | 2,609 | -4.33% | sufficient |
| NestJS | TypeScript | 2,560 | 1,802 | -29.61% | sufficient |
| ripgrep | Rust | 2,061 | 1,645 | -20.18% | sufficient |
| Redis | C, diagnostic only | 2,531 | 2,413 | -4.66% | sufficient |

The three target-language rows fell from 7,348 to 6,056 characters, a 17.58%
reduction. All four rows together fell from 9,879 to 8,469 characters, a 14.27%
reduction. NestJS pruned one redundant test block and ripgrep pruned one
low-value dependency block. Django kept both test blocks because the current
marginal-value rule did not classify either as redundant.

These are context-character measurements, not model-token or end-to-end task
savings. A paired model experiment is still required before changing the V34
model-level benefit claim.

## Correctness gates

- 75 repository and benchmark tests passed.
- A target implementation always outranks other required evidence.
- A required direct test that does not fit changes the evidence state to
  `blocked-partial`.
- Context is never sliced inside a source block.
- If an ordered high-priority block does not fit, lower-priority blocks cannot
  pass it.
- A tiny exact lookup abstains when the estimated saving is below 15%.
- Existing small useful, behavior-dependency, and large first-use paths remain
  active in regression tests.

## Limitations

The cost model is deterministic and intentionally conservative, but its
320-byte tool envelope, 160-byte search envelope, repository-discovery term,
and 15% margin are initial calibration values. They must be frozen before the
held-out model experiment and revised only from training repositories. The
current result does not establish confidence intervals or cross-repository
stability.
