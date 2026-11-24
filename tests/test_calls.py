"""Call graph resolution and call-tree rendering on a throwaway package."""
from __future__ import annotations

import pytest

from giddy import call_stack
from giddy.callgraph import Scope, report_section
from giddy.core import Options, compare, write
from giddy.git import Repo

from test_giddy import Git

FILES = {
    "src/pkg/__init__.py": "from .core import Engine\n",
    "src/pkg/core.py": '''\
from .util import helper


class Base:
    def setup(self):
        return helper(1)


class Engine(Base):
    def __init__(self):
        self.ready = False

    def setup(self):
        super().setup()
        return self.step()

    def step(self):
        return helper(2)

    def unique_method_name(self):
        return 0
''',
    "src/pkg/util.py": "def helper(x):\n    return x\n",
    "src/pkg/app.py": '''\
import pkg.core as pc
from pkg import Engine


def run(obj):
    e = Engine()
    e.setup()
    obj.unique_method_name()
    return pc.Engine().step()
''',
    "scripts/go.py": "from pkg.app import run\n\nif __name__ == '__main__':\n    run(None)\n",
    "docs/guide.md": "Text\n\n```python\nfrom pkg.app import run\nrun(1)\n```\n",
}


@pytest.fixture
def pkg(tmp_path, monkeypatch):
    g = Git(tmp_path)
    g("init", "-q", "-b", "main")
    for path, text in FILES.items():
        g.write(path, text)
    g.commit("c0")
    monkeypatch.chdir(tmp_path)
    return g


def callers(graph, did):
    return sorted(graph.incoming(did))


def trees_md(pkg):
    """giddy's own call-tree section (callgraph.report_section), which compare() no longer appends."""
    res = compare(Options(path="src", calls=False))
    return report_section(res.repo, res.meta["new"], res.files, (str(res.repo.root / "src"),),
                          ("scripts", "docs"), res.report.parent)


def test_resolution(pkg):
    g = Scope(Repo("."), "worktree", None, ("src",), ("scripts", "docs")).build()
    assert callers(g, "pkg.util.helper") == ["pkg.core.Base.setup", "pkg.core.Engine.step"]
    assert callers(g, "pkg.core.Base.setup") == ["pkg.core.Engine.setup"]              # super()
    assert callers(g, "pkg.core.Engine.step") == ["pkg.app.run", "pkg.core.Engine.setup"]  # self., pc.Engine()
    assert callers(g, "pkg.core.Engine.setup") == []                    # e.setup(): Base.setup or Engine.setup
    assert all(s.inferred for s in g.incoming("pkg.core.Engine.unique_method_name")["pkg.app.run"])
    assert callers(g, "pkg.core.Engine.__init__") == ["pkg.app.run"]                   # Engine() via re-export
    assert callers(g, "pkg.app.run") == ["docs/guide.md#L4.<module>", "go.<module>"]


def test_report_section_levels_and_bold(pkg):
    pkg.write("src/pkg/util.py", "def helper(x):\n    return x + 1\n")
    pkg.write("src/pkg/app.py", FILES["src/pkg/app.py"].replace("return pc", "x = 1\n    return pc"))
    pkg.commit("c1")
    md = trees_md(pkg)
    section = md[md.index("## Call trees"):]
    assert section.count("<details open>") == 1 and section.count("<details>") == 1   # open section, one closed tree
    tree = section[section.index("<pre>"):section.index("</pre>")]
    assert tree.startswith('<pre>\n<b><a href="../../src/pkg/util.py#L1" target="_blank" rel="noopener">util.helper()</a></b> <i>MOD</i>')
    assert '<b><a href="../../src/pkg/app.py#L10" target="_blank" rel="noopener">app.run()</a></b> <i>MOD</i> ~' in tree   # call site; bold, no own tree
    assert "go (module)" in tree and "docs/guide.md fence L4" in tree
    assert "## Call graph" not in compare(Options(path="src", calls=False)).markdown


def test_api(pkg, tmp_path):
    rep = call_stack(["pkg.util.helper", "src/pkg/core.py:Engine.step"], out=tmp_path / "o", paths=("src",))
    assert rep.path.name == "calls_util.helper+1.md" and rep.path.is_file()
    assert "`src/pkg/util.py:1` · `helper(x)`" in rep.markdown
    assert "`src/pkg/core.py:17` · `Engine.step(self)`" in rep.markdown
    with pytest.raises(Exception):
        call_stack("pkg.nothing", paths=("src",))


def test_external_callers_and_order(pkg):
    pkg.write("src/pkg/util.py", "def helper(x):\n    return x + 1\n")
    pkg.commit("c1")
    md = trees_md(pkg)
    tree = md[md.index("<pre>"):md.index("</pre>")].splitlines()
    run = next(i for i, l in enumerate(tree) if ">app.run()<" in l)
    assert tree[run + 1].endswith('── ... <a href="../../docs/guide.md#L5" target="_blank" rel="noopener">'
                                  '<i>docs/guide.md fence L4</i></a>')
    assert tree[run + 2].endswith('── ... <a href="../../scripts/go.py#L4" target="_blank" rel="noopener">'
                                  '<i>scripts/go (module)</i></a>')
    assert ">core.Engine.setup()<" in tree[run + 3]          # internal sibling of app.run, after its subtree


