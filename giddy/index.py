"""Report naming and the `commits.md` index of an output directory.

Report file name
    [_]<branch>_<new>_<old>[_<folder>]_<filters>.md
    leading `_`  new side is the staging area (S) or the working tree (U)
    <new>,<old>  snapshot labels: %04d commit index, S, U, E (empty tree), 7-char hash off the mainline
    <folder>     path filter relative to the repository root, `/` -> `-`; omitted at the root
    <filters>    extensions joined by `-` (`all` without filter), `+raw` in raw mode

commits.md lists every first-parent commit of each branch that has reports as `<index>_<short hash>`,
newest first. A report links on the row of its new commit, or for S/U reports on the row of the
commit it compares against. Pairing uses the commit hash stored in the report metadata, so reports
stay attached after a rebase renumbers the branch, and file names never need parsing.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

from .git import Repo

INDEX_NAME = "commits.md"
_META = re.compile(r"^<!-- giddy (\{.*\}) -->")


def sanitize(part: str) -> str:
    return re.sub(r"[^\w.+-]+", "-", part).strip("-") or "_"


def report_name(meta: dict) -> str:
    parts = [sanitize(meta["branch"]), meta["new"]["label"], meta["old"]["label"]]
    if meta["path"] not in (".", ""):
        parts.append("-".join(sanitize(p) for p in PurePosixPath(meta["path"]).parts))
    filters = "-".join(sanitize(e) for e in meta["exts"]) if meta["exts"] else "all"
    parts.append(filters + ("" if meta["code_only"] else "+raw"))
    prefix = "_" if meta["new"]["kind"] in ("index", "worktree") else ""
    return prefix + "_".join(parts) + ".md"


def read_meta(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            m = _META.match(fh.readline())
        return json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def _anchor_sha(meta: dict) -> str | None:
    new, old = meta["new"], meta["old"]
    if new["kind"] == "commit":
        return new["sha"]
    return old["sha"] if old["kind"] == "commit" else None


def _cell(text: str) -> str:
    return text.replace("|", "\\|")

def _dotdate(date: str) -> str:
    return date.replace('-','.')

def _joinone(exts: list | None = None, joint: str = ',.'):
    return '' if exts is None else joint.join(exts)

def write_index(repo: Repo, outdir: Path) -> Path:
    """Rebuild `outdir/commits.md` from the metadata line of every report in `outdir`.

    Each report links on exactly one row: in the section of its own branch when that section
    contains its commit, else in the first section that does, else under "Other reports".
    """
    found: list[tuple[str, dict]] = []
    for path in sorted(outdir.glob("*.md")):
        meta = read_meta(path) if path.name != INDEX_NAME else None
        if meta is not None:
            found.append((path.name, meta))

    local = repo.local_branches()
    sections = [repo.branch or "HEAD"]
    sections += [b for b in dict.fromkeys(m.get("branch") for _, m in found)
                 if b in local and b not in sections]
    commits = {b: repo.commits(b) for b in sections}
    shas = {b: {c["sha"] for c in rows} for b, rows in commits.items()}

    links: dict[tuple[str, str], list[tuple[str, dict]]] = defaultdict(list)
    rest = []
    for name, meta in found:
        sha = _anchor_sha(meta)
        home = meta.get("branch")
        order = ([home] if home in shas else []) + sections
        branch = next((b for b in order if sha in shas[b]), None)
        if branch is None:
            rest.append(name)
        else:
            links[(branch, sha)].append((name, meta))

    out = [f"# commits · {repo.root.name}", ""]
    for branch in sections:
        rows = commits[branch]
        out += [f"## {branch} ({len(rows)} commits)", "", "| commit | date | subject | reports |", "|---|---|---|---|"]
        for c in reversed(rows):
            # cell = " · ".join(f"[{m['new']['label']}←{m['old']['label']}]({n})" for n, m in links.get((branch, c["sha"]), []))
            cell = " · ".join(f"[{m['new']['label']}←{m['old']['label']}_{m['path']}/*.{_joinone(m['exts'])}]({n})"
                              for n, m in links.get((branch, c["sha"]), []))
            out.append(f"| {c['index']:04d}_{c['short']} | {_dotdate(c['date'])} | {_cell(c['subject'])} | {cell} |")
        out.append("")
    if rest:
        out += ["## Other reports", ""] + [f"- [{n}]({n})" for n in rest] + [""]
    target = outdir / INDEX_NAME
    target.write_text("\n".join(out), encoding="utf-8")
    return target
