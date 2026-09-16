# Security Policy

## Supported versions

Security fixes are applied to the latest published AIRP release.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose source code, overwrite repository files, or execute unintended commands. Contact the maintainer privately through the security-reporting channel configured on the GitHub repository. Include the AIRP version, operating system, reproduction steps, and expected impact.

## Trust model

AIRP adapters run a local `UserPromptSubmit` hook in Codex, Claude Code, and DeepSeek Harness; Cursor and other MCP clients launch the bundled stdio server. It reads supported source files in the active repository, builds task-specific evidence locally, and places selected excerpts in model context. It does not call a model API itself. Whether those excerpts leave the machine depends on the host coding agent and its model configuration. DeepSeek or another model endpoint does not receive source from AIRP directly; the configured host decides what context it sends.

AIRP ignores symbolic links and common dependency/build directories. Use `.airpignore` for additional repository-specific exclusions. Review the generated Hook or MCP configuration before installation and use AIRP only on repositories whose local code and test commands you trust. Optional `semantic-build` commands execute the configured language-server binary with repository access; install language servers from trusted sources and review their network and telemetry policies.
