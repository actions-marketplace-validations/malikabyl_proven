import io
import json
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import proven  # noqa: E402
from proven import Config, Issue, check, format_report, load_config, main  # noqa: E402
from proven_types import Fact, NotVerified, unverified, verify  # noqa: E402

TODAY = date(2026, 11, 3)
BASE_TOML = """
[proven]
src = ["src"]
tests = ["tests"]
check_installed = false
"""


def project(tmp_path, files, toml=BASE_TOML, today=TODAY):
    (tmp_path / "proven.toml").write_text(textwrap.dedent(toml), encoding="utf-8")
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    return load_config(tmp_path, today=today)


def codes(issues, code=None):
    found = [(i.code, i.message) for i in issues]
    return [m for c, m in found if c == code] if code else found


def run(tmp_path, src, toml=BASE_TOML, extra=None):
    files = {"src/app.py": src, **(extra or {})}
    cfg = project(tmp_path, files, toml)
    cfg.disable.append("P6")
    return check(cfg)


# ---------------------------------------------------------------- P1

def test_p1_invented_package(tmp_path):
    issues = run(tmp_path, "import anthropik\n", extra={"requirements.txt": "httpx==0.27.0\n"})
    assert any("anthropik" in m for m in codes(issues, "P1"))


def test_p1_missing_requirements_is_one_issue(tmp_path):
    issues = run(tmp_path, "import anthropik\nimport httpx\n")
    messages = codes(issues, "P1")
    assert len(messages) == 1
    assert "no requirements file found" in messages[0] and "2 external import(s)" in messages[0]


def test_p1_unpinned_and_pinned(tmp_path):
    extra = {"requirements.txt": "requests>=2\nhttpx==0.27.0\n"}
    issues = run(tmp_path, "import requests\nimport httpx\n", extra=extra)
    messages = codes(issues, "P1")
    assert len(messages) == 1 and "requests" in messages[0]


def test_p1_import_name_differs_from_package(tmp_path):
    extra = {"requirements.txt": "pyyaml==6.0.2\n"}
    assert codes(run(tmp_path, "import yaml\n", extra=extra), "P1") == []


def test_p1_proven_types_comes_from_proven_judge(tmp_path):
    extra = {"requirements.txt": "proven-judge==0.1.0\n"}
    assert codes(run(tmp_path, "from proven_types import Fact\n", extra=extra), "P1") == []


def test_p1_stdlib_local_and_optional_imports_pass(tmp_path):
    src = """
    import json
    import helpers
    try:
        import ujson
    except ImportError:
        ujson = None
    """
    assert codes(run(tmp_path, src, extra={"src/helpers.py": "X = 1\n"}), "P1") == []


def test_p1_not_installed(tmp_path):
    toml = BASE_TOML.replace("check_installed = false", "check_installed = true")
    extra = {"requirements.txt": "no-such-pkg-proven==1.0\n"}
    issues = run(tmp_path, "import no_such_pkg_proven\n", toml=toml, extra=extra)
    assert any("is not installed" in m for m in codes(issues, "P1"))


# ---------------------------------------------------------------- P2

def test_p2_invented_function(tmp_path):
    issues = run(tmp_path, "import json\njson.loadz('{}')\n")
    assert codes(issues, "P2") == ['module "json" has no name "loadz"']


def test_p2_invented_from_import_and_submodule(tmp_path):
    issues = run(tmp_path, "from os import pathz\nimport os.pathz\n")
    messages = codes(issues, "P2")
    assert any("pathz" in m for m in messages) and len(messages) == 2


def test_p2_class_attribute(tmp_path):
    issues = run(tmp_path, "from datetime import date\ndate.todayy()\ndate.today()\n")
    assert codes(issues, "P2") == ['class "datetime.date" has no attribute "todayy"']


def test_p2_local_module(tmp_path):
    extra = {"src/helpers.py": "def real():\n    return 1\n"}
    issues = run(tmp_path, "from helpers import fake\nimport helpers\nhelpers.real()\n", extra=extra)
    assert codes(issues, "P2") == ['module "helpers" has no name "fake"']


def test_p2_shadowed_name_is_not_checked(tmp_path):
    src = "import json\n\ndef f(json):\n    return json.whatever\n"
    assert codes(run(tmp_path, src), "P2") == []


