#!/usr/bin/env python3
"""Proven — a judge for code written by AI.

The syntax is plain Python. The rules are against hallucinations.
The judge reads code before it runs and rejects everything that is not proven:
invented packages and functions, facts from memory, unverified model
output, swallowed errors and code without tests.

    python proven.py check          check the project
    python proven.py explain P4     explain a rule
    python proven.py rules          list the rules
    python proven.py baseline       accept the current issues as old debt
    python proven.py init           create proven.toml

Standard library only, Python 3.11+.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import io
import json
import os
import re
import sys
import tokenize
import tomllib
import types
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

VERSION = "0.1.0"

# code: (title, what it means, how to fix)
RULES: dict[str, tuple[str, str, str]] = {
    "P0": (
        "A waiver needs a reason",
        "A rule can be switched off for one line, but only with an explanation. "
        "The human has the final say, but not silently.",
        "Add the reason: # proven: allow P3: <why it is fine here>",
    ),
    "P1": (
        "Imports are real",
        "Every external package is listed in requirements and pinned with ==. "
        "AI models invent package names, and attackers register packages "
        "under those names.",
        "Check the exact package name on PyPI, add a name==version line "
        "to requirements.txt and install the package.",
    ),
    "P2": (
        "Calls are real",
        "The module, function, class or attribute really exists in the installed "
        "version. The most common hallucination in code is a plausible name that does not exist.",
        "Open the documentation for the installed version and use a name that is there.",
    ),
    "P3": (
        "No facts from memory",
        "Official figures (minimum wages, indexes, tax rates), law article numbers and URLs "
        "must not be typed into code by hand. Only from a dated source: "
        "Fact(value, source=..., date=...), a database or a config. "
        "A Fact that is too old is an error too.",
        'Wrap the value in Fact(..., source="where it comes from", date="YYYY-MM-DD") '
        "or read it from a database or a config.",
    ),
    "P4": (
        "Model output is verified",
        "Everything a model returns is Unverified. It must not be sent to a person "
        "or written to a database until it has passed verify(). The check cannot be "
        "faked with Verified(...).",
        "Pass the text through verify(text, checker) and send the result.",
    ),
    "P5": (
        "Errors are not swallowed",
        "A bare except and a handler that silently does nothing are forbidden: "
        "generated code often \"works\" because it hides the failure.",
        "Catch a specific exception and really handle it, "
        "or log it and re-raise it (raise).",
    ),
    "P6": (
        "Functions are proven by tests",
        "Every public function and class is used in at least one test, "
        "and the test really checks something. assert True does not count.",
        "Write a test in tests/ that calls the function and checks the result with assert.",
    ),
}

DEFAULT_EXCLUDE = [
    ".git", ".venv", "venv", "env", "__pycache__", "build", "dist",
    "node_modules", ".mypy_cache", ".pytest_cache", ".tox",
]

# import name -> PyPI package name, where they differ
DEFAULT_IMPORT_MAP = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "cv2": "opencv-python",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "dateutil": "python-dateutil",
    "jwt": "pyjwt",
    "fitz": "pymupdf",
    "Crypto": "pycryptodome",
    "attr": "attrs",
    "telegram": "python-telegram-bot",
    "pkg_resources": "setuptools",
    "proven_types": "proven-judge",
}

# Kazakhstan's monthly index (MRP) and minimum wage (MZP) in Latin and Cyrillic, VAT, taxes, rates
DEFAULT_FACT_NAMES = [
    "MRP", "MZP", "МРП", "МЗП", "VAT", "НДС", "TAX", "НАЛОГ",
    "СТАВКА", "TAX_RATE", "INTEREST_RATE",
]

DEFAULT_SOURCES = [
    "llm", "ask_llm", "call_llm", "ask_claude", "unverified", "dify_chat",
    "messages.create", "chat.completions.create", "responses.create",
]
DEFAULT_SINKS = [
    "send", "send_message", "send_text", "reply", "answer_user",
    "whatsapp_send", "wazzup_send", "insert", "insert_one", "upsert", "save", "write",
]
DEFAULT_SANITIZERS = ["verify", "verify_answer", "check_citations"]

DEFAULT_TOML = """\
# Proven — rules against hallucinations. Every key is optional.
[proven]
src = ["."]                       # where the code is
tests = ["tests"]                 # where the tests are
requirements = ["requirements.txt"]
check_installed = true            # P1: the package must be installed
check_attributes = true           # P2: check names inside modules (imports them)
local_modules = []                # your own modules outside this folder

[proven.import_map]               # import name -> PyPI package name
# yaml = "pyyaml"

[proven.facts]                    # P3
# names = ["FX_RATE"]             # added to the default names
max_age_days = 400                # older than this, a Fact is out of date
allowed_url_hosts = []            # domains that may appear in code

