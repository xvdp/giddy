"""Static Python call graph and incoming-call trees.

Index
    Every `.py` file in the scope folders and every ```python fence in `.md` files under the script
    folders is parsed with `ast`. Definitions: module-level code (`<module>`), classes, and functions
    reached through classes only (a closure's calls belong to its enclosing function, as in the diff
    units). Module names come from the package layout: parent folders holding `__init__.py`.

Call resolution, per call site `f(...)`, `a.b.f(...)`
    exact       local definitions, `import` / `from ... import` aliases (relative imports and package
                re-exports followed), `self.m` / `cls.m` through the class and its indexed bases,
                `super().m`, `Class.m`, `module.f`; `Class(...)` is a call to the class
    inferred    a call on a receiver of unknown type, `x.m(...)`, matches by method name when exactly
                one indexed method has that name; rendered with `~`
    unresolved  calls into code outside the index; dropped
Calls passed as values (`map(f, xs)`, callbacks) are not calls and are not traced.

Trees
    A tree lists the callers of a root definition, then their callers, recursively: an incoming call
    hierarchy. Calls to `C.__init__` include calls to `C`. Changed definitions are processed from the
    lowest call level up: level 0 calls no other changed definition, level k calls one of level k-1.
    A changed definition that appears in an earlier tree is bold there and gets no tree of its own.
"""
from __future__ import annotations

import ast
import html
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .git import GiddyError, Repo

MAX_DEPTH = 8
MAX_LINES = 300
NEW_TAB = ' target="_blank" rel="noopener"'   # honoured by browsers; GitHub strips it, VSCode opens files in the editor
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*(python|py|python3)\b[^\n]*\n(.*?)^\1\2\s*$", re.M | re.S)


@dataclass
class Def:
    id: str                     # "<module>.<qualname>", "<module>.<module>" for module-level code
    module: str
    qual: str
    kind: str                   # function | class | module
    path: str                   # file path, repo-relative when inside the repository
    line: int
    args: str = ""
    cls: str | None = None      # owning class id for methods
    bases: list[tuple[str, ...]] = field(default_factory=list)


@dataclass
class Site:
    caller: str
    line: int
    inferred: bool


@dataclass
class _Module:
    name: str
    path: str
    line0: int                  # line offset of a markdown fence
    imports: dict[str, str] = field(default_factory=dict)
    top: dict[str, str] = field(default_factory=dict)          # top-level name -> def id
    raw: list[tuple[str, tuple[str, ...], int, str | None]] = field(default_factory=list)


def _chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return _chain(node.value) + (node.attr,)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super":
        return ("super()",)
    return ("?",)


class _Collector(ast.NodeVisitor):
    def __init__(self, graph: "CallGraph", mod: _Module):
        self.g, self.m = graph, mod
        self.stack: list[Def] = []
        self.module_def = graph.add(Def(f"{mod.name}.<module>", mod.name, "<module>", "module", mod.path, mod.line0 + 1))

    def scope(self) -> Def:
        return self.stack[-1] if self.stack else self.module_def

    def qual(self, name: str) -> str:
        return ".".join([d.qual for d in self.stack[-1:]] + [name]) if self.stack else name

    def in_function(self) -> bool:
        return any(d.kind == "function" for d in self.stack)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self.in_function():
            self.generic_visit(node)
            return
        q = self.qual(node.name)
        d = self.g.add(Def(f"{self.m.name}.{q}", self.m.name, q, "class", self.m.path, self.m.line0 + node.lineno,
                           bases=[_chain(b) for b in node.bases]))
        if not self.stack:
            self.m.top[node.name] = d.id
        self.stack.append(d)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node) -> None:
        if self.in_function():
            self.generic_visit(node)
            return
        q = self.qual(node.name)
        owner = self.stack[-1].id if self.stack else None
        try:
            args = ast.unparse(node.args)
        except Exception:                                   # pragma: no cover
            args = ""
        d = self.g.add(Def(f"{self.m.name}.{q}", self.m.name, q, "function", self.m.path,
                           self.m.line0 + node.lineno, args, owner))
        if not self.stack:
            self.m.top[node.name] = d.id
        self.stack.append(d)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.asname:
                self.m.imports[a.asname] = a.name
            else:
                head = a.name.split(".")[0]
                self.m.imports.setdefault(head, head)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        if node.level:
            pkg = self.m.name.split(".")
            if not self.m.path.endswith("__init__.py"):
                pkg = pkg[:-1]
            pkg = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
            base = ".".join(pkg + ([base] if base else []))
        for a in node.names:
            if a.name != "*":
                self.m.imports[a.asname or a.name] = f"{base}.{a.name}" if base else a.name

    def visit_Call(self, node: ast.Call) -> None:
        d = self.scope()
        cls = d.id if d.kind == "class" else d.cls
        self.m.raw.append((d.id, _chain(node.func), self.m.line0 + node.lineno, cls))
        self.generic_visit(node)


