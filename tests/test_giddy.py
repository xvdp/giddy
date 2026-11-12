"""End-to-end tests on throwaway repositories built in tmp_path."""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from giddy.__main__ import main
from giddy.core import Options, compare, write
from giddy.git import GiddyError, Repo
from giddy.index import read_meta
from giddy.lang import PythonAdapter
from giddy.snapshots import resolve, resolve_pair

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent"}

V0 = '''\
"""module doc"""
import os

X = 1


class A:
    """class doc"""
    k = 1

    def run(self, a):
        """doc"""
        return a + 1

    @property
    def p(self):
        return 1

    @p.setter
    def p(self, v):
        pass


class B:
    def run(self, a):
        def inner(z):
            return z * 2
        return inner(a)


def top(x):
    # comment
    return x
'''


class Git:
    def __init__(self, root: Path):
        self.root = root

    def __call__(self, *args: str) -> str:
        env = {**os.environ, **ENV}
        return subprocess.run(["git", *args], cwd=self.root, env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text) if text.startswith("\n") else text)

    def commit(self, msg: str) -> str:
        self("add", "-A")
        self("commit", "-q", "-m", msg)
        return self("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    g = Git(tmp_path)
    g("init", "-q", "-b", "main")
    g.write("pkg/mod.py", V0)
    g.write("README.md", "hello\n")
    g.commit("c0")                                                  # 0000
    g.write("pkg/mod.py", V0.replace("return inner(a)", "return inner(a) + 1"))
    g.commit("c1: B.run")                                           # 0001
    g.write("pkg/mod.py", g.root.joinpath("pkg/mod.py").read_text()
            .replace('"""doc"""', '"""changed doc"""').replace("# comment", "# other comment"))
    g.commit("c2: docs only")                                       # 0002
    g.write("pkg/mod.py", g.root.joinpath("pkg/mod.py").read_text()
            .replace("return z * 2", "return z * 3").replace("X = 1", "X = 2"))
    g.write("pkg/sub/deep.py", "def d():\n    return 0\n")
    g.write("lib.rs", "fn main() {\n    let a = 1;\n}\n")
    g.commit("c3: closure, constant, new file")                     # 0003
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GIDDY_DIR", raising=False)
    return g


def keys(result):
    return {fc.path: [u.key for u in fc.units] for fc in result.files}


# -- snapshot resolution ----------------------------------------------------------------------------
def test_resolve_points(repo):
    r = Repo(repo.root)
    head = repo("rev-parse", "HEAD")
    assert resolve(r, "HEAD").index == 3
    assert resolve(r, "-1").index == 2
    assert resolve(r, "~3").index == 0
    assert resolve(r, "1").index == 1
    assert resolve(r, head[:7]).index == 3
    assert resolve(r, "S").kind == "index" and resolve(r, "unstaged").kind == "worktree"
    with pytest.raises(GiddyError):
        resolve(r, "-9")
    with pytest.raises(GiddyError):
        resolve(r, "nope")


def test_pair_defaults_and_order(repo):
    r = Repo(repo.root)
    new, old = resolve_pair(r, None, None)
    assert (new.label, old.label) == ("0003", "0002")
    new, old = resolve_pair(r, "0", None)
    assert (new.label, old.label) == ("0000", "E")
    new, old = resolve_pair(r, "1", "3")                    # reversed input
    assert (new.label, old.label) == ("0003", "0001")
    new, old = resolve_pair(r, "S")
    assert (new.label, old.label) == ("S", "0003")
    new, old = resolve_pair(r, "0001", "U")
    assert (new.label, old.label) == ("U", "0001")
    with pytest.raises(GiddyError):
        resolve_pair(r, "3", "HEAD")


# -- python units -----------------------------------------------------------------------------------
def test_python_units_qualified_and_unique():
    units = PythonAdapter().units(V0, code_only=True)
    names = [u.key for u in units]
    assert names == ["<module>", "A", "A.run", "A.p", "A.p#2", "B", "B.run", "top"]
    assert "inner" not in " ".join(names)                  # closure stays inside B.run


def test_docstring_comment_only_change_is_dropped(repo):
    res = compare(Options(new="2"))
    assert res.files == [] and res.skipped == ["pkg/mod.py"]
    raw = compare(Options(new="2", code_only=False))
    assert set(keys(raw)["pkg/mod.py"]) == {"A.run", "top"}


def test_method_change_reports_enclosing_function(repo):
    res = compare(Options(new="1"))
    assert keys(res) == {"pkg/mod.py": ["B.run"]}
    unit = res.files[0].units[0]
    assert unit.status == "modified" and (unit.added, unit.removed) == (1, 1)
    assert "def inner(z):" in "\n".join(l.text for l in unit.full_code())


def test_closure_constant_and_added_file(repo):
    res = compare(Options(exts=("py",)))
    assert keys(res) == {"pkg/mod.py": ["<module>", "B.run"], "pkg/sub/deep.py": ["d"]}
    module = res.files[0].units[0]
    assert [r.tag + r.line.text for r in module.rows] == ["-X = 1", "+X = 2"]   # imports untouched
    fn = res.files[0].units[1]
    assert len(fn.hunks) == 1 and fn.rows[0].line.text == "    def run(self, a):"  # whole method
    assert fn.rows[-1].line.text == "        return inner(a) + 1"
    new_file = res.files[1]
    assert new_file.status == "A" and [l.text for l in new_file.code] == ["def d():", "    return 0"]


def test_ext_and_path_filters(repo):
    assert set(keys(compare(Options(exts=None)))) == {"pkg/mod.py", "pkg/sub/deep.py", "lib.rs"}
    assert set(keys(compare(Options(exts=("rs",))))) == {"lib.rs"}
    assert set(keys(compare(Options(path="pkg/sub")))) == {"pkg/sub/deep.py"}
    with pytest.raises(GiddyError):
        compare(Options(path="/"))


def test_staged_unstaged_untracked(repo):
    repo.write("pkg/sub/deep.py", "def d():\n    return 1\n")
    repo("add", "pkg/sub/deep.py")
    repo.write("pkg/sub/deep.py", "def d():\n    return 2\n")
    repo.write("pkg/new.py", "def n():\n    pass\n")
    staged = compare(Options(new="S"))
    assert staged.files[0].units[0].rows[-1].line.text == "    return 1"
    work = compare(Options(new="U"))
    assert keys(work) == {"pkg/sub/deep.py": ["d"]}
    assert work.files[0].units[0].rows[-1].line.text == "    return 2"
    assert "pkg/new.py" in keys(compare(Options(new="U", untracked=True)))


def test_rename_delete_and_syntax_error(repo):
    repo("mv", "pkg/sub/deep.py", "pkg/sub/deeper.py")
    repo.write("pkg/broken.py", "def f(:\n")
    repo("rm", "-q", "lib.rs")
    repo.commit("c4")
    res = compare(Options(exts=None))
    by = {fc.path: fc for fc in res.files}
    assert by["lib.rs"].status == "D"
    assert by["pkg/broken.py"].note == "parse failed: line diff"
    assert "pkg/sub/deeper.py" not in by                  # pure rename, no code change


# -- output -----------------------------------------------------------------------------------------
def test_report_name_markdown_and_index(repo):
    res = write(compare(Options(exts=("py",), calls=False)))
    assert res.report == repo.root / ".giddy" / "main_0003_0002_py.md"
    md = res.report.read_text()
    assert read_meta(res.report)["new"]["index"] == 3
    assert md.count("<details open>") == 3 and md.count("<details>") == 1    # file fold + 2 units, new file
    assert "<summary><i>MOD</i> <b>module</b>, <b>B.run()</b></summary>" in md or "<i>MOD</i> <b>B.run()</b>" in md
    assert "```diff\n@@ -25,4 +25,4 @@ B.run()\n     def run(self, a):" in md
    assert "```python\ndef d():\n    return 0\n```" in md
    closed = compare(Options(exts=("py",), open_max=0, calls=False)).markdown
    assert closed.count("<details open>") == 1 and "<pre>-        return inner(a) + 1" not in closed
    assert "<pre>-            return z * 2\n+            return z * 3</pre>" in closed
    assert "](../pkg/mod.py#L" in md and '](#f1)' in md

    repo.write("pkg/sub/deep.py", "def d():\n    return 5\n")
    repo("add", "-A")
    os.chdir(repo.root / "pkg" / "sub")
    staged = write(compare(Options(new="S", exts=("py",))))
    assert staged.report.name == "_main_S_0003_pkg-sub_py.md"

    index = (repo.root / ".giddy" / "commits.md").read_text()
    row = next(l for l in index.splitlines() if l.startswith("| 0003_"))
    assert "main_0003_0002_py.md" in row and "_main_S_0003_pkg-sub_py.md" in row
    assert sum(l.startswith("| 000") for l in index.splitlines()) == 4


def test_cli(repo, capsys):
    assert main(["-1", "HEAD", "-e", "py,rs"]) == 0
    assert (repo.root / ".giddy" / "main_0003_0002_py-rs.md").is_file()
    assert main(["2"]) == 0 and "no code changes" in capsys.readouterr().out
    assert main(["bogus"]) == 2
    assert main(["1", "--stdout"]) == 0 and capsys.readouterr().out.startswith("<!-- giddy ")
    out = repo.root / "custom" / "x.md"
    assert main(["1", "-o", str(out)]) == 0 and out.is_file()


def test_one_line_class_crlf_and_empty_repo(tmp_path, monkeypatch):
    g = Git(tmp_path)
    g("init", "-q", "-b", "main")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_bytes(b'class A: pass\r\ndef f():\r\n    """doc"""\r\n')
    with pytest.raises(GiddyError):
        compare(Options())
    assert keys(compare(Options(new="U", untracked=True))) == {"a.py": ["A", "f"]}
    g.commit("c0")
    (tmp_path / "a.py").write_bytes(b'class A: x = 1\r\ndef f():\r\n    """doc"""\r\n    return 1\r\n')
    res = compare(Options(new="U"))
    cls, fn = res.files[0].units
    assert [l.text for l in cls.full_code()] == ["class A: x = 1"]
    assert [l.text for l in fn.full_code()] == ["def f():", "    return 1"]


def test_nearest_code_changes_when_empty(repo, capsys):
    assert main(["2", "-e", "py"]) == 0                     # 0002 changed docstrings only
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "giddy: no code changes 0002 <- 0001 under '.' [py]"
    assert out[1] == "giddy: previous code change: 0001 " + repo("rev-parse", "--short", "HEAD~2") + " c1: B.run"
    assert out[2].startswith("giddy: next code change: 0003 ")

    assert main(["2", "-e", "rs"]) == 0                     # lib.rs first appears in 0003
    out = capsys.readouterr().out.splitlines()
    assert out[1:3] == ["giddy: previous code change: none", out[2]] and " 0003 " in out[2]

    repo.write("pkg/sub/deep.py", 'def d():\n    """doc"""\n    return 0\n')
    repo.commit("c4: docstring")                            # 0004: no code change at HEAD
    assert main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "giddy: no code changes 0004 <- 0003 under '.'"
    assert out[1].startswith("giddy: last code change under '.': 0003 ") and out[2].startswith("git log")

    repo.write("pkg/sub/deep.py", 'def d():\n    """doc 2"""\n    return 0\n')
    repo("add", "-A")
    assert main(["S", "-p", "pkg/sub"]) == 0                # staged docstring edit; 0004 skipped too
    out = capsys.readouterr().out.splitlines()
    assert out[1].startswith("giddy: last code change under 'pkg/sub': 0003 ")