[proven.taint]                    # P4: added to the defaults
sources = []                      # functions whose output is unverified
sinks = []                        # where unverified data must not go
sanitizers = []                   # functions that verify

[proven.rules]
disable = []                      # for example ["P6"] for a while
"""

# Law article numbers: English (Art. 46, Article 46, § 46), Russian (ст. 46, статья 46),
# Kazakh (46-бап, бап 46).
LEGAL_RE = re.compile(
    r"(?i)(?<!\w)(?:art\.|article(?!\w))\s*\d+"
    r"|§\s*\d+"
    r"|(?<!\w)(?:ст\.|стать(?:я|и|е|ю|ей|ёй)(?!\w))\s*\d+"
    r"|\d+\s*-?\s*бап(?!\w)"
    r"|(?<!\w)бап\s*\d+"
)
URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")
# "allow", or the Russian "разрешить"
WAIVER_RE = re.compile(
    r"proven\s*:\s*(?:allow|разрешить)\s+(P\s*\d)\s*[:—–-]?\s*(.*)$", re.IGNORECASE
)
_MISSING = object()


@dataclass
class Issue:
    """One issue: where, which rule, what is wrong."""

    path: str
    line: int
    col: int
    code: str
    message: str

    @property
    def hint(self) -> str:
        return RULES[self.code][2]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path, "line": self.line, "col": self.col,
            "code": self.code, "rule": RULES[self.code][0],
            "message": self.message, "hint": self.hint,
        }


@dataclass
class Config:
    """Settings from proven.toml (or the defaults)."""

    root: Path
    src: list[str] = field(default_factory=lambda: ["."])
    tests: list[str] = field(default_factory=lambda: ["tests"])
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    requirements: list[str] = field(default_factory=lambda: ["requirements.txt"])
    check_installed: bool = True
    check_attributes: bool = True
    local_modules: list[str] = field(default_factory=list)
    import_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_IMPORT_MAP))
    fact_names: list[str] = field(default_factory=lambda: list(DEFAULT_FACT_NAMES))
    fact_max_age_days: int = 400
    allowed_url_hosts: list[str] = field(default_factory=list)
    taint_sources: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    taint_sinks: list[str] = field(default_factory=lambda: list(DEFAULT_SINKS))
    taint_sanitizers: list[str] = field(default_factory=lambda: list(DEFAULT_SANITIZERS))
    disable: list[str] = field(default_factory=list)
    baseline: str = ".proven-baseline.json"
    today: date = field(default_factory=date.today)


# ---------------------------------------------------------------- config

def _str_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f'proven.toml: "{key}" must be a list of strings')
    return list(value)


def _norm_code(code: str) -> str:
    return re.sub(r"\s+", "", code).upper()


def load_config(start: str | Path = ".", today: date | None = None) -> Config:
    """Find proven.toml in the start folder or above it and read it."""
    start = Path(start).resolve()
    base = start if start.is_dir() else start.parent
    found = next((d / "proven.toml" for d in (base, *base.parents) if (d / "proven.toml").is_file()), None)
    cfg = Config(root=found.parent if found else base)
    if today is not None:
        cfg.today = today
    if found is None:
        return cfg
    data = tomllib.loads(found.read_text(encoding="utf-8")).get("proven", {})
    for key in ("src", "tests", "exclude", "requirements", "local_modules"):
        if key in data:
            setattr(cfg, key, _str_list(data[key], key))
    for key in ("check_installed", "check_attributes"):
        if key in data:
            setattr(cfg, key, bool(data[key]))
    if "baseline" in data:
        cfg.baseline = str(data["baseline"])
    cfg.import_map.update({str(k): str(v) for k, v in data.get("import_map", {}).items()})
    facts = data.get("facts", {})
    if "names" in facts:
        cfg.fact_names += _str_list(facts["names"], "facts.names")
    if "max_age_days" in facts:
        cfg.fact_max_age_days = int(facts["max_age_days"])
    if "allowed_url_hosts" in facts:
        cfg.allowed_url_hosts = _str_list(facts["allowed_url_hosts"], "facts.allowed_url_hosts")
    taint = data.get("taint", {})
    cfg.taint_sources += _str_list(taint.get("sources", []), "taint.sources")
    cfg.taint_sinks += _str_list(taint.get("sinks", []), "taint.sinks")
    cfg.taint_sanitizers += _str_list(taint.get("sanitizers", []), "taint.sanitizers")
    cfg.disable = [_norm_code(c) for c in _str_list(data.get("rules", {}).get("disable", []), "rules.disable")]
    return cfg


# ---------------------------------------------------------------- files

def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _excluded(path: Path, cfg: Config) -> bool:
    parts = Path(_rel(path, cfg.root)).parts
    for ex in cfg.exclude:
        ex_parts = Path(ex).parts
        if len(ex_parts) == 1 and ex_parts[0] in parts:
            return True
        if parts[: len(ex_parts)] == ex_parts:
            return True
    return False


def _collect(paths: list[Path], cfg: Config) -> list[Path]:
    out: set[Path] = set()
    for p in paths:
        p = p.resolve()
        if p.is_file() and p.suffix == ".py":
            out.add(p)
        elif p.is_dir():
            out.update(f.resolve() for f in p.rglob("*.py") if not _excluded(f, cfg))
    return sorted(out)


def _is_test_file(path: Path) -> bool:
    return path.name.startswith("test_") or path.name.endswith("_test.py")


@dataclass
class _File:
    path: Path
    rel: str
    tree: ast.Module
    parents: dict[ast.AST, ast.AST]
    waivers: dict[int, dict[str, str]]


def _waivers(source: str) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                m = WAIVER_RE.search(tok.string)
                if m:
                    out.setdefault(tok.start[0], {})[_norm_code(m.group(1))] = m.group(2).strip()
    except (tokenize.TokenError, SyntaxError):
        return out
    return out


def _parse(path: Path, cfg: Config) -> _File | Issue:
    rel = _rel(path, cfg.root)
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        return Issue(rel, exc.lineno or 1, exc.offset or 1, "P2",
                     f"code does not parse: {exc.msg} — not a real Python construct")
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    return _File(path, rel, tree, parents, _waivers(source))


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{_dotted(node.func)}()"
    return "?"


def _last(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _norm_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _guarded_import(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """An import inside try ... except ImportError is an optional dependency."""
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.Try):
            for handler in cur.handlers:
                kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
                if any(k is not None and _last(_dotted(k)) in ("ImportError", "ModuleNotFoundError") for k in kinds):
                    return True
        cur = parents.get(cur)
    return False


# ---------------------------------------------------------------- project

class _Project:
    """What is known about the project: its own modules, requirements, an import cache."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.local = self._local_modules()
        self.reqs = self._requirements()
        self._mods: dict[str, object] = {}
        self._names: dict[str, set[str] | None] = {}

    def _local_modules(self) -> set[str]:
        names = set(self.cfg.local_modules)
        for p in _collect([self.cfg.root], self.cfg):
            rel = Path(_rel(p, self.cfg.root))
            names.add(rel.stem)
            names.update(parent.name for parent in rel.parents if parent.name)
        return names

    def _requirements(self) -> dict[str, str]:
        reqs: dict[str, str] = {}
        for name in self.cfg.requirements:
            path = self.cfg.root / name
            if not path.is_file():
                continue
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line or line.startswith("-"):
                    continue
                m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$", line)
                if m:
                    reqs[_norm_pkg(m.group(1))] = m.group(3).split(";")[0].strip()
        return reqs

    def external(self, top: str) -> bool:
        return top not in sys.stdlib_module_names and top not in self.local

    def installed(self, top: str) -> bool:
        try:
            return importlib.util.find_spec(top) is not None
        except (ImportError, ValueError):
            return False

    def importable(self, top: str) -> bool:
        if top in self.local:
            return False
        return top in sys.stdlib_module_names or self.installed(top)

    def module_exists(self, dotted: str) -> bool | None:
        """True: exists, False: definitely does not, None: unknown."""
        top = dotted.split(".")[0]
        if top in self.local:
            return True if self._local_file(dotted) else None
        if not self.importable(top):
            return None
        try:
            return importlib.util.find_spec(dotted) is not None
        except ModuleNotFoundError:
            return False
        except Exception:  # noqa: BLE001 — a broken third-party package: nothing to judge
            self._mods[dotted] = None
            return None

    def module(self, dotted: str) -> object:
        if dotted not in self._mods:
            try:
                self._mods[dotted] = importlib.import_module(dotted)
            except (Exception, SystemExit):  # noqa: BLE001 — importing third-party code can fail in any way
                self._mods[dotted] = None
                return None
        return self._mods[dotted]

    def _local_file(self, dotted: str) -> Path | None:
        rel = Path(*dotted.split("."))
        bases = [self.cfg.root] + [self.cfg.root / s for s in self.cfg.src if (self.cfg.root / s).is_dir()]
        for base in bases:
            for cand in (base / rel.with_suffix(".py"), base / rel / "__init__.py"):
                if cand.is_file():
                    return cand
        return None

    def local_names(self, dotted: str) -> set[str] | None:
        if dotted in self._names:
            return self._names[dotted]
        path = self._local_file(dotted)
        names: set[str] | None = None
        if path is not None:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                tree = None
            if tree is not None:
                names = _top_names(tree.body)
                if "__getattr__" in names:
                    names = None
        self._names[dotted] = names
        return names

    def has_name(self, module: str, name: str) -> bool | None:
        top = module.split(".")[0]
        if top in self.local:
            names = self.local_names(module)
            if names is None:
                return None
            return name in names or self._local_file(f"{module}.{name}") is not None
        if not self.importable(top):
            return None
        mod = self.module(module)
        if mod is None:
            return None
        if hasattr(mod, name):
            return True
        return bool(self.module_exists(f"{module}.{name}"))

    def check_chain(self, target: str, chain: list[str]) -> str | None:
        """Check the chain target.a.b.c. Return the error text or None."""
        top = target.split(".")[0]
        if top in self.local:
            if self.local_names(target) is None:
                return None
            if self.has_name(target, chain[0]) is False:
                return f'module "{target}" has no name "{chain[0]}"'
            return None
        if not self.importable(top):
            return None
        obj = self._object(target)
        path = target
        for attr in chain:
            if isinstance(obj, types.ModuleType):
                if hasattr(obj, attr):
                    obj = getattr(obj, attr)
                elif self.module_exists(f"{obj.__name__}.{attr}"):
                    obj = self.module(f"{obj.__name__}.{attr}")
                else:
                    return f'module "{path}" has no name "{attr}"'
            elif isinstance(obj, type):
                if not hasattr(obj, attr):
                    return f'class "{path}" has no attribute "{attr}"'
                obj = getattr(obj, attr)
            else:
                return None
            path = f"{path}.{attr}"
        return None

    def _object(self, target: str) -> object:
        if self.module_exists(target):
            mod = self.module(target)
            return _MISSING if mod is None else mod
        if "." not in target:
            return _MISSING
        parent, attr = target.rsplit(".", 1)
        mod = self.module(parent)
        if mod is None or not hasattr(mod, attr):
            return _MISSING
        return getattr(mod, attr)