class CallGraph:
    def __init__(self):
        self.defs: dict[str, Def] = {}
        self.modules: dict[str, _Module] = {}
        self.callers: dict[str, list[Site]] = defaultdict(list)
        self.callees: dict[str, set[str]] = defaultdict(set)
        self._methods: dict[str, list[str]] = defaultdict(list)

    def add(self, d: Def) -> Def:
        self.defs[d.id] = d
        return d

    # -- building -------------------------------------------------------------------------------
    def add_source(self, module: str, path: str, source: str, line0: int = 0) -> None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            return
        mod = _Module(module, path, line0)
        self.modules[module] = mod
        _Collector(self, mod).visit(tree)

    def resolve(self) -> "CallGraph":
        for d in self.defs.values():
            if d.kind == "function" and d.cls:
                self._methods[d.qual.rsplit(".", 1)[-1]].append(d.id)
        for mod in self.modules.values():
            for caller, chain, line, cls in mod.raw:
                target, inferred = self._target(mod, chain, cls)
                if target and target != caller:
                    self.callers[target].append(Site(caller, line, inferred))
                    self.callees[caller].add(target)
        return self

    def _module_of(self, dotted: str) -> tuple[str | None, list[str]]:
        """Longest indexed module prefix of `dotted` (suffix match for src layouts), and the rest."""
        parts = dotted.split(".")
        for k in range(len(parts), 0, -1):
            name = ".".join(parts[:k])
            if name in self.modules:
                return name, parts[k:]
            hits = [m for m in self.modules if m.endswith("." + name)]
            if len(hits) == 1:
                return hits[0], parts[k:]
        return None, parts

    def lookup(self, dotted: str, depth: int = 0) -> str | None:
        """Definition id for a dotted name, following package re-exports."""
        if dotted in self.defs:
            return dotted
        mod, rest = self._module_of(dotted)
        if mod is None or depth > 5:
            return None
        if not rest:
            return None
        m = self.modules[mod]
        if rest[0] in m.top:
            cand = ".".join([m.top[rest[0]]] + rest[1:])
            return cand if cand in self.defs else self._member(m.top[rest[0]], rest[1:])
        if rest[0] in m.imports:
            return self.lookup(".".join([m.imports[rest[0]]] + rest[1:]), depth + 1)
        return None

    def _member(self, cls_id: str, rest: list[str]) -> str | None:
        if len(rest) != 1:
            return None
        return self._mro_find(cls_id, rest[0])

    def _bases(self, cls_id: str) -> list[str]:
        d = self.defs.get(cls_id)
        if d is None:
            return []
        mod = self.modules.get(d.module)
        out = []
        for chain in d.bases:
            if mod is not None and chain[0] != "?":
                t = self._dotted(mod, chain)
                tid = self.lookup(t) if t else None
                if tid and self.defs[tid].kind == "class":
                    out.append(tid)
        return out

    def _mro_find(self, cls_id: str, name: str, skip_self: bool = False, seen: set | None = None) -> str | None:
        seen = seen or set()
        if cls_id in seen:
            return None
        seen.add(cls_id)
        if not skip_self and f"{cls_id}.{name}" in self.defs:
            return f"{cls_id}.{name}"
        for b in self._bases(cls_id):
            hit = self._mro_find(b, name, False, seen)
            if hit:
                return hit
        return None

    def _dotted(self, mod: _Module, chain: tuple[str, ...]) -> str | None:
        head = chain[0]
        if head in mod.top:
            return ".".join((mod.top[head],) + chain[1:])
        if head in mod.imports:
            return ".".join((mod.imports[head],) + chain[1:])
        return None

    def _target(self, mod: _Module, chain: tuple[str, ...], cls: str | None) -> tuple[str | None, bool]:
        if chain[0] in ("self", "cls") and len(chain) == 2 and cls:
            return self._mro_find(cls, chain[1]), False
        if chain[0] == "super()" and len(chain) == 2 and cls:
            return self._mro_find(cls, chain[1], skip_self=True), False
        if chain[0] != "?" and not (chain[0] in ("self", "cls") and len(chain) > 2):
            dotted = self._dotted(mod, chain)
            if dotted:
                hit = self.lookup(dotted)
                if hit:
                    return hit, False
                if len(chain) == 1:
                    return None, False
            elif len(chain) == 1:
                return None, False
        if len(chain) >= 2:
            cands = self._methods.get(chain[-1], [])
            if len(cands) == 1:
                return cands[0], True
        return None, False

    def incoming(self, did: str) -> dict[str, list[Site]]:
        """Callers of `did` grouped by caller; `C.__init__` also collects calls to `C`."""
        sites = list(self.callers.get(did, []))
        d = self.defs.get(did)
        if d is not None and d.cls and d.qual.endswith(".__init__"):
            sites += self.callers.get(d.cls, [])
        out: dict[str, list[Site]] = defaultdict(list)
        for s in sites:
            out[s.caller].append(s)
        return out


