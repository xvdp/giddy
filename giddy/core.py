"""Entry point for one comparison: resolve snapshots, diff, render, write report and index."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .callgraph import report_section
from .diffing import FileChange, changed_paths, compare_file, is_reported, matches_ext
from .git import GiddyError, Repo
from .index import report_name, write_index
from .render import render
from .history import last_change, next_change
from .snapshots import Snapshot, describe, resolve_pair

ENV_OUT = "GIDDY_DIR"
DEFAULT_DIR = ".giddy"


@dataclass
class Options:
    new: str | None = None              # first comparison point; None => HEAD
    old: str | None = None              # second comparison point; None => previous of `new`
    path: str = "."                     # folder or file filter, relative to cwd or absolute
    exts: tuple[str, ...] | None = None # None => every file
    code_only: bool = True
    context: int = 3
    untracked: bool = False
    open_max: int = 40                  # folds with at most this many body lines render open
    out: str | None = None              # output directory or .md file; None => $GIDDY_DIR or <root>/.giddy
    calls: bool = True                  # append incoming-call trees of changed Python definitions
    scripts: tuple[str, ...] = ()       # extra caller folders: .py files, ```python fences in .md files


@dataclass
class Result:
    markdown: str
    meta: dict
    files: list[FileChange]
    skipped: list[str]
    repo: Repo | None = None
    report: Path | None = None
    index: Path | None = None


def _pathspec(repo: Repo, path: str) -> str:
    target = Path(path).resolve()
    try:
        rel = target.relative_to(repo.root)
    except ValueError as err:
        raise GiddyError(f"{path} is outside the repository {repo.root}") from err
    return rel.as_posix() or "."


def output_target(repo: Repo, out: str | None) -> tuple[Path, str | None]:
    """(directory, explicit file name or None)."""
    raw = out or os.environ.get(ENV_OUT)
    if not raw:
        return repo.root / DEFAULT_DIR, None
    p = Path(raw).expanduser()
    p = p if p.is_absolute() else Path.cwd() / p
    if p.suffix.lower() == ".md":
        return p.parent.resolve(), p.name
    return p.resolve(), None


def compare(opts: Options) -> Result:
    start = Path(opts.path)
    repo = Repo(start if start.exists() else ".")
    pathspec = _pathspec(repo, opts.path)
    new, old = resolve_pair(repo, opts.new, opts.old)
    exts = tuple(e.lower().lstrip(".") for e in opts.exts) if opts.exts else None

    files, skipped = [], []
    for status, path, old_path in changed_paths(repo, new, old, pathspec, opts.untracked):
        if not (matches_ext(path, exts) or (old_path and matches_ext(old_path, exts))):
            continue
        fc = compare_file(repo, new, old, status, path, old_path, opts.code_only, opts.context)
        if is_reported(fc):
            files.append(fc)
        else:
            skipped.append(path)

    meta = {"giddy": __version__, "branch": repo.branch or "HEAD", "new": describe(repo, new),
            "old": describe(repo, old), "path": pathspec, "exts": list(exts) if exts else None,
            "code_only": opts.code_only}
    outdir, explicit = output_target(repo, opts.out)
    calls = ""
    if opts.calls and files:
        scope = (str(repo.root / pathspec),)
        calls = report_section(repo, meta["new"], files, scope, tuple(opts.scripts), outdir)
    md = render(meta, files, skipped, repo.root, outdir, opts.open_max, calls)
    return Result(md, meta, files, skipped, repo, outdir / (explicit or report_name(meta)))


def nearest_changes(result: Result) -> dict[str, tuple[Snapshot | None, bool]]:
    """Code changes around an empty comparison, under the same path, extensions and mode.

    "previous": newest code change at or before the old side (HEAD for S/U against HEAD).
    "next": oldest code change after the new side; only when the new side is a commit other than HEAD.
    Each value: (commit snapshot or None, True when the search stopped at history.LIMIT candidates).
    """
    repo, meta = result.repo, result.meta
    assert repo is not None
    exts = tuple(meta["exts"]) if meta["exts"] else None
    args = (meta["path"], exts, meta["code_only"])
    new, old = meta["new"], meta["old"]
    out: dict[str, tuple[Snapshot | None, bool]] = {}
    out["previous"] = last_change(repo, old["sha"], *args) if old["kind"] == "commit" else (None, False)
    if new["kind"] == "commit" and new["sha"] != repo.head:
        out["next"] = next_change(repo, new["sha"], *args)
    return out


def write(result: Result, index: bool = True) -> Result:
    assert result.report is not None and result.repo is not None
    result.report.parent.mkdir(parents=True, exist_ok=True)
    result.report.write_text(result.markdown, encoding="utf-8")
    if index:
        result.index = write_index(result.repo, result.report.parent)
    return result