def test_recursion_first_and_external_indent():
    from pathlib import Path
    from giddy.callgraph import CallGraph, TreeWriter
    g = CallGraph()
    g.add_source("m", "m.py", "def f():\n    zrec()\n\ndef zrec():\n    f()\n\ndef alpha():\n    f()\n")
    g.add_source("s", "/ext/s.py", "import m\n\ndef top():\n    m.f()\n\ndef outer():\n    top()\n",
                 origin="ext", rel="s.py")
    g.resolve()
    text, _ = TreeWriter(g, Path("/"), Path("/"), {}).tree("m.f")
    import re
    rows = [re.sub(r"<[^>]+>", "", l) for l in text.splitlines()[2:-1]]
    assert rows == ["├── m.zrec()", "│   └── m.f()  ↻", "├── m.alpha()", "└── ... ext/s.top()",
                    "        └── ext/s.outer()"]


def test_gaffy_view_section(pkg):
    pkg.write("src/pkg/util.py", "def helper(x):\n    return x + 1\n")
    pkg.commit("c1")
    res = compare(Options(path="src"))
    assert res.gaffy is not None and res.gaffy.seeds == ["src/pkg/util.py#helper"]
    assert res.gaffy.installed is None                       # no VS Code command line in tests
    md = res.markdown
    assert "[Call graph](#calls)" in md and "## Call graph" in md
    assert res.report.parent == pkg.root / ".baggy" / "giddy"
    assert res.gaffy.path == pkg.root / ".baggy" / "gaffy" / f"giddy_{res.report.stem}.gaffy"
    assert f"]({'../gaffy/giddy_' + res.report.stem}.gaffy)" in md
    assert md.rstrip().endswith("Seeds: `src/pkg/util.py#helper`")       # last section
    assert "https://github.com/xvdp/gaffy" in md and "VS Code Marketplace" in md
    write(res)
    import json
    doc = json.loads(res.gaffy.path.read_text())
    assert doc["view"] == {"seeds": ["src/pkg/util.py#helper"], "arrange": "grow", "direction": "both",
                           "depth": 1000, "save": True}


def test_gaffy_installed_and_opened(pkg, tmp_path, monkeypatch):
    log = tmp_path / "code.log"
    fake = tmp_path / "code"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\necho xvdp.gaffy\n")
    fake.chmod(0o755)
    monkeypatch.setenv("GIDDY_CODE", str(fake))
    pkg.write("src/pkg/util.py", "def helper(x):\n    return x + 2\n")
    pkg.commit("c2")
    res = compare(Options(path="src"))
    assert res.gaffy.installed is True and "not installed" not in res.markdown
    write(res)
    import time
    for _ in range(50):
        if log.exists() and "-r" in log.read_text():
            break
        time.sleep(0.05)
    assert f"-r {res.gaffy.path}" in log.read_text()


def test_gaffy_view_between(pkg):
    pkg.write("src/pkg/util.py", "def helper(x):\n    return x + 3\n")
    pkg.write("src/pkg/app.py", FILES["src/pkg/app.py"].replace("return pc", "y = 2\n    return pc"))
    pkg.commit("c3")
    import json
    res = compare(Options(path="src"))
    assert len(res.gaffy.seeds) == 2
    view = json.loads(res.gaffy.text)["view"]
    assert view["between"] is True and view["arrange"] == "grow" and view["depth"] == 1000
    assert "call paths between the 2 changed definitions, any number of calls" in res.markdown
    wide = json.loads(compare(Options(path="src", graph_depth=3, between=False)).gaffy.text)["view"]
    assert "between" not in wide and wide["depth"] == 3


def test_gaffy_seeds_every_parsed_language(pkg):
    from giddy.gaffyview import qualified
    assert qualified("SimpleKNN::knn") == "SimpleKNN.knn"
    assert qualified("coord2Morton(float3 coord, float3 minn)") == "coord2Morton"
    assert qualified("helper#2") == "helper" and qualified("CustomMin::operator()") is None
    pkg.write("src/web/app.ts", "export class Shoo {\n    run(a: number) { return a; }\n}\ninterface Goo { a: number }\n")
    pkg.write("src/knn.cu", "namespace SimpleKNN {}\nvoid SimpleKNN::knn(int P) {\n    int a = P;\n}\n")
    pkg.write("src/notes.md", "```python\ndef magoo():\n    return 1\n```\n")
    pkg.commit("c4")
    res = compare(Options(path="src"))
    assert set(res.gaffy.seeds) == {"src/web/app.ts#Shoo", "src/web/app.ts#Shoo.run", "src/knn.cu#SimpleKNN.knn"}


def test_no_view_section_and_console(pkg, capsys, monkeypatch):
    from giddy.__main__ import main
    pkg.write("src/pkg/data.json", "{}\n")
    pkg.write("src/pkg/util.py", "X = 1\n")
    pkg.commit("c5")
    monkeypatch.chdir(pkg.root)
    res = compare(Options(path="src"))
    assert res.gaffy is None and res.markdown.rstrip().endswith("functions or classes in parsed source files.")
    assert main(["-p", "src"]) == 0
    assert "giddy: gaffy view: none" in capsys.readouterr().out
    pkg.write("src/pkg/util.py", "def helper(x):\n    return x + 5\n")
    pkg.commit("c6")
    assert main(["-p", "src"]) == 0
    out = capsys.readouterr().out
    assert "giddy: gaffy view (1 seeds) -> " in out and ".baggy/gaffy/giddy_" in out and "not opened" in out