def test_p2_syntax_error(tmp_path):
    issues = run(tmp_path, "def f(:\n")
    assert any("does not parse" in m for m in codes(issues, "P2"))


# ---------------------------------------------------------------- P3

def test_p3_fact_by_memory(tmp_path):
    issues = run(tmp_path, "MRP = 3932\nмзп_2026 = 85000\nRATE_LIMIT = 10\n")
    assert len(codes(issues, "P3")) == 2


def test_p3_fact_with_source_passes(tmp_path):
    src = 'MRP = Fact(4325, source="budget law, Art. 9", date="2026-01-01")\n'
    assert codes(run(tmp_path, src), "P3") == []


def test_p3_fact_problems(tmp_path):
    src = """
    A = Fact(1, source="", date="2026-01-01")
    B = Fact(2, source="law")
    C = Fact(3, source="law", date="01.01.2026")
    D = Fact(4, source="law", date="2024-01-01")
    """
    messages = codes(run(tmp_path, src), "P3")
    assert len(messages) == 4
    assert any("out of date" in m for m in messages)


def test_p3_legal_reference_and_url(tmp_path):
    src = '''
    """Documentation may mention Article 46 — that is not code."""
    A = "Legal basis: Article 46 of the Labour Code"
    B = "46-бап"
    C = "Основание: статья 46 ТК"
    D = "https://adilet.zan.kz/x"
    E = "https://api.example.com/v1"
    F = "See Art. 12 and § 5"
    '''
    toml = BASE_TOML + '\n[proven.facts]\nallowed_url_hosts = ["example.com"]\n'
    messages = codes(run(tmp_path, src, toml=toml), "P3")
    assert len(messages) == 5
    assert any("adilet.zan.kz" in m for m in messages)


# ---------------------------------------------------------------- P4

def test_p4_raw_answer_sent(tmp_path):
    src = """
    def handle(phone, q):
        raw = llm(q)
        text = f"Answer: {raw.strip()}"
        send(phone, text)
    """
    assert codes(run(tmp_path, src), "P4") == ['unverified model output goes into "send"']


def test_p4_verified_answer_passes(tmp_path):
    src = """
    def handle(phone, q):
        raw = llm(q)
        send(phone, verify(raw, checker))
        ok = verify(raw, checker)
        send(phone, ok)
    """
    assert codes(run(tmp_path, src), "P4") == []


def test_p4_taint_through_helper_and_annotations(tmp_path):
    src = """
    def ask(q):
        reply = client.messages.create(model="m", messages=[])
        return reply.content[0].text

    def relay(text: Unverified):
        db.insert(text)

    def handle(q):
        for part in ask(q).split():
            save(part)
    """
    messages = codes(run(tmp_path, src), "P4")
    assert len(messages) == 3
    assert any('public function "ask" returns unverified' in m for m in messages)


def test_p4_reassignment_clears_and_forgery_is_caught(tmp_path):
    src = """
    def handle(q):
        raw = llm(q)
        raw = "placeholder"
        send(raw)
        send(Verified(llm(q)))
    """
    issues = [i for i in run(tmp_path, src) if i.code == "P4"]
    # send(raw) after reassignment is clean; only the forged line is caught — both as a forgery and as a leak
    assert {i.line for i in issues} == {6}
    assert any("faked" in i.message for i in issues)


def test_p4_custom_sink(tmp_path):
    toml = BASE_TOML + '\n[proven.taint]\nsinks = ["notify"]\n'
    src = "def h(q):\n    notify(llm(q))\n"
    assert len(codes(run(tmp_path, src, toml=toml), "P4")) == 1


# ---------------------------------------------------------------- P5

def test_p5_swallowed_errors(tmp_path):
    src = """
    try:
        x = 1
    except:
        pass
    try:
        x = 2
    except (ValueError, KeyError):
        ...
    """
    assert len(codes(run(tmp_path, src), "P5")) == 2


def test_p5_handled_errors_pass(tmp_path):
    src = """
    import logging
    try:
        x = 1
    except ValueError:
        logging.exception("failure")
        raise
    try:
        x = 2
    except:
        logging.error("rolling back")
        raise
    """
    assert codes(run(tmp_path, src), "P5") == []


# ---------------------------------------------------------------- P6