# -- loading ----------------------------------------------------------------------------------------
def module_name(path: str, exists) -> str:
    """Dotted module name from the package layout: parents holding `__init__.py` are packages."""
    p = PurePosixPath(path)
    parts = [p.stem] if p.stem != "__init__" else []
    parent = p.parent
    while str(parent) not in (".", "/", "") and exists(str(parent / "__init__.py")):
        parts.insert(0, parent.name)
        parent = parent.parent
    return ".".join(parts) if parts else p.parent.name


def _fences(text: str) -> list[tuple[int, str]]:
    """(line offset, code) of every python fence in markdown text."""
    out = []
    for m in FENCE.finditer(text):
        start = text.count("\n", 0, m.start(4))
        out.append((start, m.group(4)))
    return out


@dataclass
class Scope:
    """Files to index: repository files from one snapshot, plus folders outside the repository."""
    repo: Repo
    kind: str = "worktree"                  # commit | index | worktree
    sha: str | None = None
    paths: tuple[str, ...] = (".",)         # .py files under these, repo-relative or absolute
    scripts: tuple[str, ...] = ()           # .py files and ```python fences in .md files under these

    def build(self) -> CallGraph:
        repo, g = self.repo, CallGraph()
        listed = set(repo.files(self.kind, self.sha))
        exists = (lambda p: p in listed)
        wanted: dict[str, bool] = {}                        # repo path -> markdown allowed
        outside: list[tuple[Path, bool]] = []
        for folder, md in [(p, False) for p in self.paths] + [(p, True) for p in self.scripts]:
            full = Path(folder).expanduser().resolve()
            try:
                rel = full.relative_to(repo.root).as_posix()
            except ValueError:
                outside.append((full, md))
                continue
            prefix = "" if rel in ("", ".") else rel.rstrip("/") + "/"
            for f in listed:
                if (f == rel or f.startswith(prefix)) and (f.endswith(".py") or (md and f.endswith(".md"))):
                    wanted[f] = wanted.get(f, False) or md
        names = sorted(wanted)
        if self.kind == "worktree":
            blobs = [self._disk(repo.root / f) for f in names]
        else:
            prefix = f"{self.sha}:" if self.kind == "commit" else ":"
            blobs = repo.blobs([prefix + f for f in names])
        for f, data in zip(names, blobs):
            if data is not None:
                self._add(g, f, data.decode("utf-8", "replace"), exists)
        for folder, md in outside:
            files = [folder] if folder.is_file() else sorted(folder.rglob("*"))
            for f in files:
                if f.suffix == ".py" or (md and f.suffix == ".md"):
                    data = self._disk(f)
                    if data is not None:
                        self._add(g, str(f), data.decode("utf-8", "replace"), lambda p: Path(p).exists())
        return g.resolve()

    @staticmethod
    def _disk(p: Path) -> bytes | None:
        try:
            return p.read_bytes() if p.is_file() else None
        except OSError:
            return None

    @staticmethod
    def _add(g: CallGraph, path: str, text: str, exists) -> None:
        if path.endswith(".md"):
            for line0, code in _fences(text):
                g.add_source(f"{path}#L{line0 + 1}", path, code, line0)
        else:
            g.add_source(module_name(path, exists), path, text)