def _top_names(body: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                names.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, (ast.If, ast.Try)):
            names |= _top_names(node.body) | _top_names(node.orelse)
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    names |= _top_names(handler.body)
                names |= _top_names(node.finalbody)
    return names


# ---------------------------------------------------------------- P1

def _rule_imports(f: _File, pr: _Project) -> list[Issue]:
    issues: list[Issue] = []
    seen: set[str] = set()
    for node in ast.walk(f.tree):
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        else:
            continue
        if _guarded_import(node, f.parents):
            continue
        for full in modules:
            top = full.split(".")[0]
            if not pr.external(top) or top in seen:
                continue
            seen.add(top)
            pkg = _norm_pkg(pr.cfg.import_map.get(top, top))
            spec = pr.reqs.get(pkg)
            where = ", ".join(pr.cfg.requirements)
            if spec is None:
                issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P1",
                                    f'package "{top}" is not in {where} — it may be invented'))
            elif not (spec.startswith("@") or (spec.startswith("==") and "*" not in spec)):
                issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P1",
                                    f'version of "{pkg}" is not pinned with == (now: "{spec or "any"}")'))
            if pr.cfg.check_installed and not pr.installed(top):
                issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P1",
                                    f'module "{top}" is not installed'))
    return issues


# ---------------------------------------------------------------- P2