def test_p6_untested_function_and_empty_tests(tmp_path):
    files = {
        "src/app.py": "def tested():\n    return 1\n\ndef forgotten():\n    return 2\n\nclass Model:\n    pass\n\ndef _private():\n    return 3\n",
        "tests/test_app.py": "from app import tested, Model\n\ndef test_ok():\n    assert tested() == 1 and Model\n\ndef test_fake():\n    assert True\n\ndef test_nothing():\n    tested()\n",
    }
    issues = check(project(tmp_path, files))
    messages = codes(issues, "P6")
    assert any("forgotten" in m for m in messages)
    assert any("test_fake" in m and "constant" in m for m in messages)
    assert any("test_nothing" in m for m in messages)
    assert not any('"tested"' in m or "Model" in m or "_private" in m for m in messages)


def test_p6_pytest_raises_counts_as_check(tmp_path):
    files = {
        "src/app.py": "def boom():\n    raise ValueError\n",
        "tests/test_app.py": "import pytest\nfrom app import boom\n\ndef test_boom():\n    with pytest.raises(ValueError):\n        boom()\n",
    }
    assert check(project(tmp_path, files)) == []


# ---------------------------------------------------------------- waivers, debt, disabling

def test_waiver_needs_reason(tmp_path):
    src = """
    MRP = 3932  # proven: allow P3: test value for the example calculation
    # proven: allow P3: value from the client's specification
    MZP = 85000
    НДС = 12  # proven: разрешить P3: the Russian keyword works too
    VAT = 12  # proven: allow P3
    """
    issues = run(tmp_path, src)
    assert codes(issues, "P3") == ['"VAT" is a number typed from memory — it needs a dated source']
    assert codes(issues, "P0") == ["waiver of P3 without a reason"]


def test_disable_rule(tmp_path):
    toml = BASE_TOML + '\n[proven.rules]\ndisable = ["P3"]\n'
    assert run(tmp_path, "MRP = 3932\n", toml=toml) == []


def test_baseline(tmp_path, monkeypatch):
    cfg = project(tmp_path, {"src/app.py": "MRP = 3932\n"})
    monkeypatch.chdir(tmp_path)
    assert main(["baseline"]) == 0
    assert check(cfg) == []
    (tmp_path / "src/app.py").write_text("MRP = 3932\nMZP = 85000\n", encoding="utf-8")
    left = check(cfg)
    assert [i.message for i in left if i.code == "P3"] == ['"MZP" is a number typed from memory — it needs a dated source']
    assert len(check(cfg, use_baseline=False)) > len(left)


# ---------------------------------------------------------------- output and command line

def test_issue_and_report():
    issue = Issue("a.py", 3, 1, "P3", "bad")
    assert issue.to_dict()["rule"] == "No facts from memory"
    report = format_report([issue])
    assert "a.py:3:1  P3  bad" in report and "Code rejected" in report
    assert format_report([]) == "Proven: no issues. Code accepted."


def test_config_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert isinstance(cfg, Config) and cfg.root == tmp_path.resolve()
    assert "МРП" in cfg.fact_names and "verify" in cfg.taint_sanitizers


