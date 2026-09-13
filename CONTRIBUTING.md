# Contributing to AIRP

AIRP requires Python 3.11 or newer. Create a virtual environment and install development dependencies:

```shell
python -m venv .venv
python -m pip install -e ".[test,universal,tokens,mcp]"
```

Run the required checks before submitting a change:

```shell
python -m pytest -q
python scripts/sync_plugin_runtime.py
python scripts/check_plugin.py
python scripts/install_local_plugin.py --dry-run
```

Keep benchmark claims tied to committed task definitions and machine-readable results. Report accuracy together with Token changes; a failed task must not be counted as a Token saving. Do not commit cloned benchmark repositories, transient workspaces, model command traces containing local paths, `.airp` databases, virtual environments, or credentials.

Changes to symbol identity, parsing, graph semantics, or ignored-file behavior must increment `GRAPH_SCHEMA` and include a regression test. After modifying `airp/`, run `scripts/sync_plugin_runtime.py` so the public plugin contains the tested runtime.