def _rule_attributes(f: _File, pr: _Project) -> list[Issue]:
    if not pr.cfg.check_attributes:
        return []
    issues: list[Issue] = []
    bind: dict[str, str] = {}
    shadowed = {n.id for n in ast.walk(f.tree) if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load)}
    shadowed |= {a.arg for a in ast.walk(f.tree) if isinstance(a, ast.arg)}
    for node in ast.walk(f.tree):
        if isinstance(node, ast.Import) and not _guarded_import(node, f.parents):
            for a in node.names:
                if pr.module_exists(a.name) is False:
                    issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P2",
                                        f'module "{a.name}" does not exist'))
                    continue
                if a.asname:
                    bind[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    bind[top] = top
        elif (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
              and not _guarded_import(node, f.parents)):
            for a in node.names:
                if a.name == "*":
                    continue
                if pr.has_name(node.module, a.name) is False:
                    issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P2",
                                        f'module "{node.module}" has no name "{a.name}"'))
                    continue
                bind[a.asname or a.name] = f"{node.module}.{a.name}"
    for name in shadowed:
        bind.pop(name, None)
    if not bind:
        return issues
    for node in ast.walk(f.tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load):
            continue
        if isinstance(f.parents.get(node), ast.Attribute):
            continue
        chain: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name) or cur.id not in bind:
            continue
        chain.reverse()
        problem = pr.check_chain(bind[cur.id], chain)
        if problem:
            issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P2", problem))
    return issues