def test_cli_check_json(tmp_path, capsys):
    project(tmp_path, {"src/app.py": "import json\njson.loadz('1')\n"})
    assert main(["check", str(tmp_path / "src"), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["accepted"] is False
    assert [i["code"] for i in data["issues"]] == ["P2"]
    assert data["issues"][0]["hint"].startswith("Open the documentation")


def test_cli_explain_rules_init(tmp_path, monkeypatch, capsys):
    assert main(["explain", "p4"]) == 0
    assert "verify" in capsys.readouterr().out
    assert main(["explain", "P9"]) == 1
    assert main(["rules"]) == 0
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert load_config(tmp_path).fact_max_age_days == 400
    assert main(["init"]) == 1
    assert main([]) == 0


def test_hook_post(tmp_path, monkeypatch, capsys):
    project(tmp_path, {"src/app.py": "MRP = 3932\n", "src/other.py": "MZP = 1\n", "notes.md": "x"})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = {"tool_input": {"file_path": str(tmp_path / "src/app.py")}}
    assert main(["check", "--hook", "post"], stdin=io.StringIO(json.dumps(payload))) == 2
    err = capsys.readouterr().err
    assert "MRP" in err and "MZP" not in err
    payload = {"tool_input": {"file_path": str(tmp_path / "notes.md")}}
    assert main(["check", "--hook", "post"], stdin=io.StringIO(json.dumps(payload))) == 0


def test_hook_stop(tmp_path, monkeypatch):
    project(tmp_path, {"src/app.py": "MRP = 3932\n"})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert main(["check", "--hook", "stop"], stdin=io.StringIO("{}")) == 2
    assert main(["check", "--hook", "stop"], stdin=io.StringIO('{"stop_hook_active": true}')) == 0


# ---------------------------------------------------------------- proven_types

def test_verify_and_unverified():
    raw = unverified("answer")
    assert verify(raw, lambda text: []) == "answer"
    with pytest.raises(NotVerified) as err:
        verify(raw, lambda text: ["the quote is not in the database", ""])
    assert err.value.problems == ["the quote is not in the database"]


def test_fact():
    fact = Fact(4325, source="law", date="2026-01-01")
    assert fact.age_days(date(2026, 1, 11)) == 10
    with pytest.raises(ValueError):
        Fact(1, source=" ", date="2026-01-01")
    with pytest.raises(ValueError):
        Fact(1, source="law", date="yesterday")


# ---------------------------------------------------------------- examples and the judge itself

def test_examples():
    cfg = load_config(ROOT / "examples", today=TODAY)
    bad = check(cfg, [str(ROOT / "examples/bad")])
    assert {i.code for i in bad} == {"P1", "P2", "P3", "P4", "P5", "P6"}
    assert check(cfg, [str(ROOT / "examples/good")]) == []


def test_judge_accepts_itself():
    issues = check(load_config(ROOT))
    assert issues == [], format_report(issues)


def test_version():
    assert proven.VERSION.count(".") == 2


# ---------------------------------------------------------------- GitHub annotations

def test_format_github(tmp_path, monkeypatch):
    cfg = project(tmp_path, {"src/app.py": "MRP = 3932\n"})
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    out = proven.format_github(check(cfg), cfg)
    assert out.startswith("::error file=src/app.py,line=1,col=1,title=P3 No facts from memory::")
    assert proven._gh_escape("a:b,c%\n", prop=True) == "a%3Ab%2Cc%25%0A"


def test_cli_check_github(tmp_path, monkeypatch, capsys):
    project(tmp_path, {"src/app.py": "MRP = 3932\n"})
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main(["check", str(tmp_path / "src"), "--github"]) == 1
    out = capsys.readouterr().out
    assert "::error file=src/app.py,line=1" in out and "Code rejected" in out
    assert summary.read_text(encoding="utf-8").startswith("### Proven")


# ---------------------------------------------------------------- MCP server

def mcp_session(messages, check_installed=False):
    stdin = io.StringIO("\n".join(m if isinstance(m, str) else json.dumps(m) for m in messages) + "\n")
    stdout = io.StringIO()
    assert proven.serve_mcp(stdin, stdout, check_installed=check_installed) == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_mcp_handshake_and_tools():
    replies = mcp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
        "not json",
    ])
    init, tools, ping, unknown, parse_error = replies
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"]["version"] == proven.VERSION
    assert [t["name"] for t in tools["result"]["tools"]] == ["proven_check", "proven_explain", "proven_rules"]
    assert ping["result"] == {}
    assert unknown["error"]["code"] == -32601
    assert parse_error["error"]["code"] == -32700


def test_mcp_unknown_protocol_version_gets_latest():
    [init] = mcp_session([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}}])
    assert init["result"]["protocolVersion"] == proven.MCP_VERSIONS[-1]


def test_mcp_check_explain_rules(tmp_path):
    project(tmp_path, {"src/app.py": "MRP = 3932\n", "tests/test_app.py": "def test_x():\n    assert 1 + 1 == 2\n"})
    call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}}
    checked, one_file, explained, listed, missing, bad_rule = mcp_session([
        call(1, "proven_check", {"path": str(tmp_path)}),
        call(2, "proven_check", {"path": str(tmp_path), "files": ["src/app.py"]}),
        call(3, "proven_explain", {"code": "p4"}),
        call(4, "proven_rules", {}),
        call(5, "proven_check", {"path": str(tmp_path / "nowhere")}),
        call(6, "proven_explain", {"code": "P9"}),
    ])
    result = checked["result"]
    assert result["isError"] is False and result["structuredContent"]["accepted"] is False
    assert [i["code"] for i in result["structuredContent"]["issues"]] == ["P3"]
    assert "Code rejected" in result["content"][0]["text"]
    assert one_file["result"]["structuredContent"]["issues"][0]["path"] == "src/app.py"
    assert "verify" in explained["result"]["content"][0]["text"]
    assert "P6" in listed["result"]["content"][0]["text"]
    assert missing["result"]["isError"] is True and bad_rule["result"]["isError"] is True


