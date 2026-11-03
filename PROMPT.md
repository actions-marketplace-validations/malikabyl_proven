# A cheat sheet for the AI model

Paste the text below into `CLAUDE.md`, a system prompt or the project instructions. The model then follows the rules from the first try, and the judge catches the rest.

---

```markdown
## Code follows the Proven rules

Principle: nothing exists until it is proven. The judge `python proven.py check` checks the code.
The code is not accepted until the judge says "Code accepted". When you get the judge's report, fix every issue and check again.

P1 Imports are real. Every external package goes in requirements.txt with a version pinned with ==.
   Not sure of the package name — do not guess, ask.
P2 Calls are real. Use only functions and attributes that certainly exist in the installed version.
   Not sure — open the documentation or the source code, do not guess.
P3 No facts from memory. Official figures (minimum wages, indexes), rates, taxes, law article numbers
   and URLs are not typed in as numbers or strings.
   Only Fact(value, source="where it comes from", date="YYYY-MM-DD"), a database or a config.
   If you do not know the exact value and its source, leave the question to a person, do not invent it.
P4 Model output is verified. Everything a model returns is Unverified.
   Before sending it to a person or writing it to a database: verify(text, checker).
   Never create Verified(...) directly.
P5 Errors are not swallowed. No bare except and no except: pass.
   Catch a specific exception, log it and re-raise it (raise).
P6 Functions are proven by tests. Every public function is called in a test in tests/,
   and the test checks the result with assert or pytest.raises. assert True is forbidden.

A waiver only with a reason in a comment: # proven: allow P3: <why>
Do not add a waiver to get past the judge. If a rule gets in the way, explain it to a person and ask.

from proven_types import Fact, Unverified, unverified, verify
```

---

## Why a cheat sheet and not only the judge

The judge catches errors after the code is written. The cheat sheet cuts the number of errors before that: every regeneration costs tokens and time. Together they work like a language with a compiler: the rules are known in advance, and the compiler lets no issue through.
