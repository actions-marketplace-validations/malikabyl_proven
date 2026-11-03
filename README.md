# Proven

**A judge for code written by AI.** The syntax is plain Python. The rules are against hallucinations.

The core principle: **nothing exists until it is proven.** A package exists if it is pinned in requirements. A function exists if it is in the installed version. A fact exists if it has a source and a date. A model's answer exists for the user once it has passed a check. A function works if a test proves it.

## Why

AI models write more code than people can read. The languages that win are the ones where the compiler acts as a reviewer: TypeScript became the #1 language on GitHub in 2025 ([GitHub Octoverse 2025](https://github.blog/news-insights/octoverse/octoverse-a-new-developer-joins-github-every-second-as-ai-leads-typeScript-to-1/)), and GitHub links this to typed languages making AI-assisted code more reliable. A new language is not the answer — models barely know new languages. What helps is the old language that models know best, with a strict judge on top of it. That is how TypeScript grew on top of JavaScript.

Proven does the same for Python, but it catches hallucinations rather than type errors.

## Six rules

| Code | Rule | What it catches |
|------|------|-----------------|
| P1 | Imports are real | Invented packages, unpinned versions, modules that are not installed |
| P2 | Calls are real | Functions, classes, attributes and modules that do not exist |
| P3 | No facts from memory | Official figures, tax rates, law article numbers and URLs typed into code by hand; outdated facts |
| P4 | Model output is verified | A model's answer goes to a user or a database without a check; a forged check |
| P5 | Errors are not swallowed | Bare `except`, silent `except: pass` |
| P6 | Functions are proven by tests | A public function with no test; a test that checks nothing |

Every rule in detail: [SPEC.md](SPEC.md).

## What it looks like

The code in [`examples/bad/bot.py`](examples/bad/bot.py) is how an AI model writes when nobody checks it — a legal-answers bot for Kazakhstan:

```python
import anthropik                     # a typo
MRP = 3932                           # Kazakhstan's monthly index, the 2025 value "from memory"

def handle(phone, question):
    raw = llm(question)
    answer = f"{raw}\n\nLegal basis: Article 46 of the Labour Code"
    try:
        json.loadz(answer)
    except:
        pass
    send(phone, answer)
```

The judge:

```
$ python proven.py check examples/bad
bad/bot.py:4:1  P1  package "anthropik" is not in requirements.txt — it may be invented
bad/bot.py:5:1  P1  version of "requests" is not pinned with == (now: ">=2.31")
bad/bot.py:7:1  P3  "MRP" is a number typed from memory — it needs a dated source
bad/bot.py:9:10  P3  address "adilet.zan.kz" typed by hand — move it to a config or allowed_url_hosts
bad/bot.py:12:1  P6  function "llm" is not called in any test
bad/bot.py:19:1  P6  function "send" is not called in any test
bad/bot.py:23:1  P6  function "handle" is not called in any test
bad/bot.py:25:14  P3  law article number typed by hand: "Article 46" — take it from the database
bad/bot.py:27:9  P2  module "json" has no name "loadz"
bad/bot.py:28:5  P5  bare except catches everything, even Ctrl+C and program exit
bad/bot.py:30:5  P4  unverified model output goes into "send"

How to fix:
  P1 Imports are real — Check the exact package name on PyPI, add a name==version line ...
  ...

Proven: 11 issue(s) in 1 file(s). Code rejected.
```

The same bot fixed according to the rules — [`examples/good/bot.py`](examples/good/bot.py):

```python
MRP = Fact(4325, source='Law of the Republic of Kazakhstan "On the Republican Budget '
                        'for 2026–2028" No. 239-VIII',
           date="2026-01-01")

def ask_model(question, llm) -> Unverified:
    return unverified(llm(question))

def answer(question, llm, checker, send):
    raw = ask_model(question, llm)
    send(verify(raw, checker))        # the user only gets what has been verified
```

```
$ python proven.py check examples/good
Proven: no issues. Code accepted.
```

## Quick start

Python 3.11+. No dependencies — the standard library only.

1. Copy `proven.py` and `proven_types.py` into your project.
2. Create the settings: `python proven.py init`
3. Check the code: `python proven.py check`

Exit code: `0` — code accepted, `1` — there are issues. For machines there is `--json`.

```
python proven.py rules          # list the rules
python proven.py explain P4     # explain a rule
```

## The loop with an AI model

Proven works like a compiler for an AI model: the model writes code, the judge says what is wrong and how to fix it, the model fixes it. The judge's messages are written so they can be handed to a model as they are.

**Claude Code.** The repository already has [`.claude/settings.json`](.claude/settings.json): after every edit of a `.py` file the judge checks it, and if there are issues, Claude sees the report and fixes them. Copy the `.claude` folder into your project.

**Rules in the prompt.** A cheat sheet for a system prompt or `CLAUDE.md` is in [PROMPT.md](PROMPT.md). The model then follows the rules from the first try, and the judge catches the rest.

**GitHub.** The workflow [`.github/workflows/proven.yml`](.github/workflows/proven.yml) runs the judge and the tests on every push and pull request.

## An existing project

On old code the judge will find many issues at once. To avoid fixing everything in one day:

```
python proven.py baseline
```

The current issues are written to `.proven-baseline.json` as old debt. From then on the judge skips them but rejects any new issue. Pay the debt off gradually and rewrite the baseline.

## Waivers

The human has the final say. A rule can be switched off for one line — but only with a reason:

```python
MRP = 3932  # proven: allow P3: computing an archived 2025 fine
```

A waiver without a reason is itself an issue (P0). The comment goes on the same line or on the line above. The Russian keyword works too: `# proven: разрешить P3: reason`.

## Honest limits

The judge catches what is **invented**, not what is **wrong**:

- **P3** checks that a fact has a source and a recent date, not that the value matches the law. Comparing it with the source is up to a person or to the check in `verify()`.
- **P2** checks names in modules and classes. Methods on objects (`response.jsonn()`) cannot be checked statically — tests catch them (P6).
- **P2** imports installed packages to look inside, which runs their import code. Turn it off with `check_attributes = false`.
- **P4** follows data inside a file and through functions by name. It is cautious: when in doubt, it treats data as unverified.
- **P6** matches functions and tests by name, not by module.
- Logic that is wrong but consistent will not be caught by any static judge. Tests and your final word catch it.

## Layout

```
proven.py             the judge (CLI)
proven_types.py       Unverified, Verified, verify(), Fact
proven.toml           settings (the judge checks itself)
SPEC.md               the rules in detail and every setting
PROMPT.md             a cheat sheet for an AI model
examples/bad          code with hallucinations
examples/good         the same code following the rules
tests/                the judge's tests
.claude/settings.json the hook for Claude Code
.github/workflows/    the check on GitHub
```

Check the judge itself:

```
pip install -r requirements-dev.txt
python proven.py check     # the judge accepts itself
python -m pytest
```

> The values in the examples (Kazakhstan's MRP and others) were checked against the source on 3 November 2026 and serve as illustrations. In live code, check them against [adilet.zan.kz](https://adilet.zan.kz), Kazakhstan's official legal database.

## License

MIT, see [LICENSE](LICENSE).