def test_cli_mcp(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"jsonrpc": "2.0", "id": 7, "method": "ping"}\n'))
    assert main(["mcp", "--no-installed-check"]) == 0
    assert json.loads(capsys.readouterr().out) == {"jsonrpc": "2.0", "id": 7, "result": {}}


# ---------------------------------------------------------------- 0.2.0: field test on a real project

def fake_venv(root, packages):
    """A project .venv with packages: {distribution: (module, version, source)}."""
    (root / ".venv").mkdir()
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    site = root / ".venv" / "lib" / "python3.12" / "site-packages"
    for dist, (module, version, source) in packages.items():
        (site / module).mkdir(parents=True)
        (site / module / "__init__.py").write_text(source, encoding="utf-8")
        info = site / f"{dist.replace('-', '_')}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n", encoding="utf-8")
        (info / "RECORD").write_text(f"{module}/__init__.py,,\n{info.name}/METADATA,,\n", encoding="utf-8")


def test_project_root_without_proven_toml(tmp_path):
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    assert load_config(tmp_path / "tools").root == tmp_path.resolve()


def test_project_venv_and_distribution_names(tmp_path):
    (tmp_path / "requirements.txt").write_text("python-dotenv==1.0.1\nrequests==2.32.3\n", encoding="utf-8")
    fake_venv(tmp_path, {"python-dotenv": ("dotenv", "1.0.1", "def load_dotenv():\n    return True\n"),
                         "requests": ("requests", "2.32.3", "from .api import get, post\n")})
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "app.py").write_text(
        "import dotenv\nimport requests\ndotenv.load_dotenv()\nrequests.get('x')\nrequests.gett('x')\n", encoding="utf-8")
    cfg = load_config(tmp_path / "tools")
    cfg.disable.append("P6")
    issues = check(cfg, [str(tmp_path / "tools")])
    assert codes(issues, "P1") == []
    assert codes(issues, "P2") == ['module "requests" has no name "gett"']
    m = proven.measured(cfg)
    assert m["requirements"] == ["requirements.txt"] and m["environments"] == [".venv"]
    assert "packages from: the judge's own + .venv" in proven.format_measured(m)


def test_measured_warns_without_project_environment(tmp_path):
    cfg = project(tmp_path, {"src/app.py": "X = 1\n"})
    cfg.check_installed = True
    line = proven.format_measured(proven.measured(cfg))
    assert "requirements: none found (looked for requirements.txt)" in line
    assert "run the judge with the project's Python" in line


def test_p3_local_hosts_are_not_facts(tmp_path):
    src = 'A = "http://127.0.0.1:8000/ask"\nB = "http://localhost/x"\nC = "http://printer.local/"\nD = "https://example.org/"\n'
    assert codes(run(tmp_path, src), "P3") == ['address "example.org" typed by hand — move it to a config or allowed_url_hosts']


def test_p5_waiver_inside_the_handler(tmp_path):
    src = """
    try:
        x = 1
    except OSError:
        pass  # proven: allow P5: logging a call must not break the answer
    try:
        x = 2
    except OSError:
        pass  # proven: allow P5
    """
    issues = run(tmp_path, src)
    assert len(codes(issues, "P5")) == 1 and codes(issues, "P0") == ["waiver of P5 without a reason"]


def test_p6_project_without_tests_is_one_issue(tmp_path):
    cfg = project(tmp_path, {"src/app.py": "def a():\n    return 1\n\ndef b():\n    return 2\n\nclass C:\n    pass\n"})
    assert codes(check(cfg), "P6") == ['no tests found in tests: 3 public function(s) and class(es) are not proven — '
                                       'write tests, or set disable = ["P6"] in proven.toml until there are some']


def test_p4_annotated_return_is_fine(tmp_path):
    src = "def ask(q) -> Unverified:\n    return llm(q)\n\ndef _inner(q):\n    return llm(q)\n"
    assert codes(run(tmp_path, src), "P4") == []
