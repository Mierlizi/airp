# Cross-client compatibility

AIRP v0.8 separates its context-selection protocol from the AI coding host. The
same repository evidence can therefore be delivered through a native prompt
hook, a generated pre-context command, or an MCP server.

## Support matrix

| Host | Integration | Verified in this repository | Current limit |
| --- | --- | --- | --- |
| Codex | Native `UserPromptSubmit` hook and bundled skill/MCP server | Installed plugin, hook, regression suite, and clean-install checks | Requires Python 3.11 or newer |
| Claude Code | Native `UserPromptSubmit` hook in a Claude plugin | Manifest, hook contract, generated plugin, and clean subprocess execution | Claude Code CLI was unavailable for the v0.8 release check, so the desktop/CLI host path still needs field validation |
| DeepSeek Harness | Generated bundle using the official `@deepseek-ai/dsh-hooks-codex` bridge | Bundle/config generation and clean subprocess execution | DeepSeek Harness was unavailable for the v0.8 release check and its plugin API is still marked developer preview |
| Cursor | Generated stdio MCP configuration | Configuration and bundled launcher startup | MCP tool schemas and calls add overhead; it does not provide the same automatic prompt-hook path |
| Custom harnesses | `airp precontext` text or JSON output | CLI behavior, decision schema, and host-attributed diagnostics | The harness must call the command before constructing the model request |

The model provider and the coding host are separate concerns. DeepSeek models,
for example, can be used through an OpenAI-compatible or Anthropic-compatible
API, but AIRP must be connected to the agent or harness that assembles the
model's context.

## Generate a local integration

Run these commands from an AIRP checkout. Each command writes a self-contained
bundle to an empty directory and pins the Python interpreter used to create it.

```powershell
python scripts/configure_client.py claude-code --output .airp-clients/claude
python scripts/configure_client.py deepseek-harness --output .airp-clients/dsh
python scripts/configure_client.py cursor --output .airp-clients/cursor
```

For Claude Code, test the generated plugin directly:

```powershell
claude --plugin-dir .airp-clients/claude
```

For DeepSeek Harness, install the official compatibility package and pass the
generated patch file to DSH. The generator prints the exact local commands and
paths because DSH packaging may change while its extension API is in preview.

For Cursor, merge the generated `mcpServers.airp` entry from
`.airp-clients/cursor/mcp.json` into the project's `.cursor/mcp.json`. Preserve
other servers already configured in that file.

## Generic pre-context interface

Any harness that can run a local command can request AIRP context without MCP:

```powershell
airp precontext "trace the authentication failure" --repo . --host generic --format json
```

The JSON result includes the activation decision, evidence, estimated context
cost, and host identifier. `--format text` emits only the context selected for
the model. The harness should omit empty text and retain its normal repository
tools as a fallback when AIRP reports insufficient evidence.

## Diagnostics and privacy

AIRP records the host name with each local diagnostic event so field trials can
compare activation, cost, and failure behavior by integration. Source text is
not included in the diagnostic event log. Generated bundles contain absolute
local paths and should remain local; `.airp-clients/` is ignored by Git.

Run the cross-client process check with:

```powershell
python scripts/check_integrations.py
```

This check validates generated files and launches each bundled entry point in a
clean subprocess. It does not replace an end-to-end test inside a host that is
not installed on the machine.

## Upstream interface references

- [Claude Code hooks](https://code.claude.com/docs/en/hooks-guide)
- [Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference)
- [Claude Code plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces)
- [DeepSeek Harness Codex hook bridge](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/hooks/hooks-codex/README.md)
- [DeepSeek Harness plugin publishing](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.md)
- [Cursor MCP support](https://docs.cursor.com/context/model-context-protocol)
