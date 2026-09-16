# AIRP Codex Plugin

AIRP uses a `UserPromptSubmit` hook to build repository evidence locally and inject it before the first model request. This removes the extra model → MCP → model round trip that caused short code tasks to consume more total tokens even when AIRP's response itself was compact.

When AIRP actually enables sufficient context, the injected receipt includes a bounded estimated context-saving percentage and a final-response footer. The skill appends that footer exactly once to the final answer. Abstained, skipped, partial, and failed routes omit it. This is a deterministic UTF-8-byte/tool-envelope proxy against native retrieval, not billed tokens or total conversation cost.

The hook applies to code tasks of every size. Exact symbol and file questions receive a small narrow pack; multi-symbol work receives a balanced pack; dependency, call-chain, and impact questions receive a broader pack. It also selects understand, impact, edit, or test evidence from the task semantics. Non-code chat adds no context. The injected block tells the agent which source evidence is sufficient and when a focused follow-up read is necessary.

The expanded local audit covers 24 tasks across exact facts, behavior reasoning, workflow semantics, relationship impact, hook protocol, and language scope. AIRP produced sufficient evidence for 24/24 tasks while reducing hook payload tokens by 20.01% relative to the compact MCP JSON response. The paired 48-call Agent experiment reduced total model tokens by 59.06%; a subsequent targeted fix added complete transitive bodies for value-computation tasks and deterministic cardinality facts for collection-count questions.

A later paired experiment covers four fixed public repositories, Python, JavaScript, Rust, and C, 268–5,054 production source lines, and executable edit validators. Both groups completed 12/12 tasks. AIRP reduced total model tokens by 18.54% and tool calls by 22.00%; read and relationship tasks saved 46.66%, while edit tasks saved 6.22%. Results varied from a 54.14% reduction on fd/Rust to a 26.39% increase on sds/C. Treat `status: partial` as a warning that focused repository inspection remains necessary.

V32 adds an exact-symbol fast-start path for large unindexed repositories. On fixed Django, NestJS, ripgrep, and Redis commits spanning 56,409–532,132 indexed source lines, all eight paired answers were correct. AIRP reduced total model tokens by 65.83%, tool calls by 88.89%, and model-stage latency by 35.30%. Fast-start context arrived in 0.50–3.71 seconds and remained 616–1,898 characters. These four tasks are exact local reading tasks; they do not establish the same reduction for broad impact analysis or large edits.
V34 adds a bounded edit frontier for large repositories: the target implementation plus budgeted declarations, direct dependencies, tests, representative callers, and a focused validation hint. Four large Python, TypeScript, Rust, and C edit tasks passed in both groups; total model tokens fell 38.31%, tool calls 26.67%, and latency 15.77%. Python, TypeScript, and Rust saved tokens; the C task increased 1.57%, so C validation guidance still needs a post-change model replication.

V35 adds evidence obligations, marginal information-value pruning, atomic context packing, post-pack sufficiency checks, and a 15% estimated-saving activation margin. On the same deterministic large-repository tasks, Python, TypeScript, and Rust context characters fell 17.58% in aggregate while all three remained sufficient. This is a zero-model-call context result and does not replace the V34 paired-model claim.

V0.7.1 withholds all source bodies when evidence is partial or atomic packing cannot preserve sufficiency. The compact diagnostic names the reason and next action. Every enabled, skipped, or failed decision is written to a bounded local `.airp/hook-events.jsonl` journal containing no prompt, repository path, symbol name, or source text. Run `python -m airp --repo TARGET hook-report` to inspect activation, latency, and deterministic cost estimates. These estimates are not billed tokens and must be paired with host usage records in a field trial.

Edit packs now include exact line locations, repository-specific validation hints, exception-boundary diagnostics, and warnings for local error suppression such as `errors="ignore"`, Rust `.ok()`, and empty catch blocks. A targeted Python behavior edit passed in both groups while AIRP reduced model tokens by 73.08% and tool calls from 14 to 4.

AIRP v0.9 adds a revision-bound semantic relationship overlay. Optional Rust Analyzer and TypeScript Language Server adapters import exact Call Hierarchy edges, replace matching static candidates, and automatically become stale after any indexed source change.

AIRP v0.8 shares one context compiler across Codex, Claude Code, and DeepSeek Harness Hook adapters. The repository also generates a self-contained Cursor MCP configuration and exposes a generic `precontext` JSON/text command. Host names are recorded in the privacy-preserving local field-trial journal. Generated integrations pin a validated Python 3.11+ interpreter.

The default plugin exposes no MCP server. AIRP's standalone CLI and MCP adapter remain in the project for explicit transaction workflows and compatibility, but they no longer impose tool schema or post-tool inference costs on every task.

The hook reads source files in the active repository and places selected excerpts in model context. Codex therefore requires the user to review and trust the installed hook definition. The plugin bundles the pure-Python AIRP runtime. The repository installer records the Python 3.11+ interpreter used during installation in the user's installed copy, so later hook calls do not depend on a shell alias. Python repositories work without third-party runtime packages. Install the optional `tree-sitter-language-pack` dependency into that Python environment for JavaScript, TypeScript, TSX, Go, Rust, Java, C, C++, C#, Ruby, and PHP parsing.

The installer validates Python 3.11+ and pins its absolute path into the installed Hook definition. The clean-environment check creates a new stdlib-only virtual environment and verifies Hook activation plus diagnostic reporting.

Validate from the AIRP project root:

```powershell
.\.venv\Scripts\python.exe scripts/check_plugin.py
.\.venv\Scripts\python.exe scripts/check_clean_install.py
```
