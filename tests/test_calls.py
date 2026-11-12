"""Call graph resolution and call-tree rendering on a throwaway package."""
from __future__ import annotations

import pytest

from giddy import call_stack
from giddy.callgraph import Scope
from giddy.core import Options, compare
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
    md = compare(Options(path="src", scripts=("scripts", "docs"))).markdown
    section = md[md.index("## Call trees"):]
    assert section.count("<details open>") == 1 and section.count("<details>") == 1   # open section, one closed tree
    tree = section[section.index("<pre>"):section.index("</pre>")]
    assert tree.startswith('<pre>\n<b><a href="../src/pkg/util.py#L1" target="_blank" rel="noopener">util.helper()</a></b> <i>MOD</i>')
    assert '<b><a href="../src/pkg/app.py#L10" target="_blank" rel="noopener">app.run()</a></b> <i>MOD</i> ~' in tree   # call site; bold, no own tree
    assert "go (module)" in tree and "docs/guide.md fence L4" in tree
    assert "[Call trees](#calls)" in md
    assert "## Call trees" not in compare(Options(path="src", calls=False)).markdown


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
    md = compare(Options(path="src", scripts=("scripts", "docs"))).markdown
    tree = md[md.index("<pre>"):md.index("</pre>")].splitlines()
    run = next(i for i, l in enumerate(tree) if ">app.run()<" in l)
    assert tree[run + 1].endswith('── ... <a href="../docs/guide.md#L5" target="_blank" rel="noopener">'
                                  '<i>docs/guide.md fence L4</i></a>')
    assert tree[run + 2].endswith('── ... <a href="../scripts/go.py#L4" target="_blank" rel="noopener">'
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
