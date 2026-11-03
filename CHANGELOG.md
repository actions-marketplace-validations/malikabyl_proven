# Changelog

## 0.2.0 — 2026-11-03

From the first field test, on 272 files of a production legal bot: 2,204 issues, most of them false alarms about the environment. On a model of that project the same files now give 4 issues instead of 10, all real.

- **The report says what it measured with**: the project root, the Python, the requirements files and the environments it read packages from (`measured` in `--json`, in hooks and in the MCP tool).
- **Project root without `proven.toml`**: the nearest folder with `pyproject.toml`, `setup.cfg`, `setup.py`, `requirements.txt` or `.git` — not the folder being checked.
- **The project's virtual environment**: packages in `.venv`, `venv`, `env` or `$VIRTUAL_ENV` count as installed (P1); names in them are checked by reading their code, without importing it (P2).
- **Module → package from metadata**: `import dotenv` is matched with `python-dotenv` from the installed package's metadata, not only from the built-in list.
- **No requirements file**: one P1 issue for the project instead of one per import.
- **P3**: local addresses (`localhost`, `127.0.0.1`, `0.0.0.0`, `*.localhost`, `*.local`) are not facts.
- **P4**: a public function that returns model output must be annotated `-> Unverified`.
- **P5**: a waiver can go inside the handler: `pass  # proven: allow P5: <why>`.
- **P6**: a project with no tests at all gets one issue instead of one per function.

## 0.1.0 — 2026-11-03

First release: the six rules, `proven-judge` on PyPI, the GitHub Action, the pre-commit hook and the MCP server.
