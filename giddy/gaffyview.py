"""gaffy call graph of a report: a VS Code view file seeded with the report's changed Python definitions.

gaffy (VS Code extension, https://github.com/xvdp/gaffy) resolves the seeds by name when the view opens, draws one
caller level and one callee level around each from the language server of the working tree, and saves the view
(`"save": true`). Seeds: `<repository path>#<qualified name>`; view: `<repo>/.gaffy/views/giddy_<report>.gaffy`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

EXTENSION = "xvdp.gaffy"
GITHUB = "https://github.com/xvdp/gaffy"
ENV_CODE = "GIDDY_CODE"         # VS Code command line, default `code`


@dataclass
class GaffyView:
    path: Path                  # <repo>/.gaffy/views/giddy_<report stem>.gaffy
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


def seeds(files) -> list[str]:
    """`<repository path>#<qualified name>` of the added and modified Python functions and classes."""
    out: list[str] = []
    for fc in files:
        if not fc.path.endswith((".py", ".pyi")) or fc.status == "D" or not fc.full_code:
            continue
        for u in fc.units:
            if u.status in ("added", "modified") and u.unit.kind in ("function", "class"):
                seed = f"{fc.path}#{u.key.split('#')[0]}"
                if seed not in out:
                    out.append(seed)
    return out


def view(repo_root: Path, report: Path, meta: dict, files) -> GaffyView | None:
    """View of one level up and one level down around every changed definition; None without one."""
    s = seeds(files)
    if not s:
        return None
    doc = {"gaffy": 1, "name": f"giddy {meta['new']['label']} <- {meta['old']['label']}",
           "view": {"seeds": s, "arrange": "auto", "direction": "both", "depthUp": 1, "depthDown": 1, "save": True}}
    path = repo_root / ".gaffy" / "views" / f"giddy_{report.stem}.gaffy"
    fresh = meta["new"]["kind"] in ("worktree", "index")
    return GaffyView(path, json.dumps(doc, indent=1) + "\n", s, installed(), fresh)


def section(v: GaffyView, outdir: Path) -> str:
    """Report section: link to the view, its seeds, and where to get gaffy when VS Code lacks it."""
    link = Path(os.path.relpath(v.path, outdir)).as_posix()
    lines = ['<a id="calls" name="calls"></a>', "", "## Call graph", "",
             f"[{v.path.name}]({link}): gaffy view, one caller level and one callee level around each of the "
             f"{len(v.seeds)} changed definitions; built from the working tree when it opens in VS Code, then saved.", ""]
    if not v.installed:
        why = ("gaffy not checked: VS Code's command line `code` is missing or failed" if v.installed is None
               else "gaffy is not installed in VS Code")
        lines += [f"{why}: search the VS Code Marketplace for **gaffy** (publisher xvdp), or see {GITHUB}.", ""]
    lines += ["Seeds: " + ", ".join(f"`{x}`" for x in v.seeds)]
    return "\n".join(lines)


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
