# Security Policy

## Supported versions

Security fixes are applied to the latest published AIRP release.

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose source code, overwrite repository files, or execute unintended commands. Contact the maintainer privately through the security-reporting channel configured on the GitHub repository. Include the AIRP version, operating system, reproduction steps, and expected impact.

## Trust model

The Codex plugin runs a local `UserPromptSubmit` hook. It reads supported source files in the active repository, builds task-specific evidence locally, and places selected excerpts in model context. It does not call a model API itself. Whether those excerpts leave the machine depends on the host coding agent and its model configuration.

AIRP ignores symbolic links and common dependency/build directories. Use `.airpignore` for additional repository-specific exclusions. Review `plugins/airp/hooks/hooks.json` before installation and use AIRP only on repositories whose local code and test commands you trust.