# -- rendering --------------------------------------------------------------------------------------
@dataclass
class Root:
    id: str
    tag: str = ""               # ADD | MOD | "" (API)


def display(g: CallGraph, did: str) -> str:
    """`gett.stack.RenderStack.reference()`: module without its top package, qualname, `()` for functions."""
    d = g.defs[did]
    if "#L" in d.module or d.module.endswith((".py", ".md")) or "/" in d.module:
        base = d.path + (f" fence L{d.line}" if d.path.endswith(".md") else "")
        return base if d.kind == "module" else f"{base} {d.qual}()"
    mod = d.module.split(".", 1)[1] if "." in d.module else d.module
    if d.kind == "module":
        return f"{mod} (module)"
    return f"{mod}.{d.qual}" + ("()" if d.kind == "function" else "")


def _href(root: Path, outdir: Path, path: str, line: int) -> str:
    full = Path(path) if os.path.isabs(path) else root / path
    rel = Path(os.path.relpath(full, outdir)).as_posix()
    return html.escape(rel, quote=True) + f"#L{line}"


class TreeWriter:
    def __init__(self, g: CallGraph, root: Path, outdir: Path, changed: dict[str, str]):
        self.g, self.root, self.outdir, self.changed = g, root, outdir, changed

    def caller(self, cid: str, sites: list[Site]) -> str:
        """Caller name linked to its first call site; further call sites as `+L<n>` links."""
        c = self.g.defs[cid]
        link = lambda s, t: f'<a href="{_href(self.root, self.outdir, c.path, s.line)}"{NEW_TAB}>{t}</a>'
        text = link(sites[0], html.escape(display(self.g, cid)))
        if cid in self.changed:
            text = f"<b>{text}</b> <i>{self.changed[cid]}</i>"
        if all(s.inferred for s in sites):
            text += " ~"
        extra = " ".join(link(s, f"+L{s.line}") for s in sites[1:4])
        return text + (" " + extra if extra else "") + (f" +{len(sites) - 4}" if len(sites) > 4 else "")

    def tree(self, rid: str) -> tuple[str, set[str]]:
        """`<pre>` incoming-call tree of `rid` and the changed definitions shown in it."""
        d = self.g.defs[rid]
        href = _href(self.root, self.outdir, d.path, d.line)
        tag = f" <i>{self.changed[rid]}</i>" if rid in self.changed else ""
        lines = [f'<b><a href="{href}"{NEW_TAB}>{html.escape(display(self.g, rid))}</a></b>{tag}']
        shown, seen = set(), {rid}
        self._walk(rid, "", 1, {rid}, seen, lines, shown)
        if len(lines) == 1:
            lines.append("└── (no callers in scope)")
        return "<pre>\n" + "\n".join(lines) + "\n</pre>", shown

    def _walk(self, did, prefix, depth, ancestors, seen, lines, shown) -> None:
        callers = sorted(self.g.incoming(did).items(), key=lambda kv: display(self.g, kv[0]))
        for k, (cid, sites) in enumerate(callers):
            if len(lines) >= MAX_LINES:
                lines.append(prefix + "└── … truncated")
                return
            last = k == len(callers) - 1
            text = self.caller(cid, sorted(sites, key=lambda s: s.line))
            if cid in self.changed:
                shown.add(cid)
            expand = False
            if cid in ancestors:
                text += "  ↻"
            elif cid in seen:
                text += "  ↑"
            elif depth >= MAX_DEPTH:
                text += "  …"
            else:
                expand = True
                seen.add(cid)
            lines.append(prefix + ("└── " if last else "├── ") + text)
            if expand:
                self._walk(cid, prefix + ("    " if last else "│   "), depth + 1, ancestors | {cid}, seen,
                           lines, shown)


