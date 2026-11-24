"""gaffy call graph of a report: a VS Code view file seeded with the report's changed definitions.

gaffy (VS Code extension, https://github.com/xvdp/gaffy) resolves the seeds by name when the view opens, builds the
graph from the language servers of the working tree, and saves the view (`"save": true`). Seeds: `<repository
path>#<qualified name>` of the added and modified functions and classes in parsed source files (markdown fences
excluded). View: `<repo>/.baggy/gaffy/giddy_<report>.gaffy`, beside giddy's reports in `<repo>/.baggy/giddy/`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .lang import MARKDOWN

EXTENSION = "xvdp.gaffy"
GITHUB = "https://github.com/xvdp/gaffy"
ENV_CODE = "GIDDY_CODE"         # VS Code command line, default `code`
VIEW_DIR = Path(".baggy") / "gaffy"


@dataclass
class GaffyView:
    path: Path                  # <repo>/.baggy/gaffy/giddy_<report stem>.gaffy
    text: str                   # view document (JSON)
    seeds: list[str]
    installed: bool | None      # gaffy listed by VS Code; None: no VS Code command line
    fresh: bool                 # snapshot of the working tree or index: rewrite an existing view


def code_cli() -> str | None:
    return shutil.which(os.environ.get(ENV_CODE, "code"))


def installed() -> bool | None:
    """Whether `code --list-extensions` lists gaffy; None when the command line is missing or fails."""
    exe = code_cli()
    if not exe:
        return None
    try:
        run = subprocess.run([exe, "--list-extensions"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if run.returncode != 0:
        return None
    return EXTENSION in {line.strip().lower() for line in run.stdout.splitlines()}


def qualified(key: str) -> str | None:
    """gaffy qualified name of a unit key: parameter list (C++ overloads) and `#n` (repeated name) dropped, `::`
    written `.`; None for operators (no name a language server's symbols spell)."""
    name = re.sub(r"\(.*$", "", key).split("#")[0].replace("::", ".")
    return None if not name or "operator" in name.rsplit(".", 1)[-1] else name


def seeds(files) -> list[str]:
    """`<repository path>#<qualified name>` of the added and modified functions and classes of parsed source files;
    interfaces, structs and markdown fences excluded."""
    out: list[str] = []
    for fc in files:
        if fc.status == "D" or not fc.full_code or fc.path.rsplit(".", 1)[-1].lower() in MARKDOWN:
            continue
        for u in fc.units:
            unit = u.unit
            if u.status not in ("added", "modified"):
                continue
            if unit.kind != "function" and not (unit.kind == "class" and unit.label.startswith("class ")):
                continue
            qual = qualified(u.key)
            seed = f"{fc.path}#{qual}"
            if qual and seed not in out:
                out.append(seed)
    return out


NO_LIMIT = 1000                 # gaffy view depth read as "every level": gaffy.maxNodes bounds the data


def view(repo_root: Path, report: Path, meta: dict, files, depth: int = 0, between: bool = True) -> GaffyView | None:
    """View in gaffy's Expand mode (`>>`, one layer at a time) around the changed definitions; None without one.
    gaffy asks the language server for callers and calls from the seeds `depth` levels deep (0: until
    gaffy.maxNodes). `between` (two or more seeds): drawn are the call paths between changed definitions, any
    number of calls; unchecking Options > Seeds > Only paths between seeds keeps them and opens nothing else."""
    s = seeds(files)
    if not s:
        return None
    spec = {"seeds": s, "arrange": "grow", "direction": "both", "depth": depth if depth > 0 else NO_LIMIT, "save": True}
    if between and len(s) > 1:
        spec["between"] = True
    doc = {"gaffy": 1, "name": f"giddy {meta['new']['label']} <- {meta['old']['label']}", "view": spec}
    path = repo_root / VIEW_DIR / f"giddy_{report.stem}.gaffy"
    fresh = meta["new"]["kind"] in ("worktree", "index")
    return GaffyView(path, json.dumps(doc, indent=1) + "\n", s, installed(), fresh)


def _heading() -> list[str]:
    return ['<a id="calls" name="calls"></a>', "", "## Call graph", ""]


def section(v: GaffyView, outdir: Path) -> str:
    """Report section (last in the report): link to the view, its seeds, where to get gaffy when VS Code lacks it."""
    link = Path(os.path.relpath(v.path, outdir)).as_posix()
    spec = json.loads(v.text)["view"]
    d = spec["depth"]
    data = ("every level gaffy.maxNodes allows" if d >= NO_LIMIT
            else f"{d} caller and callee level{'s' if d != 1 else ''}")
    what = (f"call paths between the {len(v.seeds)} changed definitions, any number of calls, in the graph data "
            f"({data} from the seeds); Expand mode `>>`" if spec.get("between")
            else f"the {len(v.seeds)} changed definitions in Expand mode `>>` (graph data: {data})")
    lines = _heading() + [f"[{v.path.name}]({link}): gaffy view, {what}; built from the working tree when it opens in "
                          "VS Code, then saved.", ""]
    if not v.installed:
        why = ("gaffy not checked: VS Code's command line `code` is missing or failed" if v.installed is None
               else "gaffy is not installed in VS Code")
        lines += [f"{why}: search the VS Code Marketplace for **gaffy** (publisher xvdp), or see {GITHUB}.", ""]
    lines += ["Seeds: " + ", ".join(f"`{x}`" for x in v.seeds)]
    return "\n".join(lines)


def no_view() -> str:
    """Report section when no definition qualifies as a seed."""
    return "\n".join(_heading() + ["No gaffy view: no added or modified functions or classes in parsed source files."])


def save(v: GaffyView) -> bool:
    """Writes the view (keeps an existing one of a commit comparison) and opens it in VS Code when gaffy is
    installed, so gaffy builds and saves it. Returns whether VS Code was asked to open it."""
    if v.fresh or not v.path.exists():
        v.path.parent.mkdir(parents=True, exist_ok=True)
        v.path.write_text(v.text, encoding="utf-8")
    exe = code_cli()
    if not v.installed or not exe:
        return False
    try:
        subprocess.Popen([exe, "-r", str(v.path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True