# ---------------------------------------------------------------- P3

def _is_number(node: ast.AST | None) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
    return (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool))


def _fact_name(name: str, patterns: list[str]) -> bool:
    up = name.upper()
    segments = set(re.split(r"[_\W]+", up))
    return any(p.upper() == up or p.upper() in segments for p in patterns)


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _fact_arg(call: ast.Call, key: str, pos: int) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == key:
            return kw.value
    return call.args[pos] if len(call.args) > pos else None


def _rule_facts(f: _File, pr: _Project) -> list[Issue]:
    cfg = pr.cfg
    issues: list[Issue] = []
    inside_fact: set[int] = set()
    for node in ast.walk(f.tree):
        if not (isinstance(node, ast.Call) and _last(_dotted(node.func)) == "Fact"):
            continue
        inside_fact.update(id(n) for n in ast.walk(node))
        where = (f.rel, node.lineno, node.col_offset + 1, "P3")
        src = _fact_arg(node, "source", 1)
        if not (isinstance(src, ast.Constant) and isinstance(src.value, str) and src.value.strip()):
            issues.append(Issue(*where, "Fact without a source: source=\"...\" is required and must be a string"))
        when = _fact_arg(node, "date", 2)
        if not (isinstance(when, ast.Constant) and isinstance(when.value, str)):
            issues.append(Issue(*where, "Fact without a date: date=\"YYYY-MM-DD\" is required"))
            continue
        try:
            d = date.fromisoformat(when.value)
        except ValueError:
            issues.append(Issue(*where, f'Fact date "{when.value}" is not in YYYY-MM-DD format'))
            continue
        if (cfg.today - d).days > cfg.fact_max_age_days:
            issues.append(Issue(*where, f"fact is out of date: the source is dated {when.value}, "
                                        f"older than {cfg.fact_max_age_days} days — check it against the source"))
    for node in ast.walk(f.tree):
        targets: list[ast.AST] = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if value is None or not _is_number(value):
            continue
        for t in targets:
            if isinstance(t, ast.Name) and _fact_name(t.id, cfg.fact_names):
                issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P3",
                                    f'"{t.id}" is a number typed from memory — it needs a dated source'))
    docs = _docstring_ids(f.tree)
    for node in ast.walk(f.tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docs or id(node) in inside_fact:
            continue
        m = LEGAL_RE.search(node.value)
        if m:
            issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P3",
                                f'law article number typed by hand: "{m.group(0)}" — take it from the database'))
        for host in URL_RE.findall(node.value):
            host = host.lower().rstrip(".")
            if not any(host == h or host.endswith("." + h) for h in cfg.allowed_url_hosts):
                issues.append(Issue(f.rel, node.lineno, node.col_offset + 1, "P3",
                                    f'address "{host}" typed by hand — move it to a config or allowed_url_hosts'))
    return issues


# ---------------------------------------------------------------- P4

def _is_unverified(annotation: ast.AST | None) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return "Unverified" in annotation.value
    return any((isinstance(n, ast.Name) and n.id == "Unverified")
               or (isinstance(n, ast.Attribute) and n.attr == "Unverified")
               for n in ast.walk(annotation))


def _walk_expr(node: ast.AST):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        if not isinstance(cur, ast.Lambda):
            stack.extend(ast.iter_child_nodes(cur))