def _reach(g: CallGraph, start: str, members: set[str]) -> set[str]:
    """Members reachable from `start` through calls, unchanged definitions in between included."""
    seen, todo, hit = {start}, [start], set()
    while todo:
        for c in g.callees.get(todo.pop(), ()):
            if c not in seen:
                seen.add(c)
                todo.append(c)
                if c in members:
                    hit.add(c)
    hit.discard(start)
    return hit


def levels(g: CallGraph, ids: list[str]) -> dict[str, int]:
    """Call level within `ids`: 0 reaches no other member through calls; k reaches one of level k-1.
    Members on a common cycle share a level."""
    members = set(ids)
    reach = {i: _reach(g, i, members) for i in ids}
    memo: dict[str, int] = {}

    def level(i: str, stack: frozenset) -> int:
        if i not in memo:
            below = [c for c in reach[i] if c not in stack and i not in reach[c]]
            memo[i] = 1 + max((level(c, stack | {i}) for c in below), default=-1)
        return memo[i]

    return {i: level(i, frozenset()) for i in ids}


def trees(g: CallGraph, roots: list[Root], root: Path, outdir: Path) -> list[tuple[Root, str]]:
    """Trees in level order; a root already shown bold in an earlier tree is skipped."""
    changed = {r.id: r.tag for r in roots if r.tag}
    writer = TreeWriter(g, root, outdir, changed)
    lv = levels(g, [r.id for r in roots])
    covered: set[str] = set()
    out = []
    for r in sorted(roots, key=lambda r: (lv[r.id], display(g, r.id))):
        if r.id in covered:
            continue
        text, shown = writer.tree(r.id)
        covered |= shown
        out.append((r, text))
    return out


def _details(summary: str, body: str, is_open: bool = False) -> str:
    return "\n".join(["<details open>" if is_open else "<details>", f"<summary>{summary}</summary>", "",
                      body, "", "</details>"])


# -- entry points -----------------------------------------------------------------------------------
def report_section(repo: Repo, snapshot: dict, files, paths: tuple[str, ...], scripts: tuple[str, ...],
                   outdir: Path) -> str:
    """Folded "Call trees" section for the added and modified Python units of a report."""
    py = [fc for fc in files if fc.path.endswith((".py", ".pyi")) and fc.status != "D" and fc.full_code]
    if not py:
        return ""
    kind = snapshot["kind"] if snapshot["kind"] in ("commit", "index") else "worktree"
    scope = Scope(repo, kind, snapshot.get("sha"), paths, scripts)
    g = scope.build()
    listed = set(repo.files(kind, snapshot.get("sha")))
    roots: list[Root] = []
    for fc in py:
        mod = module_name(fc.path, lambda p: p in listed)
        for u in fc.units:
            if u.status not in ("added", "modified") or u.unit.kind not in ("function", "class"):
                continue
            did = f"{mod}.{u.key.split('#')[0]}"
            if did in g.defs:
                roots.append(Root(did, "ADD" if u.status == "added" else "MOD"))
    if not roots:
        return ""
    built = trees(g, roots, repo.root, outdir)
    lone = [r for r, text in built if "(no callers in scope)" in text]
    blocks = [_details(f"{writer_name(g, r)}", text) for r, text in built if r not in lone]
    if lone:
        blocks.append("No callers in scope: " + ", ".join(writer_name(g, r) for r in lone))
    def rel(p: str) -> str:
        try:
            return Path(p).resolve().relative_to(repo.root).as_posix() or "."
        except ValueError:
            return p
    where = ", ".join(f"<code>{html.escape(rel(p))}</code>" for p in paths + scripts)
    summary = f"Call trees · {len(built) - len(lone)} trees · scope {where}"
    legend = ("callers of each root, links to the call sites · <b>bold</b> changed in this diff · "
              "~ resolved by method name only · ↑ expanded above · ↻ recursion · … depth limit")
    body = legend + "\n\n" + "\n\n".join(blocks)
    return "\n".join(['<a id="calls" name="calls"></a>', "", "## Call trees", "", _details(summary, body, is_open=True)])


