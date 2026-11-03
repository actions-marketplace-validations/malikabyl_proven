# Proven — specification

Version 0.2.0.

Proven is Python plus a judge. The syntax does not change: any Proven code is plain Python. What changes is which code counts as **accepted**.

**Principle:** nothing exists until it is proven.

The judge reads code statically, before it runs. The only exception is P2: to check names inside modules, the judge imports them.

---

## P1 — Imports are real

Every external package (not from the standard library and not from your project):

- is listed in a requirements file (by default `requirements.txt`);
- is pinned to a version with `==` (or with a direct `@` reference);
- is installed, if `check_installed` is on.

**Why.** AI models invent plausible package names. Attackers register packages under such names and put malicious code in them. An unpinned version means that tomorrow different code gets installed than today.

```python
import anthropik        # P1: the package is not in requirements — it may be invented
import requests         # P1: if requirements say "requests>=2.31"
```

**Not checked:** an import inside `try ... except ImportError` — that is an optional dependency.

**Import name ≠ package name.** `import yaml` is installed as `pyyaml`, `import bs4` as `beautifulsoup4`. The judge reads this from the metadata of installed packages (in its own environment and in the project's `.venv`); the common cases are also built in, and your own go in `[proven.import_map]`.

**Which environment.** A package counts as installed if it is in the judge's environment or in the project's virtual environment (`$VIRTUAL_ENV`, or `.venv`, `venv`, `env` in the project root). If no requirements file is found at all, that is one issue for the project, not one per import.

---

## P2 — Calls are real

Every name taken from a module exists in the installed version:

- `import os.pathz` — there is no such module;
- `from os import pathz` — there is no such name;
- `json.loadz(...)` — there is no such function;
- `date.todayy()` — the class has no such attribute;
- `from helpers import fake` — your module `helpers.py` has no `fake`.

Code that does not parse as Python is P2 too: it is a construct from another language.

**Why.** The most common hallucination in code is a plausible name that does not exist.

**Not checked:**
- methods on objects (`response.jsonn()`) — the object's type is unknown before the code runs; tests catch these (P6);
- names shadowed by a local variable or a parameter;
- modules with dynamic attributes (`__getattr__`).

**Safety.** To look inside a package from its own environment, the judge imports it — only the standard library and installed packages, never your own code. Packages from the project's `.venv` are read as source code, not imported; a package with `__getattr__` or `import *` at its top is not judged. Turn P2 off with `check_attributes = false`.

---

## P3 — No facts from memory

Real-world values are not typed into code as literals.

1. **Named facts.** A variable whose name contains `MRP`, `MZP`, `VAT`, `TAX`, `TAX_RATE`, `INTEREST_RATE` or their Russian forms `МРП`, `МЗП`, `НДС`, `НАЛОГ`, `СТАВКА` (the full list is `fact_names`) cannot get a number directly. MRP and MZP are Kazakhstan's monthly calculation index and minimum wage, which fines, fees and benefits are counted in.

   ```python
   MRP = 3932                                   # P3
   MRP = Fact(4325, source="Law on the national budget for 2026–2028",
              date="2026-01-01")                # accepted
   ```

2. **A Fact must have** a non-empty `source` and a `date` in `YYYY-MM-DD` format.

3. **A Fact gets old.** If the date is more than `max_age_days` old (400 by default), that is an issue: "fact is out of date — check it against the source". Last year's index stops passing on its own.

4. **Law article numbers.** A string with `Art. 46`, `Article 46`, `§ 46`, the Russian `ст. 46`, `статья 46`, `статьи 46` or the Kazakh `46-бап`, `бап 46` is an issue: the article number comes from the database.

5. **Addresses.** A string with `http://` or `https://` is an issue if its domain is not in `allowed_url_hosts`. Local addresses are always allowed: `localhost`, `127.0.0.1`, `0.0.0.0`, `*.localhost`, `*.local`.

**Not checked:** docstrings and strings inside `Fact(...)`.

**Limit.** The judge checks that a fact has an origin, not that the value is correct.

---

## P4 — Model output is verified

Everything a model returns is **unverified** (`Unverified`). Unverified data must not be passed to a **sink**: sending it to a person, writing it to a database or a file. Only a **sanitizer** — `verify()` — makes it verified.

```python
raw = llm(question)              # unverified
send(phone, raw)                 # P4
send(phone, f"Answer: {raw}")    # P4 — f-strings, .strip() and so on are unverified too
send(phone, verify(raw, check))  # accepted
```

**Forgery.** `Verified(...)` can only be created inside `verify()`. A direct `Verified(...)` call in code is P4.

**Returning it.** A public function (no `_` in front) that returns unverified data must be annotated `-> Unverified`, so that whoever calls it knows. Otherwise it is P4: the boundary of a module is where an answer leaves for a web handler, a bot or a print.

**How the judge recognises unverified data:**
- a call to a source function (`sources`): `llm`, `ask_llm`, `call_llm`, `ask_claude`, `unverified`, `dify_chat`, `*.messages.create`, `*.chat.completions.create`, `*.responses.create`;
- a function that returns unverified data (the judge finds these itself) or is annotated `-> Unverified`;
- a parameter annotated `Unverified`;
- any expression that involves unverified data.

**Default sinks (`sinks`):** `send`, `send_message`, `send_text`, `reply`, `answer_user`, `whatsapp_send`, `wazzup_send`, `insert`, `insert_one`, `upsert`, `save`, `write`.

**Default sanitizers (`sanitizers`):** `verify`, `verify_answer`, `check_citations`.

The lists are extended in `[proven.taint]`. A name without a dot is compared with the last part of the call (`send` also catches `client.send`); a name with a dot is compared with the ending (`messages.create`).

**Limit.** The analysis is cautious: if unverified data takes any part in an expression, the result is unverified. To get past it on one line, use a waiver with a reason.

### `proven_types.py`

```python
from proven_types import Unverified, Verified, unverified, verify, NotVerified, Fact

raw = unverified(resp.json()["answer"])   # mark external text
ok = verify(raw, checker)                 # checker(text) -> a list of problems
                                          # empty: Verified, otherwise NotVerified
```

`checker` is your check: every quote is in the database word for word, every cited article exists, the version in force is the one quoted. This is the "judge" of the answer.

---

## P5 — Errors are not swallowed

- A bare `except:` is an issue unless it ends with `raise`.
- A handler that consists only of `pass`, `...`, `continue` or `return None` is an issue.

```python
try:
    data = json.loads(text)
except:                       # P5
    pass

except json.JSONDecodeError:  # accepted
    log.warning("not JSON: %.80s", text)
    raise
```

**Why.** Generated code often "works" because it silently hides the failure.

---

## P6 — Functions are proven by tests

1. Every public top-level function (a name without `_`) is **called** in at least one test.
2. Every public class is **used** in at least one test.
3. Every test (`test_*`) checks something: it has an `assert` with an expression, `pytest.raises` or an `assert*` call.
4. `assert True`, `assert 1` are issues: a check with a constant checks nothing.

Tests are looked for in the `tests` folders (files `test_*.py` and `*_test.py`).

5. If there are no test files at all, P6 is one issue for the project ("no tests found …") instead of one per function.

**Limit.** Functions and tests are matched by name. Class methods are not required separately.

---

## P0 — A waiver needs a reason

```python
MRP = 3932  # proven: allow P3: computing an archived 2025 fine

# proven: allow P5: the external SDK throws anything, we log it above
```

- The comment goes on the same line or on the line above. A P5 waiver may also go inside the `except` handler.
- The reason is at least 5 characters long. Without a reason it is a P0 issue.
- The Russian keyword works too: `# proven: разрешить P3: reason`.

---

## Settings: `proven.toml`

The judge looks for `proven.toml` in the folder being checked and above it. The project root is the folder where the file is. Every key is optional.

```toml
[proven]
src = ["."]                        # where the code is
tests = ["tests"]                  # where the tests are
exclude = [".venv", "build"]       # replaces the default list of exclusions
requirements = ["requirements.txt"]
check_installed = true             # P1
check_attributes = true            # P2 (imports packages)
local_modules = []                 # your own modules outside the project folder
baseline = ".proven-baseline.json"

[proven.import_map]                # added to the defaults
yaml = "pyyaml"

[proven.facts]
names = ["FX_RATE"]                # added to the default list
max_age_days = 400
allowed_url_hosts = ["api.anthropic.com"]

[proven.taint]                     # added to the defaults
sources = ["ask_dify"]
sinks = ["notify_client"]
sanitizers = ["judge"]

[proven.rules]
disable = []                       # for example ["P6"]
```

---

## Command line

| Command | What it does |
|---------|--------------|
| `proven.py check [paths]` | Check. Exit code 0 — accepted, 1 — not accepted |
| `proven.py check --json` | The same as JSON: `{"accepted": bool, "issues": [...]}` |
| `proven.py check --no-baseline` | Show the old debt as well |
| `proven.py check --hook post` | Claude Code hook mode after a file is edited |
| `proven.py check --hook stop` | Claude Code hook mode before the work is finished |
| `proven.py check --github` | Also print GitHub Actions annotations and a job summary |
| `proven.py baseline` | Record the current issues as old debt |
| `proven.py explain P4` | Explain a rule |
| `proven.py rules` | List the rules |
| `proven.py init` | Create `proven.toml` |
| `proven.py mcp` | Run as an MCP server over stdio (`--no-installed-check` skips P1's installed check) |

The report starts with one line on what the judge measured with: the project root, the Python, the requirements files and the environments it read packages from (`measured` in `--json`). Then lines in the format `path:line:column  code  message`, and at the end, how to fix each rule that came up.

### Claude Code hooks

`--hook post` reads the event from stdin, takes `tool_input.file_path`, checks the project and shows the issues in that file only. If there are issues, the exit code is 2 and the report goes to stderr: Claude sees it and fixes the code.

`--hook stop` checks the whole project before Claude finishes its work. If there are issues, the exit code is 2 and the work continues. If the event is marked `stop_hook_active`, the judge lets it through so as not to loop forever. This mode is not on in `.claude/settings.json` by default — add it if you do not want Claude to hand in work with issues.

### MCP server

`proven mcp` speaks the Model Context Protocol over stdio: one JSON-RPC message per line, protocol versions 2024-11-05 to 2025-11-25, tools only.

| Tool | Arguments | Result |
|------|-----------|--------|
| `proven_check` | `path` — absolute path to the project or a file; `files` — optional list of files or folders | The report as text and `{"accepted": bool, "issues": [...]}` as structured content |
| `proven_explain` | `code` — a rule code such as `P4` | The rule and how to fix a violation |
| `proven_rules` | — | The list of rules |

The tools only read code, but `proven_check` imports installed packages for P2, like the command line does. While a tool runs, anything the imported packages print goes to stderr, not into the protocol.