class _Taint:
    """Tracks where model output flows inside a function."""

    PURE = {"len", "bool", "isinstance", "issubclass", "type", "id", "hash", "print", "callable"}

    def __init__(self, f: _File, pr: _Project) -> None:
        self.f = f
        self.sources = pr.cfg.taint_sources
        self.sinks = pr.cfg.taint_sinks
        self.sanitizers = pr.cfg.taint_sanitizers
        self.tainted_funcs: set[str] = set()
        self.issues: list[Issue] = []
        self.record = False
        self.returns = False
        self.types_module = f.path.name == "proven_types.py"

    @staticmethod
    def _match(name: str, patterns: list[str]) -> bool:
        for p in patterns:
            if "." in p:
                if name == p or name.endswith("." + p):
                    return True
            elif _last(name) == p:
                return True
        return False

    def expr(self, node: ast.AST | None, t: set[str]) -> bool:
        if node is None or isinstance(node, (ast.Constant, ast.Lambda)):
            return False
        if isinstance(node, ast.Name):
            return node.id in t
        if isinstance(node, ast.Attribute):
            return _dotted(node) in t or self.expr(node.value, t)
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if self._match(name, self.sanitizers):
                return False
            if self._match(name, self.sources) or _last(name) in self.tainted_funcs:
                return True
            if name in self.PURE:
                return False
            if isinstance(node.func, ast.Attribute) and self.expr(node.func.value, t):
                return True
            return (any(self.expr(a, t) for a in node.args)
                    or any(self.expr(k.value, t) for k in node.keywords))
        return any(self.expr(c, t) for c in ast.iter_child_nodes(node))

    def bind(self, target: ast.AST, tainted: bool, t: set[str]) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self.bind(elt, tainted, t)
        elif isinstance(target, ast.Starred):
            self.bind(target.value, tainted, t)
        elif isinstance(target, (ast.Name, ast.Attribute)):
            key = _dotted(target)
            if tainted:
                t.add(key)
            else:
                t.discard(key)
        elif isinstance(target, ast.Subscript) and tainted:
            t.add(_dotted(target.value))

    def _issue(self, node: ast.AST, message: str) -> None:
        if self.record:
            self.issues.append(Issue(self.f.rel, node.lineno, node.col_offset + 1, "P4", message))

    @staticmethod
    def _headers(st: ast.stmt) -> list[ast.AST]:
        if isinstance(st, (ast.If, ast.While)):
            return [st.test]
        if isinstance(st, (ast.For, ast.AsyncFor)):
            return [st.iter]
        if isinstance(st, (ast.With, ast.AsyncWith)):
            return [item.context_expr for item in st.items]
        if isinstance(st, ast.Match):
            return [st.subject]
        if isinstance(st, (ast.Try, ast.TryStar, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return []
        return [st]

    def check_sinks(self, st: ast.stmt, t: set[str]) -> None:
        for header in self._headers(st):
            for node in _walk_expr(header):
                if not isinstance(node, ast.Call):
                    continue
                name = _dotted(node.func)
                if _last(name) == "Verified" and not self.types_module:
                    self._issue(node, "Verified(...) created directly — the check cannot be faked, use verify()")
                if self._match(name, self.sinks):
                    args = [*node.args, *(k.value for k in node.keywords)]
                    if any(self.expr(a, t) for a in args):
                        self._issue(node, f'unverified model output goes into "{name}"')

    def _branch(self, body: list[ast.stmt], t: set[str]) -> None:
        inner = set(t)
        self.block(body, inner)
        t |= inner

    def block(self, stmts: list[ast.stmt], t: set[str]) -> None:
        for st in stmts:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            self.check_sinks(st, t)
            if isinstance(st, ast.Assign):
                tainted = self.expr(st.value, t)
                for target in st.targets:
                    self.bind(target, tainted, t)
            elif isinstance(st, ast.AnnAssign):
                tainted = _is_unverified(st.annotation) or self.expr(st.value, t)
                if st.value is not None or tainted:
                    self.bind(st.target, tainted, t)
            elif isinstance(st, ast.AugAssign):
                if self.expr(st.value, t):
                    self.bind(st.target, True, t)
            elif isinstance(st, ast.Return):
                if self.expr(st.value, t):
                    self.returns = True
            elif isinstance(st, (ast.For, ast.AsyncFor)):
                inner = set(t)
                self.bind(st.target, self.expr(st.iter, t), inner)
                self.block(st.body, inner)
                self.block(st.orelse, inner)
                t |= inner
            elif isinstance(st, (ast.If, ast.While)):
                self._branch(st.body, t)
                self._branch(st.orelse, t)
            elif isinstance(st, (ast.With, ast.AsyncWith)):
                for item in st.items:
                    if item.optional_vars is not None:
                        self.bind(item.optional_vars, self.expr(item.context_expr, t), t)
                self.block(st.body, t)
            elif isinstance(st, (ast.Try, ast.TryStar)):
                self._branch(st.body, t)
                for handler in st.handlers:
                    self._branch(handler.body, t)
                self._branch(st.orelse, t)
                self._branch(st.finalbody, t)
            elif isinstance(st, ast.Match):
                for case in st.cases:
                    self._branch(case.body, t)

    def run(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        args = fn.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
        t = {p.arg for p in params if p is not None and _is_unverified(p.annotation)}
        self.returns = False
        self.block(fn.body, t)
        return self.returns


def _rule_taint(f: _File, pr: _Project) -> list[Issue]:
    taint = _Taint(f, pr)
    funcs = [n for n in ast.walk(f.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    taint.tainted_funcs = {fn.name for fn in funcs if _is_unverified(fn.returns)}
    for _ in range(10):
        before = set(taint.tainted_funcs)
        for fn in funcs:
            if taint.run(fn):
                taint.tainted_funcs.add(fn.name)
        if taint.tainted_funcs == before:
            break
    taint.record = True
    taint.block(f.tree.body, set())
    for fn in funcs:
        taint.run(fn)
    return taint.issues


# ---------------------------------------------------------------- P5

def _silent(body: list[ast.stmt]) -> bool:
    for st in body:
        if isinstance(st, (ast.Pass, ast.Continue)):
            continue
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            continue
        if isinstance(st, ast.Return) and (st.value is None or (isinstance(st.value, ast.Constant) and st.value.value is None)):
            continue
        return False
    return True


def _rule_except(f: _File, pr: _Project) -> list[Issue]:
    issues: list[Issue] = []
    for node in ast.walk(f.tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        where = (f.rel, node.lineno, node.col_offset + 1, "P5")
        reraises = bool(node.body) and isinstance(node.body[-1], ast.Raise) and node.body[-1].exc is None
        if node.type is None and not reraises:
            issues.append(Issue(*where, "bare except catches everything, even Ctrl+C and program exit"))
        elif node.type is not None and _silent(node.body):
            issues.append(Issue(*where, f'error "{ast.unparse(node.type)}" is swallowed silently'))
    return issues


# ---------------------------------------------------------------- P6

def _test_problem(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    checked = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            if isinstance(node.test, ast.Constant):
                return f'"{fn.name}": assert with a constant checks nothing'
            checked = True
        elif isinstance(node, ast.Call):
            last = _last(_dotted(node.func))
            if last == "raises" or last.startswith("assert"):
                checked = True
    if not checked:
        return f'"{fn.name}" checks nothing: no assert and no pytest.raises'
    return None


def _rule_tests(src: list[_File], tests: list[_File]) -> list[Issue]:
    called: set[str] = set()
    referenced: set[str] = set()
    for tf in tests:
        for node in ast.walk(tf.tree):
            if isinstance(node, ast.Call):
                called.add(_last(_dotted(node.func)))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                referenced.add(node.attr)
    issues: list[Issue] = []
    for f in src:
        for node in f.tree.body:
            where = (f.rel, node.lineno, node.col_offset + 1, "P6")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                if node.name not in called:
                    issues.append(Issue(*where, f'function "{node.name}" is not called in any test'))
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                if node.name not in called and node.name not in referenced:
                    issues.append(Issue(*where, f'class "{node.name}" is not used in any test'))
    for tf in tests:
        if not _is_test_file(tf.path):
            continue
        for node in ast.walk(tf.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                problem = _test_problem(node)
                if problem:
                    issues.append(Issue(tf.rel, node.lineno, node.col_offset + 1, "P6", problem))
    return issues


# ---------------------------------------------------------------- putting it together

_FILE_RULES = (
    ("P1", _rule_imports),
    ("P2", _rule_attributes),
    ("P3", _rule_facts),
    ("P4", _rule_taint),
    ("P5", _rule_except),
)


def _apply_waivers(issues: list[Issue], files: dict[str, _File]) -> list[Issue]:
    out: list[Issue] = []
    for issue in issues:
        f = files.get(issue.path)
        if f is not None:
            waived = {**f.waivers.get(issue.line - 1, {}), **f.waivers.get(issue.line, {})}
            if len(waived.get(issue.code, "")) >= 5:
                continue
        out.append(issue)
    for f in files.values():
        for line, codes in f.waivers.items():
            for code, reason in codes.items():
                if len(reason) < 5:
                    out.append(Issue(f.rel, line, 1, "P0", f"waiver of {code} without a reason"))
    return out


def _baseline_key(issue: Issue) -> str:
    return f"{issue.path}|{issue.code}|{issue.message}"


def _apply_baseline(issues: list[Issue], cfg: Config) -> list[Issue]:
    path = cfg.root / cfg.baseline
    if not path.is_file():
        return issues
    allowed = Counter(json.loads(path.read_text(encoding="utf-8")).get("issues", {}))
    out: list[Issue] = []
    for issue in issues:
        key = _baseline_key(issue)
        if allowed[key] > 0:
            allowed[key] -= 1
            continue
        out.append(issue)
    return out


def check(cfg: Config, paths: list[str] | None = None, use_baseline: bool = True) -> list[Issue]:
    """Check the project. Return the issues (an empty list means the code is accepted)."""
    targets = [Path(p) if Path(p).is_absolute() else cfg.root / p for p in cfg.src] if not paths else [Path(p) for p in paths]
    test_dirs = [(cfg.root / t).resolve() for t in cfg.tests]

    def in_tests(p: Path) -> bool:
        return any(p == d or d in p.parents for d in test_dirs)

    src_paths = [p for p in _collect(targets, cfg) if not in_tests(p) and not _is_test_file(p) and p.name != "conftest.py"]
    test_paths = _collect([d for d in test_dirs if d.exists()], cfg)
    project = _Project(cfg)
    issues: list[Issue] = []
    files: dict[str, _File] = {}
    src_files: list[_File] = []
    test_files: list[_File] = []
    for group, paths_ in ((src_files, src_paths), (test_files, test_paths)):
        for p in paths_:
            parsed = _parse(p, cfg)
            if isinstance(parsed, Issue):
                issues.append(parsed)
                continue
            files[parsed.rel] = parsed
            group.append(parsed)
    for f in src_files:
        for code, rule in _FILE_RULES:
            if code not in cfg.disable:
                issues += rule(f, project)
    if "P6" not in cfg.disable:
        issues += _rule_tests(src_files, test_files)
    issues = _apply_waivers(issues, files)
    if use_baseline:
        issues = _apply_baseline(issues, cfg)
    return sorted(issues, key=lambda i: (i.path, i.line, i.col, i.code, i.message))


def format_report(issues: list[Issue]) -> str:
    """Text for a person and for an AI model: what is wrong and how to fix it."""
    if not issues:
        return "Proven: no issues. Code accepted."
    lines = [f"{i.path}:{i.line}:{i.col}  {i.code}  {i.message}" for i in issues]
    lines += ["", "How to fix:"]
    for code in sorted({i.code for i in issues}):
        lines.append(f"  {code} {RULES[code][0]} — {RULES[code][2]}")
    files = len({i.path for i in issues})
    lines += ["", f"Proven: {len(issues)} issue(s) in {files} file(s). Code rejected."]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI

def _cmd_check(args: argparse.Namespace, stdin) -> int:
    if args.hook:
        return _hook(args.hook, stdin)
    start = Path(args.paths[0]) if args.paths else Path.cwd()
    cfg = load_config(start)
    issues = check(cfg, args.paths or None, use_baseline=not args.no_baseline)
    if args.json:
        print(json.dumps({"accepted": not issues, "issues": [i.to_dict() for i in issues]},
                         ensure_ascii=False, indent=2))
    else:
        print(format_report(issues))
    return 1 if issues else 0


def _hook(kind: str, stdin) -> int:
    try:
        payload = json.loads(stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or Path.cwd())
    cfg = load_config(project)
    if kind == "post":
        file_path = str((payload.get("tool_input") or {}).get("file_path") or "")
        if not file_path.endswith(".py"):
            return 0
        rel = _rel(Path(file_path), cfg.root)
        issues = [i for i in check(cfg) if i.path == rel]
    else:
        if payload.get("stop_hook_active"):
            return 0
        issues = check(cfg)
    if issues:
        print(format_report(issues), file=sys.stderr)
        return 2
    return 0


def _cmd_explain(args: argparse.Namespace) -> int:
    code = _norm_code(args.code)
    if code not in RULES:
        print(f"There is no rule {args.code}. Rules: {', '.join(RULES)}")
        return 1
    title, text, hint = RULES[code]
    print(f"{code} — {title}\n\n{text}\n\nHow to fix: {hint}")
    return 0


def _cmd_rules() -> int:
    for code, (title, _, _) in RULES.items():
        print(f"{code}  {title}")
    return 0


def _cmd_baseline(args: argparse.Namespace) -> int:
    start = Path(args.paths[0]) if args.paths else Path.cwd()
    cfg = load_config(start)
    issues = check(cfg, args.paths or None, use_baseline=False)
    data = {"version": 1, "issues": dict(Counter(_baseline_key(i) for i in issues))}
    (cfg.root / cfg.baseline).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Proven: {len(issues)} issue(s) recorded as old debt. New code must be clean.")
    return 0


def _cmd_init() -> int:
    path = Path.cwd() / "proven.toml"
    if path.exists():
        print("proven.toml already exists — leaving it as it is.")
        return 1
    path.write_text(DEFAULT_TOML, encoding="utf-8")
    print("Created proven.toml.")
    return 0


def main(argv: list[str] | None = None, stdin=None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(prog="proven", description="Proven — a judge for code written by AI.")
    parser.add_argument("--version", action="version", version=f"proven {VERSION}")
    sub = parser.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="check the code")
    c.add_argument("paths", nargs="*")
    c.add_argument("--json", action="store_true", help="output as JSON")
    c.add_argument("--no-baseline", action="store_true", help="ignore the old debt")
    c.add_argument("--hook", choices=["post", "stop"], help="Claude Code hook mode")
    e = sub.add_parser("explain", help="explain a rule")
    e.add_argument("code")
    sub.add_parser("rules", help="list the rules")
    b = sub.add_parser("baseline", help="record the current issues as old debt")
    b.add_argument("paths", nargs="*")
    sub.add_parser("init", help="create proven.toml")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return _cmd_check(args, stdin or sys.stdin)
    if args.cmd == "explain":
        return _cmd_explain(args)
    if args.cmd == "rules":
        return _cmd_rules()
    if args.cmd == "baseline":
        return _cmd_baseline(args)
    if args.cmd == "init":
        return _cmd_init()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