def writer_name(g: CallGraph, r: Root) -> str:
    text = f"<b>{html.escape(display(g, r.id))}</b>"
    return text + (f" <i>{r.tag}</i>" if r.tag else "")


def _resolve_spec(g: CallGraph, spec, root: Path) -> str:
    """Definition id for a function object, `path.py:Qual.name`, or a dotted name."""
    if callable(spec) or isinstance(spec, type):
        import inspect
        src = inspect.getsourcefile(spec)
        qual = getattr(spec, "__qualname__", getattr(spec, "__name__", ""))
        if src is None:
            raise GiddyError(f"no source for {spec!r}")
        spec = f"{src}:{qual}"
    text = str(spec)
    if ".py:" in text:
        path, _, qual = text.partition(".py:")
        qual = qual.lstrip(":")
        full = Path(path + ".py").expanduser().resolve()
        try:
            rel = full.relative_to(root).as_posix()
        except ValueError:
            rel = str(full)
        hits = [d.id for d in g.defs.values() if d.path in (rel, str(full)) and d.qual == qual]
    else:
        hit = g.lookup(text)
        hits = [hit] if hit else [d.id for d in g.defs.values() if d.id.endswith("." + text) or d.qual == text]
    if len(hits) != 1:
        raise GiddyError(f"{spec}: {'not found in scope' if not hits else 'ambiguous: ' + ', '.join(hits[:5])}")
    return hits[0]


@dataclass
class CallReport:
    markdown: str
    path: Path | None = None


def call_stack(functions, out: str | os.PathLike | None = None, paths=(".",), scripts=(),
               snapshot: str = "U") -> CallReport:
    """Incoming-call trees for one or more functions or classes.

    functions   a function or class object, "path/to/file.py:Qual.name", a dotted name
                ("pkg.mod.Class.method"), or a list of these
    out         folder for `calls_<name>.md`; None writes nothing
    paths       folders indexed for callers (.py), default the current directory
    scripts     extra folders: .py files and ```python fences in .md files
    snapshot    "U" working tree (default), "S" staged, or a commit-ish
    """
    specs = functions if isinstance(functions, (list, tuple)) else [functions]
    repo = Repo(Path(paths[0]) if paths else ".")
    s = snapshot.strip()
    if s.lower() in ("u", "unstaged", "worktree"):
        kind, sha = "worktree", None
    elif s.lower() in ("s", "staged", "index"):
        kind, sha = "index", None
    else:
        kind, sha = "commit", repo.resolve_commit(s)
        if sha is None:
            raise GiddyError(f"unknown snapshot: {snapshot!r}")
    g = Scope(repo, kind, sha, tuple(str(p) for p in paths), tuple(str(p) for p in scripts)).build()
    ids = [_resolve_spec(g, sp, repo.root) for sp in specs]
    outdir = Path(out).expanduser().resolve() if out is not None else Path.cwd()
    built = trees(g, [Root(i) for i in ids], repo.root, outdir)
    lines = [f"# Call trees · {repo.root.name}", "",
             f"snapshot `{snapshot}` · scope " + ", ".join(f"`{p}`" for p in tuple(paths) + tuple(scripts)), ""]
    for r, text in built:
        d = g.defs[r.id]
        sig = f"({d.args})" if d.kind == "function" else ""
        lines += [f"## {display(g, r.id)}", "", f"`{d.path}:{d.line}` · `{d.qual}{sig}`", "", text, ""]
    md = "\n".join(lines).rstrip() + "\n"
    path = None
    if out is not None:
        outdir.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w.-]+", "-", display(g, ids[0]).rstrip("()")) + (f"+{len(ids) - 1}" if len(ids) > 1 else "")
        path = outdir / f"calls_{stem}.md"
        path.write_text(md, encoding="utf-8")
    return CallReport(md, path)
